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
from src.binance_client import BINANCE_API_URL, fetch_candle_debug
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
intents.message_content = True  # Required to read DM content (enable in Developer Portal → Bot → Message Content Intent)
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


def _format_ticker(ticker: str) -> str:
    """Format ticker for display (e.g. BTCUSDT -> BTC/USDT)."""
    ticker = ticker.upper().replace("/", "")
    if ticker.endswith("USDT"):
        base = ticker[:-4]
        return f"{base}/USDT"
    return ticker


@tree.command(name="addalert", description="Add a price alert (fires when price touches strike)")
@app_commands.describe(
    ticker="Ticker (e.g. BTC, ETH, BTCUSDT)",
    strike_price="Strike price to alert on",
    note="Optional note to show when alert fires",
)
async def addalert(
    interaction: discord.Interaction,
    ticker: str,
    strike_price: float,
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
            note=note,
        )
        display_ticker = _format_ticker(alert.ticker)
        await interaction.response.send_message(
            f"Alert #{alert.id} added: **{display_ticker}** @ ${strike_price:,.2f}"
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
        lines.append(f"**#{a.id}** {display} @ ${a.strike_price:,.2f}{note_str}")

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


PREFIX = "!"

HELP_TEXT = f"""
**Message commands (use `{PREFIX}` prefix):**
• `{PREFIX}setchannel` — Set this channel for price alerts
• `{PREFIX}addalert <ticker> <price> [note]` — Add alert (fires when price touches strike), e.g. `{PREFIX}addalert BTC 100000 Key level`
• `{PREFIX}removealert <id>` — Remove alert by ID
• `{PREFIX}listalerts` — List all alerts
• `{PREFIX}help` — Show this help
• `{PREFIX}debug [ticker]` — Show current 1m candle data (default: BTC)
"""


@bot.event
async def on_message(message: discord.Message) -> None:
    """Handle DMs (diagnostic) and server message commands (!prefix)."""
    if message.author.bot:
        return

    content = (message.content or "").strip()
    if not content:
        return

    # DM diagnostic
    if isinstance(message.channel, discord.DMChannel):
        if content.lower() in ("ping", "hello", "help", "diagnostic"):
            guilds = [f"• {g.name} (id={g.id})" for g in bot.guilds]
            guild_list = "\n".join(guilds) if guilds else "None"
            await message.channel.send(
                f"**Bot is online and receiving DMs.**\n\n"
                f"**Servers I'm in:**\n{guild_list}\n\n"
                f"**Message commands (use in a server):**{HELP_TEXT}"
            )
            logger.info("Diagnostic DM from %s", message.author)
        return

    # Server message commands
    if not content.startswith(PREFIX):
        return

    parts = content[len(PREFIX):].split()
    if not parts:
        return

    cmd = parts[0].lower()

    if cmd == "help":
        await message.reply(HELP_TEXT)

    elif cmd == "setchannel":
        if not message.guild:
            await message.reply("Use this in a server channel.")
            return
        set_setting(ANNOUNCEMENT_CHANNEL_KEY, str(message.channel.id))
        await message.reply(f"Announcement channel set to {message.channel.mention}")

    elif cmd == "listalerts":
        alerts = get_all_alerts()
        if not alerts:
            await message.reply("No active alerts.")
            return
        lines = []
        for a in alerts:
            display = _format_ticker(a.ticker)
            note_str = f" — {a.note}" if a.note else ""
            lines.append(f"**#{a.id}** {display} @ ${a.strike_price:,.2f}{note_str}")
        await message.reply("**Active Alerts:**\n" + "\n".join(lines))

    elif cmd == "addalert":
        # !addalert BTC 100000 Key resistance
        if len(parts) < 3:
            await message.reply(
                f"Usage: `{PREFIX}addalert <ticker> <price> [note]`\n"
                f"Example: `{PREFIX}addalert BTC 100000 Key level`"
            )
            return
        ticker = parts[1]
        try:
            strike_price = float(parts[2])
        except ValueError:
            await message.reply("Price must be a number.")
            return
        note = " ".join(parts[3:]) if len(parts) > 3 else ""
        if strike_price <= 0:
            await message.reply("Price must be positive.")
            return
        try:
            alert = add_alert(ticker=ticker, strike_price=strike_price, note=note)
            display = _format_ticker(alert.ticker)
            await message.reply(
                f"Alert #{alert.id} added: **{display}** @ ${strike_price:,.2f}"
                + (f"\nNote: {note}" if note else "")
            )
        except Exception as e:
            logger.exception("Failed to add alert: %s", e)
            await message.reply(f"Failed to add alert: {e}")

    elif cmd == "debug":
        ticker = parts[1] if len(parts) > 1 else "BTC"
        candle, error = fetch_candle_debug(ticker)
        if error:
            await message.reply(
                f"**Could not fetch candle for {ticker}**\n"
                f"**API:** `{BINANCE_API_URL}`\n"
                f"**Error:** {error}\n\n"
                f"Binance may block some regions (e.g. US). Render servers run in US — try a different host or proxy if blocked."
            )
            return
        display = f"{candle['ticker'][:-4]}/{candle['ticker'][-4:]}" if candle["ticker"].endswith("USDT") else candle["ticker"]
        await message.reply(
            f"**{display} — Latest closed 1m candle**\n"
            f"**API:** `{BINANCE_API_URL}`\n"
            f"Open: ${candle['open']:,.2f} | High: ${candle['high']:,.2f} | Low: ${candle['low']:,.2f} | Close: ${candle['close']:,.2f}\n"
            f"Volume: {candle['volume']:,.2f}\n"
            f"Open: {candle['open_time']} | Close: {candle['close_time']}"
        )

    elif cmd == "removealert":
        if len(parts) < 2:
            await message.reply(f"Usage: `{PREFIX}removealert <id>`")
            return
        try:
            alert_id = int(parts[1])
        except ValueError:
            await message.reply("ID must be a number. Use `!listalerts` to see IDs.")
            return
        removed = remove_alert(alert_id)
        if removed:
            await message.reply(f"Alert #{alert_id} removed.")
        else:
            await message.reply(f"Alert #{alert_id} not found.")

    else:
        await message.reply(f"Unknown command. Use `{PREFIX}help` for commands.")


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
    # Sync slash commands to ALL guilds the bot is in (ensures commands appear in every server)
    synced = 0
    for guild in bot.guilds:
        try:
            await tree.sync(guild=discord.Object(id=guild.id))
            synced += 1
            logger.info("Synced slash commands to guild: %s (id=%s)", guild.name, guild.id)
        except Exception as e:
            logger.warning("Failed to sync to guild %s: %s", guild.name, e)

    if synced == 0:
        await tree.sync()
        logger.info("No guilds found; synced globally (may take up to 1 hour)")

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
