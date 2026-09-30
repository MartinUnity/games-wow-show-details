"""
Phase 2 tests: `utils/` is importable and callable without Streamlit.

Exit criteria (docs/MIGRATION_PLAN.md):
- `utils.data_io` / `utils.data_engine` / `utils.replay_engine` import with
  Streamlit *unavailable* and their compute functions run from bare Python.
- `WOW_USE_SQLITE=1` serves `load_csv` from the derived DB with the same
  frame shape as the CSV path.

(Phase 5 retired the Streamlit app, `views/` and `utils/st_compat.py`;
the two tests that exercised that seam are gone with it.)
"""

import importlib.util
import pathlib
import subprocess
import sys

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import make_fixture_log as fx  # noqa: E402


def _load_parser():
    spec = importlib.util.spec_from_file_location(
        "wow_parser_phase2_test", ROOT / "wow-parser.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def fixture_csv(tmp_path_factory):
    """Real parser over the synthetic sessions → CSV in tmp."""
    tmp = tmp_path_factory.mktemp("phase2")
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


def test_utils_importable_without_streamlit():
    """The core Phase 2 exit criterion: import utils with Streamlit blocked."""
    code = (
        "import sys; sys.modules['streamlit'] = None\n"
        "import utils.data_io, utils.data_engine, utils.replay_engine\n"
        "from utils.data_engine import ("
        "combat_time_series, spell_aggregates, compute_totals_summary,"
        " compute_all_encounters_stats, compute_runs)\n"
        "print('ok')"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "ok"


def test_bare_compute_functions_produce_results(fixture_csv):
    from utils.data_engine import (
        compute_all_encounters_stats,
        compute_runs,
        compute_totals_summary,
    )
    from utils.data_io import compute_character_counts

    totals_df, meta = compute_totals_summary(path=str(fixture_csv))
    assert not totals_df.empty
    assert meta["total_combats"] == 6
    assert meta["total_duration_s"] > 0

    runs_df, enc_summary = compute_runs(path=str(fixture_csv))
    assert not runs_df.empty
    assert not enc_summary.empty
    assert set(enc_summary["combat_id"]) == {1, 2, 3, 4, 5, 6}

    meta2, enc_df, dmg, heal, top = compute_all_encounters_stats(
        path=str(fixture_csv))
    assert meta2["n_encounters"] == 6
    assert not dmg.empty
    assert not heal.empty

    chars = compute_character_counts(path=str(fixture_csv))
    assert not chars.empty
    assert int(chars["combats"].max()) == 4  # Zethrok fought all 4 of his

    # Replay manuscript on the raw fixture log (bare call, no Streamlit).
    from utils.replay_engine import generate_replay_manuscript
    log_path = fixture_csv.parent / "log.txt"
    out = generate_replay_manuscript(
        pd.Timestamp("2026-03-21 18:39:17"),
        pd.Timestamp("2026-03-21 18:40:00"),
        str(log_path),
    )
    assert out is None or isinstance(out, str)  # fixture may lack coords


def test_wow_use_sqlite_flag_matches_csv(fixture_csv, tmp_path, monkeypatch):
    """WOW_USE_SQLITE=1: same frame (modulo documented ""/NaN alignment),
    same compute results."""
    import config
    from storage.sync import sync_from_csv
    import utils.data_io as data_io

    db_path = tmp_path / "p2.db"
    sync_from_csv(str(fixture_csv), str(db_path))

    csv_frame = data_io.load_csv(str(fixture_csv))

    monkeypatch.setenv("WOW_USE_SQLITE", "1")
    monkeypatch.setattr(config, "CSV_PATH", str(fixture_csv))
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    try:
        db_frame = data_io.load_csv(str(fixture_csv))
    finally:
        monkeypatch.delenv("WOW_USE_SQLITE")

    assert list(db_frame.columns) == list(csv_frame.columns)
    pd.testing.assert_frame_equal(
        db_frame.fillna(""), csv_frame.fillna(""), check_dtype=False
    )

    # A compute function driven through the flag path gets identical results.
    from utils.data_engine import compute_totals_summary
    with monkeypatch.context() as m:
        m.setenv("WOW_USE_SQLITE", "1")
        totals_db, meta_db = compute_totals_summary(path=str(fixture_csv))
    totals_csv, meta_csv = compute_totals_summary(path=str(fixture_csv))
    pd.testing.assert_frame_equal(totals_db, totals_csv, check_dtype=False)
    assert meta_db == meta_csv


def test_sqlite_is_default_when_db_exists(fixture_csv, tmp_path, monkeypatch):
    """Phase 5: once the derived store exists, load_csv uses it by default;
    WOW_USE_SQLITE=0 forces the CSV."""
    import config
    from storage.sync import sync_from_csv
    import utils.data_io as data_io
    from api import deps

    db_path = tmp_path / "default.db"
    sync_from_csv(str(fixture_csv), str(db_path))
    monkeypatch.setattr(config, "CSV_PATH", str(fixture_csv))
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.delenv("WOW_USE_SQLITE", raising=False)

    assert deps.use_sqlite() is True
    monkeypatch.setenv("WOW_USE_SQLITE", "0")
    assert deps.use_sqlite() is False

    # Default (no env): SQLite row count visible via the derived store.
    monkeypatch.delenv("WOW_USE_SQLITE")
    df_default = data_io.load_csv(str(fixture_csv))
    monkeypatch.setenv("WOW_USE_SQLITE", "0")
    df_csv = data_io.load_csv(str(fixture_csv))
    assert len(df_default) == len(df_csv)
    pd.testing.assert_frame_equal(
        df_default.fillna(""), df_csv.fillna(""), check_dtype=False
    )


