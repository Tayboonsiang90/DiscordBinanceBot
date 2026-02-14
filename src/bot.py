"""Discord bot with slash commands for Binance price alerts."""

import sys
from pathlib import Path

# Ensure project root is on path (for both "python src/bot.py" and "python -m src.bot")
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import asyncio
import logging
import os

import discord
from discord import app_commands
from dotenv import load_dotenv

from src.alert_service import check_alerts_and_send
from src.database import (
    add_alert,
    get_all_alerts,
    get_setting,
    init_db,
    remove_alert,
    set_setting,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
ANNOUNCEMENT_CHANNEL_KEY = "announcement_channel_id"

intents = discord.Intents.default()
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


def _format_ticker(ticker: str) -> str:
    """Format ticker for display (e.g. BTCUSDT -> BTC/USDT)."""
    ticker = ticker.upper().replace("/", "")
    if ticker.endswith("USDT"):
        base = ticker[:-4]
        return f"{base}/USDT"
    return ticker


@tree.command(name="addalert", description="Add a price alert for a crypto pair")
@app_commands.describe(
    ticker="Ticker (e.g. BTC, ETH, BTCUSDT)",
    strike_price="Strike price to alert on",
    direction="Up = alert when candle High >= strike. Down = alert when candle Low <= strike",
    note="Optional note to show when alert fires",
)
@app_commands.choices(direction=[
    app_commands.Choice(name="Up", value="up"),
    app_commands.Choice(name="Down", value="down"),
])
async def addalert(
    interaction: discord.Interaction,
    ticker: str,
    strike_price: float,
    direction: str,
    note: str = "",
) -> None:
    """Add a price alert."""
    if strike_price <= 0:
        await interaction.response.send_message("Strike price must be positive.", ephemeral=True)
        return

    try:
        alert = add_alert(
            ticker=ticker,
            strike_price=strike_price,
            direction=direction,
            note=note,
        )
        display_ticker = _format_ticker(alert.ticker)
        await interaction.response.send_message(
            f"Alert #{alert.id} added: **{display_ticker}** {direction} @ ${strike_price:,.2f}"
            + (f"\nNote: {note}" if note else ""),
            ephemeral=True,
        )
    except Exception as e:
        logger.exception("Failed to add alert: %s", e)
        await interaction.response.send_message(
            f"Failed to add alert: {e}",
            ephemeral=True,
        )


@tree.command(name="removealert", description="Remove an alert by ID")
@app_commands.describe(alert_id="The alert ID from /listalerts")
async def removealert_cmd(interaction: discord.Interaction, alert_id: int) -> None:
    """Remove an alert."""
    removed = remove_alert(alert_id)
    if removed:
        await interaction.response.send_message(f"Alert #{alert_id} removed.", ephemeral=True)
    else:
        await interaction.response.send_message(
            f"Alert #{alert_id} not found.",
            ephemeral=True,
        )


@tree.command(name="listalerts", description="List all active alerts")
async def listalerts(interaction: discord.Interaction) -> None:
    """List all alerts."""
    alerts = get_all_alerts()
    if not alerts:
        await interaction.response.send_message("No active alerts.", ephemeral=True)
        return

    lines = []
    for a in alerts:
        display = _format_ticker(a.ticker)
        note_str = f" — {a.note}" if a.note else ""
        lines.append(f"**#{a.id}** {display} {a.direction} @ ${a.strike_price:,.2f}{note_str}")

    await interaction.response.send_message(
        "**Active Alerts:**\n" + "\n".join(lines),
        ephemeral=True,
    )


@tree.command(name="setchannel", description="Set the announcement channel for price alerts")
@app_commands.describe(channel="Channel where alerts will be posted (default: this channel)")
async def setchannel(
    interaction: discord.Interaction,
    channel: discord.TextChannel | None = None,
) -> None:
    """Set the announcement channel."""
    target = channel or interaction.channel
    if not target:
        await interaction.response.send_message("Could not determine channel.", ephemeral=True)
        return

    set_setting(ANNOUNCEMENT_CHANNEL_KEY, str(target.id))
    await interaction.response.send_message(
        f"Announcement channel set to {target.mention}",
        ephemeral=True,
    )


async def alert_loop() -> None:
    """Background task: check alerts every 60 seconds."""
    await bot.wait_until_ready()
    logger.info("Alert loop started.")

    while not bot.is_closed():
        try:
            channel_id_str = get_setting(ANNOUNCEMENT_CHANNEL_KEY)
            channel_id = int(channel_id_str) if channel_id_str else None

            if channel_id:
                channel = bot.get_channel(channel_id)
                if channel:
                    await check_alerts_and_send(channel, fallback_channel_id=channel_id)
                else:
                    logger.warning("Announcement channel %s not found.", channel_id)
            else:
                # No channel set; try first available text channel
                for guild in bot.guilds:
                    for ch in guild.text_channels:
                        if ch.permissions_for(guild.me).send_messages:
                            await check_alerts_and_send(ch)
                            break
                    break
        except Exception as e:
            logger.exception("Alert loop error: %s", e)

        await asyncio.sleep(60)


@bot.event
async def on_ready() -> None:
    """Handle bot ready."""
    guild_id = discord.Object(id=int(DISCORD_GUILD_ID)) if DISCORD_GUILD_ID else None
    if guild_id:
        await tree.sync(guild=guild_id)
        logger.info("Synced slash commands to guild %s", DISCORD_GUILD_ID)
    else:
        await tree.sync()
        logger.info("Synced slash commands globally (may take up to 1 hour)")

    logger.info("Logged in as %s (id=%s)", bot.user, bot.user.id if bot.user else None)


def main() -> None:
    """Run the bot."""
    if not DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN not set. Create .env from .env.example")
        raise SystemExit(1)

    init_db()

    async def start() -> None:
        async with bot:
            bot.loop.create_task(alert_loop())
            await bot.start(DISCORD_TOKEN)

    asyncio.run(start())


if __name__ == "__main__":
    main()
