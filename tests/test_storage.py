"""
SQLite storage layer tests (Phase 1.5).

Hermetic end-to-end: synthetic fixture → real parser → CSV (tmp_path) →
storage.sync → storage.queries. Exit criteria covered:

- repeated full syncs produce identical DB contents (rebuildable store)
- incremental_append catches up a simulated tail flush (byte-offset watermark)
- a replaced/shrunk CSV triggers a full rebuild, not a corrupt append
- queries match the pandas-from-CSV results (the app's current data source)
"""

import importlib.util
import pathlib
import sys

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import make_fixture_log as fx  # noqa: E402


def _load_parser():
    spec = importlib.util.spec_from_file_location(
        "wow_parser_storage_test", ROOT / "wow-parser.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def fixture_csv(tmp_path_factory):
    """Run the real parser over the synthetic sessions; return the CSV path."""
    tmp = tmp_path_factory.mktemp("storage")
    wp = _load_parser()
    log = tmp / "log.txt"
    log.write_text(
        "\n".join(fx.session_zethrok() + fx.session_lumiara()) + "\n",
        encoding="utf-8",
    )
    csv_path = tmp / "fixture.csv"
    original = wp._write_boss_kills
    wp._write_boss_kills = lambda *a, **k: None
    try:
        wp.export_csv(str(log), csv_path=str(csv_path))
    finally:
        wp._write_boss_kills = original
    return csv_path


def _db_dump(db_path):
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        out = []
        for table in ("events", "boss_kills", "notes", "hidden_combats",
                      "healer_spells", "meta"):
            order = "key" if table == "meta" else "rowid"
            rows = conn.execute(
                f"SELECT * FROM {table} ORDER BY {order}"
            ).fetchall()
            if table == "meta":
                # synced_at is wall-clock; not part of content identity.
                rows = [(k, v) for k, v in rows if k != "synced_at"]
            out.append((table, rows))
        return out
    finally:
        conn.close()


def test_full_sync_is_idempotent(fixture_csv):
    from storage.sync import sync_from_csv

    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        db1 = os.path.join(td, "a.db")
        db2 = os.path.join(td, "b.db")
        n1 = sync_from_csv(str(fixture_csv), db1)
        n2 = sync_from_csv(str(fixture_csv), db2)
        assert n1 == n2 > 0
        assert _db_dump(db1) == _db_dump(db2)
        # No temp file left behind after the atomic swap.
        assert not os.path.exists(db1 + ".tmp")


def _copy_csv(dst_dir, fixture_csv):
    import shutil
    p = pathlib.Path(dst_dir) / "fixture.csv"
    shutil.copy(fixture_csv, p)
    return p


def test_incremental_append_catches_up_tail_flush(fixture_csv, tmp_path):
    import csv
    from storage import queries
    from storage.sync import incremental_append, sync_from_csv

    import tempfile, os

    csv_path = _copy_csv(tmp_path, fixture_csv)
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "c.db")
        before = sync_from_csv(str(csv_path), db)

        # Simulate a tail-mode flush: append one new encounter (combat 7).
        with open(csv_path, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow([7, "03/21/2026 20:00:00.000000", "SWING_DAMAGE",
                        "Zethrok-TheMaelstrom-EU", "New Mob", "Melee",
                        200, 200, "damage", 2962, "The Maelstrom", 0])
            w.writerow([7, "03/21/2026 20:00:05.000000", "SPELL_DAMAGE",
                        "Zethrok-TheMaelstrom-EU", "New Mob", "Flame Shock",
                        500, 500, "damage", 2962, "The Maelstrom", 188389])

        appended = incremental_append(str(csv_path), db)
        assert appended == 2
        assert queries.db_stats(db)["row_count"] == before + 2
        assert queries.latest_combat_id(db) == 7
        new = queries.load_events(combat_id=7, db_path=db)
        assert len(new) == 2
        assert list(new["amount"]) == [200, 500]

        # Second call with no new bytes → no-op, no duplicates.
        assert incremental_append(str(csv_path), db) == 0
        assert queries.db_stats(db)["row_count"] == before + 2


def test_shrunk_csv_triggers_full_rebuild(fixture_csv, tmp_path):
    import tempfile, os
    from storage.sync import incremental_append, sync_from_csv

    import tempfile, os

    csv_path = _copy_csv(tmp_path, fixture_csv)
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "d.db")
        n = sync_from_csv(str(csv_path), db)
        # Truncate the CSV to its first half (simulates --full-import rewrite).
        lines = csv_path.read_text(encoding="utf-8").splitlines(keepends=True)
        csv_path.write_text("".join(lines[: len(lines) // 2]), encoding="utf-8")
        incremental_append(str(csv_path), db)
        from storage import queries
        assert queries.db_stats(db)["row_count"] == len(lines) // 2 - 1
        assert queries.db_stats(db)["row_count"] < n


def test_queries_match_pandas_from_csv(fixture_csv):
    """The frame the app gets from SQLite equals the frame from
    pd.read_csv (modulo the documented "" vs NaN difference and the extra
    rowid/ts helper columns)."""
    import tempfile, os
    from storage import queries
    from storage.sync import sync_from_csv

    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "e.db")
        sync_from_csv(str(fixture_csv), db)

        ref = pd.read_csv(fixture_csv)
        ref["timestamp_dt"] = pd.to_datetime(
            ref["timestamp"], format="%m/%d/%Y %H:%M:%S.%f", errors="coerce")
        got = queries.load_events(db_path=db)

        cols = [c for c in ref.columns if c != "rowid"]
        pd.testing.assert_frame_equal(
            got[cols].fillna(""), ref.fillna(""), check_dtype=False
        )

        # encounter_summary is a quick rollup the API layer will lean on.
        enc = queries.encounter_summary(db_path=db)
        assert list(enc["combat_id"]) == [1, 2, 3, 4, 5, 6]
        assert (enc["duration_s"] > 0).all()
        assert (enc["events"] > 0).all()
        assert queries.latest_combat_id(db_path=db) == 6
