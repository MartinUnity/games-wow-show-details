import argparse
import bisect
import csv
import glob
import gzip
import json
import os
import shutil
import time
from datetime import datetime

from config import (
    BOSS_KILLS_PATH,
    CSV_BACKUP_DIR,
    CSV_PATH,
    DATA_DIR,
    LOG_DIR,
    MAX_CSV_BACKUPS,
)
# Note: CSV stores only `spell_id`; `source_spec`/`source_role` are not persisted


# Alias kept so existing references inside this file don't need touching.
OUTPUT_CSV = CSV_PATH


def get_latest_log_file(directory):
    """Finds the most recently modified WoWCombatLog file."""
    search_pattern = os.path.join(directory, "WoWCombatLog-*.txt")
    log_files = glob.glob(search_pattern)
    return max(log_files, key=os.path.getmtime) if log_files else None


def parse_combat_line(line, current_char_name):
    """Parses a raw CSV line into a structured dictionary."""
    if not line:
        return None, current_char_name

    # The combat log places the timestamp and event type before the first comma,
    # separated by whitespace. Split those off first, then parse the remaining CSV
    # fields which start at the source GUID.
    try:
        header, rest = line.split(",", 1)
    except ValueError:
        return None, current_char_name

    header = header.strip()
    try:
        timestamp_str, event_type = header.rsplit(None, 1)
    except ValueError:
        return None, current_char_name

    try:
        rest_parts = list(csv.reader([rest]))[0]
    except Exception:
        return None, current_char_name

    if len(rest_parts) < 3:
        return None, current_char_name

    source_name = rest_parts[1].replace('"', "")
    source_flags_hex = rest_parts[2]

    try:
        source_flags = int(source_flags_hex, 16)
    except Exception:
        return None, current_char_name

    # Bits: 0x001 = Mine, 0x400 = TYPE_PLAYER. If both are present it's the
    # active player character. Also accept any "mine" actions (pets/totems).
    is_mine = bool(source_flags & 0x1)
    is_player = bool(source_flags & 0x400)
    is_active_player = (source_flags & 0x401) == 0x401

    if is_active_player and current_char_name is None:
        current_char_name = source_name

    # ── SPELL_ABSORBED special case ──────────────────────────────────────────
    # src/dst fields describe attacker→defender (enemy→player), so is_mine on
    # src will always be False.  The shield *caster* sits deeper in the row.
    #
    # Two variants exist (distinguished by whether field[8] is a GUID or a
    # numeric spell ID):
    #
    #   Melee/auto variant (18 fields):
    #     [0..7] = std src/dst block
    #     [8]  casterGUID  [9] casterName  [10] casterFlags  [11] casterFlags2
    #     [12] shieldSpellID  [13] "shieldName"  [14] school
    #     [15] amount  [16] remaining  [17] nil
    #
    #   Spell-attack variant (21 fields):
    #     [0..7] = std src/dst block
    #     [8]  attackSpellID  [9] "attackSpellName"  [10] school
    #     [11] casterGUID  [12] casterName  [13] casterFlags  [14] casterFlags2
    #     [15] shieldSpellID  [16] "shieldName"  [17] school
    #     [18] amount  [19] remaining  [20] nil
    if event_type == "SPELL_ABSORBED" and len(rest_parts) >= 18:

        def _safe_int(v):
            try:
                return int(v)
            except Exception:
                return 0

        if rest_parts[8][0].isdigit():
            # Spell-attack variant
            if len(rest_parts) < 21:
                return None, current_char_name
            caster_name = rest_parts[12].replace('"', "")
            caster_flags = _parse_flags(rest_parts[13])
            shield_name = rest_parts[16].replace('"', "")
            amount = _safe_int(rest_parts[18])
        else:
            # Melee/auto variant
            caster_name = rest_parts[9].replace('"', "")
            caster_flags = _parse_flags(rest_parts[10])
            shield_name = rest_parts[13].replace('"', "")
            amount = _safe_int(rest_parts[15])

        if not bool(caster_flags & 0x1):
            return None, current_char_name

        if (caster_flags & 0x401) == 0x401 and current_char_name is None:
            current_char_name = caster_name

        target_name = rest_parts[5].replace('"', "") if len(rest_parts) > 5 else ""
        return {
            "timestamp": timestamp_str,
            "event": event_type,
            "source": caster_name,
            "target": target_name,
            "spell_name": shield_name,
            "amount": amount,
            "effective_amount": amount,
            "type": "absorb",
        }, current_char_name

    # Only interested in actions performed by the player (or their pet/totem).
    if not is_mine:
        return None, current_char_name

    data = {
        "timestamp": timestamp_str,
        "event": event_type,
        "source": source_name,
        "target": "",
        "spell_name": "Unknown",
        "amount": 0,
        "effective_amount": 0,
        "type": "other",
        "spell_id": 0,
    }

    # Helper to parse integers and handle 'nil'
    def to_int(val):
        try:
            return int(val)
        except Exception:
            return 0

    # Parse Healing
    if "HEAL" in event_type and len(rest_parts) >= 10:
        data["type"] = "heal"
        # Spell name is at index 9 in the rest_parts (spell id at 8)
        data["spell_name"] = (
            rest_parts[9].replace('"', "") if len(rest_parts) > 9 else "Unknown"
        )
        # Robust spell_id extraction: try several candidate positions and
        # also attempt to locate the spell_name in the parts and take the
        # preceding numeric token as the id when possible.
        sid = 0
        try:
            # candidate indices commonly used for spell id
            candidates = [8, 11, 15]
            for c in candidates:
                if len(rest_parts) > c and str(rest_parts[c]).isdigit():
                    sid = int(rest_parts[c])
                    break
            # fallback: find the index of the spell name and use the token before it
            if sid == 0:
                sname = data["spell_name"]
                for idx, val in enumerate(rest_parts):
                    if isinstance(val, str) and val.replace('"', "") == sname and idx > 0:
                        prev = rest_parts[idx - 1]
                        if str(prev).isdigit():
                            sid = int(prev)
                            break
        except Exception:
            sid = 0
        data["spell_id"] = sid
        # spell id (best-effort)
        try:
            sid = int(rest_parts[8]) if len(rest_parts) > 8 and str(rest_parts[8]).isdigit() else 0
        except Exception:
            sid = 0
        data["spell_id"] = sid
        # Destination/target name is typically at index 5
        if len(rest_parts) > 5:
            data["target"] = rest_parts[5].replace('"', "")
        amount = to_int(rest_parts[-5])
        overheal = to_int(rest_parts[-3])
        data["amount"] = amount
        data["effective_amount"] = max(0, amount - overheal)

    # Parse Damage
    elif "DAMAGE" in event_type and "SPELL" in event_type and len(rest_parts) >= 10:
        data["type"] = "damage"
        data["spell_name"] = (
            rest_parts[9].replace('"', "") if len(rest_parts) > 9 else "Unknown"
        )
        # Robust extraction for damage spell id (same approach as heals)
        sid = 0
        try:
            candidates = [8, 11, 15]
            for c in candidates:
                if len(rest_parts) > c and str(rest_parts[c]).isdigit():
                    sid = int(rest_parts[c])
                    break
            if sid == 0:
                sname = data["spell_name"]
                for idx, val in enumerate(rest_parts):
                    if isinstance(val, str) and val.replace('"', "") == sname and idx > 0:
                        prev = rest_parts[idx - 1]
                        if str(prev).isdigit():
                            sid = int(prev)
                            break
        except Exception:
            sid = 0
        data["spell_id"] = sid
        try:
            sid = int(rest_parts[8]) if len(rest_parts) > 8 and str(rest_parts[8]).isdigit() else 0
        except Exception:
            sid = 0
        data["spell_id"] = sid
        # Destination/target name is typically at index 5
        if len(rest_parts) > 5:
            data["target"] = rest_parts[5].replace('"', "")
        amount = (
            to_int(rest_parts[-11]) if len(rest_parts) >= 11 else to_int(rest_parts[-1])
        )
        data["amount"] = amount
        data["effective_amount"] = amount

    # Parse Melee Damage (SWING_DAMAGE has no spell prefix block)
    elif event_type == "SWING_DAMAGE" and len(rest_parts) >= 1:
        data["type"] = "damage"
        data["spell_name"] = "Melee"
        data["spell_id"] = 0
        if len(rest_parts) > 5:
            data["target"] = rest_parts[5].replace('"', "")
        amt = to_int(rest_parts[-8]) if len(rest_parts) >= 8 else to_int(rest_parts[-1])
        data["amount"] = amt
        data["effective_amount"] = amt

    else:
        return None, current_char_name

    return data, current_char_name


