"""SQLite storage for price alerts."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "alerts.db"


@dataclass
class Alert:
    """Represents a price alert."""

    id: int
    ticker: str
    strike_price: float
    direction: str  # "up" or "down"
    note: str
    channel_id: Optional[int]
    created_at: str


def _ensure_data_dir() -> None:
    """Create data directory if it doesn't exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_connection() -> sqlite3.Connection:
    """Get a database connection."""
    _ensure_data_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                strike_price REAL NOT NULL,
                direction TEXT NOT NULL CHECK(direction IN ('up', 'down', 'touch')),
                note TEXT DEFAULT '',
                channel_id INTEGER,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        # Migration: add 'touch' to direction (recreate if old schema)
        cursor = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='alerts'"
        )
        row = cursor.fetchone()
        if row and "'touch'" not in (row[0] or ""):
            conn.execute("""
                CREATE TABLE alerts_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    strike_price REAL NOT NULL,
                    direction TEXT NOT NULL CHECK(direction IN ('up', 'down', 'touch')),
                    note TEXT DEFAULT '',
                    channel_id INTEGER,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.execute("INSERT INTO alerts_new SELECT * FROM alerts")
            conn.execute("DROP TABLE alerts")
            conn.execute("ALTER TABLE alerts_new RENAME TO alerts")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.commit()
    finally:
        conn.close()


def add_alert(
    ticker: str,
    strike_price: float,
    direction: str = "touch",
    note: str = "",
    channel_id: Optional[int] = None,
) -> Alert:
    """Add a new alert."""
    ticker = ticker.upper().replace("/", "")
    if not ticker.endswith("USDT"):
        ticker = f"{ticker}USDT"

    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            INSERT INTO alerts (ticker, strike_price, direction, note, channel_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ticker, strike_price, direction, note, channel_id),
        )
        conn.commit()
        row_id = cursor.lastrowid
    finally:
        conn.close()

    return get_alert_by_id(row_id)  # type: ignore


def get_alert_by_id(alert_id: int) -> Optional[Alert]:
    """Get an alert by ID."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
        return _row_to_alert(row) if row else None
    finally:
        conn.close()


def get_all_alerts() -> list[Alert]:
    """Get all active alerts."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM alerts ORDER BY id").fetchall()
        return [_row_to_alert(r) for r in rows]
    finally:
        conn.close()


def get_alerts_for_ticker(ticker: str) -> list[Alert]:
    """Get all alerts for a ticker."""
    ticker = ticker.upper().replace("/", "")
    if not ticker.endswith("USDT"):
        ticker = f"{ticker}USDT"

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM alerts WHERE ticker = ? ORDER BY id",
            (ticker,),
        ).fetchall()
        return [_row_to_alert(r) for r in rows]
    finally:
        conn.close()


def remove_alert(alert_id: int) -> bool:
    """Remove an alert by ID. Returns True if removed."""
    conn = get_connection()
    try:
        cursor = conn.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_distinct_tickers() -> list[str]:
    """Get list of tickers that have active alerts."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM alerts ORDER BY ticker"
        ).fetchall()
        return [r["ticker"] for r in rows]
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    """Set a key-value setting."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def get_setting(key: str) -> Optional[str]:
    """Get a setting value."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,),
        ).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def _row_to_alert(row: sqlite3.Row) -> Alert:
    """Convert a sqlite3.Row to an Alert."""
    return Alert(
        id=row["id"],
        ticker=row["ticker"],
        strike_price=row["strike_price"],
        direction=row["direction"],
        note=row["note"] or "",
        channel_id=row["channel_id"],
        created_at=row["created_at"],
    )
