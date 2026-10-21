"""SQLite ledger: every forecast, prediction, paper trade and result is recorded,
tagged by strategy version, so versions can be compared side by side."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS forecasts (
    run_ts TEXT, target_date TEXT, source TEXT, value REAL,
    PRIMARY KEY (run_ts, target_date, source)
);
CREATE TABLE IF NOT EXISTS observations (
    ts TEXT PRIMARY KEY, obs_date TEXT, max_so_far REAL
);
CREATE TABLE IF NOT EXISTS predictions (
    run_ts TEXT, target_date TEXT, version TEXT, label TEXT, lo REAL, hi REAL,
    model_p REAL, final_p REAL, market_p REAL, yes_bid REAL, yes_ask REAL,
    fc_mean REAL, fc_sigma REAL,
    PRIMARY KEY (run_ts, target_date, version, label)
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_ts TEXT, target_date TEXT, version TEXT, label TEXT, side TEXT,
    price REAL, prob REAL, edge REAL, shares REAL, stake REAL,
    pnl REAL, settled INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS results (
    target_date TEXT PRIMARY KEY, winner_label TEXT, hko_reading REAL,
    cutoff_consistent INTEGER, source TEXT, settled_ts TEXT
);
-- One row per 30-minute snapshot round (the folder name under data/snapshots/).
CREATE TABLE IF NOT EXISTS snapshots (
    run_ts TEXT PRIMARY KEY, folder TEXT, sources_ok INTEGER, sources_failed TEXT,
    pages_ok INTEGER, pages_failed TEXT, xcheck_mismatches INTEGER, seconds REAL
);
-- Top of book + cost to actually buy $10 (walking the book, fee included), per bucket per round.
CREATE TABLE IF NOT EXISTS market_quotes (
    run_ts TEXT, target_date TEXT, label TEXT,
    yes_bid REAL, yes_ask REAL, yes_bid_size REAL, yes_ask_size REAL,
    no_ask REAL, no_ask_size REAL,
    yes_cost_10 REAL, no_cost_10 REAL,          -- average price per share for $10 incl. taker fee
    depth_yes_usd REAL, depth_no_usd REAL,      -- dollars offered within 2 cents of the best ask
    fee_rate REAL, volume REAL, page_buy_yes REAL, page_buy_no REAL,
    PRIMARY KEY (run_ts, target_date, label)
);
-- Feed number vs number shown on the page (or vs a second feed).
CREATE TABLE IF NOT EXISTS xchecks (
    run_ts TEXT, check_name TEXT, item TEXT, feed_value REAL, page_value REAL, ok INTEGER,
    PRIMARY KEY (run_ts, check_name, item)
);
CREATE TABLE IF NOT EXISTS scores (
    target_date TEXT, version TEXT, run_ts TEXT,
    brier_bot REAL, brier_market REAL, logloss_bot REAL, logloss_market REAL,
    p_winner_bot REAL, p_winner_market REAL, pnl REAL,
    PRIMARY KEY (target_date, version)
);
"""


# Columns added after the first version; added to an existing ledger without touching old rows.
#   exec_price = what $10 really costs per share: average over the order book + taker fee.
#   pnl        = P&L at exec_price (the honest number); pnl_quoted = old way, at the top price, no fee.
MIGRATIONS = [
    ("trades", "exec_price", "REAL"), ("trades", "fee_per_share", "REAL"),
    ("trades", "exec_shares", "REAL"), ("trades", "pnl_quoted", "REAL"),
]


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    for table, col, typ in MIGRATIONS:
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if col not in have:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    return con


def backup(dest: Path) -> Path:
    """Consistent copy of the ledger while the bot may be writing (SQLite online backup)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = connect()
    try:
        out = sqlite3.connect(dest)
        with out:
            src.backup(out)
        out.close()
    finally:
        src.close()
    return dest


@contextmanager
def db(path: Path | None = None):
    con = connect(path)
    try:
        yield con
        con.commit()
    finally:
        con.close()
