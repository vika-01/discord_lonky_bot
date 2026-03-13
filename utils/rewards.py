import discord


def tier_emoji(tier: str) -> str:
    t = (tier or "").lower()
    if t == "gold":
        return "🥇"
    if t == "silver":
        return "🥈"
    return "🥉"


def tier_color(tier: str) -> discord.Color:
    t = (tier or "").lower()
    if t == "gold":
        return discord.Color.gold()
    if t == "silver":
        return discord.Color.light_grey()
    return discord.Color.orange()


async def announce_medal(
    channel: discord.abc.Messageable | None,
    user: discord.abc.User,
    game_name: str,
    tier: str,
    medal_award: int,
    total_medals: int | None = None,
    streak: int | None = None,
):
    
    if channel is None:
        return

    emoji = tier_emoji(tier)

    embed = discord.Embed(
        title=f"{emoji} Medal earned!",
        description=(
            f"{user.mention} earned **{tier}** in **{game_name}** "
            f"(+{medal_award} medals)."
        ),
        color=tier_color(tier),
    )

    if streak is not None:
        embed.add_field(name="Streak", value=str(streak), inline=True)
    if total_medals is not None:
        embed.add_field(name="Total medals", value=str(total_medals), inline=True)

    try:
        await channel.send(embed=embed)
    except Exception:
        # fallback plain text
        try:
            msg = f"{emoji} {user.mention} earned **{tier}** in **{game_name}** (+{medal_award} medals)."
            if streak is not None:
                msg += f" Streak: **{streak}**."
            if total_medals is not None:
                msg += f" Total medals: **{total_medals}**."
            await channel.send(msg)
        except Exception:
            pass


async def announce_xp(
    channel: discord.abc.Messageable | None,
    user: discord.abc.User,
    xp_gained: int,
    new_level: int | None = None,
    new_xp: int | None = None,
    reason: str | None = None,
):
    
    if channel is None:
        return

    title = "✨ XP earned!"
    if new_level is not None and new_xp is not None:
        desc = f"{user.mention} gained **+{xp_gained} XP**"
        if reason:
            desc += f" for **{reason}**"
        desc += f". Now Level **{new_level}** (XP: **{new_xp}**)."
    else:
        desc = f"{user.mention} gained **+{xp_gained} XP**."
        if reason:
            desc += f" Reason: **{reason}**."

    embed = discord.Embed(title=title, description=desc)

    try:
        await channel.send(embed=embed)
    except Exception:
        try:
            await channel.send(desc)
        except Exception:
            pass


async def announce_reward_generic(
    channel: discord.abc.Messageable | None,
    user: discord.abc.User,
    title: str,
    text: str,
):
    
    if channel is None:
        return
    embed = discord.Embed(title=title, description=f"{user.mention} {text}")
    try:
        await channel.send(embed=embed)
    except Exception:
        try:
            await channel.send(f"{user.mention} {text}")
        except Exception:
            pass

async def announce_level_up(channel, user, new_level: int):
    if channel is None:
        return
    try:
        await channel.send(f"🎉 {user.mention} reached **Level {new_level}**!")
    except Exception:
        pass