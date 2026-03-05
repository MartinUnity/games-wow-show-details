import argparse
import bisect
import csv
import glob
import os
import time
from datetime import datetime

# --- CONFIGURATION ---
LOG_DIR = "/home/martin/.local/share/Steam/steamapps/compatdata/4076040504/pfx/drive_c/Program Files (x86)/World of Warcraft/_retail_/Logs"
OUTPUT_CSV = "parsed_combat_data.csv"


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
        data["spell_name"] = rest_parts[9].replace('"', "") if len(rest_parts) > 9 else "Unknown"
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
        data["spell_name"] = rest_parts[9].replace('"', "") if len(rest_parts) > 9 else "Unknown"
        # Destination/target name is typically at index 5
        if len(rest_parts) > 5:
            data["target"] = rest_parts[5].replace('"', "")
        amount = to_int(rest_parts[-11]) if len(rest_parts) >= 11 else to_int(rest_parts[-1])
        data["amount"] = amount
        data["effective_amount"] = amount

    # Parse Melee Damage (SWING_DAMAGE has no spell prefix block)
    elif event_type == "SWING_DAMAGE" and len(rest_parts) >= 1:
        data["type"] = "damage"
        data["spell_name"] = "Melee"
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
            parsed_data, current_char_name = parse_combat_line(line.strip(), current_char_name)

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
            elif parsed_data["type"] == "heal":
                total_healing += parsed_data["effective_amount"]

    if debug:
        return  # Skip the table if we are just debugging lines

    # Calculate Summary
    if not first_event_time or not last_event_time or first_event_time == last_event_time:
        duration_seconds = 1  # Avoid division by zero
    else:
        duration_seconds = (last_event_time - first_event_time).total_seconds()

    avg_dps = total_damage / duration_seconds
    avg_hps = total_healing / duration_seconds

    # Print Table
    print(f"| {'Player':<25} | {'Avg DPS':<10} | {'Avg HPS':<10} | {'Duration (s)':<12} |")
    print("-" * 66)
    name_display = current_char_name if current_char_name else "No Player Found"
    print(f"| {name_display:<25} | {avg_dps:<10.1f} | {avg_hps:<10.1f} | {duration_seconds:<12.1f} |")


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