def run_test_mode(filepath, debug=False):
    """Reads the entire file, prints debug info or a final summary table."""
    print(f"Testing parser against: {filepath}\n")

    current_char_name = None
    total_damage = 0
    total_healing = 0
    first_event_time = None
    last_event_time = None

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            parsed_data, current_char_name = parse_combat_line(
                line.strip(), current_char_name
            )

            if not parsed_data:
                continue

            if debug:
                print(parsed_data)
                continue

            # Time tracking for duration
            try:
                # Format: 3/4/2026 16:48:20.3061
                dt = datetime.strptime(parsed_data["timestamp"], "%m/%d/%Y %H:%M:%S.%f")
                if not first_event_time:
                    first_event_time = dt
                last_event_time = dt
            except ValueError:
                pass

            # Aggregation
            if parsed_data["type"] == "damage":
                total_damage += parsed_data["effective_amount"]
            elif parsed_data["type"] in ("heal", "absorb"):
                total_healing += parsed_data["effective_amount"]

    if debug:
        return  # Skip the table if we are just debugging lines

    # Calculate Summary
    if (
        not first_event_time
        or not last_event_time
        or first_event_time == last_event_time
    ):
        duration_seconds = 1  # Avoid division by zero
    else:
        duration_seconds = (last_event_time - first_event_time).total_seconds()

    avg_dps = total_damage / duration_seconds
    avg_hps = total_healing / duration_seconds

    # Print Table
    print(
        f"| {'Player':<25} | {'Avg DPS':<10} | {'Avg HPS':<10} | {'Duration (s)':<12} |"
    )
    print("-" * 66)
    name_display = current_char_name if current_char_name else "No Player Found"
    print(
        f"| {name_display:<25} | {avg_dps:<10.1f} | {avg_hps:<10.1f} | {duration_seconds:<12.1f} |"
    )


# ---------------------------------------------------------------------------
# Encounter / combat detection (GUID-tracking state machine)
# ---------------------------------------------------------------------------

# Seconds of hostile inactivity before force-closing an open encounter
# (handles flee / evade / disconnect without a UNIT_DIED event).
ENCOUNTER_TIMEOUT_SECS = 8.0

