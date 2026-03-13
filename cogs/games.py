import os
import random
import math
import aiosqlite
import discord
from discord.ext import commands

from utils.rewards import announce_medal

GUILD_ID = int(os.getenv("GUILD_ID", "0"))
DB_PATH = os.path.join("data", "bot.db")
os.makedirs("data", exist_ok=True)

GAME_GUESS = "guess"
GAME_TTT = "ttt"
GAME_RPS = "rps"

GAME_LABELS = {
    GAME_GUESS: "Guess the Number",
    GAME_TTT: "Tic-Tac-Toe",
    GAME_RPS: "Rock-Paper-Scissors",
}

GAME_CHOICES = [GAME_GUESS, GAME_TTT, GAME_RPS]


# ----------------------------
# Global Level / XP
# ----------------------------
def level_from_xp(xp: int) -> int:
    """
    Level 1 at 0 XP, grows smoothly with sqrt.
    """
    if xp < 0:
        xp = 0
    return 1 + int(math.sqrt(xp / 100.0))


def xp_for_level(level: int) -> int:
    """
    Approx inverse of level_from_xp for display.
    """
    if level <= 1:
        return 0
    return int(((level - 1) ** 2) * 100)


async def add_xp(user_id: int, amount: int):
    """
    Adds XP to user profile, recalculates level.
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

        await db.execute(
            "UPDATE user_profile SET xp=?, level=? WHERE user_id=?",
            (new_xp, new_level, user_id)
        )
        await db.commit()

    leveled_up = new_level > old_level
    return new_xp, new_level, amount, leveled_up


async def get_profile(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO user_profile (user_id, xp, level)
            VALUES (?, 0, 1)
            ON CONFLICT(user_id) DO NOTHING
        """, (user_id,))
        cur = await db.execute("SELECT xp, level FROM user_profile WHERE user_id=?", (user_id,))
        xp, level = await cur.fetchone()
    return int(xp), int(level)


# ----------------------------
# Rewards (streak tiers)
# ----------------------------
def reward_for_streak(streak: int):
    """
    Returns (tier_name, medals_awarded, xp_awarded)
    """
    base_xp = 20
    if streak >= 5:
        return "Gold", 3, base_xp + 30
    if streak >= 3:
        return "Silver", 2, base_xp + 20
    return "Bronze", 1, base_xp + 10


# ----------------------------
# DB helpers
# ----------------------------
async def init_games_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS game_stats (
            user_id INTEGER NOT NULL,
            game TEXT NOT NULL,
            wins INTEGER NOT NULL DEFAULT 0,
            medals INTEGER NOT NULL DEFAULT 0,
            streak INTEGER NOT NULL DEFAULT 0,
            best_streak INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, game)
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_profile (
            user_id INTEGER PRIMARY KEY,
            xp INTEGER NOT NULL DEFAULT 0,
            level INTEGER NOT NULL DEFAULT 1
        )
        """)
        await db.commit()

        cur = await db.execute("PRAGMA table_info(game_stats)")
        cols = [r[1] for r in await cur.fetchall()]
        if "streak" not in cols:
            await db.execute("ALTER TABLE game_stats ADD COLUMN streak INTEGER NOT NULL DEFAULT 0")
        if "best_streak" not in cols:
            await db.execute("ALTER TABLE game_stats ADD COLUMN best_streak INTEGER NOT NULL DEFAULT 0")
        await db.commit()

        await db.execute("DELETE FROM user_profile WHERE user_id = 0")
        await db.execute("DELETE FROM game_stats WHERE user_id = 0")
        await db.commit()


async def ensure_row(db: aiosqlite.Connection, user_id: int, game: str):
    await db.execute("""
        INSERT INTO game_stats (user_id, game, wins, medals, streak, best_streak)
        VALUES (?, ?, 0, 0, 0, 0)
        ON CONFLICT(user_id, game) DO NOTHING
    """, (user_id, game))


async def record_win(user_id: int, game: str):
    """
    Increments wins, increments streak, updates best_streak,
    awards medals by tier, and adds global XP.
    Returns:
      (tier, medals_awarded, new_streak, total_medals_game, xp_gained, new_xp, new_level, leveled_up)
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await ensure_row(db, user_id, game)

        cur = await db.execute(
            "SELECT wins, medals, streak, best_streak FROM game_stats WHERE user_id=? AND game=?",
            (user_id, game)
        )
        wins, medals, streak, best_streak = await cur.fetchone()

        new_streak = int(streak) + 1
        tier, medal_award, xp_award = reward_for_streak(new_streak)

        new_medals = int(medals) + medal_award
        new_wins = int(wins) + 1
        new_best = max(int(best_streak), new_streak)

        await db.execute("""
            UPDATE game_stats
            SET wins=?, medals=?, streak=?, best_streak=?
            WHERE user_id=? AND game=?
        """, (new_wins, new_medals, new_streak, new_best, user_id, game))
        await db.commit()

    new_xp, new_level, xp_gained, leveled_up = await add_xp(user_id, xp_award)
    return tier, medal_award, new_streak, new_medals, xp_gained, new_xp, new_level, leveled_up