def detect_encounters(lines, timeout_secs=ENCOUNTER_TIMEOUT_SECS):
    """Scan raw combat-log lines and return a list of encounter intervals:

        [(combat_id, start_dt, end_dt, frozenset(enemy_guids)), ...]

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

    for line in lines:
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
                encounters.append((combat_id, encounter_start, last_hostile_dt, frozenset(encounter_enemies)))
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
                    encounters.append((combat_id, encounter_start, dt, frozenset(encounter_enemies)))
                    encounter_start = None
                    encounter_enemies.clear()
                    last_hostile_dt = None

            elif bool(dead_flags & _FLAG_TYPE_PLAYER) and encounter_start is not None:
                # The player died → close encounter immediately
                encounters.append((combat_id, encounter_start, dt, frozenset(encounter_enemies)))
                encounter_start = None
                active_enemies.clear()
                encounter_enemies.clear()
                last_hostile_dt = None

    # Close any encounter still open when the log ends
    if encounter_start is not None and last_hostile_dt is not None:
        encounters.append((combat_id, encounter_start, last_hostile_dt, frozenset(encounter_enemies)))

    return encounters


def assign_encounter_id(event_dt, encounters):
    """Binary-search the sorted encounter intervals and return the combat_id
    that covers *event_dt*, or 0 if the event falls outside all encounters."""
    if event_dt is None or not encounters:
        return 0
    # encounters is chronologically sorted (built incrementally above).
    starts = [e[1] for e in encounters]
    idx = bisect.bisect_right(starts, event_dt) - 1
    if idx >= 0:
        cid, start, end, _ = encounters[idx]
        if start <= event_dt <= end:
            return cid
    return 0


def export_csv(filepath, csv_path=OUTPUT_CSV):
    """Scan the log file and write parsed player events to a CSV file."""
    print(f"Exporting parsed events to CSV: {csv_path}")
    header = ["combat_id", "timestamp", "event", "source", "target", "spell_name", "amount", "effective_amount", "type"]

    # Pass 1 – detect encounter intervals using the full (unfiltered) log.
    with open(filepath, "r", encoding="utf-8") as infile:
        encounters = detect_encounters(infile)
    print(f"  Detected {len(encounters)} encounter(s).")

    # Pass 2 – extract player events and stamp each with the right combat_id.
    with open(csv_path, "w", encoding="utf-8", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(header)

        current_char_name = None
        with open(filepath, "r", encoding="utf-8") as infile:
            for line in infile:
                parsed_data, current_char_name = parse_combat_line(line.strip(), current_char_name)
                if not parsed_data:
                    continue

                try:
                    dt = datetime.strptime(parsed_data.get("timestamp", ""), "%m/%d/%Y %H:%M:%S.%f")
                except Exception:
                    dt = None

                cid = assign_encounter_id(dt, encounters)

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
                    ]
                )


def _backup_file(path):
    """Rename existing file to a .bak with timestamp to avoid data loss."""
    if not os.path.exists(path):
        return
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    bak = f"{path}.bak.{ts}"
    try:
        os.rename(path, bak)
        print(f"Backed up existing '{path}' to '{bak}'")
    except Exception as e:
        print(f"Warning: failed to backup {path}: {e}")


def export_csv_from_files(filepaths, csv_path=OUTPUT_CSV):
    """Export parsed events from multiple log files (in order) into a single CSV.

    Encounter detection runs across all files as one continuous stream so that
    state (active enemies, open encounters) is preserved across file boundaries.
    """
    print(f"Exporting parsed events from {len(filepaths)} files to CSV: {csv_path}")
    header = ["combat_id", "timestamp", "event", "source", "target", "spell_name", "amount", "effective_amount", "type"]

    # Pass 1 – stream all files through the encounter detector as one sequence.
    def _all_lines():
        for fp in filepaths:
            try:
                with open(fp, "r", encoding="utf-8") as fh:
                    yield from fh
            except Exception:
                continue

    encounters = detect_encounters(_all_lines())
    print(f"  Detected {len(encounters)} encounter(s) across {len(filepaths)} file(s).")

    # Pass 2 – extract player events and stamp each with the right combat_id.
    with open(csv_path, "w", encoding="utf-8", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(header)

        current_char_name = None
        for fp in filepaths:
            try:
                with open(fp, "r", encoding="utf-8") as infile:
                    for line in infile:
                        parsed_data, current_char_name = parse_combat_line(line.strip(), current_char_name)
                        if not parsed_data:
                            continue

                        try:
                            dt = datetime.strptime(parsed_data.get("timestamp", ""), "%m/%d/%Y %H:%M:%S.%f")
                        except Exception:
                            dt = None

                        cid = assign_encounter_id(dt, encounters)

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
                            ]
                        )
            except Exception:
                continue


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
    ]

    # Continue combat_id numbering from what is already in the CSV.
    combat_id = _read_max_combat_id(csv_path)
    print(f"Tail mode started.  Resuming from combat_id={combat_id}.  Ctrl-C to stop.")

    # ── Incremental encounter state (mirrors detect_encounters internals) ──
    active_enemies: dict = {}  # guid -> last_seen_dt
    encounter_enemies: set = set()  # all enemy guids seen this encounter
    dead_guids: set = set()  # permanently dead; never cleared between encounters
    encounter_start = None  # datetime when current encounter opened
    last_hostile_dt = None  # datetime of the most recent hostile event
    line_buffer: list = []  # raw log lines accumulated since encounter opened
    current_char_name = None

    def _flush(enc_start, close_dt):
        """Parse buffered lines, stamp with the next combat_id, append to CSV."""
        nonlocal combat_id, current_char_name
        combat_id += 1
        rows = []
        for raw_line in line_buffer:
            parsed, current_char_name = parse_combat_line(raw_line.strip(), current_char_name)
            if not parsed:
                continue
            try:
                evt_dt = datetime.strptime(parsed.get("timestamp", ""), "%m/%d/%Y %H:%M:%S.%f")
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
                raw = _parse_raw_event(line)

                # ── Timeout check against the incoming line's timestamp ──
                if raw and encounter_start is not None and last_hostile_dt is not None:
                    if (raw["dt"] - last_hostile_dt).total_seconds() > ENCOUNTER_TIMEOUT_SECS:
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

                    elif bool(dst_flags & _FLAG_TYPE_PLAYER) and encounter_start is not None:
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
        # find all logs in the directory sorted by modification time (oldest first)
        pattern = os.path.join(LOG_DIR, "WoWCombatLog-*.txt")
        files = glob.glob(pattern)
        if not files:
            print("No WoWCombatLog files found for full import.")
            exit(1)
        files_sorted = sorted(files, key=os.path.getmtime)
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
