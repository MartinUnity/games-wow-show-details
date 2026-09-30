"""
Synthetic WoW combat log fixture generator (Phase 0.1).

This machine has no real combat logs / CSV, so Phase 0 of
``docs/MIGRATION_PLAN.md`` builds deterministic synthetic logs and runs the
*real* parser (``wow-parser.py``) over them to produce
``parsed_combat_data.csv`` plus the sidecar files.

The generated lines use the exact field layouts the parser expects (field map
in ``docs/IMPROVEMENT_PLAN.md`` P1; verbatim reference lines in
``tests/test_parser.py``)::

    SPELL_DAMAGE    41 rest-fields  damage at #31  (rest_parts[-11])
    SPELL_HEAL      35 rest-fields  amount at #31 (rest_parts[-5]),
                                      overheal at #33 (rest_parts[-3])
    SWING_DAMAGE    37 rest-fields  damage at #29  (rest_parts[-9])

Encounter-detection coverage exercised by the two small sessions:

    combat 1  closes via UNIT_DIED (all enemies dead)
    combat 2  boss fight, closes via UNIT_DIED + ENCOUNTER_START/END sidecar
              (kill_flag=1)
    combat 3  closes via 8 s hostile-timeout (no death event)
    combat 4  closes via UNIT_DIED (same mob GUID as combat 3 — proves
              dead_guids is per-death, not per-encounter)
    combat 5  healer character (second Name-Server player source), heals with
              partial and full overheal, closes via UNIT_DIED
    combat 6  still open at EOF → closed by the end-of-log branch

Usage:
    python tests/make_fixture_log.py [outdir]         # two small sessions
    python tests/make_fixture_log.py --stress outdir  # one ~60k-line log
"""

from __future__ import annotations

import argparse
import os
import random
from datetime import datetime, timedelta

# ── Units: (guid, name, flags_hex) ──────────────────────────────────────────
# Player flags 0x511 = MINE | FRIENDLY | TYPE_PLAYER | 0x100 (matches real logs).
# Pet 0x911 = MINE | FRIENDLY | TYPE_NPC | 0x100 → kept by parser (is_mine) and
# friendly for encounter detection.
# Enemy NPC flags: 0x10a28 (neutral) / 0x10840 (hostile) — both pass
# _is_enemy_npc (TYPE_NPC, not friendly, not mine/party/raid).
ZETHROK = ("Player-1596-074EDF15", "Zethrok-TheMaelstrom-EU", "0x511")
LUMIARA = ("Player-1596-0A11CE02", "Lumiara-TheMaelstrom-EU", "0x511")
CRABPET = ("Creature-0-4251-0-100288-250061-0000C7AB01", "Crabpet", "0x911")

CRAB = ("Creature-0-4251-0-70386-250061-00003DCB46", "Tideshell Crab", "0x10a28")
BOSS = ("Creature-0-4251-0-70386-250061-0000B05511", "Spiritflayer Jin'ma", "0x10840")
STINGER = ("Creature-0-4251-0-70386-250061-00005110A2", "Spiteful Stinger", "0x10a28")
HOUND = ("Creature-0-4251-0-70386-250061-00009E2207", "Deepwater Hound", "0x10a28")
FIN = ("Creature-0-4251-0-70386-250061-000077F0C3", "Murky Fin", "0x10a28")

ZONE_ID = 2962
ZONE_NAME = "The Maelstrom"

# (spell_id, spell_name, school) — ids/names are flavour only; the parser and
# downstream views only rely on spell_id being a stable integer.
FLAME_SHOCK = (188389, "Flame Shock", "0xc")
LIGHTNING_BOLT = (194, "Lightning Bolt", "0x8")
UNLEASH_LIFE = (73685, "Unleash Life", "0x8")
HEALING_WAVE = (2060, "Healing Wave", "0x2")
HOLY_LIGHT = (633, "Holy Light", "0x2")
SMITE = (212, "Smite", "0x2")
SNAPPING_SLICE = (388897, "Snapping Slice", "0x1")
SHADOW_FLAY = (51522, "Shadow Flay", "0x8")


def q(name: str) -> str:
    """CSV-quote a name field the way the real log does."""
    return f'"{name}"'


def _line(dt: datetime, event: str, fields: list[str]) -> str:
    return f"{dt.strftime('%m/%d/%Y %H:%M:%S.%f')}  {event}," + ",".join(fields)