async def reset_streak(user_id: int, game: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await ensure_row(db, user_id, game)
        await db.execute("UPDATE game_stats SET streak=0 WHERE user_id=? AND game=?", (user_id, game))
        await db.commit()


async def get_leaderboard(game: str, limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (await db.execute("""
            SELECT user_id, medals, wins, best_streak
            FROM game_stats
            WHERE game = ? AND user_id != 0
            ORDER BY medals DESC, wins DESC, best_streak DESC, user_id ASC
            LIMIT ?
        """, (game, limit))).fetchall()
    return rows


async def get_overall_leaderboard(limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (await db.execute("""
            SELECT user_id,
                   SUM(medals) AS total_medals,
                   SUM(wins) AS total_wins,
                   MAX(best_streak) AS best_streak_any
            FROM game_stats
            WHERE user_id != 0
            GROUP BY user_id
            ORDER BY total_medals DESC, total_wins DESC, best_streak_any DESC, user_id ASC
            LIMIT ?
        """, (limit,))).fetchall()
    return rows


async def get_level_leaderboard(limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (await db.execute("""
            SELECT user_id, level, xp
            FROM user_profile
            WHERE user_id != 0
            ORDER BY level DESC, xp DESC, user_id ASC
            LIMIT ?
        """, (limit,))).fetchall()
    return rows


async def get_user_stats(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (await db.execute("""
            SELECT game, medals, wins, streak, best_streak
            FROM game_stats
            WHERE user_id = ?
            ORDER BY medals DESC, wins DESC, game ASC
        """, (user_id,))).fetchall()
    return rows


# ----------------------------
# Guess the Number (1..10)
# ----------------------------
class GuessView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.target = random.randint(1, 10)
        self.tries = 0
        self.max_tries = 5
        self.finished = False

        for n in range(1, 11):
            self.add_item(GuessButton(n))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This game is not yours. Run /games to start your own.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        self.disable_all_items()


class GuessButton(discord.ui.Button):
    def __init__(self, n: int):
        super().__init__(label=str(n), style=discord.ButtonStyle.secondary, row=(n - 1) // 5)
        self.n = n

    async def callback(self, interaction: discord.Interaction):
        view: GuessView = self.view
        if view.finished:
            await interaction.response.send_message("This game is already finished.", ephemeral=True)
            return

        view.tries += 1

        if self.n == view.target:
            view.finished = True
            view.disable_all_items()

            tier, medal_award, new_streak, total_medals, xp_gained, new_xp, new_level, leveled_up = await record_win(
                interaction.user.id, GAME_GUESS
            )

            # public medal announcement
            if medal_award > 0:
                await announce_medal(
                    interaction.channel,
                    interaction.user,
                    GAME_LABELS.get(GAME_GUESS, GAME_GUESS),
                    tier,
                    medal_award,
                    total_medals=total_medals,
                    streak=new_streak
                )

            # public level-up announcement
            if leveled_up and interaction.channel:
                await interaction.channel.send(f"🎉 {interaction.user.mention} reached **Level {new_level}**!")

            await interaction.response.edit_message(
                content=(
                    f"Correct. The number was **{view.target}**. You won in **{view.tries}** tries.\n"
                    f"Reward: **{tier} (+{medal_award} medals)** | Streak: **{new_streak}** | Game medals: **{total_medals}**\n"
                    f"Global: **+{xp_gained} XP** → Level **{new_level}** (XP: {new_xp})"
                ),
                view=view
            )
            return

        if view.tries >= view.max_tries:
            view.finished = True
            view.disable_all_items()
            await reset_streak(interaction.user.id, GAME_GUESS)
            await interaction.response.edit_message(
                content=f"Game over. The number was **{view.target}**. (Streak reset.)",
                view=view
            )
            return

        hint = "higher" if self.n < view.target else "lower"
        await interaction.response.edit_message(
            content=f"Not quite — try **{hint}**. Tries: {view.tries}/{view.max_tries}",
            view=view
        )


# ----------------------------
# Rock Paper Scissors (first to 3 wins)
# ----------------------------
class RPSView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.moves = ["rock", "paper", "scissors"]
        self.user_score = 0
        self.bot_score = 0
        self.finished = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This game is not yours. Run /games to start your own.", ephemeral=True)
            return False
        return True

    def _round_result(self, user: str, bot: str) -> str:
        if user == bot:
            return "draw"
        wins = {("rock", "scissors"), ("paper", "rock"), ("scissors", "paper")}
        return "user" if (user, bot) in wins else "bot"

    def _status_text(self) -> str:
        return f"Score: You **{self.user_score}** — **{self.bot_score}** Bot (first to 3 wins)."

    async def _finish(self, interaction: discord.Interaction, user_won: bool):
        self.finished = True
        self.disable_all_items()

        if user_won:
            tier, medal_award, new_streak, total_medals, xp_gained, new_xp, new_level, leveled_up = await record_win(
                interaction.user.id, GAME_RPS
            )

            # public medal announcement
            if medal_award > 0:
                await announce_medal(
                    interaction.channel,
                    interaction.user,
                    GAME_LABELS.get(GAME_RPS, GAME_RPS),
                    tier,
                    medal_award,
                    total_medals=total_medals,
                    streak=new_streak
                )

            # public level-up announcement
            if leveled_up and interaction.channel:
                await interaction.channel.send(f"🎉 {interaction.user.mention} reached **Level {new_level}**!")

            await interaction.response.edit_message(
                content=(
                    f"You won the match.\n{self._status_text()}\n"
                    f"Reward: **{tier} (+{medal_award} medals)** | Streak: **{new_streak}** | Game medals: **{total_medals}**\n"
                    f"Global: **+{xp_gained} XP** → Level **{new_level}** (XP: {new_xp})"
                ),
                view=self
            )
        else:
            await reset_streak(interaction.user.id, GAME_RPS)
            await interaction.response.edit_message(
                content=f"I won the match.\n{self._status_text()}\n(Streak reset.)",
                view=self
            )

    async def play(self, interaction: discord.Interaction, user_move: str):
        if self.finished:
            await interaction.response.send_message("This match is already finished.", ephemeral=True)
            return

        bot_move = random.choice(self.moves)
        res = self._round_result(user_move, bot_move)

        if res == "user":
            self.user_score += 1
            line = f"You chose **{user_move}**, I chose **{bot_move}**. You win this round."
        elif res == "bot":
            self.bot_score += 1
            line = f"You chose **{user_move}**, I chose **{bot_move}**. I win this round."
        else:
            line = f"You chose **{user_move}**, I chose **{bot_move}**. It's a draw."

        if self.user_score >= 3:
            await self._finish(interaction, user_won=True)
            return
        if self.bot_score >= 3:
            await self._finish(interaction, user_won=False)
            return

        await interaction.response.edit_message(
            content=f"{line}\n{self._status_text()}\nChoose your next move:",
            view=self
        )

    @discord.ui.button(label="Rock", style=discord.ButtonStyle.secondary, row=0)
    async def rock(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self.play(interaction, "rock")

    @discord.ui.button(label="Paper", style=discord.ButtonStyle.secondary, row=0)
    async def paper(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self.play(interaction, "paper")

    @discord.ui.button(label="Scissors", style=discord.ButtonStyle.secondary, row=0)
    async def scissors(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self.play(interaction, "scissors")

    async def on_timeout(self):
        self.disable_all_items()


# ----------------------------
# Tic Tac Toe (vs bot)
# ----------------------------
class TTTView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.board = [" "] * 9
        self.finished = False

        for i in range(9):
            self.add_item(TTTButton(i))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This game is not yours. Run /games to start your own.", ephemeral=True)
            return False
        return True

    def winner(self, b):
        lines = [
            (0, 1, 2), (3, 4, 5), (6, 7, 8),
            (0, 3, 6), (1, 4, 7), (2, 5, 8),
            (0, 4, 8), (2, 4, 6),
        ]
        for a, c, d in lines:
            if b[a] != " " and b[a] == b[c] == b[d]:
                return b[a]
        if " " not in b:
            return "draw"
        return None

    def bot_move(self):
        def try_move(sym):
            for i in range(9):
                if self.board[i] == " ":
                    temp = self.board[:]
                    temp[i] = sym
                    if self.winner(temp) == sym:
                        return i
            return None

        win = try_move("O")
        if win is not None:
            return win

        block = try_move("X")
        if block is not None:
            return block

        if self.board[4] == " ":
            return 4

        choices = [i for i in range(9) if self.board[i] == " "]
        return random.choice(choices) if choices else None

    def sync_buttons(self):
        for item in self.children:
            if isinstance(item, TTTButton):
                val = self.board[item.idx]
                item.label = val if val != " " else "·"
                item.disabled = (val != " ") or self.finished

    async def end(self):
        self.finished = True
        self.sync_buttons()
        self.disable_all_items()

    async def on_timeout(self):
        await self.end()


class TTTButton(discord.ui.Button):
    def __init__(self, idx: int):
        super().__init__(label="·", style=discord.ButtonStyle.secondary, row=idx // 3)
        self.idx = idx

    async def callback(self, interaction: discord.Interaction):
        view: TTTView = self.view
        if view.finished:
            await interaction.response.send_message("This game is already finished.", ephemeral=True)
            return

        if view.board[self.idx] != " ":
            await interaction.response.send_message("That cell is already taken.", ephemeral=True)
            return

        view.board[self.idx] = "X"
        w = view.winner(view.board)

        if w == "X":
            await view.end()
            tier, medal_award, new_streak, total_medals, xp_gained, new_xp, new_level, leveled_up = await record_win(
                interaction.user.id, GAME_TTT
            )

            # public medal announcement
            if medal_award > 0:
                await announce_medal(
                    interaction.channel,
                    interaction.user,
                    GAME_LABELS.get(GAME_TTT, GAME_TTT),
                    tier,
                    medal_award,
                    total_medals=total_medals,
                    streak=new_streak
                )

            # public level-up announcement
            if leveled_up and interaction.channel:
                await interaction.channel.send(f"🎉 {interaction.user.mention} reached **Level {new_level}**!")

            await interaction.response.edit_message(
                content=(
                    f"You win.\n"
                    f"Reward: **{tier} (+{medal_award} medals)** | Streak: **{new_streak}** | Game medals: **{total_medals}**\n"
                    f"Global: **+{xp_gained} XP** → Level **{new_level}** (XP: {new_xp})"
                ),
                view=view
            )
            return

        if w == "draw":
            await view.end()
            await reset_streak(interaction.user.id, GAME_TTT)
            await interaction.response.edit_message(content="It's a draw. (Streak reset.)", view=view)
            return

        bot_i = view.bot_move()
        if bot_i is not None:
            view.board[bot_i] = "O"

        w = view.winner(view.board)
        if w == "O":
            await view.end()
            await reset_streak(interaction.user.id, GAME_TTT)
            await interaction.response.edit_message(content="I win. (Streak reset.)", view=view)
            return

        if w == "draw":
            await view.end()
            await reset_streak(interaction.user.id, GAME_TTT)
            await interaction.response.edit_message(content="It's a draw. (Streak reset.)", view=view)
            return

        view.sync_buttons()
        await interaction.response.edit_message(content="Your turn (X).", view=view)


# ----------------------------
# Game Menu
# ----------------------------
class GamesMenuView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=120)
        self.user_id = user_id

        options = [
            discord.SelectOption(label="Guess the Number (1–10)", value=GAME_GUESS),
            discord.SelectOption(label="Tic-Tac-Toe (vs bot)", value=GAME_TTT),
            discord.SelectOption(label="Rock-Paper-Scissors (to 3 wins)", value=GAME_RPS),
        ]

        self.select = discord.ui.Select(
            placeholder="Choose a game...",
            min_values=1,
            max_values=1,
            options=options
        )
        self.select.callback = self.on_select
        self.add_item(self.select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This menu is not yours. Run /games to start your own.", ephemeral=True)
            return False
        return True

    async def on_select(self, interaction: discord.Interaction):
        choice = self.select.values[0]

        if choice == GAME_GUESS:
            view = GuessView(self.user_id)
            await interaction.response.edit_message(
                content="Guess a number from 1 to 10. You have 5 tries.",
                view=view
            )
            return

        if choice == GAME_TTT:
            view = TTTView(self.user_id)
            view.sync_buttons()
            await interaction.response.edit_message(
                content="Tic-Tac-Toe: you are X. Your turn.",
                view=view
            )
            return

        if choice == GAME_RPS:
            view = RPSView(self.user_id)
            await interaction.response.edit_message(
                content=f"Rock-Paper-Scissors.\n{view._status_text()}\nChoose your move:",
                view=view
            )
            return

        await interaction.response.send_message("Unknown game.", ephemeral=True)

    async def on_timeout(self):
        self.disable_all_items()


# ----------------------------
# Cog
# ----------------------------
class Games(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.ready = False

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.ready:
            await init_games_db()
            self.ready = True

    @discord.slash_command(
        name="games",
        description="Choose a mini game (ephemeral).",
        guild_ids=[GUILD_ID] if GUILD_ID else None
    )
    async def games(self, ctx: discord.ApplicationContext):
        view = GamesMenuView(ctx.author.id)
        await ctx.respond("Pick a game from the menu:", view=view, ephemeral=True)

    @discord.slash_command(
        name="leaderboard",
        description="Leaderboard for one game (sorted by medals). Visible to everyone.",
        guild_ids=[GUILD_ID] if GUILD_ID else None
    )
    async def leaderboard(
        self,
        ctx: discord.ApplicationContext,
        game: str = discord.Option(str, choices=GAME_CHOICES, description="Choose a game")
    ):
        rows = await get_leaderboard(game, limit=10)
        if not rows:
            await ctx.respond("No data yet for this game.")
            return

        lines = []
        for i, (uid, medals, wins, best_streak) in enumerate(rows, start=1):
            lines.append(f"{i}. <@{uid}> — medals: **{medals}**, wins: **{wins}**, best streak: **{best_streak}**")

        await ctx.respond(
            f"**Leaderboard — {GAME_LABELS.get(game, game)}**\n" + "\n".join(lines),
            ephemeral=False
        )

    @discord.slash_command(
        name="overall_leaderboard",
        description="Overall leaderboard across all games (sum of medals). Visible to everyone.",
        guild_ids=[GUILD_ID] if GUILD_ID else None
    )
    async def overall_leaderboard(self, ctx: discord.ApplicationContext):
        rows = await get_overall_leaderboard(limit=10)
        if not rows:
            await ctx.respond("No game data yet.")
            return

        lines = []
        for i, (uid, total_medals, total_wins, best_streak_any) in enumerate(rows, start=1):
            lines.append(
                f"{i}. <@{uid}> — medals: **{total_medals}**, wins: **{total_wins}**, best streak: **{best_streak_any}**"
            )

        await ctx.respond("**Overall Leaderboard (all games)**\n" + "\n".join(lines), ephemeral=False)

    @discord.slash_command(
        name="level_leaderboard",
        description="Global level leaderboard (XP/Level). Visible to everyone.",
        guild_ids=[GUILD_ID] if GUILD_ID else None
    )
    async def level_leaderboard(self, ctx: discord.ApplicationContext):
        rows = await get_level_leaderboard(limit=10)
        if not rows:
            await ctx.respond("No player levels yet.")
            return

        lines = []
        for i, (uid, level, xp) in enumerate(rows, start=1):
            lines.append(f"{i}. <@{uid}> — level **{level}** (XP: **{xp}**)")

        await ctx.respond("**Global Level Leaderboard**\n" + "\n".join(lines), ephemeral=False)

    @discord.slash_command(
        name="profile",
        description="Show your global level and XP.",
        guild_ids=[GUILD_ID] if GUILD_ID else None
    )
    async def profile(self, ctx: discord.ApplicationContext):
        xp, level = await get_profile(ctx.author.id)
        next_level = level + 1
        next_xp = xp_for_level(next_level)
        need = max(0, next_xp - xp)
        await ctx.respond(
            f"**Your profile**\nLevel: **{level}**\nXP: **{xp}**\nXP to next level: **{need}**",
            ephemeral=True
        )

    @discord.slash_command(
        name="mygame_stats",
        description="Your medals/wins/streaks per game.",
        guild_ids=[GUILD_ID] if GUILD_ID else None
    )
    async def mygame_stats(self, ctx: discord.ApplicationContext):
        rows = await get_user_stats(ctx.author.id)
        if not rows:
            await ctx.respond("No game stats yet. Play /games to start.", ephemeral=True)
            return

        lines = []
        for game, medals, wins, streak, best_streak in rows:
            lines.append(
                f"- {GAME_LABELS.get(game, game)}: medals **{medals}**, wins **{wins}**, streak **{streak}**, best **{best_streak}**"
            )

        xp, level = await get_profile(ctx.author.id)
        await ctx.respond(
            "**Your game stats**\n" + "\n".join(lines) + f"\n\nGlobal level: **{level}** (XP: **{xp}**)",
            ephemeral=True
        )


def setup(bot: discord.Bot):
    bot.add_cog(Games(bot))