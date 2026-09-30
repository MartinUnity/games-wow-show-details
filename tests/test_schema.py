"""
CSV storage contract tests (Phase 0.3).

Runs the *real* parser (``export_csv``) over the synthetic fixture logs and
asserts the exact contract the Phase 1 SQLite layer will mirror:

- header is the 12-column CSV schema
- melee (SWING_DAMAGE) amounts are > 0 and not double-counted vs _LANDED
- heal effective_amount = amount - overheal (partial and full-overheal)
- encounter detection yields combats 1..6 with the expected close reasons
- two player-like (Name-Server) sources + pet; no enemy sources
- zone id/name stamped on every in-encounter row
- boss-kill sidecar record emitted with kill_flag=1

Everything runs in ``tmp_path``; the repo's live CSV and sidecars are never
touched. Regenerate the dev dataset with ``make fixture``.
"""

import importlib.util
import pathlib
import sys

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import make_fixture_log as fx  # noqa: E402

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


@pytest.fixture(scope="module")
def parser():
    spec = importlib.util.spec_from_file_location(
        "wow_parser_schema_test", ROOT / "wow-parser.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def exported(tmp_path_factory, parser):
    """Parse the two synthetic sessions; return (csv_df, boss_kill_records)."""
    tmp = tmp_path_factory.mktemp("schema")
    lines = fx.session_zethrok() + fx.session_lumiara()
    log = tmp / "WoWCombatLog-032126_180000.txt"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    csv_path = tmp / "out.csv"
    captured: dict = {}
    original = parser._write_boss_kills
    parser._write_boss_kills = lambda records, **kw: captured.update(records=records)
    try:
        parser.export_csv(str(log), csv_path=str(csv_path))
    finally:
        parser._write_boss_kills = original

    df = pd.read_csv(csv_path)
    return df, captured["records"]


def test_csv_header_contract(exported):
    df, _ = exported
    assert list(df.columns) == EXPECTED_COLUMNS


def test_melee_damage_positive_and_not_double_counted(exported):
    df, _ = exported
    melee = df[df["spell_name"] == "Melee"]
    assert len(melee) > 0
    assert (melee["amount"] > 0).all()
    assert (melee["amount"] != -1).all()
    # 14 SWING_DAMAGE lines generated (incl. 1 pet swing); the 1
    # SWING_DAMAGE_LANDED line must not add a 15th row.
    assert len(melee) == 14


def test_spell_damage_and_spell_id(exported):
    df, _ = exported
    flame = df[df["spell_name"] == "Flame Shock"]
    assert len(flame) > 0
    assert (flame["spell_id"] == 188389).all()
    assert (flame["type"] == "damage").all()
    assert (flame["effective_amount"] == flame["amount"]).all()


def test_heal_effective_amount_subtracts_overheal(exported):
    df, _ = exported
    heals = df[df["type"] == "heal"]
    # Partial overheal: 5752 - 5304 = 448.
    partial = heals[heals["amount"] == 5752]
    assert len(partial) == 1
    assert partial.iloc[0]["effective_amount"] == 448
    # Full overheal: 4586 - 4586 = 0 (genuine zero).
    full = heals[heals["amount"] == 4586]
    assert len(full) == 1
    assert full.iloc[0]["effective_amount"] == 0
    # Full overheal (Zethrok's 5000) also present.
    assert (heals[heals["amount"] == 5000]["effective_amount"] == 0).all()


def test_encounter_detection_yields_six_combats(exported):
    df, _ = exported
    cids = sorted(df.loc[df["combat_id"] > 0, "combat_id"].unique())
    assert cids == [1, 2, 3, 4, 5, 6]


def test_two_player_like_sources_and_pet(exported):
    df, _ = exported
    sources = set(df["source"].unique())
    player_like = {s for s in sources if len(s.split("-")) == 3 and all(s.split("-"))}
    assert player_like == {"Zethrok-TheMaelstrom-EU", "Lumiara-TheMaelstrom-EU"}
    assert "Crabpet" in sources
    # Enemy names must never appear as a source (player-only filter).
    for enemy in ("Tideshell Crab", "Spiritflayer Jin'ma", "Spiteful Stinger",
                  "Deepwater Hound", "Murky Fin"):
        assert enemy not in sources


def test_zone_stamped_on_encounter_rows(exported):
    df, _ = exported
    enc = df[df["combat_id"] > 0]
    assert len(enc) == len(df)  # every parsed event falls inside an encounter
    assert (enc["zone_id"] == 2962).all()
    assert (enc["zone_name"] == "The Maelstrom").all()


def test_boss_kill_sidecar_record(exported):
    _, records = exported
    assert records == [
        {
            "boss_name": "Spiritflayer Jin'ma",
            "start_ts": "03/21/2026 18:01:00.000000",
            "end_ts": "03/21/2026 18:02:30.000000",
            "kill_flag": 1,
            "zone_id": 2962,
        }
    ]