def _std(src, dst) -> list[str]:
    """Standard 8-field src/dst block shared by all combat events."""
    return [src[0], q(src[1]), src[2], "0x80000000",
            dst[0], q(dst[1]), dst[2], "0x80000000"]


def spell_damage(dt, src, dst, spell, amount: int) -> str:
    """41 rest-fields; damage at #31 (rest_parts[-11]), spell id at [8]."""
    fields = _std(src, dst) + [
        str(spell[0]), q(spell[1]), spell[2],
        dst[0], "0000000000000000", "44368", "44971", "0", "0",
        "484", "0", "0", "0", "1", "0", "0", "0",
        "8756.04", "-3873.23", "2393", "5.1329", "83",
        str(amount), str(max(amount - 1, 0)), "-1", "12", "0", "0", "0",
        "nil", "nil", "nil", "ST",
    ]
    assert len(fields) == 41, len(fields)
    return _line(dt, "SPELL_DAMAGE", fields)


def spell_heal(dt, src, dst, spell, amount: int, overheal: int) -> str:
    """35 rest-fields; amount at #31 (rest[-5]), overheal at #33 (rest[-3])."""
    fields = _std(src, dst) + [
        str(spell[0]), q(spell[1]), spell[2],
        src[0], "0000000000000000", "88400", "88400", "232", "542", "491",
        "495", "0", "0", "0",
        "64585", "64603", "0", "8743.32", "-3896.62", "2393", "1.1884", "112",
        str(amount), str(amount), str(overheal), "0", "nil",
    ]
    assert len(fields) == 35, len(fields)
    return _line(dt, "SPELL_HEAL", fields)


def _swing(dt, event: str, src, dst, amount: int) -> str:
    """37 rest-fields; damage at #29 (rest_parts[-9])."""
    fields = _std(src, dst) + [
        src[0], "0000000000000000", "85247", "88400", "232", "548", "393",
        "495", "0", "0", "0",
        "64558", "64603", "0", "8751.49", "-3899.90", "2393", "1.9502", "112",
        "142",
        str(amount), "-1", "1", "0", "0", "0", "1", "nil", "nil",
    ]
    assert len(fields) == 37, len(fields)
    return _line(dt, event, fields)


def swing_damage(dt, src, dst, amount: int) -> str:
    return _swing(dt, "SWING_DAMAGE", src, dst, amount)


def swing_landed(dt, src, dst, amount: int) -> str:
    """Duplicate of the SWING_DAMAGE hit; parser must NOT extract this."""
    return _swing(dt, "SWING_DAMAGE_LANDED", src, dst, amount)


def unit_died(dt, killer, victim) -> str:
    """dst (victim) is the unit that died — closes the encounter."""
    return _line(dt, "UNIT_DIED", _std(killer, victim))


def zone_change(dt, zone_id: int, zone_name: str) -> str:
    return _line(dt, "ZONE_CHANGE", [str(zone_id), q(zone_name)])


def encounter_start(dt, instance_id: int, boss_name: str, zone_id: int) -> str:
    """parts: [instance_id, boss_name, 208, 1, zone_id]."""
    return _line(dt, "ENCOUNTER_START",
                 [str(instance_id), q(boss_name), "208", "1", str(zone_id)])


def encounter_end(dt, instance_id: int, boss_name: str, kill_flag: int) -> str:
    """parts: [instance_id, boss_name, 208, 1, kill_flag, duration_ms]."""
    return _line(dt, "ENCOUNTER_END",
                 [str(instance_id), q(boss_name), "208", "1",
                  str(kill_flag), "78661"])


# ── Sessions ─────────────────────────────────────────────────────────────────

