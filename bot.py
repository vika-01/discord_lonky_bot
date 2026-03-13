import os
import discord
from dotenv import load_dotenv

import socket
import sys

_lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    _lock.bind(("127.0.0.1", 54321))
except OSError:
    print("Another instance of the bot is already running. Exiting.")
    sys.exit(1)

load_dotenv()

print("OWM_API_KEY loaded:", bool(os.getenv("OWM_API_KEY")))
k = (os.getenv("OWM_API_KEY") or "").strip()
print("OWM_API_KEY length:", len(k), "last4:", k[-4:] if k else "NONE")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing in .env")

GUILD_ID_STR = os.getenv("GUILD_ID", "0").strip()
GUILD_ID = int(GUILD_ID_STR) if GUILD_ID_STR.isdigit() else 0
if not GUILD_ID:
    raise RuntimeError("GUILD_ID is missing or invalid in .env")

intents = discord.Intents.default()
intents.members = True 
bot = discord.Bot(intents=intents, quild_ids=[GUILD_ID])

EXTENSIONS = [
    "cogs.ai",
    "cogs.calculator",
    "cogs.planner",
    "cogs.quiz",
    "cogs.games",
    "cogs.timer",
    "cogs.welcome",
    "cogs.weather",
]

for ext in EXTENSIONS:
    try:
        bot.load_extension(ext)
        print(f"Loaded extension: {ext}")
    except Exception as e:
        print(f"FAILED to load extension: {ext} -> {type(e).__name__}: {e}")

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

    try:
        await bot.sync_commands(guild_ids=[GUILD_ID])
        print(f"Synced commands to guild {GUILD_ID}")
    except Exception as e:
        print(f"Command sync failed: {type(e).__name__}: {e}")

@bot.event
async def on_application_command_error(ctx: discord.ApplicationContext, error: Exception):
    print(f"Command error in /{ctx.command}: {type(error).__name__}: {error}")
    try:
        if ctx.interaction and not ctx.interaction.response.is_done():
            await ctx.respond("Command failed due to an internal error.", ephemeral=True)
    except Exception:
        pass

bot.run(DISCORD_TOKEN)