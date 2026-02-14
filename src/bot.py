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
import time

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
POLL_INTERVAL_KEY = "poll_interval_seconds"
MIN_POLL_INTERVAL = 30
MAX_POLL_INTERVAL = 300
DEFAULT_POLL_INTERVAL = 60

intents = discord.Intents.default()
intents.message_content = True  # Required to read DM content (enable in Developer Portal → Bot → Message Content Intent)
intents.reactions = True  # Required for reaction-add events (delete alert on trash click)
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


@tree.command(name="listalerts", description="List all active alerts (paginated)")
async def listalerts_slash(interaction: discord.Interaction) -> None:
    """List all alerts with pagination."""
    alerts = get_all_alerts()
    if not alerts:
        await interaction.response.send_message("No active alerts.", ephemeral=True)
        return

    lines = _build_alert_lines(alerts)
    pages = _pack_into_pages(lines, add_no_channel=False)  # ephemeral, no channel reminder needed
    view = ListAlertsView(pages=pages, author_id=interaction.user.id)
    await interaction.response.send_message(
        content=view._page_content(),
        view=view,
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

NO_CHANNEL_REMINDER = f"\n\n⚠️ **No announcement channel set.** Run `!setchannel` in your desired channel to receive alerts."

MAX_PAGE_CHARS = 1950  # Leave room under 2000 for safety


def _build_alert_lines(alerts: list) -> list[str]:
    """Build display lines for alerts (truncate long notes)."""
    lines = []
    for a in alerts:
        display = _format_ticker(a.ticker)
        note_str = ""
        if a.note:
            n = (a.note[:45] + "…") if len(a.note) > 45 else a.note
            note_str = f" — {n}"
        lines.append(f"**#{a.id}** {display} @ ${a.strike_price:,.2f}{note_str}")
    return lines


def _pack_into_pages(lines: list[str], add_no_channel: bool) -> list[str]:
    """Pack lines into pages that fill up to MAX_PAGE_CHARS each."""
    pages: list[str] = []
    # Reserve space for "**Active Alerts:** (page X/Y)\n" (~45 chars)
    page_header_overhead = 45
    max_body = MAX_PAGE_CHARS - page_header_overhead
    current_lines: list[str] = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1
        if current_len + line_len > max_body and current_lines:
            pages.append("\n".join(current_lines))
            current_lines = [line]
            current_len = line_len
        else:
            current_lines.append(line)
            current_len += line_len

    if current_lines:
        body = "\n".join(current_lines)
        if add_no_channel and len(body) + len(NO_CHANNEL_REMINDER) <= max_body:
            body += NO_CHANNEL_REMINDER
        pages.append(body)

    return pages


class ListAlertsView(discord.ui.View):
    """Pagination view for !listalerts with ⬆️/⬇️ buttons."""

    def __init__(
        self,
        pages: list[str],
        *,
        author_id: int,
        timeout: float = 300,
    ):
        super().__init__(timeout=timeout)
        self.pages = pages
        self.author_id = author_id
        self.current = 0
        self._update_buttons()

    def _page_content(self) -> str:
        total = len(self.pages)
        header = f"**Active Alerts:** (page {self.current + 1}/{total})\n"
        return header + self.pages[self.current]

    def _update_buttons(self) -> None:
        self.prev_button.disabled = self.current <= 0
        self.next_button.disabled = self.current >= len(self.pages) - 1

    @discord.ui.button(emoji="⬆️", style=discord.ButtonStyle.secondary, row=0)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Only the user who ran the command can change pages.", ephemeral=True)
            return
        if self.current > 0:
            self.current -= 1
            self._update_buttons()
        await interaction.response.edit_message(content=self._page_content(), view=self)

    @discord.ui.button(emoji="⬇️", style=discord.ButtonStyle.secondary, row=0)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Only the user who ran the command can change pages.", ephemeral=True)
            return
        if self.current < len(self.pages) - 1:
            self.current += 1
            self._update_buttons()
        await interaction.response.edit_message(content=self._page_content(), view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        try:
            if self.message:
                await self.message.edit(view=self)
        except discord.NotFound:
            pass


def _has_announcement_channel() -> bool:
    return bool(get_setting(ANNOUNCEMENT_CHANNEL_KEY))


_last_no_channel_reminder: float = 0
REMINDER_COOLDOWN = 3600  # 1 hour


async def _send_no_channel_reminder_if_due() -> None:
    global _last_no_channel_reminder
    if time.time() - _last_no_channel_reminder < REMINDER_COOLDOWN:
        return
    _last_no_channel_reminder = time.time()
    for guild in bot.guilds:
        for ch in guild.text_channels:
            if ch.permissions_for(guild.me).send_messages:
                try:
                    await ch.send(
                        "⚠️ **Binance Alert Bot:** No announcement channel set. "
                        "Alerts won't be sent. Run `!setchannel` in your desired channel."
                    )
                    return
                except Exception:
                    continue


HELP_TEXT = f"""
**Message commands (use `{PREFIX}` prefix):**
• `{PREFIX}setchannel` — Set this channel for price alerts
• `{PREFIX}addalert <ticker> <price> [note]` — Add one alert
• `{PREFIX}bulkaddalert <ticker> <price1> <price2> ... [note]` — Add multiple alerts at once, e.g. `{PREFIX}bulkaddalert BTC 250000 200000 190000 https://polymarket.com/...`
• `{PREFIX}removealert <id>` — Remove alert by ID
• `{PREFIX}listalerts` — List all alerts
• `{PREFIX}help` — Show this help
• `{PREFIX}debug [ticker]` — Show current 1m candle data (default: BTC)
• `{PREFIX}setinterval <seconds>` — Set poll interval (30–300)
• `{PREFIX}interval` — Show current poll interval
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
        reply = HELP_TEXT
        if not _has_announcement_channel():
            reply += NO_CHANNEL_REMINDER
        await message.reply(reply)

    elif cmd == "setchannel":
        if not message.guild:
            await message.reply("Use this in a server channel.")
            return
        set_setting(ANNOUNCEMENT_CHANNEL_KEY, str(message.channel.id))
        await message.reply(f"Announcement channel set to {message.channel.mention}")

    elif cmd == "listalerts":
        alerts = get_all_alerts()
        if not alerts:
            reply = "No active alerts."
            if not _has_announcement_channel():
                reply += NO_CHANNEL_REMINDER
            await message.reply(reply)
            return
        lines = _build_alert_lines(alerts)
        pages = _pack_into_pages(lines, add_no_channel=not _has_announcement_channel())
        view = ListAlertsView(pages=pages, author_id=message.author.id)
        await message.reply(content=view._page_content(), view=view)

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
            reply = (
                f"Alert #{alert.id} added: **{display}** @ ${strike_price:,.2f}"
                + (f"\nNote: {note}" if note else "")
            )
            if not _has_announcement_channel():
                reply += NO_CHANNEL_REMINDER
            await message.reply(reply)
        except Exception as e:
            logger.exception("Failed to add alert: %s", e)
            await message.reply(f"Failed to add alert: {e}")

    elif cmd == "bulkaddalert":
        # !bulkaddalert BTC 250000 200000 190000 https://polymarket.com/...
        if len(parts) < 3:
            await message.reply(
                f"Usage: `{PREFIX}bulkaddalert <ticker> <price1> <price2> ... [note]`\n"
                f"Example: `{PREFIX}bulkaddalert BTC 250000 200000 190000 polymarket link`"
            )
            return
        ticker = parts[1]
        prices: list[float] = []
        note = ""
        for i, p in enumerate(parts[2:]):
            try:
                v = float(p)
                if v <= 0:
                    await message.reply(f"Price must be positive (got {v}).")
                    return
                prices.append(v)
            except ValueError:
                note = " ".join(parts[2 + i :])
                break
        if not prices:
            await message.reply("At least one price is required.")
            return
        try:
            added = []
            for strike_price in prices:
                alert = add_alert(ticker=ticker, strike_price=strike_price, note=note)
                added.append(f"#{alert.id} ${strike_price:,.0f}")
            display = _format_ticker(alert.ticker)
            reply = f"Added **{len(added)}** alerts for **{display}**: " + ", ".join(added)
            if note:
                reply += f"\nNote: {note}"
            if not _has_announcement_channel():
                reply += NO_CHANNEL_REMINDER
            await message.reply(reply)
        except Exception as e:
            logger.exception("Failed to bulk add alerts: %s", e)
            await message.reply(f"Failed to bulk add alerts: {e}")

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

    elif cmd == "setinterval":
        if len(parts) < 2:
            current = get_setting(POLL_INTERVAL_KEY) or str(DEFAULT_POLL_INTERVAL)
            await message.reply(
                f"Current polling interval: **{current}** seconds (range: {MIN_POLL_INTERVAL}–{MAX_POLL_INTERVAL})\n"
                f"Usage: `{PREFIX}setinterval <seconds>`"
            )
            return
        try:
            seconds = int(parts[1])
        except ValueError:
            await message.reply("Interval must be a number.")
            return
        if seconds < MIN_POLL_INTERVAL or seconds > MAX_POLL_INTERVAL:
            await message.reply(
                f"Interval must be between {MIN_POLL_INTERVAL} and {MAX_POLL_INTERVAL} seconds."
            )
            return
        set_setting(POLL_INTERVAL_KEY, str(seconds))
        await message.reply(f"Polling interval set to **{seconds}** seconds.")

    elif cmd == "interval":
        current = get_setting(POLL_INTERVAL_KEY) or str(DEFAULT_POLL_INTERVAL)
        await message.reply(
            f"Current polling interval: **{current}** seconds (range: {MIN_POLL_INTERVAL}–{MAX_POLL_INTERVAL})"
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


def _get_poll_interval() -> int:
    """Get poll interval from settings, clamped to valid range."""
    raw = get_setting(POLL_INTERVAL_KEY)
    if not raw:
        return DEFAULT_POLL_INTERVAL
    try:
        secs = int(raw)
        return max(MIN_POLL_INTERVAL, min(MAX_POLL_INTERVAL, secs))
    except ValueError:
        return DEFAULT_POLL_INTERVAL


async def alert_loop() -> None:
    """Background task: check alerts at configured interval."""
    await bot.wait_until_ready()
    interval = _get_poll_interval()
    logger.info("Alert loop started (interval=%ds).", interval)

    while not bot.is_closed():
        try:
            channel_id_str = get_setting(ANNOUNCEMENT_CHANNEL_KEY)
            channel_id = int(channel_id_str) if channel_id_str else None

            if channel_id:
                channel = bot.get_channel(channel_id)
                if channel is None:
                    try:
                        channel = await bot.fetch_channel(channel_id)
                    except discord.NotFound:
                        channel = None
                if channel:
                    await check_alerts_and_send(channel, fallback_channel_id=channel_id)
                else:
                    logger.warning("Announcement channel %s not found.", channel_id)
            else:
                # No channel set; send reminder once per hour if there are alerts
                if get_all_alerts():
                    await _send_no_channel_reminder_if_due()
        except Exception as e:
            logger.exception("Alert loop error: %s", e)

        interval = _get_poll_interval()
        await asyncio.sleep(interval)


TRASH_EMOJI = "\N{WASTEBASKET}"  # 🗑️


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent) -> None:
    """Delete alert message when user clicks trash reaction."""
    if payload.user_id == bot.user.id:
        return
    if str(payload.emoji) != TRASH_EMOJI and payload.emoji.name != "wastebasket":
        return
    channel = bot.get_channel(payload.channel_id) or await bot.fetch_channel(payload.channel_id)
    if not channel:
        return
    try:
        message = await channel.fetch_message(payload.message_id)
    except discord.NotFound:
        return
    if message.author.id != bot.user.id:
        return
    try:
        await message.delete()
    except discord.Forbidden:
        pass


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