def session_zethrok() -> list[str]:
    """Character 1 (DPS). Combats 1-4. 3/21/2026 18:00 session."""
    b = datetime(2026, 3, 21, 18, 0, 0)
    t = lambda s: b + timedelta(seconds=s)
    L: list[str] = []

    L.append(zone_change(t(0), ZONE_ID, ZONE_NAME))

    # Combat 1 — trash pull, closes via UNIT_DIED.
    L.append(spell_damage(t(10.000), ZETHROK, CRAB, FLAME_SHOCK, 602))
    L.append(swing_damage(t(11.000), ZETHROK, CRAB, 100))
    L.append(swing_landed(t(11.002), ZETHROK, CRAB, 100))  # must not double-count
    # Enemy action: no MINE bit → excluded from CSV, but keeps encounter alive.
    L.append(spell_damage(t(12.000), CRAB, ZETHROK, SNAPPING_SLICE, 3153))
    L.append(spell_damage(t(15.000), ZETHROK, CRAB, FLAME_SHOCK, 540))
    L.append(swing_damage(t(20.000), ZETHROK, CRAB, 120))
    L.append(unit_died(t(25.000), ZETHROK, CRAB))

    # Combat 2 — scripted boss, kill (sidecar record) + UNIT_DIED close.
    # NOTE: heals are NOT hostile events, so consecutive *hits* must stay < 8 s
    # apart or the detector force-closes the fight (true to real logs).
    L.append(encounter_start(t(60.000), 3433, BOSS[1], ZONE_ID))
    L.append(spell_damage(t(60.050), ZETHROK, BOSS, LIGHTNING_BOLT, 1500))
    L.append(swing_damage(t(65.000), CRABPET, BOSS, 250))  # pet source in CSV
    L.append(spell_damage(t(70.000), BOSS, ZETHROK, SHADOW_FLAY, 2100))
    L.append(spell_damage(t(75.000), ZETHROK, BOSS, FLAME_SHOCK, 650))
    L.append(spell_damage(t(80.000), ZETHROK, BOSS, LIGHTNING_BOLT, 2200))
    L.append(swing_damage(t(85.000), ZETHROK, BOSS, 130))
    L.append(spell_damage(t(90.000), ZETHROK, BOSS, FLAME_SHOCK, 700))
    L.append(spell_heal(t(92.000), ZETHROK, ZETHROK, UNLEASH_LIFE, 4000, 0))
    L.append(swing_damage(t(95.000), ZETHROK, BOSS, 95))
    # Full overheal → effective 0 (a genuine zero, not the old -1 sentinel).
    L.append(spell_heal(t(97.000), ZETHROK, ZETHROK, UNLEASH_LIFE, 5000, 5000))
    L.append(spell_damage(t(100.000), ZETHROK, BOSS, FLAME_SHOCK, 580))
    L.append(spell_damage(t(105.000), ZETHROK, BOSS, LIGHTNING_BOLT, 1800))
    L.append(swing_damage(t(110.000), ZETHROK, BOSS, 140))
    L.append(spell_damage(t(115.000), ZETHROK, BOSS, FLAME_SHOCK, 620))
    L.append(spell_damage(t(120.000), ZETHROK, BOSS, LIGHTNING_BOLT, 2000))
    L.append(swing_damage(t(125.000), ZETHROK, BOSS, 105))
    L.append(spell_damage(t(130.000), ZETHROK, BOSS, FLAME_SHOCK, 710))
    L.append(spell_damage(t(135.000), ZETHROK, BOSS, LIGHTNING_BOLT, 2300))
    L.append(swing_damage(t(140.000), ZETHROK, BOSS, 115))
    L.append(spell_damage(t(145.000), ZETHROK, BOSS, FLAME_SHOCK, 800))
    L.append(swing_damage(t(148.500), ZETHROK, BOSS, 160))
    L.append(encounter_end(t(150.000), 3433, BOSS[1], 1))
    L.append(unit_died(t(150.001), ZETHROK, BOSS))

    # Combat 3 — no death; force-closed by the 8 s timeout.
    L.append(spell_damage(t(180.000), ZETHROK, STINGER, LIGHTNING_BOLT, 300))
    L.append(swing_damage(t(185.000), ZETHROK, STINGER, 80))

    # Combat 4 — 20 s later the timeout closes combat 3 and this hit opens
    # combat 4 (same mob GUID, which is *not* dead yet).
    L.append(spell_damage(t(205.000), ZETHROK, STINGER, FLAME_SHOCK, 450))
    L.append(swing_damage(t(210.000), ZETHROK, STINGER, 100))
    L.append(spell_damage(t(216.000), ZETHROK, STINGER, FLAME_SHOCK, 380))
    L.append(unit_died(t(220.000), ZETHROK, STINGER))

    return L


