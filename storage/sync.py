"""
storage.sync — keep the SQLite store in sync with the CSV + sidecar files.

The CSV is the source of truth (written by wow-parser.py); the DB is derived
and always rebuildable:

    sync_from_csv(csv_path, db_path)
        Full, transactional rebuild. Builds into ``<db_path>.tmp`` and
        atomically swaps it in, so a reader never sees a half-written DB.

    incremental_append(csv_path, db_path)
        Cheap catch-up for the tail-mode case: reads only the bytes appended
        since the last sync (byte-offset watermark in the ``meta`` table) and
        appends those rows. Falls back to a full rebuild whenever the CSV was
        replaced/shrunk or the watermark is missing.

Both calls also refresh the small sidecar tables (boss_kills, notes,
hidden_combats, healer_spells) — they are tiny, so a full table replace per
sync is the simple, correct choice.

Stdlib only — importable from the stdlib-only parser.
"""

import calendar
import csv
import json
import os
import time
from datetime import datetime

from config import (
    BOSS_KILLS_PATH,
    DB_PATH,
    HEALER_SPELLS_PATH,
    HIDDEN_PATH,
    NOTES_PATH,
)
from storage import db as sdb

_TS_FORMAT = "%m/%d/%Y %H:%M:%S.%f"
_CHUNK_SIZE = 50_000


def _ts_to_epoch(ts_str: str) -> float:
    """Parse the CSV timestamp into a UTC epoch. 0.0 on failure (defensive)."""
    try:
        dt = datetime.strptime(ts_str, _TS_FORMAT)
        return float(calendar.timegm(dt.timetuple()) + dt.microsecond / 1e6)
    except Exception:
        return 0.0


def _row_values(csv_row: list) -> tuple:
    """Map a 12-field CSV row to the events-table column order."""
    def _int(v):
        try:
            return int(v)
        except Exception:
            return 0

    # CSV: 0 combat_id, 1 timestamp, 2 event, 3 source, 4 target,
    #      5 spell_name, 6 amount, 7 effective_amount, 8 type,
    #      9 zone_id, 10 zone_name, 11 spell_id
    return (
        _int(csv_row[0]),
        csv_row[1],
        _ts_to_epoch(csv_row[1]),
        csv_row[2],
        csv_row[3],
        csv_row[4],
        csv_row[5],
        _int(csv_row[6]),
        _int(csv_row[7]),
        csv_row[8],
        _int(csv_row[9]),
        csv_row[10],
        _int(csv_row[11]) if len(csv_row) > 11 else 0,
    )


def _insert_rows(conn, rows) -> int:
    cols = ", ".join(
        ["combat_id", "timestamp", "ts", "event", "source", "target",
         "spell_name", "amount", "effective_amount", "type",
         "zone_id", "zone_name", "spell_id"]
    )
    ph = ", ".join("?" * 13)
    # cols/ph are static literals built above — no user input.
    conn.executemany(
        f"INSERT INTO events ({cols}) VALUES ({ph})",  # noqa: S608
        rows)
    return len(rows)


# ── Sidecar tables ───────────────────────────────────────────────────────────


