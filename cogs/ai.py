import os
import json
import re
import discord
from discord.ext import commands

GUILD_ID = int(os.getenv("GUILD_ID", "0"))

DATA_FILE = os.path.join("data", "knowledge.json")
CAPITALS_FILE = os.path.join("data", "capitals.json")


def normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def tokens(text: str) -> set[str]:
    t = normalize(text).split()
    stop = {
        "the", "a", "an", "is", "are", "to", "of", "and", "or", "in", "on", "for",
        "with", "what", "who", "how", "whats", "what's", "tell", "me", "please"
    }
    return {w for w in t if w not in stop and len(w) >= 2}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def substring_score(query: str, patterns: list[str]) -> float:
    q = normalize(query)
    best = 0.0
    for p in patterns:
        pn = normalize(p)
        if pn and pn in q:
            best = max(best, 1.0)
        else:
            best = max(best, jaccard(tokens(pn), tokens(q)) * 0.9)
    return best


class OfflineAI(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.entries = []
        self.by_id = {}
        self.memory = {}
        self.capitals = {}

        self._load_knowledge()
        self._load_capitals()

    # ---------- LOADERS ----------
    def _load_knowledge(self):
        if not os.path.exists(DATA_FILE):
            raise RuntimeError(f"Missing {DATA_FILE}")

        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.entries = data.get("entries", [])
        self.by_id = {e["id"]: e for e in self.entries}

        for e in self.entries:
            all_text = " ".join(
                e.get("patterns", [])
                + [e.get("answer", ""), e.get("more", ""), e.get("examples", "")]
            )
            e["_tok"] = tokens(all_text)

    def _load_capitals(self):
        if not os.path.exists(CAPITALS_FILE):
            return

        with open(CAPITALS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)

        self.capitals = {normalize(k): v for k, v in raw.items()}

    # ---------- HELPERS ----------
    def _wants_more(self, text: str) -> bool:
        t = normalize(text)
        return any(x in t for x in ["tell more", "more", "explain", "details"])

    def _wants_examples(self, text: str) -> bool:
        t = normalize(text)
        return any(x in t for x in ["example", "examples", "code example"])

    def _try_capital(self, question: str):
        q = normalize(question)
        m = re.search(r"(?:what is )?(?:the )?capital of (.+)$", q)
        if not m:
            return None, None

        country = m.group(1).strip()
        country = country.replace("the ", "").strip()
        cap = self.capitals.get(normalize(country))
        return country, cap

    def _best_match(self, question: str):
        qtok = tokens(question)
        qnorm = normalize(question)

        best = None
        best_score = 0.0

        for e in self.entries:
            ps = substring_score(qnorm, e.get("patterns", []))
            ts = jaccard(qtok, e.get("_tok", set()))
            score = max(ps, ts)
            if score > best_score:
                best_score = score
                best = e

        return best, best_score

    # ---------- COMMANDS ----------
    @discord.slash_command(
        name="ai",
        description="Ask a question. Write 'tell me more' after the answer to get more information.",
        guild_ids=[GUILD_ID] if GUILD_ID else None
    )
    async def ai(self, ctx: discord.ApplicationContext, question: str):
        user_id = ctx.author.id

        # capital questions
        country, capital = self._try_capital(question)
        if country is not None:
            if capital:
                self.memory[user_id] = f"capital::{normalize(country)}"
                await ctx.respond(f"The capital of {country.title()} is {capital}.")
            else:
                await ctx.respond(f"I don't have the capital of {country.title()} in my offline database yet.")
            return

        # follow-up
        if user_id in self.memory and (self._wants_more(question) or self._wants_examples(question)):
            key = self.memory[user_id]

            if key.startswith("capital::"):
                await ctx.respond("You can ask about another country like: capital of Germany.")
                return

            entry = self.by_id.get(key)
            if not entry:
                await ctx.respond("Ask the question again.")
                return

            if self._wants_examples(question) and entry.get("examples"):
                await ctx.respond(entry["examples"])
                return

            if entry.get("more"):
                await ctx.respond(entry["more"])
            else:
                await ctx.respond("No additional details available.")
            return

        # normal knowledge answer
        entry, score = self._best_match(question)

        if not entry or score < 0.18:
            await ctx.respond(
                "I don't know that yet. Try asking about programming, math, science, geography or literature."
            )
            return

        self.memory[user_id] = entry["id"]
        await ctx.respond(entry.get("answer"))


def setup(bot: discord.Bot):
    bot.add_cog(OfflineAI(bot))