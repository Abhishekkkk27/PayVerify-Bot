"""
SQLite database layer for payment records.

Uses parameterized queries throughout to prevent SQL injection.
Amounts are stored as TEXT (Decimal serialisation) to avoid floating-point drift.
"""

import sqlite3
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from config import DB_PATH

logger = logging.getLogger(__name__)

# ── Schema ───────────────────────────────────────────────────────────────────

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS payments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id      TEXT    NOT NULL UNIQUE,
    telegram_user_id INTEGER NOT NULL,
    username        TEXT,
    amount          TEXT    NOT NULL,
    payment_code    TEXT    NOT NULL UNIQUE,
    status          TEXT    NOT NULL DEFAULT 'PENDING',
    created_at      TEXT    NOT NULL,
    verified_at     TEXT,
    utr             TEXT,
    sender          TEXT
);
"""


def _connect() -> sqlite3.Connection:
    """Return a new connection with row-factory enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db() -> None:
    """Create the payments table if it does not exist."""
    with _connect() as conn:
        conn.execute(_CREATE_TABLE)
        conn.commit()
    logger.info("Database initialised (%s)", DB_PATH)


# ── Write operations ─────────────────────────────────────────────────────────

def create_payment(
    payment_id: str,
    telegram_user_id: int,
    username: Optional[str],
    amount: Decimal,
    payment_code: str,
) -> None:
    """Insert a new PENDING payment record."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO payments
                (payment_id, telegram_user_id, username, amount, payment_code, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'PENDING', ?)
            """,
            (payment_id, telegram_user_id, username, str(amount), payment_code, now),
        )
        conn.commit()
    logger.info("Payment created: %s  code=%s  amount=%s", payment_id, payment_code, amount)


def update_payment_status(
    payment_code: str,
    status: str,
    utr: Optional[str] = None,
    sender: Optional[str] = None,
) -> None:
    """Update payment status and optional UTR / sender."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE payments
               SET status      = ?,
                   verified_at  = CASE WHEN ? = 'VERIFIED' THEN ? ELSE verified_at END,
                   utr          = COALESCE(?, utr),
                   sender       = COALESCE(?, sender)
             WHERE payment_code = ?
            """,
            (status, status, now, utr, sender, payment_code),
        )
        conn.commit()
    logger.info("Payment %s -> %s  utr=%s", payment_code, status, utr)


# ── Read operations ──────────────────────────────────────────────────────────

def get_pending_payment_by_user(telegram_user_id: int) -> Optional[sqlite3.Row]:
    """Return the most recent PENDING payment for a user, or None."""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM payments
             WHERE telegram_user_id = ?
               AND status = 'PENDING'
             ORDER BY created_at DESC
             LIMIT 1
            """,
            (telegram_user_id,),
        ).fetchone()
    return row


def get_payment_by_code(payment_code: str) -> Optional[sqlite3.Row]:
    """Fetch a payment by its unique code."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM payments WHERE payment_code = ?",
            (payment_code,),
        ).fetchone()
    return row


def payment_code_exists(payment_code: str) -> bool:
    """Check whether a payment code is already in use."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM payments WHERE payment_code = ?",
            (payment_code,),
        ).fetchone()
    return row is not None


def get_payment_by_id(payment_id: str) -> Optional[sqlite3.Row]:
    """Fetch a payment by its payment_id."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM payments WHERE payment_id = ?",
            (payment_id,),
        ).fetchone()
    return row


def cancel_pending_payments(telegram_user_id: int) -> int:
    """Cancel all PENDING payments for a user. Returns count cancelled."""
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE payments
               SET status = 'CANCELLED'
             WHERE telegram_user_id = ?
               AND status = 'PENDING'
            """,
            (telegram_user_id,),
        )
        conn.commit()
    return cur.rowcount
