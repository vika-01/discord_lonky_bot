import os
import aiohttp
import discord
from discord.ext import commands


def get_owm_key() -> str:
    return (os.getenv("OWM_API_KEY") or "").strip()


async def fetch_json(session: aiohttp.ClientSession, url: str, params: dict | None = None):
    async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=20)) as response:
        if response.status == 401:
            key = get_owm_key()
            tail = key[-4:] if key else "NONE"
            length = len(key)
            text = await response.text()
            raise RuntimeError(
                "OpenWeather API key rejected (HTTP 401).\n"
                f"Key diagnostics: length={length}, last4={tail}\n"
                "Fix: ensure .env contains the correct OWM_API_KEY and restart the bot process.\n"
                f"Response: {text}"
            )

        response.raise_for_status()
        return await response.json()


async def geocode_city(session: aiohttp.ClientSession, city: str):
    key = get_owm_key()
    if not key:
        raise RuntimeError("OWM_API_KEY not set.")

    url = "https://api.openweathermap.org/geo/1.0/direct"
    data = await fetch_json(session, url, {
        "q": city,
        "limit": 1,
        "appid": key
    })
    if not data:
        raise ValueError("City not found.")
    return data[0]


async def get_weather(session: aiohttp.ClientSession, lat: float, lon: float):
    key = get_owm_key()
    if not key:
        raise RuntimeError("OWM_API_KEY not set.")

    url = "https://api.openweathermap.org/data/2.5/weather"
    return await fetch_json(session, url, {
        "lat": lat,
        "lon": lon,
        "appid": key,
        "units": "metric",
        "lang": "en"
    })


async def get_capital(session: aiohttp.ClientSession, country_code: str):
    url = f"https://restcountries.com/v3.1/alpha/{country_code}"
    data = await fetch_json(session, url)
    if isinstance(data, list) and data:
        capital = data[0].get("capital")
        if isinstance(capital, list) and capital:
            return capital[0]
    return None


class WeatherCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @discord.slash_command(name="weather", description="Get today's weather and country capital")
    async def weather(self, ctx: discord.ApplicationContext, city: str = None):
        if not city:
            await ctx.respond(
                "Please provide a city name. Example: `/weather city:London`",
                ephemeral=True
            )
            return

        await ctx.defer()

        try:
            async with aiohttp.ClientSession() as session:
                geo = await geocode_city(session, city.strip())
                lat = float(geo["lat"])
                lon = float(geo["lon"])
                country_code = str(geo.get("country", "")).upper()

                weather_data = await get_weather(session, lat, lon)

                temperature = weather_data["main"]["temp"]
                feels_like = weather_data["main"]["feels_like"]
                description = weather_data["weather"][0]["description"]
                city_name = weather_data.get("name") or geo.get("name") or city

                capital = await get_capital(session, country_code) if country_code else None

                message = (
                    f"📍 **{city_name}, {country_code or '??'}**\n"
                    f"🌡 Temperature: **{temperature}°C**\n"
                    f"🤔 Feels like: **{feels_like}°C**\n"
                    f"☁ Condition: **{description.capitalize()}**\n\n"
                    f"🏛 Capital of this country: **{capital if capital else 'Unknown'}**"
                )

                await ctx.followup.send(message)

        except Exception as e:
            await ctx.followup.send(f"Error: {e}")


def setup(bot: discord.Bot):
    bot.add_cog(WeatherCog(bot))