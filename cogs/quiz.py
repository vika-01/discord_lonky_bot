import os
import random
import math
import aiosqlite
from datetime import datetime, timezone

import discord
from discord.ext import commands

from utils.rewards import announce_reward_generic, announce_level_up

GUILD_ID = int(os.getenv("GUILD_ID", "0"))
DB_PATH = os.path.join("data", "bot.db")
os.makedirs("data", exist_ok=True)

QUIZ_SIZE = 30  # questions per run


# ----------------------------
# Question bank
# Each: (question, [A,B,C], correct_index)
# ----------------------------
QUESTIONS = [
    # Geography
    ("What is the capital of Japan?", ["Tokyo", "Osaka", "Kyoto"], 0),
    ("What is the capital of Canada?", ["Toronto", "Ottawa", "Vancouver"], 1),
    ("What is the capital of Australia?", ["Sydney", "Canberra", "Melbourne"], 1),
    ("Which country has the largest population?", ["India", "China", "United States"], 1),
    ("Which ocean is the largest?", ["Atlantic", "Pacific", "Indian"], 1),
    ("Which continent is the Sahara Desert in?", ["Asia", "Africa", "South America"], 1),
    ("Which country is in the Baltic region?", ["Latvia", "Portugal", "Mexico"], 0),
    ("The Nile is primarily a:", ["Desert", "River", "Mountain"], 1),
    ("Which one is NOT a continent?", ["Europe", "Greenland", "Africa"], 1),
    ("The highest mountain above sea level is:", ["Everest", "K2", "Kilimanjaro"], 0),
    ("Which country is famous for fjords?", ["Norway", "Spain", "Egypt"], 0),
    ("Which sea lies between Europe and Africa?", ["Red Sea", "Mediterranean Sea", "Arabian Sea"], 1),
    ("Which is the largest country by area?", ["Canada", "Russia", "Brazil"], 1),
    ("Which river flows through London?", ["Thames", "Seine", "Danube"], 0),
    ("Which country is known as the Land of the Rising Sun?", ["Japan", "Thailand", "South Korea"], 0),

    # Science
    ("H2O is:", ["Hydrogen", "Water", "Oxygen"], 1),
    ("Which gas do plants absorb most for photosynthesis?", ["Oxygen", "Carbon dioxide", "Nitrogen"], 1),
    ("The chemical symbol for gold is:", ["Ag", "Au", "Gd"], 1),
    ("What is the freezing point of water (Celsius)?", ["0", "32", "100"], 0),
    ("Which is a mammal?", ["Shark", "Dolphin", "Eagle"], 1),
    ("The 'Red Planet' is:", ["Mars", "Venus", "Jupiter"], 0),
    ("Which planet is the largest in our Solar System?", ["Saturn", "Jupiter", "Neptune"], 1),
    ("Light speed in vacuum is about:", ["300,000 km/s", "30,000 km/s", "3,000 km/s"], 0),
    ("What is the main gas in Earth's atmosphere?", ["Oxygen", "Nitrogen", "Carbon dioxide"], 1),
    ("Which organ pumps blood through the body?", ["Lungs", "Heart", "Liver"], 1),
    ("DNA stands for:", ["Deoxyribonucleic acid", "Dynamic network algorithm", "Digital numeric array"], 0),
    ("What is the boiling point of water (Celsius)?", ["0", "50", "100"], 2),
    ("Which type of energy is stored in food?", ["Kinetic", "Chemical", "Nuclear"], 1),
    ("Electric current is measured in:", ["Watts", "Amperes", "Volts"], 1),
    ("Which is a renewable energy source?", ["Coal", "Wind", "Oil"], 1),

    # Math
    ("How many continents are there?", ["6", "7", "8"], 1),
    ("2^5 equals:", ["16", "32", "64"], 1),
    ("Which number is prime?", ["21", "29", "35"], 1),
    ("What is 9 × 7?", ["56", "63", "72"], 1),
    ("Pi is approximately:", ["2.14", "3.14", "4.13"], 1),
    ("A quadratic equation has degree:", ["1", "2", "3"], 1),
    ("What is 12% of 50?", ["6", "8", "10"], 0),
    ("What is the square root of 81?", ["7", "8", "9"], 2),
    ("What is 15 + 27?", ["42", "41", "43"], 0),
    ("What is 100 / 4?", ["20", "25", "30"], 1),
    ("A triangle with all equal sides is:", ["Isosceles", "Equilateral", "Scalene"], 1),
    ("What is 3! (factorial)?", ["6", "9", "3"], 0),
    ("Which is an even number?", ["17", "24", "31"], 1),
    ("If x=3, what is 2x+5?", ["11", "12", "13"], 0),
    ("What is 7×8?", ["54", "56", "58"], 1),

    # Literature / Arts
    ("Who wrote '1984'?", ["George Orwell", "Aldous Huxley", "Ray Bradbury"], 0),
    ("Who painted the Mona Lisa?", ["Van Gogh", "Leonardo da Vinci", "Picasso"], 1),
    ("The author of 'War and Peace' is:", ["Tolstoy", "Dostoevsky", "Chekhov"], 0),
    ("Shakespeare wrote:", ["Hamlet", "The Odyssey", "The Divine Comedy"], 0),
    ("Who wrote 'The Hobbit'?", ["J.R.R. Tolkien", "C.S. Lewis", "J.K. Rowling"], 0),
    ("The Odyssey is attributed to:", ["Homer", "Virgil", "Socrates"], 0),
    ("A novel is usually:", ["A long fiction story", "A short poem", "A math proof"], 0),
    ("Which is a genre of literature?", ["Satire", "Satellite", "Satin"], 0),

    # Programming / Tech
    ("SQL is most related to:", ["Databases", "Photos", "Audio"], 0),
    ("What does CPU stand for?", ["Central Processing Unit", "Computer Personal Unit", "Central Program Utility"], 0),
    ("In Git, a 'commit' is:", ["A snapshot of changes", "A server", "A database"], 0),
    ("What does HTML stand for?", ["HyperText Markup Language", "HighText Machine Language", "Hyper Transfer Meta Language"], 0),
    ("Which one is a programming paradigm?", ["Object-oriented", "Overclocked", "Overscoped"], 0),
    ("Python is:", ["A programming language", "A browser", "A database"], 0),
    ("Which data type stores True/False?", ["String", "Boolean", "Float"], 1),
    ("A loop is used to:", ["Repeat actions", "Store images", "Encrypt data"], 0),
    ("A variable is used to:", ["Store data", "Draw graphics", "Play music"], 0),
    ("Which is a version control system?", ["Git", "JPEG", "HTTP"], 0),

    # General knowledge
    ("Which planet is closest to the Sun?", ["Mercury", "Venus", "Earth"], 0),
    ("The Great Wall is in:", ["India", "China", "Mexico"], 1),
    ("Which metal is liquid at room temperature?", ["Mercury", "Iron", "Aluminum"], 0),
    ("Which element has atomic number 1?", ["Helium", "Hydrogen", "Oxygen"], 1),
    ("Which currency is used in Latvia?", ["Lats", "Euro", "Dollar"], 1),
    ("Which language is mainly spoken in Brazil?", ["Spanish", "Portuguese", "French"], 1),
    ("Which is the largest mammal?", ["Elephant", "Blue whale", "Giraffe"], 1),
    ("Which part of the cell contains DNA?", ["Nucleus", "Membrane", "Ribosome"], 0),
]


