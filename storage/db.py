"""
storage.db — schema and connection helper for the derived SQLite store.

Stdlib only (importable from wow-parser.py). Timestamps are stored twice:
the original display string and a UTC epoch (``timegm`` — deterministic
across machines, unlike local-time ``.timestamp()``) for range queries.
"""

import os
import sqlite3

from config import DB_PATH

# Columns mirrored 1:1 from the CSV contract (see docs/MIGRATION_PLAN.md).
EVENT_COLUMNS = [
    "combat_id",
    "timestamp",
    "event",
    "source",
    "target",
    "spell_name",
    "amount",
    "effective_amount",
    "type",
    "zone_id",
    "zone_name",
    "spell_id",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    rowid            INTEGER PRIMARY KEY,   -- stable append order
    combat_id        INTEGER NOT NULL,
    timestamp        TEXT    NOT NULL,      -- original display string
    ts               REAL    NOT NULL,      -- UTC epoch seconds
    event            TEXT    NOT NULL,
    source           TEXT    NOT NULL,
    target           TEXT    NOT NULL,
    spell_name       TEXT    NOT NULL,
    amount           INTEGER NOT NULL,
    effective_amount INTEGER NOT NULL,
    type             TEXT    NOT NULL,
    zone_id          INTEGER NOT NULL,
    zone_name        TEXT    NOT NULL,
    spell_id         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_combat_ts ON events (combat_id, ts);
CREATE INDEX IF NOT EXISTS idx_events_source ON events (source);
CREATE INDEX IF NOT EXISTS idx_events_type ON events (type);

CREATE TABLE IF NOT EXISTS boss_kills (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    boss_name TEXT NOT NULL,
    start_ts  TEXT NOT NULL,
    end_ts    TEXT NOT NULL,
    kill_flag INTEGER NOT NULL,
    zone_id   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    combat_id INTEGER PRIMARY KEY,
    note      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hidden_combats (
    combat_id INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS healer_spells (
    spec        TEXT NOT NULL,
    spell_value TEXT NOT NULL,   -- spell id or name, stringified
    PRIMARY KEY (spec, spell_value)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Open a connection with sane defaults for the app/server layers."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables/indexes if missing (idempotent)."""
    conn.executescript(_SCHEMA)
    conn.commit()


def build_new_db(db_path: str) -> sqlite3.Connection:
    """Create (or replace) a fresh database file with the schema applied."""
    parent = os.path.dirname(os.path.abspath(db_path))
    os.makedirs(parent, exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path)
    init_schema(conn)
    return conn
