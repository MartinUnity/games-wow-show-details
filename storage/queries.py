"""
storage.queries — the thin pandas-returning query surface.

This is the only storage API the FastAPI layer (Phase 3) and, optionally, the
Streamlit app (Phase 2.3) should use. Functions mirror what
``utils/data_io.load_csv`` produced so views/aggregations can switch data
sources without logic changes.

Requires pandas (unlike storage.db / storage.sync, which are stdlib-only).
"""

import os

import pandas as pd

from config import DB_PATH
from storage import db as sdb

_TS_FORMAT = "%m/%d/%Y %H:%M:%S.%f"


def _frame_from_cursor(cur) -> pd.DataFrame:
    cols = [d[0] for d in cur.description]
    return pd.DataFrame(cur.fetchall(), columns=cols)


def _events_sql(combat_id, character, select: str) -> tuple:
    # ``select`` is an internal constant column list (never user input);
    # all dynamic values go through ? placeholders.
    sql = f"SELECT {select} FROM events"  # noqa: S608
    where, args = [], []
    if combat_id is not None:
        where.append("combat_id = ?")
        args.append(int(combat_id))
    if character is not None:
        where.append("source = ?")
        args.append(character)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY rowid"
    return sql, args


def load_events_raw(combat_id: int | None = None,
                    character: str | None = None,
                    db_path: str = DB_PATH) -> pd.DataFrame:
    """All events including the internal ``rowid``/``ts`` helper columns
    (for tooling/tests; app code should use :func:`load_events`)."""
    conn = sdb.connect(db_path)
    try:
        sql, args = _events_sql(combat_id, character, select="*")
        df = _frame_from_cursor(conn.execute(sql, args))
    finally:
        conn.close()
    if not df.empty:
        df["timestamp_dt"] = pd.to_datetime(
            df["timestamp"], format=_TS_FORMAT, errors="coerce")
    else:
        df["timestamp_dt"] = pd.Series(dtype="datetime64[ns]")
    return df


def load_events(combat_id: int | None = None,
                character: str | None = None,
                db_path: str = DB_PATH) -> pd.DataFrame:
    """Events as a DataFrame shaped exactly like ``data_io.load_csv`` output:

    the 12 CSV columns plus a parsed ``timestamp_dt`` column (no internal
    ``rowid``/``ts`` columns).

    One documented difference from the CSV loader: empty fields come back as
    ``""`` (SQLite) rather than ``NaN`` (pandas.read_csv).
    """
    conn = sdb.connect(db_path)
    try:
        sql, args = _events_sql(combat_id, character,
                                select=", ".join(sdb.EVENT_COLUMNS))
        df = _frame_from_cursor(conn.execute(sql, args))
    finally:
        conn.close()
    if not df.empty:
        df["timestamp_dt"] = pd.to_datetime(
            df["timestamp"], format=_TS_FORMAT, errors="coerce")
    else:
        df["timestamp_dt"] = pd.Series(dtype="datetime64[ns]")
    return df


def encounter_summary(db_path: str = DB_PATH) -> pd.DataFrame:
    """One row per encounter (combat_id > 0): bounds, duration, event count,
    zone. Ordered by combat_id."""
    conn = sdb.connect(db_path)
    try:
        cur = conn.execute(
            """
            SELECT combat_id,
                   MIN(ts)                AS start_ts,
                   MAX(ts)                AS end_ts,
                   MAX(ts) - MIN(ts)      AS duration_s,
                   COUNT(*)               AS events,
                   zone_id,
                   zone_name
            FROM events
            WHERE combat_id > 0
            GROUP BY combat_id
            ORDER BY combat_id
            """
        )
        return _frame_from_cursor(cur)
    finally:
        conn.close()


def latest_combat_id(db_path: str = DB_PATH) -> int:
    conn = sdb.connect(db_path)
    try:
        row = conn.execute(
            "SELECT MAX(combat_id) FROM events WHERE combat_id > 0"
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    finally:
        conn.close()


def db_stats(db_path: str = DB_PATH) -> dict:
    """Watermark-style stats for /api/health and the SSE poll: row count,
    latest combat id, last event epoch, last sync time. Empty dict if the DB
    does not exist yet."""
    if not os.path.exists(db_path):
        return {}
    conn = sdb.connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(combat_id), 0), COALESCE(MAX(ts), 0)"
            " FROM events"
        ).fetchone()
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        return {
            "row_count": int(row[0]),
            "max_combat_id": int(row[1]),
            "last_event_ts": float(row[2]),
            "synced_at": float(meta["synced_at"]) if "synced_at" in meta else None,
        }
    finally:
        conn.close()


def count_encounters(db_path: str = DB_PATH) -> int:
    """Distinct in-combat encounter count (combat_id > 0) — sidebar display."""
    if not os.path.exists(db_path):
        return 0
    conn = sdb.connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(DISTINCT combat_id) FROM events WHERE combat_id > 0"
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()


def load_boss_kills(db_path: str = DB_PATH) -> list[dict]:
    conn = sdb.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT boss_name, start_ts, end_ts, kill_flag, zone_id"
            " FROM boss_kills ORDER BY start_ts"
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def load_hidden(db_path: str = DB_PATH) -> set[int]:
    conn = sdb.connect(db_path)
    try:
        return {
            r[0] for r in conn.execute("SELECT combat_id FROM hidden_combats")
        }
    finally:
        conn.close()


def load_notes(db_path: str = DB_PATH) -> dict[int, str]:
    conn = sdb.connect(db_path)
    try:
        return {
            r[0]: r[1]
            for r in conn.execute("SELECT combat_id, note FROM notes")
        }
    finally:
        conn.close()


def load_healer_spells(db_path: str = DB_PATH) -> dict:
    """Rebuild the spec -> [int | str, ...] mapping (ids back to ints)."""
    conn = sdb.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT spec, spell_value FROM healer_spells ORDER BY rowid"
        ).fetchall()
    finally:
        conn.close()
    out: dict = {}
    for spec, value in rows:
        try:
            value = int(value)
        except Exception:
            pass
        out.setdefault(spec, []).append(value)
    return out
