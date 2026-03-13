import os
import calendar
import aiosqlite
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

GUILD_ID = int(os.getenv("GUILD_ID", "0"))
DB_PATH = os.path.join("data", "bot.db")
os.makedirs("data", exist_ok=True)

# ---------- TIMEZONE ----------
RIGA_UTC_OFFSET = int(os.getenv("RIGA_UTC_OFFSET", "2"))


def get_local_tz():
    try:
        return ZoneInfo("Europe/Riga")
    except Exception:
        return timezone(timedelta(hours=RIGA_UTC_OFFSET))


TZ = get_local_tz()

# ---------- REMINDERS ----------
REMINDER_MAP = {
    "none": [],
    "1h": [1],
    "3h": [3],
    "6h": [6],
    "12h": [12],
    "24h": [24],
    "all": [24, 12, 6, 3, 1],
}
VALID_REMINDERS = list(REMINDER_MAP.keys())
REMINDER_CHOICES = ["none", "1h", "3h", "6h", "12h", "24h", "all"]


# ---------- DATABASE ----------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            event_time_utc TEXT NOT NULL,
            reminder_key TEXT NOT NULL DEFAULT 'none'
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            remind_time_utc TEXT NOT NULL,
            sent INTEGER NOT NULL DEFAULT 0
        )
        """)
        await db.commit()

        # Migration: add reminder_key if older DB exists
        cur = await db.execute("PRAGMA table_info(plans)")
        cols = [r[1] for r in await cur.fetchall()]
        if "reminder_key" not in cols:
            await db.execute("ALTER TABLE plans ADD COLUMN reminder_key TEXT NOT NULL DEFAULT 'none'")
            await db.commit()


def parse_local_datetime(dt_str: str) -> datetime:
    dt = datetime.strptime(dt_str.strip(), "%Y-%m-%d %H:%M")
    return dt.replace(tzinfo=TZ)


async def rebuild_reminders(db: aiosqlite.Connection, plan_id: int, dt_local: datetime, reminder_key: str):
    # remove old reminders and build new ones from reminder_key
    await db.execute("DELETE FROM reminders WHERE plan_id=?", (plan_id,))
    hours = REMINDER_MAP.get(reminder_key, [])
    now_local = datetime.now(TZ)
    for h in hours:
        remind_local = dt_local - timedelta(hours=h)
        if remind_local > now_local:
            await db.execute(
                "INSERT INTO reminders (plan_id, remind_time_utc, sent) VALUES (?,?,0)",
                (plan_id, remind_local.astimezone(timezone.utc).isoformat())
            )


# ---------- MODALS ----------
class EditTextModal(discord.ui.Modal):
    def __init__(self, plan_id: int, user_id: int):
        super().__init__(title="Edit plan text")
        self.plan_id = plan_id
        self.user_id = user_id
        self.new_text = discord.ui.InputText(
            label="New text",
            placeholder="Example: Dentist appointment",
            min_length=1,
            max_length=200
        )
        self.add_item(self.new_text)

    async def callback(self, interaction: discord.Interaction):
        text = (self.new_text.value or "").strip()
        if not text:
            await interaction.response.send_message("Text cannot be empty.", ephemeral=True)
            return

        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "UPDATE plans SET title=? WHERE id=? AND user_id=?",
                (text, self.plan_id, self.user_id)
            )
            await db.commit()

        if cur.rowcount == 0:
            await interaction.response.send_message("That plan no longer exists.", ephemeral=True)
            return

        await interaction.response.send_message("Plan updated.", ephemeral=True)


class RescheduleModal(discord.ui.Modal):
    def __init__(self, plan_id: int, user_id: int):
        super().__init__(title="Reschedule plan")
        self.plan_id = plan_id
        self.user_id = user_id
        self.new_time = discord.ui.InputText(
            label="New date/time (YYYY-MM-DD HH:MM)",
            placeholder="2026-02-16 10:00",
            min_length=10,
            max_length=16
        )
        self.add_item(self.new_time)

    async def callback(self, interaction: discord.Interaction):
        try:
            dt_local = parse_local_datetime(self.new_time.value)
        except Exception:
            await interaction.response.send_message("Wrong format. Use: YYYY-MM-DD HH:MM", ephemeral=True)
            return

        if dt_local <= datetime.now(TZ):
            await interaction.response.send_message("Time must be in the future.", ephemeral=True)
            return

        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT reminder_key FROM plans WHERE id=? AND user_id=?",
                (self.plan_id, self.user_id)
            )
            row = await cur.fetchone()
            if not row:
                await interaction.response.send_message("That plan no longer exists.", ephemeral=True)
                return
            reminder_key = row[0] or "none"

            await db.execute(
                "UPDATE plans SET event_time_utc=? WHERE id=? AND user_id=?",
                (dt_local.astimezone(timezone.utc).isoformat(), self.plan_id, self.user_id)
            )
            await rebuild_reminders(db, self.plan_id, dt_local, reminder_key)
            await db.commit()

        await interaction.response.send_message("Rescheduled. Reminders updated.", ephemeral=True)


# ---------- MYPLANS VIEW (select + buttons) ----------
class PlansView(discord.ui.View):
    def __init__(self, user_id: int, index_to_pid: dict[int, int], message_text: str):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.index_to_pid = index_to_pid
        self.selected_index: int | None = None
        self.selected_plan_id: int | None = None
        self.message_text = message_text

        options = []
        for idx, pid in index_to_pid.items():
            options.append(discord.SelectOption(label=f"{idx}", description="Select this plan", value=str(idx)))

        self.select = discord.ui.Select(
            placeholder="Select a plan number…",
            min_values=1,
            max_values=1,
            options=options[:25]
        )
        self.select.callback = self._on_select  # type: ignore
        self.add_item(self.select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            try:
                await interaction.response.send_message("This menu is not yours. Use /myplans.", ephemeral=True)
            except Exception:
                pass
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction):
        idx = int(self.select.values[0])
        self.selected_index = idx
        self.selected_plan_id = self.index_to_pid.get(idx)
        await interaction.response.edit_message(
            content=f"{self.message_text}\n\nSelected: **#{idx}**",
            view=self
        )

    @discord.ui.button(label="Done", style=discord.ButtonStyle.success, row=1)
    async def done(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not self.selected_plan_id:
            await interaction.response.send_message("Select a plan number first.", ephemeral=True)
            return

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM reminders WHERE plan_id=?", (self.selected_plan_id,))
            cur = await db.execute(
                "DELETE FROM plans WHERE id=? AND user_id=?",
                (self.selected_plan_id, self.user_id)
            )
            await db.commit()

        if cur.rowcount == 0:
            await interaction.response.send_message("That plan no longer exists.", ephemeral=True)
            return

        await interaction.response.edit_message(content="Plan marked as completed and removed.", view=None)

    @discord.ui.button(label="Edit text", style=discord.ButtonStyle.primary, row=1)
    async def edit_text(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not self.selected_plan_id:
            await interaction.response.send_message("Select a plan number first.", ephemeral=True)
            return
        await interaction.response.send_modal(EditTextModal(self.selected_plan_id, self.user_id))

    @discord.ui.button(label="Reschedule", style=discord.ButtonStyle.secondary, row=1)
    async def reschedule(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not self.selected_plan_id:
            await interaction.response.send_message("Select a plan number first.", ephemeral=True)
            return
        await interaction.response.send_modal(RescheduleModal(self.selected_plan_id, self.user_id))

    @discord.ui.button(label="Clear past", style=discord.ButtonStyle.danger, row=1)
    async def clear_past(self, button: discord.ui.Button, interaction: discord.Interaction):
        now_utc = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT id FROM plans WHERE user_id=? AND event_time_utc < ?",
                (self.user_id, now_utc)
            )
            past_ids = [r[0] for r in await cur.fetchall()]
            for pid in past_ids:
                await db.execute("DELETE FROM reminders WHERE plan_id=?", (pid,))

            await db.execute(
                "DELETE FROM plans WHERE user_id=? AND event_time_utc < ?",
                (self.user_id, now_utc)
            )
            await db.commit()

        await interaction.response.edit_message(content="Past plans cleared.", view=None)


# ---------- CALENDAR VIEW (buttons) ----------
class CalendarView(discord.ui.View):
    def __init__(self, cog, user_id: int, year: int, month: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.user_id = user_id
        self.year = year
        self.month = month

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            try:
                await interaction.response.send_message("This calendar is not yours. Use /calendar.", ephemeral=True)
            except Exception:
                pass
            return False
        return True

    @discord.ui.button(label="⬅", style=discord.ButtonStyle.secondary)
    async def prev(self, button: discord.ui.Button, interaction: discord.Interaction):
        if self.month == 1:
            self.month = 12
            self.year -= 1
        else:
            self.month -= 1

        text = await self.cog.render_calendar_text(self.user_id, self.year, self.month)
        await interaction.response.edit_message(content=text, view=self)

    @discord.ui.button(label="➡", style=discord.ButtonStyle.secondary)
    async def next(self, button: discord.ui.Button, interaction: discord.Interaction):
        if self.month == 12:
            self.month = 1
            self.year += 1
        else:
            self.month += 1

        text = await self.cog.render_calendar_text(self.user_id, self.year, self.month)
        await interaction.response.edit_message(content=text, view=self)


# ---------- COG ----------
class Planner(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.ready = False
        self.reminder_loop.start()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.ready:
            await init_db()
            self.ready = True

    # /plan
    @discord.slash_command(
        name="plan",
        description="Create an event: YYYY-MM-DD HH:MM + title + reminder schedule.",
        guild_ids=[GUILD_ID] if GUILD_ID else None
    )
    async def plan(
        self,
        ctx: discord.ApplicationContext,
        datetime_local: str,
        title: str,
        reminders: str = discord.Option(
            str,
            description="Reminder: none, 1h, 3h, 6h, 12h, 24h before or all",
            choices=REMINDER_CHOICES
        )
    ):
        if reminders not in VALID_REMINDERS:
            await ctx.respond("Reminder must be: none, 1h, 3h, 6h, 12h, 24h, all.", ephemeral=True)
            return

        try:
            dt_local = parse_local_datetime(datetime_local)
        except Exception:
            await ctx.respond("Date/time format must be `YYYY-MM-DD HH:MM` (example: `2026-02-16 10:00`).", ephemeral=True)
            return

        if dt_local <= datetime.now(TZ):
            await ctx.respond("Time must be in the future.", ephemeral=True)
            return

        title_clean = (title or "").strip()
        if not title_clean:
            await ctx.respond("Title cannot be empty.", ephemeral=True)
            return

        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "INSERT INTO plans (user_id, channel_id, title, event_time_utc, reminder_key) VALUES (?,?,?,?,?)",
                (ctx.author.id, ctx.channel.id, title_clean, dt_local.astimezone(timezone.utc).isoformat(), reminders)
            )
            plan_id = cur.lastrowid
            await rebuild_reminders(db, plan_id, dt_local, reminders)
            await db.commit()

        await ctx.respond(
            f"Event created: **{title_clean}**\n"
            f"Time (local): **{dt_local.strftime('%Y-%m-%d %H:%M')}**\n"
            f"Reminders: **{reminders}**.",
            ephemeral=True  # EPHEMERAL
        )

    # /myplans (sections + manage)
    @discord.slash_command(
        name="myplans",
        description="Show and manage your plans (Today / Tomorrow / Later / Past).",
        guild_ids=[GUILD_ID] if GUILD_ID else None
    )
    async def myplans(self, ctx: discord.ApplicationContext):
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute(
                "SELECT id, title, event_time_utc FROM plans WHERE user_id=? ORDER BY event_time_utc ASC",
                (ctx.author.id,)
            )).fetchall()

        if not rows:
            await ctx.respond("No plans.", ephemeral=True)
            return

        today = date.today()
        now_local = datetime.now(TZ)

        sections = {"Today": [], "Tomorrow": [], "Later": [], "Past": []}
        for pid, title, utc in rows:
            dt_local = datetime.fromisoformat(utc).replace(tzinfo=timezone.utc).astimezone(TZ)

            if dt_local < now_local:
                sections["Past"].append((pid, title, dt_local))
            elif dt_local.date() == today:
                sections["Today"].append((pid, title, dt_local))
            elif dt_local.date() == today + timedelta(days=1):
                sections["Tomorrow"].append((pid, title, dt_local))
            else:
                sections["Later"].append((pid, title, dt_local))

        parts = []
        idx = 1
        index_to_pid: dict[int, int] = {}

        for group in ["Today", "Tomorrow", "Later", "Past"]:
            items = sections[group]
            if not items:
                continue

            lines = []
            for pid, title, dt in items:
                if group in ("Today", "Tomorrow"):
                    time_str = dt.strftime("%H:%M")
                else:
                    time_str = dt.strftime("%Y-%m-%d %H:%M")
                lines.append(f"{idx}. {time_str} — {title}")
                index_to_pid[idx] = pid
                idx += 1

            parts.append(f"**{group}:**\n" + "\n".join(lines))

        message_text = "\n\n".join(parts)

        view = PlansView(ctx.author.id, dict(list(index_to_pid.items())[:25]), message_text)
        await ctx.respond(content=message_text, view=view, ephemeral=True)

    # calendar helpers
    async def _days_with_plans(self, user_id: int, year: int, month: int) -> set[int]:
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute(
                "SELECT event_time_utc FROM plans WHERE user_id=?",
                (user_id,)
            )).fetchall()

        days = set()
        for (utc_str,) in rows:
            dt_local = datetime.fromisoformat(utc_str).replace(tzinfo=timezone.utc).astimezone(TZ)
            if dt_local.year == year and dt_local.month == month:
                days.add(dt_local.day)
        return days

    async def render_calendar_text(self, user_id: int, year: int, month: int) -> str:
        days_with_plans = await self._days_with_plans(user_id, year, month)

        cal = calendar.Calendar(firstweekday=0)  # 0 = Monday
        weeks = cal.monthdayscalendar(year, month)

        lines = []
        lines.append(f"{calendar.month_name[month]} {year}")
        lines.append("Mo  Tu  We  Th  Fr  Sa  Su")

        for week in weeks:
            row = []
            for day in week:
                if day == 0:
                    cell = "   "
                else:
                    mark = "." if day in days_with_plans else " "
                    cell = f"{day:2}{mark}"
                row.append(cell)
            lines.append(" ".join(row))

        cal_block = "```text\n" + "\n".join(lines) + "\n```"

        if days_with_plans:
            bold_days = ", ".join(f"**{d}**" for d in sorted(days_with_plans))
            extra = f"Days with plans (date): {bold_days}"
        else:
            extra = "Days with plans (date): **none**"
        return cal_block + "\n" + extra

    # /calendar (current month + buttons)
    @discord.slash_command(
        name="calendar",
        description="Show calendar for the current month (days with plans are marked with a dot).",
        guild_ids=[GUILD_ID] if GUILD_ID else None
    )
    async def calendar_current(self, ctx: discord.ApplicationContext):
        now = datetime.now(TZ)
        view = CalendarView(self, ctx.author.id, now.year, now.month)
        text = await self.render_calendar_text(ctx.author.id, now.year, now.month)
        text2 = f"\n📅 Today: **{now.day} {calendar.month_name[now.month]}**"
        await ctx.respond(text+text2, view=view, ephemeral=True)

    # reminders loop
    @tasks.loop(seconds=60)
    async def reminder_loop(self):
        if not self.ready:
            return

        now_utc = datetime.now(timezone.utc)

        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (await db.execute("""
                SELECT r.id, p.user_id, p.channel_id, p.title, p.event_time_utc
                FROM reminders r
                JOIN plans p ON r.plan_id = p.id
                WHERE r.sent=0 AND r.remind_time_utc <= ?
                ORDER BY r.remind_time_utc ASC
                LIMIT 50
            """, (now_utc.isoformat(),))).fetchall()

            for rid, uid, cid, title, event_utc in rows:
                dt_local = datetime.fromisoformat(event_utc).replace(tzinfo=timezone.utc).astimezone(TZ)
                msg = f"Reminder: **{title}** at {dt_local.strftime('%Y-%m-%d %H:%M')}"

                try:
                    user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                    await user.send(msg)
                except Exception:
                    ch = self.bot.get_channel(cid)
                    if ch:
                        await ch.send(f"<@{uid}> {msg}")

                await db.execute("UPDATE reminders SET sent=1 WHERE id=?", (rid,))

            await db.commit()


def setup(bot: discord.Bot):
    bot.add_cog(Planner(bot))