# ----------------------------
# Shared global leveling curve
# ----------------------------
def level_from_xp(xp: int) -> int:
    if xp < 0:
        xp = 0
    return 1 + int(math.sqrt(xp / 100.0))


async def init_quiz_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # quiz awards
        await db.execute("""
        CREATE TABLE IF NOT EXISTS awards (
            user_id INTEGER PRIMARY KEY,
            gold INTEGER NOT NULL DEFAULT 0,
            updated_utc TEXT NOT NULL
        )
        """)

        # shared global profile
        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_profile (
            user_id INTEGER PRIMARY KEY,
            xp INTEGER NOT NULL DEFAULT 0,
            level INTEGER NOT NULL DEFAULT 1
        )
        """)

        # cleanup ghost user
        await db.execute("DELETE FROM awards WHERE user_id = 0")
        await db.execute("DELETE FROM user_profile WHERE user_id = 0")

        await db.commit()


async def add_gold(user_id: int, amount: int = 1):
    now_utc = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO awards (user_id, gold, updated_utc)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                gold = gold + excluded.gold,
                updated_utc = excluded.updated_utc
        """, (user_id, amount, now_utc))
        await db.commit()


async def get_gold(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT gold FROM awards WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def add_xp(user_id: int, amount: int):
    """
    Adds XP to shared user_profile.
    Returns: (new_xp, new_level, gained, leveled_up)
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


def xp_for_correct(correct: int) -> int:
    """
    XP tiers (awarded once at the end):
      10-19 correct: +25 XP
      20-29 correct: +35 XP
      30 correct:    +50 XP
    else: +0
    """
    if correct >= 30:
        return 50
    if correct >= 20:
        return 35
    if correct >= 10:
        return 25
    return 0


# ----------------------------
# Quiz UI
# ----------------------------
class QuizView(discord.ui.View):
    def __init__(self, owner_id: int, questions, max_q: int = QUIZ_SIZE):
        super().__init__(timeout=900)
        self.owner_id = owner_id

        # random 30 from the large pool each run
        pool = list(questions)
        random.shuffle(pool)
        self.pool = pool[:min(max_q, len(pool))]
        self.max_q = len(self.pool)

        self.index = 0
        self.correct = 0
        self.answered = 0

        self.last_feedback = None
        self.current = self.pool[0]

    def build_embed(self):
        q, opts, _ = self.current
        emb = discord.Embed(title=f"Quiz ({self.index+1}/{self.max_q})", description=q)
        emb.add_field(name="A", value=opts[0], inline=False)
        emb.add_field(name="B", value=opts[1], inline=False)
        emb.add_field(name="C", value=opts[2], inline=False)
        if self.last_feedback:
            emb.add_field(name="Last answer", value=self.last_feedback, inline=False)
        emb.set_footer(text="Choose A/B/C or press Stop.")
        return emb

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            try:
                await interaction.response.send_message("This quiz is not yours. Start your own with /quiz.", ephemeral=True)
            except Exception:
                pass
            return False
        return True

    async def finish(self, interaction: discord.Interaction, stopped: bool):
        for item in self.children:
            item.disabled = True

        answered = max(1, self.answered)
        percent = (self.correct / answered) * 100.0

        text = "Quiz stopped.\n" if stopped else "Quiz finished.\n"
        text += f"Score: **{self.correct}/{answered}** (**{percent:.1f}%**)."

        # --- XP at end (tiered by correct answers)
        xp_gain = xp_for_correct(self.correct)
        new_xp = None
        new_level = None
        leveled_up = False
        if xp_gain > 0:
            new_xp, new_level, gained, leveled_up = await add_xp(self.owner_id, xp_gain)

        # --- Gold award (>=70%)
        bonus = ""
        earned_gold = False
        if percent >= 70.0:
            earned_gold = True
            await add_gold(self.owner_id, 1)
            gold = await get_gold(self.owner_id)
            bonus = f"\nYou earned **1 gold award**. Total gold: **{gold}**. 🏅"

        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except Exception:
            pass

        # public announcements
        if earned_gold:
            await announce_reward_generic(
                interaction.channel,
                interaction.user,
                "🏅 Quiz award!",
                "earned **1 Gold Award** from the quiz!"
            )

        if leveled_up and new_level is not None:
            await announce_level_up(interaction.channel, interaction.user, new_level)

        # edit the quiz message
        try:
            xp_line = f"\nXP: **+{xp_gain}**."
            if xp_gain > 0 and new_level is not None and new_xp is not None:
                xp_line = f"\nXP: **+{xp_gain}** → Level **{new_level}** (XP: {new_xp})"

            await interaction.message.edit(content=text + bonus + xp_line, embed=None, view=self)
        except Exception:
            try:
                await interaction.followup.send(text + bonus, ephemeral=True)
            except Exception:
                pass

    async def next_question(self, interaction: discord.Interaction):
        self.index += 1
        if self.index >= self.max_q:
            await self.finish(interaction, stopped=False)
            return

        self.current = self.pool[self.index]

        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except Exception:
            pass

        try:
            await interaction.message.edit(embed=self.build_embed(), view=self)
        except Exception:
            pass

    async def answer(self, interaction: discord.Interaction, chosen_index: int):
        _, opts, ans = self.current
        self.answered += 1

        if chosen_index == ans:
            self.correct += 1
            self.last_feedback = "Correct."
        else:
            self.last_feedback = f"Incorrect. Right answer: **{opts[ans]}**."

        await self.next_question(interaction)

    @discord.ui.button(label="A", style=discord.ButtonStyle.primary)
    async def btn_a(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self.answer(interaction, 0)

    @discord.ui.button(label="B", style=discord.ButtonStyle.primary)
    async def btn_b(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self.answer(interaction, 1)

    @discord.ui.button(label="C", style=discord.ButtonStyle.primary)
    async def btn_c(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self.answer(interaction, 2)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger)
    async def btn_stop(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self.finish(interaction, stopped=True)


# ----------------------------
# Cog
# ----------------------------
class Quiz(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.db_ready = False

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.db_ready:
            await init_quiz_db()
            self.db_ready = True

    @discord.slash_command(
        name="quiz",
        description="Start a 30-question quiz with A/B/C answers and awards.",
        guild_ids=[GUILD_ID] if GUILD_ID else None
    )
    async def quiz(self, ctx: discord.ApplicationContext):
        if len(QUESTIONS) < QUIZ_SIZE:
            await ctx.respond("Not enough questions in the quiz bank yet.", ephemeral=True)
            return

        view = QuizView(owner_id=ctx.author.id, questions=QUESTIONS, max_q=QUIZ_SIZE)
        await ctx.respond(embed=view.build_embed(), view=view)

    @discord.slash_command(
        name="awards",
        description="Show your quiz awards.",
        guild_ids=[GUILD_ID] if GUILD_ID else None
    )
    async def awards(self, ctx: discord.ApplicationContext):
        gold = await get_gold(ctx.author.id)
        msg = f"Your gold awards: **{gold}**."
        if gold > 0:
            msg += " 🏅"
        await ctx.respond(msg, ephemeral=True)


def setup(bot: discord.Bot):
    bot.add_cog(Quiz(bot))