"""
Regression tests for the combat-log line parser (wow-parser.py).

These run on ``parse_combat_line`` with real, inlined raw log lines so they are
deterministic and need no file I/O. They guard the field-index extraction for
melee, spell damage, and healing (including overheal), which is the part that
feeds every downstream metric in the app.

Raw lines were captured verbatim from a real combat log and are stable fixtures.
"""

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def parser():
    """Load wow-parser.py (hyphenated name) as a module, cached per test run."""
    spec = importlib.util.spec_from_file_location("wow_parser_under_test", ROOT / "wow-parser.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── Real raw log lines (verbatim) ─────────────────────────────────────────────

# SWING_DAMAGE: Zethrok -> Tideshell Crab. True melee damage is field index 28
# (rest_parts[-9]) = 100. The adjacent field [-8] is a -1 sentinel (the old bug).
MELEE_LINE = (
    '3/21/2026 18:39:17.1781  SWING_DAMAGE,Player-1596-074EDF15,'
    '"Zethrok-TheMaelstrom-EU",0x511,0x80000000,'
    'Creature-0-4251-0-70386-250061-00003DCB46,"Tideshell Crab",0x10a28,0x80000000,'
    'Player-1596-074EDF15,0000000000000000,85247,88400,232,548,393,495,0,0,0,'
    '64558,64603,0,8751.49,-3899.90,2393,1.9502,112,142,100,-1,1,0,0,0,1,nil,nil'
)

# SPELL_DAMAGE: Zethrok -> Tideshell Crab, "Flame Shock". Damage is field index 30
# (rest_parts[-11]) = 602; spell id at index 8 = 188389.
SPELL_DAMAGE_LINE = (
    '3/21/2026 18:39:12.2921  SPELL_DAMAGE,Player-1596-074EDF15,'
    '"Zethrok-TheMaelstrom-EU",0x511,0x80000000,'
    'Creature-0-4251-0-70386-250061-00003DCB46,"Tideshell Crab",0x10a28,0x80000000,'
    '188389,"Flame Shock",0xc,'
    'Creature-0-4251-0-70386-250061-00003DCB46,0000000000000000,44368,44971,0,0,'
    '484,0,0,0,1,0,0,0,8756.04,-3873.23,2393,5.1329,83,602,601,-1,12,0,0,0,'
    'nil,nil,nil,ST'
)

# SPELL_HEAL (full overheal): Unleash Life, amount=4586, overheal=4586 -> effective 0.
HEAL_FULL_OVERHEAL_LINE = (
    '3/21/2026 18:39:15.6591  SPELL_HEAL,Player-1596-074EDF15,'
    '"Zethrok-TheMaelstrom-EU",0x511,0x80000000,Player-1596-074EDF15,'
    '"Zethrok-TheMaelstrom-EU",0x511,0x80000000,73685,"Unleash Life",0x8,'
    'Player-1596-074EDF15,0000000000000000,88400,88400,232,542,491,495,0,0,0,'
    '64585,64603,0,8743.32,-3896.62,2393,1.1884,112,4586,4586,4586,0,nil'
)

# SPELL_HEAL (partial overheal): Unleash Life, amount=5752, overheal=5304
# -> effective 448. Source is the "mine" player (flag 0x511) so it is kept.
# This proves effective_amount = amount - overheal is not hard-coded to 0.
HEAL_PARTIAL_OVERHEAL_LINE = (
    '3/21/2026 18:43:05.7341  SPELL_HEAL,Player-1596-074EDF15,'
    '"Zethrok-TheMaelstrom-EU",0x511,0x80000000,Player-1596-074EDF15,'
    '"Zethrok-TheMaelstrom-EU",0x511,0x80000000,73685,"Unleash Life",0x8,'
    'Player-1596-074EDF15,0000000000000000,99660,99660,232,571,529,363,0,0,0,'
    '64603,64603,0,5602.01,-4453.52,2395,5.8684,119,5752,5752,5304,0,nil'
)

# SPELL_DAMAGE from an enemy (Tideshell Crab -> Zethrok). Source flag 0x10a28 has
# no "mine" bit, so the parser must return None (player-only actions are kept).
CREATURE_ATTACK_LINE = (
    '3/21/2026 18:39:16.7101  SPELL_DAMAGE,Creature-0-4251-0-70386-250061-00003DCB46,'
    '"Tideshell Crab",0x10a28,0x80000000,Player-1596-074EDF15,'
    '"Zethrok-TheMaelstrom-EU",0x511,0x80000000,388897,"Snapping Slice",0x1,'
    'Player-1596-074EDF15,0000000000000000,85247,88400,232,548,491,495,0,0,0,'
    '64561,64603,0,8748.25,-3898.61,2393,1.9581,112,3153,4752,-1,1,0,0,0,'
    'nil,nil,nil,ST'
)


def test_melee_damage_is_positive(parser):
    """Regression guard for the SWING_DAMAGE field bug: melee must be > 0, not -1."""
    data, _ = parser.parse_combat_line(MELEE_LINE, "Zethrok-TheMaelstrom-EU")
    assert data is not None
    assert data["event"] == "SWING_DAMAGE"
    assert data["type"] == "damage"
    assert data["spell_name"] == "Melee"
    assert data["target"] == "Tideshell Crab"
    assert data["amount"] > 0
    assert data["amount"] != -1
    assert data["amount"] == 100
    assert data["effective_amount"] == 100
    assert data["spell_id"] == 0


def test_spell_damage_reads_field_30(parser):
    data, _ = parser.parse_combat_line(SPELL_DAMAGE_LINE, "Zethrok-TheMaelstrom-EU")
    assert data is not None
    assert data["event"] == "SPELL_DAMAGE"
    assert data["type"] == "damage"
    assert data["spell_name"] == "Flame Shock"
    assert data["target"] == "Tideshell Crab"
    assert data["amount"] == 602
    assert data["effective_amount"] == 602
    assert data["spell_id"] == 188389


def test_heal_full_overheal_has_zero_effective(parser):
    data, _ = parser.parse_combat_line(HEAL_FULL_OVERHEAL_LINE, "Zethrok-TheMaelstrom-EU")
    assert data is not None
    assert data["type"] == "heal"
    assert data["spell_name"] == "Unleash Life"
    assert data["amount"] == 4586
    assert data["effective_amount"] == 0  # fully overhealed -> genuine 0, not a bug
    assert data["spell_id"] == 73685


def test_heal_partial_overheal_subtracts_overheal(parser):
    data, _ = parser.parse_combat_line(HEAL_PARTIAL_OVERHEAL_LINE, "Zethrok-TheMaelstrom-EU")
    assert data is not None
    assert data["type"] == "heal"
    assert data["amount"] == 5752
    assert data["effective_amount"] == 448  # 5752 - 5304
    assert data["spell_id"] == 73685


def test_non_player_action_is_filtered(parser):
    """Enemy actions (no 'mine' bit on the source) are dropped, not parsed."""
    data, _ = parser.parse_combat_line(CREATURE_ATTACK_LINE, "Zethrok-TheMaelstrom-EU")
    assert data is None


def test_melee_not_double_counted_with_landed(parser):
    """
    SWING_DAMAGE and SWING_DAMAGE_LANDED describe the same hit. Only the
    SWING_DAMAGE branch extracts damage, so a LANDED line must not produce a
    duplicate damage record (it should be ignored by the damage branches).
    """
    landed = MELEE_LINE.replace("SWING_DAMAGE,", "SWING_DAMAGE_LANDED,")
    data, _ = parser.parse_combat_line(landed, "Zethrok-TheMaelstrom-EU")
    # LANDED must not be classified as a damage event (would double-count).
    assert data is None or data["type"] != "damage" or data["event"] != "SWING_DAMAGE"


# ── Boss-kill sidecar (ENCOUNTER_START/END) ─────────────────────────────────────
# Verbatim scripted-boss-encounter events from a real combat log. These are
# independent of detect_encounters (which keys off combat activity) and feed the
# boss_kills.jsonl sidecar. BossKillTracker collects them incrementally during the
# export pass, replacing the old separate extract_boss_kills re-read (P4.3).

ENCOUNTER_START_LINE = (
    '3/5/2026 22:17:05.9371  ENCOUNTER_START,3433,"Spiritflayer Jin\'ma",208,1,2962'
)
ENCOUNTER_END_LINE = (
    '3/5/2026 22:18:24.6131  ENCOUNTER_END,3433,"Spiritflayer Jin\'ma",208,1,1,78661'
)

EXPECTED_BOSS_KILL = {
    "boss_name": "Spiritflayer Jin'ma",
    "start_ts": "03/05/2026 22:17:05.937100",
    "end_ts": "03/05/2026 22:18:24.613100",
    "kill_flag": 1,
    "zone_id": 2962,
}


def test_boss_kill_tracker_collects_encounter_pair(parser):
    """A START→END pair must fold into exactly one sidecar record."""
    tracker = parser.BossKillTracker()
    for line in (ENCOUNTER_START_LINE, ENCOUNTER_END_LINE):
        tracker.feed(line)
    assert tracker.records == [EXPECTED_BOSS_KILL]


def test_boss_kill_tracker_ignores_orphan_end(parser):
    """An END with no preceding START must not produce a record."""
    tracker = parser.BossKillTracker()
    tracker.feed(ENCOUNTER_END_LINE)
    assert tracker.records == []


def test_export_csv_writes_boss_kill_sidecar(parser, tmp_path, monkeypatch):
    """export_csv must emit the boss-kill sidecar from the same streaming pass."""
    captured = {}
    monkeypatch.setattr(
        parser,
        "_write_boss_kills",
        lambda records, **kw: captured.__setitem__("records", records),
    )
    log = tmp_path / "log.txt"
    log.write_text(
        "\n".join([ENCOUNTER_START_LINE, ENCOUNTER_END_LINE]) + "\n",
        encoding="utf-8",
    )
    csv_path = tmp_path / "out.csv"
    parser.export_csv(str(log), csv_path=str(csv_path))
    assert captured["records"] == [EXPECTED_BOSS_KILL]
    assert csv_path.exists()
    assert csv_path.read_text(encoding="utf-8").splitlines()[0].startswith("combat_id")
