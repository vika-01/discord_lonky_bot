import os
import discord
from discord.ext import commands

GUILD_ID = int(os.getenv("GUILD_ID", "0"))
WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", "0"))


INSTRUCTIONS_TEXT = (
    "**Bot quick guide**\n"
    "- **/ai** question — Ask anything. After the answer, type **tell me more** for extra details.\n"
    "- **/calc** expression — Calculator (supports + - * / and quadratic equations).\n"
    "- **/plan** — Add a plan with reminders.\n"
    "- **/myplans** — View and manage your plans.\n"
    "- **/calendar** — View your monthly calendar with plan days.\n"
    "- **/quiz** — Start a quiz (up to 30 questions).\n"
    "- **/games** — Play games and earn rewards.\n"
    "- **/timer** — Focus timer with real-time countdown.\n"
    "- **/fire_lock_in** — Focus cycles with real-time countdown and stage DMs.\n"
    "- **/weather** city: — Find out the current weather and temperature in the city you specified.\n"
)


class Welcome(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    async def _pick_welcome_channel(self, guild: discord.Guild) -> discord.abc.Messageable | None:
        if WELCOME_CHANNEL_ID:
            ch = guild.get_channel(WELCOME_CHANNEL_ID)
            if isinstance(ch, (discord.TextChannel, discord.Thread)):
                return ch

        if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
            return guild.system_channel

        for ch in guild.text_channels:
            perms = ch.permissions_for(guild.me)
            if perms.send_messages:
                return ch

        return None

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if GUILD_ID and member.guild.id != GUILD_ID:
            return

        channel = await self._pick_welcome_channel(member.guild)
        if not channel:
            return

        welcome_msg = (
            f"Welcome {member.mention}!\n"
            f"Please check the bot guide below to get started.\n\n"
            f"{INSTRUCTIONS_TEXT}"
        )

        try:
            await channel.send(welcome_msg)
        except Exception:
            pass

        print(f"[WELCOME] on_member_join fired: {member} ({member.id})")


def setup(bot: discord.Bot):
    bot.add_cog(Welcome(bot))