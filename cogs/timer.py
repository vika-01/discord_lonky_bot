import os
import math
import asyncio
import aiosqlite
import discord
from discord.ext import commands

from utils.rewards import announce_level_up  # public level-up announcement

GUILD_ID = int(os.getenv("GUILD_ID", "0"))
DB_PATH = os.path.join("data", "bot.db")
os.makedirs("data", exist_ok=True)

# XP -> shared global user_profile (used by level_leaderboard)
XP_PER_MINUTE_TIMER = 1          # /timer: 30 min -> +30 XP
XP_PER_MINUTE_LOCKIN = 2         # /fire_lock_in: focus minutes -> XP
XP_BONUS_LOCKIN_COMPLETE = 50    # bonus if fully completed (not cancelled)

# Limits
MAX_TIMER_MIN = 180
MAX_CYCLES = 8
MAX_FOCUS_MIN = 180
MAX_BREAK_MIN = 60


# ----------------------------
# Global Level / XP (shared with games/quiz)
# ----------------------------
def level_from_xp(xp: int) -> int:
    if xp < 0:
        xp = 0
    return 1 + int(math.sqrt(xp / 100.0))


async def init_user_profile_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_profile (
                user_id INTEGER PRIMARY KEY,
                xp INTEGER NOT NULL DEFAULT 0,
                level INTEGER NOT NULL DEFAULT 1
            )
        """)
        # cleanup ghost user 0 if it ever got inserted
        await db.execute("DELETE FROM user_profile WHERE user_id = 0")
        await db.commit()


async def add_xp(user_id: int, amount: int):
    """
    Adds XP to shared user_profile.
    Returns: (new_xp, new_level, xp_gained, leveled_up)
    """
    amount = max(0, int(amount))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO user_profile (user_id, xp, level)
            VALUES (?, 0, 1)
            ON CONFLICT(user_id) DO NOTHING
        """, (user_id,))

        cur = await db.execute("SELECT xp, level FROM user_profile WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        current_xp = int(row[0]) if row else 0
        old_level = int(row[1]) if row else 1

        new_xp = current_xp + amount
        new_level = level_from_xp(new_xp)

        await db.execute("UPDATE user_profile SET xp=?, level=? WHERE user_id=?", (new_xp, new_level, user_id))
        await db.commit()

    leveled_up = new_level > old_level
    return new_xp, new_level, amount, leveled_up


async def dm_user(user: discord.User, text: str) -> bool:
    try:
        await user.send(text)
        return True
    except Exception:
        return False


def fmt_mmss(seconds: int) -> str:
    seconds = max(0, int(seconds))
    m = seconds // 60
    s = seconds % 60
    return f"{m:02d}:{s:02d}"


# ----------------------------
# Cancel-only view
# ----------------------------
class CancelOnlyView(discord.ui.View):
    def __init__(self, *, user_id: int, label: str, on_cancel):
        super().__init__(timeout=None)
        self.user_id = user_id
        self._on_cancel = on_cancel
        self.add_item(_CancelButton(label=label, owner=self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This session is not yours.", ephemeral=True)
            return False
        return True

    async def cancel(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self._on_cancel(interaction)

    def disable_all(self):
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True


class _CancelButton(discord.ui.Button):
    def __init__(self, *, label: str, owner: CancelOnlyView):
        super().__init__(label=label, style=discord.ButtonStyle.danger)
        self.owner = owner

    async def callback(self, interaction: discord.Interaction):
        await self.owner.cancel(interaction)


# ----------------------------
# /timer session
# ----------------------------
class TimerSession:
    def __init__(self, *, user: discord.User, label: str, total_seconds: int):
        self.user = user
        self.user_id = user.id
        self.label = label
        self.total_seconds = total_seconds
        self.remaining = total_seconds

        self.message: discord.Message | None = None
        self.task: asyncio.Task | None = None
        self.view: CancelOnlyView | None = None

    def text_running(self) -> str:
        return (
            f"⏳ **Timer** — {self.label}\n"
            f"Time remaining: **{fmt_mmss(self.remaining)}**"
        )

    def text_done(self) -> str:
        return f"✅ **Timer** — {self.label}\n**Time is up.**"

    def text_cancelled(self) -> str:
        return f"🛑 **Timer cancelled** — {self.label}"

    async def edit_safe(self, content: str, view: discord.ui.View | None):
        if not self.message:
            return
        try:
            await self.message.edit(content=content, view=view)
        except Exception:
            pass

    async def run(self, *, ctx: discord.ApplicationContext, unregister_cb):
        try:
            while self.remaining > 0:
                await asyncio.sleep(1)
                self.remaining -= 1
                await self.edit_safe(self.text_running(), self.view)
        except asyncio.CancelledError:
            return
        finally:
            await unregister_cb(self.user_id)

        # finished
        minutes = int(self.total_seconds / 60)
        xp_gain = minutes * XP_PER_MINUTE_TIMER
        new_xp, new_level, gained, leveled_up = await add_xp(self.user_id, xp_gain)

        # remove buttons completely after finish
        await self.edit_safe(self.text_done(), None)

        # public level-up announcement (even though the timer message is ephemeral)
        if leveled_up:
            await announce_level_up(ctx.channel, self.user, new_level)

        dm_ok = await dm_user(
            self.user,
            f"✅ Timer finished: **{self.label}**\nXP: **+{gained}** → Level **{new_level}** (XP: {new_xp})"
        )
        if not dm_ok:
            try:
                await ctx.followup.send("Timer finished, but I couldn't DM you (your DMs may be closed).", ephemeral=True)
            except Exception:
                pass


# ----------------------------
# /fire_lock_in session
# ----------------------------
class FireLockInSession:
    def __init__(self, *, user: discord.User, activity: str, cycles: int, focus_min: int, break_min: int):
        self.user = user
        self.user_id = user.id

        self.activity = (activity or "Studying").strip()[:60]
        self.cycles = cycles
        self.focus_min = focus_min
        self.break_min = break_min

        self.phase = "focus"  # focus/break
        self.current_cycle = 1
        self.remaining = focus_min * 60

        self.focus_seconds_done = 0

        self.message: discord.Message | None = None
        self.task: asyncio.Task | None = None
        self.view: CancelOnlyView | None = None

    def phase_name(self) -> str:
        return "Focus" if self.phase == "focus" else "Break"

    def text_running(self) -> str:
        return (
            f"🔥 **FIRE LOCK-IN** — {self.activity}\n"
            f"Cycle: **{self.current_cycle}/{self.cycles}** | Phase: **{self.phase_name()}**\n"
            f"Time remaining: **{fmt_mmss(self.remaining)}**"
        )

    def text_cancelled(self) -> str:
        return f"🛑 **FIRE LOCK-IN cancelled** — {self.activity}"

    def text_finished(self) -> str:
        return (
            f"🔥 **FIRE LOCK-IN finished** — {self.activity}\n"
            f"**Great job — you did it!**"
        )

    async def edit_safe(self, content: str, view: discord.ui.View | None):
        if not self.message:
            return
        try:
            await self.message.edit(content=content, view=view)
        except Exception:
            pass

    async def dm_stage(self, text: str):
        await dm_user(self.user, text)

    async def run(self, *, ctx: discord.ApplicationContext, unregister_cb):
        try:
            while True:
                # countdown for current stage
                while self.remaining > 0:
                    await asyncio.sleep(1)
                    self.remaining -= 1
                    if self.phase == "focus":
                        self.focus_seconds_done += 1
                    await self.edit_safe(self.text_running(), self.view)

                # stage ended -> DM
                if self.phase == "focus":
                    await self.dm_stage(f"✅ Fire Lock-In: focus finished (cycle {self.current_cycle}/{self.cycles}).")
                    if self.break_min > 0:
                        self.phase = "break"
                        self.remaining = self.break_min * 60
                    else:
                        if self.current_cycle >= self.cycles:
                            break
                        self.current_cycle += 1
                        self.phase = "focus"
                        self.remaining = self.focus_min * 60
                else:
                    await self.dm_stage(f"🟦 Fire Lock-In: break finished (cycle {self.current_cycle}/{self.cycles}).")
                    if self.current_cycle >= self.cycles:
                        break
                    self.current_cycle += 1
                    self.phase = "focus"
                    self.remaining = self.focus_min * 60

                await self.edit_safe(self.text_running(), self.view)

        except asyncio.CancelledError:
            return
        finally:
            await unregister_cb(self.user_id)

        # finished all cycles
        focus_minutes_done = int(self.focus_seconds_done / 60)
        xp_gain = focus_minutes_done * XP_PER_MINUTE_LOCKIN
        bonus = XP_BONUS_LOCKIN_COMPLETE
        new_xp, new_level, gained, leveled_up = await add_xp(self.user_id, xp_gain + bonus)

        await self.edit_safe(self.text_finished(), None)

        # public level-up announcement
        if leveled_up:
            await announce_level_up(ctx.channel, self.user, new_level)

        await self.dm_stage(
            f"🔥 Fire Lock-In complete: **{self.activity}**\n"
            f"Focus time: **{focus_minutes_done} min**\n"
            f"XP: **+{xp_gain} + {bonus} bonus** → Level **{new_level}** (XP: {new_xp})\n"
            f"Great job — you did it!"
        )


# ----------------------------
# Cog
# ----------------------------
class Timer(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self._db_ready = False
        self.active: dict[int, asyncio.Task] = {}  # one active session per user

    async def _has_active(self, user_id: int) -> bool:
        task = self.active.get(user_id)
        if not task:
            return False
        if task.done():
            self.active.pop(user_id, None)
            return False
        return True

    async def _register(self, user_id: int, task: asyncio.Task):
        old = self.active.get(user_id)
        if old and old.done():
            self.active.pop(user_id, None)
        self.active[user_id] = task

    async def _unregister(self, user_id: int):
        self.active.pop(user_id, None)

    @commands.Cog.listener()
    async def on_ready(self):
        if self._db_ready:
            return
        await init_user_profile_db()
        self._db_ready = True

    @discord.slash_command(
        name="timer",
        description="Start a timer with real-time countdown (DM at the end).",
        guild_ids=[GUILD_ID] if GUILD_ID else None
    )
    async def timer(self, ctx: discord.ApplicationContext, minutes: int, activity: str = "Studying"):
        if await self._has_active(ctx.author.id):
            await ctx.respond("You already have an active timer/session. Cancel it first.", ephemeral=True)
            return

        minutes = int(minutes)
        if minutes < 1 or minutes > MAX_TIMER_MIN:
            await ctx.respond(f"Timer must be between 1 and {MAX_TIMER_MIN} minutes.", ephemeral=True)
            return

        label = (activity or "Studying").strip()[:60]
        session = TimerSession(user=ctx.author, label=label, total_seconds=minutes * 60)

        async def on_cancel(interaction: discord.Interaction):
            if session.task and not session.task.done():
                session.task.cancel()
            await self._unregister(session.user_id)
            await session.edit_safe(session.text_cancelled(), None)

        view = CancelOnlyView(user_id=ctx.author.id, label="Cancel timer", on_cancel=on_cancel)
        session.view = view

        await ctx.respond(session.text_running(), view=view, ephemeral=True)
        session.message = await ctx.interaction.original_response()

        task = asyncio.create_task(session.run(ctx=ctx, unregister_cb=self._unregister))
        session.task = task
        await self._register(ctx.author.id, task)

    @discord.slash_command(
        name="fire_lock_in",
        description="Fire Lock-In with real-time countdown (DM each stage + final).",
        guild_ids=[GUILD_ID] if GUILD_ID else None
    )
    async def fire_lock_in(
        self,
        ctx: discord.ApplicationContext,
        activity: str = "Studying",
        cycles: int = 4,
        focus_minutes: int = 25,
        break_minutes: int = 5
    ):
        if await self._has_active(ctx.author.id):
            await ctx.respond("You already have an active timer/session. Cancel it first.", ephemeral=True)
            return

        cycles = max(1, min(MAX_CYCLES, int(cycles)))
        focus_minutes = max(1, min(MAX_FOCUS_MIN, int(focus_minutes)))
        break_minutes = max(0, min(MAX_BREAK_MIN, int(break_minutes)))

        session = FireLockInSession(
            user=ctx.author,
            activity=activity,
            cycles=cycles,
            focus_min=focus_minutes,
            break_min=break_minutes
        )

        async def on_cancel(interaction: discord.Interaction):
            if session.task and not session.task.done():
                session.task.cancel()
            await self._unregister(session.user_id)
            await session.edit_safe(session.text_cancelled(), None)

        view = CancelOnlyView(user_id=ctx.author.id, label="Cancel fire", on_cancel=on_cancel)
        session.view = view

        await ctx.respond(session.text_running(), view=view, ephemeral=True)
        session.message = await ctx.interaction.original_response()

        task = asyncio.create_task(session.run(ctx=ctx, unregister_cb=self._unregister))
        session.task = task
        await self._register(ctx.author.id, task)


def setup(bot: discord.Bot):
    bot.add_cog(Timer(bot))