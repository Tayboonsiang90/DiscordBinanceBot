"""Service to check alerts against Binance candle data and send Discord embeds."""

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from discord import Embed

from src.binance_client import fetch_latest_closed_candle
from src.database import Alert, get_all_alerts, get_distinct_tickers, remove_alert

if TYPE_CHECKING:
    from discord.abc import MessageableChannel

logger = logging.getLogger(__name__)

# Track last checked close_time per ticker to avoid duplicate processing
_last_checked: dict[str, int] = {}


def _format_ticker(ticker: str) -> str:
    """Format ticker for display (e.g. BTCUSDT -> BTC/USDT)."""
    if ticker.endswith("USDT"):
        base = ticker[:-4]
        return f"{base}/USDT"
    return ticker


async def check_alerts_and_send(
    send_channel: "MessageableChannel",
    fallback_channel_id: Optional[int] = None,
) -> None:
    """
    Fetch latest closed candle for each ticker with alerts,
    check strike conditions, send embeds for hits, remove hit alerts.
    """
    tickers = get_distinct_tickers()
    if not tickers:
        return

    for ticker in tickers:
        candle = fetch_latest_closed_candle(ticker)
        if not candle:
            continue

        close_time = candle["close_time"]
        if _last_checked.get(ticker) == close_time:
            continue
        _last_checked[ticker] = close_time

        high = candle["high"]
        low = candle["low"]

        alerts = [a for a in get_all_alerts() if a.ticker == ticker]

        for alert in alerts:
            hit = False
            price_value = 0.0

            # Touch: price crossed strike (low <= strike <= high)
            if alert.direction == "touch" or not alert.direction:
                if low <= alert.strike_price <= high:
                    hit = True
                    price_value = alert.strike_price
            elif alert.direction == "up" and high >= alert.strike_price:
                hit = True
                price_value = high
            elif alert.direction == "down" and low <= alert.strike_price:
                hit = True
                price_value = low

            if hit:
                channel = send_channel
                if alert.channel_id and hasattr(send_channel, "guild") and send_channel.guild:
                    target = send_channel.guild.get_channel(alert.channel_id)
                    if target:
                        channel = target
                elif fallback_channel_id and hasattr(send_channel, "guild") and send_channel.guild:
                    target = send_channel.guild.get_channel(fallback_channel_id)
                    if target:
                        channel = target

                embed = _build_alert_embed(alert, price_value, candle)
                try:
                    msg = await channel.send(embed=embed)
                    await msg.add_reaction("\N{WASTEBASKET}")  # 🗑️ trash - click to delete
                except Exception as e:
                    logger.exception("Failed to send alert embed: %s", e)

                remove_alert(alert.id)
                logger.info(
                    "Alert %d hit and removed for %s %s %s",
                    alert.id,
                    ticker,
                    alert.direction,
                    alert.strike_price,
                )


def _build_alert_embed(alert: Alert, price_value: float, candle: dict) -> Embed:
    """Build Discord embed for a triggered alert."""
    if alert.direction == "touch" or not alert.direction:
        direction_label = "Touched"
        price_field = "Candle Range"
        price_display = f"${candle['low']:,.2f} – ${candle['high']:,.2f}"
        color = 0x3498DB  # Blue for touch
    elif alert.direction == "up":
        direction_label = "Up"
        price_field = "Candle High"
        price_display = f"${price_value:,.2f}"
        color = 0x00FF00
    else:
        direction_label = "Down"
        price_field = "Candle Low"
        price_display = f"${price_value:,.2f}"
        color = 0xFF0000

    title = f"{_format_ticker(alert.ticker)} Price Alert"
    embed = Embed(
        title=title,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(
        name="Strike",
        value=f"${alert.strike_price:,.2f} ({direction_label})",
        inline=True,
    )
    embed.add_field(name=price_field, value=price_display, inline=True)
    if alert.note:
        embed.add_field(name="Note", value=alert.note, inline=False)

    close_time_ms = candle.get("close_time", 0)
    if close_time_ms:
        dt = datetime.fromtimestamp(close_time_ms / 1000, tz=timezone.utc)
        embed.add_field(
            name="Candle Time",
            value=dt.strftime("%Y-%m-%d %H:%M UTC"),
            inline=False,
        )

    return embed
