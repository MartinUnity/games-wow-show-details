"""
api.deps — config and data access shared by the FastAPI routes.

Reads paths from `config` *at call time* (not import time) so tests can
monkeypatch `config.CSV_PATH` / `config.DB_PATH` / sidecar paths and the
whole API follows. Honours the same `WOW_USE_SQLITE` flag as the Streamlit
app: when set and the DB exists, `utils.data_io.load_csv` serves the derived
SQLite store; otherwise the CSV is read directly.
"""

import os
import time
from collections.abc import Callable

import pandas as pd

from utils import data_io

# ── Config (live values) ─────────────────────────────────────────────────────


def cfg():
    """Fresh `config` module (attribute values read at call time)."""
    import config

    return config


def use_sqlite() -> bool:
    """SQLite is the default once the derived store exists; `WOW_USE_SQLITE=0` forces the CSV."""
    c = cfg()
    return os.environ.get("WOW_USE_SQLITE", "1") != "0" and os.path.exists(c.DB_PATH)


# ── Event-frame access ───────────────────────────────────────────────────────


def get_events() -> pd.DataFrame:
    """Full event frame, shaped exactly like the old `load_csv` output.

    Memoized per data generation (db watermark or csv mtime/size) with a
    short TTL: a full 130k-row load takes ~2 s, and several endpoints in one
    SPA page-load would otherwise each pay it again.
    """
    return memo("events", 15.0, lambda: data_io.load_csv(path=cfg().CSV_PATH))


def load_events(character: str | None = None,
                combat_id: int | None = None) -> pd.DataFrame:
    """Optionally-filtered event frame (``character='All'`` → unfiltered)."""
    df = get_events()
    if character and character != "All":
        df = df[df["source"] == character]
    if combat_id is not None:
        df = df[df["combat_id"] == int(combat_id)]
    return df


def combat_exists(combat_id: int) -> bool:
    if use_sqlite():
        from storage import db as sdb

        conn = sdb.connect(cfg().DB_PATH)
        try:
            row = conn.execute(
                "SELECT 1 FROM events WHERE combat_id = ? LIMIT 1",
                (combat_id,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()
    return bool((get_events()["combat_id"] == int(combat_id)).any())


# ── Character list (mirrors the Streamlit sidebar logic) ─────────────────────


def _is_player_like(n: str) -> bool:
    parts = str(n).split("-")
    return len(parts) == 3 and all(parts)


def character_options(include_others: bool = False) -> dict:
    """Player-like characters (Name-Realm-Region) plus, optionally, the
    non-player sources that appear in >= MIN_SOURCE_COMBATS encounters.

    Also returns `counts` ({name: distinct-combat count}) for every source —
    the SPA's character tables use it directly.
    """
    c = cfg()
    df = get_events()
    names = [n for n in df["source"].dropna().unique() if str(n).strip() != ""]
    player_like = sorted(n for n in names if _is_player_like(n))
    others = sorted(n for n in names if not _is_player_like(n))
    # Same nunique logic as compute_character_counts, computed from the
    # frame we already hold (avoids a second full CSV/DB load).
    counts = (
        df[~df["source"].isnull() & (df["source"] != "")]
        .groupby("source")["combat_id"].nunique()
    )
    counts_map = {str(k): int(v) for k, v in counts.items()}
    result = {"characters": player_like, "counts": counts_map}
    if include_others:
        result["others"] = [
            n for n in others if counts_map.get(n, 0) >= c.MIN_SOURCE_COMBATS
        ]
    return result


# ── Sidecar + DB writes (single writer: the API) ─────────────────────────────


def db_conn():
    """SQLite connection for the derived store, or None when absent."""
    if not os.path.exists(cfg().DB_PATH):
        return None
    from storage import db as sdb

    return sdb.connect(cfg().DB_PATH)


def save_note(combat_id: int, note: str) -> None:
    """Persist a note to the JSONL sidecar and (if present) the DB table."""
    c = cfg()
    data_io.save_note(combat_id, note, path=c.NOTES_PATH)
    conn = db_conn()
    if conn is not None:
        try:
            if note:
                conn.execute(
                    "INSERT OR REPLACE INTO notes (combat_id, note) VALUES (?, ?)",
                    (int(combat_id), note),
                )
            else:
                conn.execute(
                    "DELETE FROM notes WHERE combat_id = ?", (int(combat_id),))
            conn.commit()
        finally:
            conn.close()


def set_hidden(combat_id: int, hidden: bool) -> set:
    """Add/remove a combat id in the hidden set; syncs JSON + DB. Returns the
    updated set."""
    c = cfg()
    hidden_set = data_io.load_hidden(path=c.HIDDEN_PATH)
    if hidden:
        hidden_set.add(int(combat_id))
    else:
        hidden_set.discard(int(combat_id))
    data_io.save_hidden(hidden_set, path=c.HIDDEN_PATH)
    conn = db_conn()
    if conn is not None:
        try:
            conn.execute("DELETE FROM hidden_combats WHERE combat_id = ?",
                         (int(combat_id),))
            if hidden:
                conn.execute(
                    "INSERT OR REPLACE INTO hidden_combats (combat_id) VALUES (?)",
                    (int(combat_id),))
            conn.commit()
        finally:
            conn.close()
    return hidden_set


def load_notes() -> dict:
    return data_io.load_notes(path=cfg().NOTES_PATH)


def load_hidden() -> set:
    return data_io.load_hidden(path=cfg().HIDDEN_PATH)


# ── Tiny TTL memo (per process) for expensive recomputations ─────────────────

_MEMO: dict = {}


def _data_key() -> str:
    """Changes whenever the underlying data could have changed."""
    c = cfg()
    try:
        st = os.stat(c.CSV_PATH)
        base = f"csv:{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        base = "csv:missing"
    return base if not use_sqlite() else f"db:{watermark()}"


def memo(key: str, ttl: float, fn: Callable) -> object:
    """Cache ``fn()`` for *ttl* seconds, keyed by (key, data generation)."""
    full = f"{key}:{_data_key()}"
    now = time.monotonic()
    hit = _MEMO.get(full)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _MEMO[full] = (now, val)
    return val


def reset_cache() -> None:
    """Drop the process memo (tests)."""
    _MEMO.clear()


# ── Watermark (SSE) ──────────────────────────────────────────────────────────


def watermark() -> dict:
    """(max combat_id, row count) — the liveness signal for /api/events."""
    if use_sqlite():
        from storage import queries

        stats = queries.db_stats(cfg().DB_PATH)
        if stats:
            return {"max_combat_id": stats["max_combat_id"],
                    "row_count": stats["row_count"]}
    # CSV fallback: scan just the id column.
    c = cfg()
    if os.path.exists(c.CSV_PATH):
        ids = pd.read_csv(c.CSV_PATH, usecols=["combat_id"])["combat_id"]
        return {"max_combat_id": int(ids.max()) if len(ids) else 0,
                "row_count": len(ids)}
    return {"max_combat_id": 0, "row_count": 0}