# Events that constitute active melee/ranged/spell combat between two units.
_COMBAT_EVENTS = {
    "SWING_DAMAGE",
    "SWING_DAMAGE_LANDED",
    "SWING_MISSED",
    "RANGE_DAMAGE",
    "RANGE_MISSED",
    "SPELL_DAMAGE",
    "SPELL_PERIODIC_DAMAGE",
    "SPELL_MISSED",
    "SPELL_ABSORBED",
}

# WoW combat log unit-flag bit masks
_FLAG_MINE = 0x0001  # AFFILIATION_MINE
_FLAG_PARTY = 0x0002  # AFFILIATION_PARTY
_FLAG_RAID = 0x0004  # AFFILIATION_RAID
_FLAG_FRIENDLY = 0x0010  # REACTION_FRIENDLY
_FLAG_NEUTRAL = 0x0020  # REACTION_NEUTRAL
_FLAG_HOSTILE = 0x0040  # REACTION_HOSTILE
_FLAG_TYPE_PLAYER = 0x0400  # TYPE_PLAYER
_FLAG_TYPE_NPC = 0x0800  # TYPE_NPC


def _parse_flags(hex_str):
    try:
        return int(hex_str, 16)
    except Exception:
        return 0


def _is_enemy_npc(flags):
    """True for any NPC with a non-friendly reaction that is not under the
    player's own control.  Catches both strictly-hostile (0x040) and
    neutral-but-attacking (0x020) mobs."""
    return (
        bool(flags & _FLAG_TYPE_NPC)
        and not bool(flags & _FLAG_FRIENDLY)
        and not bool(flags & (_FLAG_MINE | _FLAG_PARTY | _FLAG_RAID))
    )


def _is_friendly_unit(flags):
    """True for the player's own character, pets, totems, or party members."""
    return bool(flags & (_FLAG_MINE | _FLAG_PARTY))


def _parse_raw_event(line):
    """Parse a raw combat log line into its header fields without any filtering.
    Returns a dict or None on error."""
    if not line:
        return None
    try:
        header, rest = line.split(",", 1)
    except ValueError:
        return None
    header = header.strip()
    try:
        timestamp_str, event_type = header.rsplit(None, 1)
    except ValueError:
        return None
    try:
        dt = datetime.strptime(timestamp_str, "%m/%d/%Y %H:%M:%S.%f")
    except ValueError:
        return None
    try:
        parts = list(csv.reader([rest]))[0]
    except Exception:
        return None
    # All combat events carry at least: src_guid, src_name, src_flags, src_flags2,
    # dst_guid, dst_name, dst_flags, dst_flags2  (indices 0-7).
    if len(parts) < 8:
        return None
    return {
        "dt": dt,
        "event": event_type,
        "src_guid": parts[0],
        "src_flags": _parse_flags(parts[2]),
        "dst_guid": parts[4],
        "dst_name": parts[5].strip('"'),
        "dst_flags": _parse_flags(parts[6]),
    }


def _parse_zone_change_line(line):
    """Parse a ZONE_CHANGE log line.  Returns (dt, zone_id, zone_name) or None."""
    if "ZONE_CHANGE" not in line:
        return None
    try:
        header, rest = line.split(",", 1)
    except ValueError:
        return None
    header = header.strip()
    try:
        timestamp_str, event_type = header.rsplit(None, 1)
    except ValueError:
        return None
    if event_type != "ZONE_CHANGE":
        return None
    try:
        dt = datetime.strptime(timestamp_str, "%m/%d/%Y %H:%M:%S.%f")
    except ValueError:
        return None
    try:
        parts = list(csv.reader([rest]))[0]
    except Exception:
        return None
    if len(parts) < 2:
        return None
    try:
        zone_id = int(parts[0])
    except (ValueError, TypeError):
        zone_id = 0
    zone_name = parts[1].strip('"') if len(parts) > 1 else ""
    return dt, zone_id, zone_name


def _parse_encounter_event(line):
    """Parse an ENCOUNTER_START or ENCOUNTER_END log line.

    ENCOUNTER_START → {event='START', dt, boss_name, zone_id}
    ENCOUNTER_END   → {event='END',   dt, boss_name, kill_flag}
    Returns None if the line is neither.
    """
    if "ENCOUNTER_START" not in line and "ENCOUNTER_END" not in line:
        return None
    try:
        header, rest = line.split(",", 1)
    except ValueError:
        return None
    header = header.strip()
    try:
        timestamp_str, event_type = header.rsplit(None, 1)
    except ValueError:
        return None
    if event_type not in ("ENCOUNTER_START", "ENCOUNTER_END"):
        return None
    try:
        dt = datetime.strptime(timestamp_str, "%m/%d/%Y %H:%M:%S.%f")
    except ValueError:
        return None
    try:
        parts = list(csv.reader([rest]))[0]
    except Exception:
        return None
    boss_name = parts[1].strip('"') if len(parts) > 1 else "Unknown"
    if event_type == "ENCOUNTER_START":
        try:
            zone_id = int(parts[4]) if len(parts) > 4 else 0
        except (ValueError, TypeError):
            zone_id = 0
        return {"event": "START", "dt": dt, "boss_name": boss_name, "zone_id": zone_id}
    else:  # ENCOUNTER_END
        try:
            kill_flag = int(parts[4]) if len(parts) > 4 else 0
        except (ValueError, TypeError):
            kill_flag = 0
        return {
            "event": "END",
            "dt": dt,
            "boss_name": boss_name,
            "kill_flag": kill_flag,
        }


