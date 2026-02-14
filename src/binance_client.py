"""Binance API client for fetching 1-minute candle data."""

import logging
from typing import Optional

from binance.client import Client

logger = logging.getLogger(__name__)

# Candle indices: [open_time, open, high, low, close, volume, close_time, ...]
HIGH_INDEX = 2
LOW_INDEX = 3
CLOSE_TIME_INDEX = 6


def _get_client() -> Client:
    """Get Binance client (no API key needed for public market data)."""
    return Client()


def fetch_latest_closed_candle(ticker: str) -> Optional[dict]:
    """
    Fetch the most recently closed 1-minute candle for a ticker.

    Returns dict with: high, low, close_time
    Returns None on error.
    """
    ticker = ticker.upper().replace("/", "")
    if not ticker.endswith("USDT"):
        ticker = f"{ticker}USDT"

    try:
        client = _get_client()
        klines = client.get_klines(
            symbol=ticker,
            interval=Client.KLINE_INTERVAL_1MINUTE,
            limit=2,
        )
        if not klines:
            logger.warning("No klines returned for %s", ticker)
            return None

        # Use the second-to-last candle (last closed); last one may still be forming
        # Actually: klines returns [oldest, ..., newest]. The last element is the
        # most recent candle. For "closed" we want the previous minute's candle.
        # Binance: candle is "closed" when close_time has passed.
        # Typically we request limit=2 and use index -2 (previous) to be safe,
        # or we check if the latest candle's close_time is in the past.
        candle = klines[-2] if len(klines) >= 2 else klines[-1]
        high = float(candle[HIGH_INDEX])
        low = float(candle[LOW_INDEX])
        close_time = int(candle[CLOSE_TIME_INDEX])

        return {
            "high": high,
            "low": low,
            "close_time": close_time,
        }
    except Exception as e:
        logger.exception("Failed to fetch candle for %s: %s", ticker, e)
        return None
