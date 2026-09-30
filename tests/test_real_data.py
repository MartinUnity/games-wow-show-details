"""
Real-data regression tests.

Run against ``wow-data/`` (real combat CSV + newest raw combat log) when that
directory is present; skipped cleanly on machines without it (use
``make fixture`` for the synthetic dataset instead).

Two things are pinned here that the synthetic tests cannot:

- the real CSV already in the wild matches the 12-column storage contract,
- the raw combat-log field layouts (SPELL_DAMAGE=41, SPELL_HEAL=35,
  SWING_DAMAGE=37 rest-fields) are still what ``wow-parser.py`` expects —
  if Blizzard changes the log format these fail loudly instead of silently
  mis-parsing amounts.
"""

import csv
import pathlib
from collections import Counter

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
REAL_DIR = ROOT / "wow-data"
REAL_CSV = REAL_DIR / "parsed_combat_data.csv"

EXPECTED_COLUMNS = [
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

real_logs = sorted(REAL_DIR.glob("WoWCombatLog-*.txt")) if REAL_DIR.is_dir() else []

pytestmark = pytest.mark.skipif(
    not REAL_CSV.exists(), reason="wow-data/ real data not present on this machine"
)


def test_real_csv_matches_storage_contract():
    import pandas as pd

    df = pd.read_csv(REAL_CSV)
    assert list(df.columns) == EXPECTED_COLUMNS
    assert len(df) > 1000
    assert df["combat_id"].nunique() > 100
    # Melee regression guard on real data: no -1 sentinels.
    melee = df[df["spell_name"] == "Melee"]
    assert len(melee) > 0
    assert (melee["amount"] > 0).all()
    # Player-like sources (Name-Server) exist.
    player_like = [
        s
        for s in df["source"].unique()
        if len(str(s).split("-")) == 3 and all(str(s).split("-"))
    ]
    assert len(player_like) >= 2


@pytest.mark.skipif(not real_logs, reason="no raw combat log in wow-data/")
def test_real_log_field_layouts_unchanged():
    """Every damage/heal/swing line must have the exact field count the
    parser's index math relies on."""
    expected = {
        "SPELL_DAMAGE": 41,
        "SPELL_PERIODIC_DAMAGE": 41,
        "SPELL_HEAL": 35,
        "SPELL_PERIODIC_HEAL": 35,
        "SWING_DAMAGE": 37,
        "SWING_DAMAGE_LANDED": 37,
    }
    counts: Counter = Counter()
    for path in real_logs:
        with open(path, encoding="utf-8") as f:
            for line in f:
                for ev in expected:
                    if f"  {ev}," in line:
                        n = len(list(csv.reader([line.split(",", 1)[1]]))[0])
                        counts[(ev, n)] += 1
                        break
    assert counts, "no combat lines found in wow-data logs"
    for (ev, n), c in counts.items():
        assert n == expected[ev], (
            f"log format change? {ev} lines have {n} rest-fields, "
            f"parser expects {expected[ev]} ({c} lines affected)"
        )


@pytest.mark.skipif(not real_logs, reason="no raw combat log in wow-data/")
def test_parser_runs_on_real_log():
    """End-to-end: detect_encounters + export_csv on the real raw log."""
    import importlib.util
    import tempfile
    import os

    spec = importlib.util.spec_from_file_location(
        "wow_parser_real_test", ROOT / "wow-parser.py"
    )
    wp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wp)

    import pandas as pd

    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "out.csv")
        original = wp._write_boss_kills
        wp._write_boss_kills = lambda *a, **k: None
        try:
            wp.export_csv(str(real_logs[-1]), csv_path=out)
        finally:
            wp._write_boss_kills = original
        df = pd.read_csv(out)
    assert len(df) > 100
    assert df.loc[df["combat_id"] > 0, "combat_id"].nunique() > 5
    assert (df.loc[df["spell_name"] == "Melee", "amount"] > 0).all()