def extract_boss_kills(lines):
    """Scan log lines for ENCOUNTER_START/END pairs.

    Returns a list of dicts::

        {boss_name, start_ts, end_ts, kill_flag, zone_id}

    kill_flag=1 → boss killed; 0 → wipe/reset.
    Timestamps are strings in the same format as the CSV (``%m/%d/%Y %H:%M:%S.%f``).
    """
    boss_kills = []
    open_boss = None  # {boss_name, start_dt, zone_id}
    for line in lines:
        ev = _parse_encounter_event(line)
        if ev is None:
            continue
        if ev["event"] == "START":
            open_boss = {
                "boss_name": ev["boss_name"],
                "start_dt": ev["dt"],
                "zone_id": ev["zone_id"],
            }
        elif ev["event"] == "END" and open_boss is not None:
            boss_kills.append(
                {
                    "boss_name": open_boss["boss_name"],
                    "start_ts": open_boss["start_dt"].strftime("%m/%d/%Y %H:%M:%S.%f"),
                    "end_ts": ev["dt"].strftime("%m/%d/%Y %H:%M:%S.%f"),
                    "kill_flag": ev.get("kill_flag", 0),
                    "zone_id": open_boss["zone_id"],
                }
            )
            open_boss = None
    return boss_kills


def _write_boss_kills(boss_kills, path=BOSS_KILLS_PATH, mode="w"):
    """Write boss kill records to a JSON-lines sidecar file."""
    try:
        with open(path, mode, encoding="utf-8") as f:
            for bk in boss_kills:
                f.write(json.dumps(bk) + "\n")
        if boss_kills:
            print(f"  Wrote {len(boss_kills)} boss kill record(s) → {path}")
    except Exception as e:
        print(f"Warning: failed to write boss kills: {e}")


def detect_encounters(lines, timeout_secs=ENCOUNTER_TIMEOUT_SECS):
    """Scan raw combat-log lines and return a list of encounter intervals:

        [(combat_id, start_dt, end_dt, frozenset(enemy_guids), zone_id, zone_name), ...]

    Encounter-boundary rules
    ------------------------
    OPEN  – A friendly unit (player / pet / party member) deals damage to an
            enemy NPC *or* an enemy NPC deals damage to a friendly unit.
    GROW  – Additional enemies that join the fight are added to the active set.
    CLOSE – All tracked enemies have died (UNIT_DIED / PARTY_KILL empties the
            active set), the player dies, or `timeout_secs` elapses with no new
            hostile interaction (handles flee / evade / disconnect).

    A new combat_id is assigned each time an encounter opens.
    """
    encounters = []
    active_enemies: dict = {}  # guid -> last_seen_dt
    encounter_enemies: set = set()  # all guids that appeared this encounter
    dead_guids: set = set()  # GUIDs confirmed dead; ignore subsequent hits on them
    encounter_start = None
    last_hostile_dt = None
    combat_id = 0

    # Zone tracking (updated on every ZONE_CHANGE line)
    current_zone_id = 0
    current_zone_name = ""
    encounter_zone_id = 0  # zone snapshotted when an encounter opens
    encounter_zone_name = ""

    for line in lines:
        # Detect zone transitions (different line format from combat events)
        _zc = _parse_zone_change_line(line)
        if _zc is not None:
            _, _zid, _zname = _zc
            if _zname and _zname != "UNKNOWN AREA":
                current_zone_id = _zid
                current_zone_name = _zname
            continue

        raw = _parse_raw_event(line)
        if not raw:
            continue

        event = raw["event"]
        dt = raw["dt"]
        src_guid = raw["src_guid"]
        dst_guid = raw["dst_guid"]
        src_flags = raw["src_flags"]
        dst_flags = raw["dst_flags"]

        # --- Timeout: force-close an encounter that has gone quiet ---
        if last_hostile_dt is not None and encounter_start is not None:
            if (dt - last_hostile_dt).total_seconds() > timeout_secs:
                encounters.append(
                    (
                        combat_id,
                        encounter_start,
                        last_hostile_dt,
                        frozenset(encounter_enemies),
                        encounter_zone_id,
                        encounter_zone_name,
                    )
                )
                encounter_start = None
                active_enemies.clear()
                encounter_enemies.clear()
                last_hostile_dt = None

        # --- Damage / combat events ---
        if event in _COMBAT_EVENTS:
            enemy_guid = None
            if _is_enemy_npc(src_flags) and _is_friendly_unit(dst_flags):
                enemy_guid = src_guid  # Enemy is hitting a friendly unit
            elif _is_friendly_unit(src_flags) and _is_enemy_npc(dst_flags):
                enemy_guid = dst_guid  # Friendly unit is hitting an enemy

            # Skip "in-flight" hits landing on already-dead enemies.  These
            # arrive a few milliseconds after the kill event and would otherwise
            # reopen a zero-length spurious encounter.
            if enemy_guid and enemy_guid in dead_guids:
                enemy_guid = None

            if enemy_guid:
                if encounter_start is None:
                    # A fresh encounter begins
                    combat_id += 1
                    encounter_start = dt
                    encounter_zone_id = current_zone_id
                    encounter_zone_name = current_zone_name
                active_enemies[enemy_guid] = dt
                encounter_enemies.add(enemy_guid)
                last_hostile_dt = dt

        # --- Death events ---
        elif event in ("UNIT_DIED", "PARTY_KILL"):
            dead_guid = dst_guid
            dead_flags = dst_flags

            # Mark the unit as permanently dead so stray in-flight hits are ignored
            dead_guids.add(dead_guid)

            if dead_guid in active_enemies:
                # A tracked enemy died
                del active_enemies[dead_guid]
                last_hostile_dt = dt
                if not active_enemies and encounter_start is not None:
                    # All enemies are gone → close encounter
                    encounters.append(
                        (
                            combat_id,
                            encounter_start,
                            dt,
                            frozenset(encounter_enemies),
                            encounter_zone_id,
                            encounter_zone_name,
                        )
                    )
                    encounter_start = None
                    encounter_enemies.clear()
                    last_hostile_dt = None

            elif bool(dead_flags & _FLAG_TYPE_PLAYER) and encounter_start is not None:
                # The player died → close encounter immediately
                encounters.append(
                    (
                        combat_id,
                        encounter_start,
                        dt,
                        frozenset(encounter_enemies),
                        encounter_zone_id,
                        encounter_zone_name,
                    )
                )
                encounter_start = None
                active_enemies.clear()
                encounter_enemies.clear()
                last_hostile_dt = None

    # Close any encounter still open when the log ends
    if encounter_start is not None and last_hostile_dt is not None:
        encounters.append(
            (
                combat_id,
                encounter_start,
                last_hostile_dt,
                frozenset(encounter_enemies),
                encounter_zone_id,
                encounter_zone_name,
            )
        )

    return encounters


