from __future__ import annotations

import sqlite3
from pathlib import Path

import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    locale_rail TEXT NOT NULL DEFAULT 'stars',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wallets (
    telegram_id INTEGER PRIMARY KEY REFERENCES users(telegram_id),
    credits_balance INTEGER NOT NULL DEFAULT 0,
    daily_ask INTEGER NOT NULL DEFAULT 0,
    daily_summary INTEGER NOT NULL DEFAULT 0,
    daily_card INTEGER NOT NULL DEFAULT 0,
    daily_date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriptions (
    telegram_id INTEGER PRIMARY KEY REFERENCES users(telegram_id),
    tier TEXT NOT NULL DEFAULT 'free',
    expires_at TEXT,
    source TEXT,
    external_id TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    telegram_id INTEGER NOT NULL REFERENCES users(telegram_id),
    sku TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'created',
    created_at TEXT NOT NULL,
    paid_at TEXT
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    provider_payment_id TEXT NOT NULL,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    raw TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(provider, provider_payment_id)
);

CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(telegram_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
"""


def connect() -> sqlite3.Connection:
    path = Path(config.BILLING_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(_SCHEMA)
        conn.commit()