def _sync_sidecars(conn) -> None:
    """Replace the small sidecar tables from their JSON(L) files."""
    # boss_kills.jsonl
    kills = []
    if os.path.exists(BOSS_KILLS_PATH):
        with open(BOSS_KILLS_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    kills.append(
                        (r.get("boss_name", ""), r.get("start_ts", ""),
                         r.get("end_ts", ""), int(r.get("kill_flag", 0)),
                         int(r.get("zone_id", 0)))
                    )
                except Exception:
                    pass
    conn.execute("DELETE FROM boss_kills")
    conn.executemany(
        "INSERT INTO boss_kills (boss_name, start_ts, end_ts, kill_flag, zone_id)"
        " VALUES (?, ?, ?, ?, ?)", kills)

    # encounter_notes.jsonl
    notes = []
    if os.path.exists(NOTES_PATH):
        with open(NOTES_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    notes.append((int(r["combat_id"]), r.get("note", "")))
                except Exception:
                    pass
    conn.execute("DELETE FROM notes")
    conn.executemany("INSERT INTO notes (combat_id, note) VALUES (?, ?)", notes)

    # hidden_combats.json (plain JSON list of combat ids)
    hidden = []
    if os.path.exists(HIDDEN_PATH):
        try:
            with open(HIDDEN_PATH, encoding="utf-8") as f:
                hidden = [(int(c),) for c in json.load(f)]
        except Exception:
            hidden = []
    conn.execute("DELETE FROM hidden_combats")
    conn.executemany("INSERT INTO hidden_combats (combat_id) VALUES (?)", hidden)

    # healer_spells.json (spec -> [spell id | name, ...])
    spells = []
    if os.path.exists(HEALER_SPELLS_PATH):
        try:
            with open(HEALER_SPELLS_PATH, encoding="utf-8") as f:
                mapping = json.load(f)
            for spec, vals in mapping.items():
                if isinstance(vals, list):
                    spells.extend((spec, str(v)) for v in vals)
        except Exception:
            pass
    conn.execute("DELETE FROM healer_spells")
    conn.executemany(
        "INSERT OR IGNORE INTO healer_spells (spec, spell_value) VALUES (?, ?)",
        spells)


# ── Watermark ────────────────────────────────────────────────────────────────


def _set_watermark(conn, byte_offset: int, row_count: int,
                   max_combat_id: int, last_ts: float) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        [
            ("wm_byte_offset", str(byte_offset)),
            ("wm_row_count", str(row_count)),
            ("wm_max_combat_id", str(max_combat_id)),
            ("wm_last_ts", repr(last_ts)),
            ("synced_at", repr(time.time())),
        ],
    )


def _get_watermark(conn):
    rows = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    if "wm_byte_offset" not in rows:
        return None
    return {
        "byte_offset": int(rows["wm_byte_offset"]),
        "row_count": int(rows["wm_row_count"]),
        "max_combat_id": int(rows["wm_max_combat_id"]),
        "last_ts": float(rows["wm_last_ts"]),
    }


# ── Public API ───────────────────────────────────────────────────────────────


def sync_from_csv(csv_path: str, db_path: str = DB_PATH) -> int:
    """Full rebuild of the DB from the CSV + sidecars. Returns row count."""
    if not os.path.exists(csv_path):
        raise FileNotFoundError(csv_path)

    tmp_path = db_path + ".tmp"
    for suffix in ("", "-wal", "-shm"):
        p = tmp_path + suffix
        if os.path.exists(p):
            os.remove(p)

    conn = sdb.build_new_db(tmp_path)
    file_size = os.path.getsize(csv_path)
    total_rows = 0
    try:
        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            next(reader, None)  # header
            chunk = []
            for row in reader:
                if len(row) < 12:
                    continue  # defensive: skip malformed short lines
                chunk.append(_row_values(row))
                if len(chunk) >= _CHUNK_SIZE:
                    _insert_rows(conn, chunk)
                    total_rows += len(chunk)
                    chunk = []
            if chunk:
                _insert_rows(conn, chunk)
                total_rows += len(chunk)

        _sync_sidecars(conn)
        cur = conn.execute(
            "SELECT COALESCE(MAX(combat_id), 0) FROM events")
        max_cid = cur.fetchone()[0]
        cur = conn.execute("SELECT COALESCE(MAX(ts), 0) FROM events")
        last_ts = cur.fetchone()[0]
        _set_watermark(conn, file_size, total_rows, max_cid, last_ts)
        conn.commit()
    except Exception:
        conn.close()
        for suffix in ("", "-wal", "-shm"):
            p = tmp_path + suffix
            if os.path.exists(p):
                os.remove(p)
        raise

    conn.close()
    # WAL checkpoint happens on close; swap atomically.
    for suffix in ("-wal", "-shm"):
        p = tmp_path + suffix
        if os.path.exists(p):
            os.remove(p)
    os.replace(tmp_path, db_path)
    return total_rows


def incremental_append(csv_path: str, db_path: str = DB_PATH) -> int:
    """Append CSV rows added since the last sync. Returns rows appended.

    Falls back to a full rebuild if the DB is missing, has no watermark, or
    the CSV was replaced/shrunk.
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(csv_path)

    if not os.path.exists(db_path):
        return sync_from_csv(csv_path, db_path)

    file_size = os.path.getsize(csv_path)
    conn = sdb.connect(db_path)
    try:
        wm = _get_watermark(conn)
        if wm is None or file_size < wm["byte_offset"]:
            conn.close()
            return sync_from_csv(csv_path, db_path)
        if file_size == wm["byte_offset"]:
            # Nothing new, but sidecars may have changed — keep them fresh.
            _sync_sidecars(conn)
            conn.commit()
            return 0

        # Read only the appended bytes; trim a partial trailing line so a
        # mid-flush read can never duplicate a row on the next pass.
        with open(csv_path, "rb") as f:
            f.seek(wm["byte_offset"])
            data = f.read()
        cut = len(data)
        if not data.endswith(b"\n"):
            nl = data.rfind(b"\n")
            if nl < 0:
                return 0  # not even one full line yet
            cut = nl + 1
        new_offset = wm["byte_offset"] + cut

        rows = []
        lines = data[:cut].decode("utf-8", errors="replace").splitlines()
        for row in csv.reader(lines):
            if len(row) < 12:
                continue
            rows.append(_row_values(row))

        if rows:
            _insert_rows(conn, rows)
            cur = conn.execute("SELECT COALESCE(MAX(combat_id), 0) FROM events")
            max_cid = cur.fetchone()[0]
            cur = conn.execute("SELECT COALESCE(MAX(ts), 0) FROM events")
            last_ts = cur.fetchone()[0]
        else:
            max_cid, last_ts = wm["max_combat_id"], wm["last_ts"]

        _sync_sidecars(conn)
        _set_watermark(conn, new_offset, wm["row_count"] + len(rows),
                       max_cid, last_ts)
        conn.commit()
        return len(rows)
    finally:
        conn.close()