def assign_encounter_info(event_dt, encounters):
    """Binary-search the sorted encounter intervals and return
    (combat_id, zone_id, zone_name) for the encounter that covers *event_dt*,
    or (0, 0, '') if the event falls outside all encounter windows."""
    if event_dt is None or not encounters:
        return 0, 0, ""
    # encounters is chronologically sorted (built incrementally above).
    starts = [e[1] for e in encounters]
    idx = bisect.bisect_right(starts, event_dt) - 1
    if idx >= 0:
        enc = encounters[idx]
        cid, start, end = enc[0], enc[1], enc[2]
        if start <= event_dt <= end:
            zone_id = enc[4] if len(enc) > 4 else 0
            zone_name = enc[5] if len(enc) > 5 else ""
            return cid, zone_id, zone_name
    return 0, 0, ""


def assign_encounter_id(event_dt, encounters):
    """Binary-search the sorted encounter intervals and return the combat_id
    that covers *event_dt*, or 0 if the event falls outside all encounters."""
    return assign_encounter_info(event_dt, encounters)[0]


def _log_sort_key(path: str):
    """Return a datetime from the filename (WoWCombatLog-MMDDYY_HHMMSS.txt[.gz])
    so that both plain and compressed logs sort in true chronological order."""
    name = os.path.basename(path)
    name = name.replace(".txt.gz", "").replace(".txt", "")
    try:
        date_part = name.split("-", 1)[1]  # MMDDYY_HHMMSS
        return datetime.strptime(date_part, "%m%d%y_%H%M%S")
    except Exception:
        return datetime.min


def _open_log(path: str):
    """Return an open text-mode file handle for a plain or gzip-compressed log."""
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, "r", encoding="utf-8")