def session_lumiara() -> list[str]:
    """Character 2 (healer). Combats 5-6. 3/21/2026 19:00 session."""
    b = datetime(2026, 3, 21, 19, 0, 0)
    t = lambda s: b + timedelta(seconds=s)
    L: list[str] = []

    L.append(zone_change(t(0), ZONE_ID, ZONE_NAME))

    # Combat 5 — healer: partial + full overheal, closes via UNIT_DIED.
    # Hits stay < 8 s apart (heals alone would trip the timeout).
    L.append(spell_damage(t(10.000), LUMIARA, HOUND, SMITE, 900))
    # 5752 - 5304 = 448 effective (mirrors the real partial-overheal line).
    L.append(spell_heal(t(12.000), LUMIARA, LUMIARA, HEALING_WAVE, 5752, 5304))
    L.append(spell_damage(t(16.000), LUMIARA, HOUND, SMITE, 850))
    L.append(spell_heal(t(20.000), LUMIARA, LUMIARA, HOLY_LIGHT, 4586, 4586))
    L.append(spell_damage(t(24.000), LUMIARA, HOUND, SMITE, 910))
    L.append(swing_damage(t(30.000), LUMIARA, HOUND, 95))
    L.append(spell_damage(t(36.000), LUMIARA, HOUND, SMITE, 870))
    L.append(swing_damage(t(42.000), LUMIARA, HOUND, 88))
    L.append(spell_damage(t(48.000), LUMIARA, HOUND, SMITE, 930))
    L.append(unit_died(t(50.000), LUMIARA, HOUND))

    # Combat 6 — still open at EOF → closed by the end-of-log branch.
    L.append(spell_damage(t(80.000), LUMIARA, FIN, SMITE, 700))
    L.append(swing_damage(t(85.000), LUMIARA, FIN, 60))

    return L


def stress_session(n_encounters: int = 40,
                   events_per_encounter: int = 1500,
                   seed: int = 42) -> list[str]:
    """One large deterministic log (~60k lines) for performance sanity."""
    rng = random.Random(seed)
    b = datetime(2026, 3, 22, 10, 0, 0)
    L: list[str] = [zone_change(b, ZONE_ID, ZONE_NAME)]
    player_spells = [FLAME_SHOCK, LIGHTNING_BOLT, SMITE]
    t = 0.0
    for i in range(n_encounters):
        # Gap between encounters is always > 8 s (timeout-close safe).
        t += rng.uniform(15.0, 40.0)
        start_dt = b + timedelta(seconds=t)
        mob = (f"Creature-0-4251-0-70386-250061-{i:010X}",
               f"Trash Mob {i}", "0x10a28")
        is_boss = (i % 10 == 5)
        if is_boss:
            L.append(encounter_start(start_dt, 3433 + i, mob[1], ZONE_ID))
        for _ in range(events_per_encounter):
            t += rng.uniform(0.05, 0.30)
            dt = b + timedelta(seconds=t)
            r = rng.random()
            if r < 0.45:
                L.append(spell_damage(dt, ZETHROK, mob,
                                      rng.choice(player_spells),
                                      rng.randint(50, 5000)))
            elif r < 0.80:
                L.append(swing_damage(dt, ZETHROK, mob, rng.randint(20, 300)))
            elif r < 0.90:
                # Enemy action — kept alive by detection, excluded from CSV.
                L.append(spell_damage(dt, mob, ZETHROK, SHADOW_FLAY,
                                      rng.randint(100, 3000)))
            else:
                L.append(spell_heal(dt, ZETHROK, ZETHROK, UNLEASH_LIFE,
                                    rng.randint(500, 6000),
                                    rng.randint(0, 3000)))
        t += 0.001
        L.append(unit_died(b + timedelta(seconds=t), ZETHROK, mob))
        if is_boss:
            L.append(encounter_end(b + timedelta(seconds=t), 3433 + i, mob[1], 1))
    return L


# ── Entry point ──────────────────────────────────────────────────────────────

def _write(outdir: str, name: str, lines: list[str]) -> None:
    path = os.path.join(outdir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {path} ({len(lines)} lines)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate synthetic combat logs.")
    ap.add_argument("outdir", nargs="?", default="tests/testdata",
                    help="directory to write log files into")
    ap.add_argument("--stress", action="store_true",
                    help="write one large (~60k line) log instead of the "
                         "two small sessions")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    if args.stress:
        _write(args.outdir, "WoWCombatLog-032226_100000.txt", stress_session())
    else:
        _write(args.outdir, "WoWCombatLog-032126_180000.txt", session_zethrok())
        _write(args.outdir, "WoWCombatLog-032126_190000.txt", session_lumiara())


if __name__ == "__main__":
    main()