def export_csv(filepath, csv_path=OUTPUT_CSV):
    """Scan the log file and write parsed player events to a CSV file."""
    print(f"Exporting parsed events to CSV: {csv_path}")
    header = [
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
        # New column for class/spec detection
        "spell_id",
    ]

    # Pass 1 – detect encounter intervals using the full (unfiltered) log.
    with open(filepath, "r", encoding="utf-8") as infile:
        encounters = detect_encounters(infile)
    print(f"  Detected {len(encounters)} encounter(s).")

    # Pass 2 – extract player events and stamp each with the right combat_id.
    src_map = {}
    with open(csv_path, "w", encoding="utf-8", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(header)

        current_char_name = None
        with open(filepath, "r", encoding="utf-8") as infile:
            for line in infile:
                parsed_data, current_char_name = parse_combat_line(
                    line.strip(), current_char_name
                )
                if not parsed_data:
                    continue

                try:
                    dt = datetime.strptime(
                        parsed_data.get("timestamp", ""), "%m/%d/%Y %H:%M:%S.%f"
                    )
                except Exception:
                    dt = None

                cid, zone_id, zone_name = assign_encounter_info(dt, encounters)

                # Previously we used parser-side spec tagging; to keep schema
                # stable we now only persist spell_id. UI layers perform run-level
                # classification based on healer_spells sidecar when rendering.

                writer.writerow(
                    [
                        cid,
                        parsed_data.get("timestamp", ""),
                        parsed_data.get("event", ""),
                        parsed_data.get("source", ""),
                        parsed_data.get("target", ""),
                        parsed_data.get("spell_name", ""),
                        parsed_data.get("amount", 0),
                        parsed_data.get("effective_amount", 0),
                        parsed_data.get("type", ""),
                        zone_id,
                        zone_name,
                        parsed_data.get("spell_id", 0),
                    ]
                )

    # Pass 3 – extract boss kill events and write sidecar file.
    with open(filepath, "r", encoding="utf-8") as infile:
        boss_kills = extract_boss_kills(infile)
    _write_boss_kills(boss_kills)


def _backup_file(path, backup_dir=CSV_BACKUP_DIR, keep=MAX_CSV_BACKUPS):
    """Copy existing file into *backup_dir* with a timestamped name, then
    prune the oldest backups so at most *keep* copies are retained."""
    if not os.path.exists(path):
        return
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    bak_name = os.path.basename(path) + f".bak.{ts}"
    bak = os.path.join(backup_dir, bak_name)
    try:
        shutil.copy2(path, bak)
        print(f"Backed up '{os.path.basename(path)}' → {backup_dir}/{bak_name}")
    except Exception as e:
        print(f"Warning: failed to backup {path}: {e}")
        return
    # Prune oldest backups in the backup dir beyond the keep limit.
    baks = sorted(
        glob.glob(os.path.join(backup_dir, os.path.basename(path) + ".bak.*"))
    )
    while len(baks) > keep:
        oldest = baks.pop(0)
        try:
            os.remove(oldest)
            print(f"  Pruned old backup: {os.path.basename(oldest)}")
        except Exception:
            pass


def archive_old_logs(log_dir=LOG_DIR, data_dir=DATA_DIR):
    """Compress and move all but the newest WoWCombatLog-*.txt from *log_dir*
    into *data_dir* as .txt.gz files.  Safe to call at any time — calling it
    while the game is running will leave the active (newest) log in place."""
    pattern = os.path.join(log_dir, "WoWCombatLog-*.txt")
    files = sorted(glob.glob(pattern), key=os.path.getmtime)
    if len(files) <= 1:
        return  # zero or one file — nothing to archive
    to_archive = files[:-1]  # everything except the newest (active) log
    os.makedirs(data_dir, exist_ok=True)
    for src in to_archive:
        dest = os.path.join(data_dir, os.path.basename(src) + ".gz")
        if os.path.exists(dest):
            # Already safely archived; just remove the uncompressed original.
            try:
                os.remove(src)
                print(f"  Archive: removed duplicate {os.path.basename(src)}")
            except Exception as e:
                print(f"  Archive: could not remove {src}: {e}")
            continue
        try:
            with open(src, "rb") as f_in, gzip.open(dest, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            os.remove(src)
            print(f"  Archive: compressed {os.path.basename(src)} → {data_dir}")
        except Exception as e:
            print(f"  Archive: failed to compress {src}: {e}")


def export_csv_from_files(filepaths, csv_path=OUTPUT_CSV):
    """Export parsed events from multiple log files (in order) into a single CSV.

    Encounter detection runs across all files as one continuous stream so that
    state (active enemies, open encounters) is preserved across file boundaries.
    """
    print(f"Exporting parsed events from {len(filepaths)} files to CSV: {csv_path}")
    header = [
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
        # New column for class/spec detection
        "spell_id",
    ]

    # Pass 1 – stream all files through the encounter detector as one sequence.
    def _all_lines():
        for fp in filepaths:
            try:
                with _open_log(fp) as fh:
                    yield from fh
            except Exception:
                continue

    encounters = detect_encounters(_all_lines())
    print(f"  Detected {len(encounters)} encounter(s) across {len(filepaths)} file(s).")

    # Pass 2 – extract player events and stamp each with the right combat_id.
    src_map = {}
    with open(csv_path, "w", encoding="utf-8", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(header)

        current_char_name = None
        for fp in filepaths:
            try:
                with _open_log(fp) as infile:
                    for line in infile:
                        parsed_data, current_char_name = parse_combat_line(
                            line.strip(), current_char_name
                        )
                        if not parsed_data:
                            continue

                        try:
                            dt = datetime.strptime(
                                parsed_data.get("timestamp", ""), "%m/%d/%Y %H:%M:%S.%f"
                            )
                        except Exception:
                            dt = None

                        cid, zone_id, zone_name = assign_encounter_info(dt, encounters)

                        # Detection no longer performed at parse time; renderer will
                        # classify runs based on spell_id and the sidecar heuristics.

                        writer.writerow(
                            [
                                cid,
                                parsed_data.get("timestamp", ""),
                                parsed_data.get("event", ""),
                                parsed_data.get("source", ""),
                                parsed_data.get("target", ""),
                                parsed_data.get("spell_name", ""),
                                parsed_data.get("amount", 0),
                                parsed_data.get("effective_amount", 0),
                                parsed_data.get("type", ""),
                                zone_id,
                                zone_name,
                                parsed_data.get("spell_id", 0),
                            ]
                        )
            except Exception:
                continue

    # Pass 3 – extract boss kill events and write sidecar file.
    boss_kills = extract_boss_kills(_all_lines())
    _write_boss_kills(boss_kills)


def _read_max_combat_id(csv_path):
    """Return the highest combat_id already recorded in the CSV (0 if absent/empty)."""
    if not os.path.exists(csv_path):
        return 0
    max_id = 0
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    cid = int(row.get("combat_id", 0))
                    if cid > max_id:
                        max_id = cid
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass
    return max_id


def run_tail_mode(csv_path=OUTPUT_CSV):
    """Watch the active WoW combat log, detect encounter boundaries using the
    same GUID state machine as detect_encounters(), and append each completed
    encounter's player events to the CSV.

    Streamlit picks up the new rows on its next TTL refresh (~30 s).
    Press Ctrl-C to stop.
    """
    header = [
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
        # New column for class/spec detection
        "spell_id",
    ]

    # Continue combat_id numbering from what is already in the CSV.
    combat_id = _read_max_combat_id(csv_path)
    print(f"Tail mode started.  Resuming from combat_id={combat_id}.  Ctrl-C to stop.")

    # Archive old log files on startup (compress all but the active log).
    archive_old_logs()

    # ── Incremental encounter state (mirrors detect_encounters internals) ──
    active_enemies: dict = {}  # guid -> last_seen_dt
    encounter_enemies: set = set()  # all enemy guids seen this encounter
    dead_guids: set = set()  # permanently dead; never cleared between encounters
    encounter_start = None  # datetime when current encounter opened
    last_hostile_dt = None  # datetime of the most recent hostile event
    line_buffer: list = []  # raw log lines accumulated since encounter opened
    current_char_name = None

    # Zone tracking
    current_zone_id = 0
    current_zone_name = ""
    encounter_zone_id = 0  # snapshotted when encounter opens
    encounter_zone_name = ""

    # Boss encounter tracking (ENCOUNTER_START / ENCOUNTER_END)
    _open_boss = None  # {boss_name, start_dt, zone_id}

    def _flush(enc_start, close_dt):
        """Parse buffered lines, stamp with the next combat_id, append to CSV."""
        nonlocal combat_id, current_char_name
        combat_id += 1
        rows = []
        for raw_line in line_buffer:
            parsed, current_char_name = parse_combat_line(
                raw_line.strip(), current_char_name
            )
            if not parsed:
                continue
            try:
                evt_dt = datetime.strptime(
                    parsed.get("timestamp", ""), "%m/%d/%Y %H:%M:%S.%f"
                )
            except Exception:
                evt_dt = None
            # Only stamp rows that fall inside the encounter window.
            if evt_dt and enc_start <= evt_dt <= close_dt:
                cid = combat_id
            else:
                cid = 0
                rows.append(
                    [
                        cid,
                        parsed.get("timestamp", ""),
                        parsed.get("event", ""),
                        parsed.get("source", ""),
                        parsed.get("target", ""),
                        parsed.get("spell_name", ""),
                        parsed.get("amount", 0),
                        parsed.get("effective_amount", 0),
                        parsed.get("type", ""),
                        encounter_zone_id,
                        encounter_zone_name,
                        parsed.get("spell_id", 0),
                    ]
                )
        # Drop out-of-combat rows (cid=0) — they carry no combat-meter value.
        rows = [r for r in rows if r[0] != 0]
        if rows:
            need_header = not os.path.exists(csv_path)
            with open(csv_path, "a", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                if need_header:
                    writer.writerow(header)
                writer.writerows(rows)
            print(f"  Encounter {combat_id}: flushed {len(rows)} rows → {csv_path}")
        else:
            print(f"  Encounter {combat_id}: no player events — skipping.")
            combat_id -= 1  # don't burn an id for empty encounters

    def _close_encounter(close_dt):
        """Snapshot state, reset, and flush the encounter buffer."""
        nonlocal encounter_start, last_hostile_dt
        enc_start = encounter_start
        encounter_start = None
        last_hostile_dt = None
        active_enemies.clear()
        encounter_enemies.clear()
        _flush(enc_start, close_dt)
        line_buffer.clear()

    # ── Open and tail the latest log file ──
    log_path = get_latest_log_file(LOG_DIR)
    if not log_path:
        print("Error: no WoWCombatLog files found.")
        return

    print(f"Watching: {log_path}")
    log_fh = open(log_path, "r", encoding="utf-8")
    log_fh.seek(0, 2)  # Jump to end — ignore historical lines
    last_log_check = time.monotonic()

    try:
        while True:
            # ── Check for a newer log file every 10 s (game restarted) ──
            if time.monotonic() - last_log_check > 10.0:
                last_log_check = time.monotonic()
                latest = get_latest_log_file(LOG_DIR)
                if latest and latest != log_path:
                    print(f"  New log detected: {latest}")
                    log_fh.close()
                    log_path = latest
                    log_fh = open(log_path, "r", encoding="utf-8")
                    log_fh.seek(0, 2)
                    # Compress the logs that are no longer active.
                    archive_old_logs()

            new_lines = log_fh.readlines()

            if not new_lines:
                # No new data — check wall-clock timeout on any open encounter.
                if encounter_start is not None and last_hostile_dt is not None:
                    elapsed = (datetime.now() - last_hostile_dt).total_seconds()
                    if elapsed > ENCOUNTER_TIMEOUT_SECS:
                        print("  Timeout: closing encounter.")
                        _close_encounter(last_hostile_dt)
                time.sleep(0.5)
                continue

            for line in new_lines:
                # ── Detect zone changes ──
                _zc = _parse_zone_change_line(line)
                if _zc is not None:
                    _, _zid, _zname = _zc
                    if _zname and _zname != "UNKNOWN AREA":
                        current_zone_id = _zid
                        current_zone_name = _zname

                # ── Detect scripted boss encounters (ENCOUNTER_START/END) ──
                _bev = _parse_encounter_event(line)
                if _bev is not None:
                    if _bev["event"] == "START":
                        _open_boss = {
                            "boss_name": _bev["boss_name"],
                            "start_dt": _bev["dt"],
                            "zone_id": _bev["zone_id"],
                        }
                    elif _bev["event"] == "END" and _open_boss is not None:
                        bk = {
                            "boss_name": _open_boss["boss_name"],
                            "start_ts": _open_boss["start_dt"].strftime(
                                "%m/%d/%Y %H:%M:%S.%f"
                            ),
                            "end_ts": _bev["dt"].strftime("%m/%d/%Y %H:%M:%S.%f"),
                            "kill_flag": _bev.get("kill_flag", 0),
                            "zone_id": _open_boss["zone_id"],
                        }
                        _write_boss_kills([bk], mode="a")
                        kw = "KILL" if bk["kill_flag"] == 1 else "WIPE"
                        print(f"  Boss {kw}: {bk['boss_name']}")
                        _open_boss = None

                raw = _parse_raw_event(line)

                # ── Timeout check against the incoming line's timestamp ──
                if raw and encounter_start is not None and last_hostile_dt is not None:
                    if (
                        raw["dt"] - last_hostile_dt
                    ).total_seconds() > ENCOUNTER_TIMEOUT_SECS:
                        print("  Timeout: closing encounter.")
                        _close_encounter(last_hostile_dt)
                        # line_buffer is now clear; continue to process this line below.

                # ── Accumulate into buffer while an encounter is open ──
                if encounter_start is not None:
                    line_buffer.append(line)

                if not raw:
                    continue

                event = raw["event"]
                dt = raw["dt"]
                src_guid = raw["src_guid"]
                dst_guid = raw["dst_guid"]
                src_flags = raw["src_flags"]
                dst_flags = raw["dst_flags"]

                # ── Damage / combat events ──
                if event in _COMBAT_EVENTS:
                    enemy_guid = None
                    if _is_enemy_npc(src_flags) and _is_friendly_unit(dst_flags):
                        enemy_guid = src_guid
                    elif _is_friendly_unit(src_flags) and _is_enemy_npc(dst_flags):
                        enemy_guid = dst_guid

                    if enemy_guid and enemy_guid in dead_guids:
                        enemy_guid = None  # in-flight hit on a dead unit

                    if enemy_guid:
                        if encounter_start is None:
                            # Fresh encounter opens on this line.
                            encounter_start = dt
                            encounter_zone_id = current_zone_id
                            encounter_zone_name = current_zone_name
                            line_buffer.clear()  # discard any stale out-of-combat lines
                            line_buffer.append(line)  # this line is the opener
                        active_enemies[enemy_guid] = dt
                        encounter_enemies.add(enemy_guid)
                        last_hostile_dt = dt

                # ── Death events ──
                elif event in ("UNIT_DIED", "PARTY_KILL"):
                    dead_guids.add(dst_guid)

                    if dst_guid in active_enemies:
                        del active_enemies[dst_guid]
                        last_hostile_dt = dt
                        if not active_enemies and encounter_start is not None:
                            # All tracked enemies are dead → close encounter.
                            _close_encounter(dt)

                    elif (
                        bool(dst_flags & _FLAG_TYPE_PLAYER)
                        and encounter_start is not None
                    ):
                        # Player died → close encounter immediately.
                        _close_encounter(dt)

    except KeyboardInterrupt:
        print("\nTail mode stopped.")
        if encounter_start is not None and last_hostile_dt is not None:
            print("  Flushing open encounter before exit...")
            _close_encounter(last_hostile_dt)
    finally:
        log_fh.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WoW Combat Log Parser")
    # 'nargs="?"' means it takes 0 or 1 arguments. 'const="summary"' sets a default if the flag is used without an arg.
    parser.add_argument(
        "--test-parser",
        nargs="?",
        const="summary",
        choices=["summary", "debug"],
        help="Run test mode. Use '--test-parser debug' for line-by-line output.",
    )
    parser.add_argument(
        "--export-csv",
        action="store_true",
        help="Scan the latest log and export parsed player events to a CSV file.",
    )
    parser.add_argument(
        "--full-import",
        action="store_true",
        help="Scan all WoWCombatLog files and perform a full import, backing up existing CSV.",
    )

    args = parser.parse_args()

    latest_log = get_latest_log_file(LOG_DIR)

    if not latest_log:
        print("Error: No WoWCombatLog files found in the specified directory.")
        exit(1)

    # If full import requested, scan all logs, backup CSV, and rewrite it.
    if args.full_import:
        # First, move old uncompressed logs to DATA_DIR and compress them.
        print("Archiving old log files...")
        archive_old_logs()

        # Collect all files: compressed archives in DATA_DIR + any remaining
        # plain .txt files in LOG_DIR (at minimum the active/latest log).
        archived = glob.glob(os.path.join(DATA_DIR, "WoWCombatLog-*.txt.gz"))
        live = glob.glob(os.path.join(LOG_DIR, "WoWCombatLog-*.txt"))
        all_files = archived + live

        if not all_files:
            print("No WoWCombatLog files found for full import.")
            exit(1)

        # Sort by the timestamp encoded in the filename for true chronological order.
        files_sorted = sorted(all_files, key=_log_sort_key)
        print(
            f"Full import: {len(files_sorted)} file(s) ({len(archived)} compressed, {len(live)} live)."
        )
        # backup existing CSV
        _backup_file(OUTPUT_CSV)
        export_csv_from_files(files_sorted, csv_path=OUTPUT_CSV)
        exit(0)

    # If export requested, run export and exit (don't enter tail mode).
    if args.export_csv:
        export_csv(latest_log)
        exit(0)

    if args.test_parser == "debug":
        run_test_mode(latest_log, debug=True)
    elif args.test_parser == "summary":
        run_test_mode(latest_log, debug=False)
    else:
        # Default (no flags): tail the active log and flush completed encounters to CSV.
        run_tail_mode()
