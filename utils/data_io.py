"""
utils/data_io.py
────────────────
All file I/O helpers and the @st.cache_data CSV loader.
No Streamlit rendering commands — only data loading/saving utilities.
"""

import json
import os

import pandas as pd
import streamlit as st

from config import (  # noqa: F401 – re-exported for backward-compat imports
    BOSS_KILLS_PATH,
    CSV_PATH,
    DATA_DIR,
    DEFAULT_NUM_COMBATS,
    DEFAULT_TOP_N_ABILITIES,
    HIDDEN_PATH,
    LOG_DIR,
    MAX_CSV_BACKUPS,
    MIN_SOURCE_COMBATS,
    NOTES_PATH,
)


# ── Log file helpers ─────────────────────────────────────────────────────────


def get_latest_log_file(directory=LOG_DIR):
    """Finds the most recently modified WoWCombatLog file."""
    import glob

    search_pattern = os.path.join(directory, "WoWCombatLog-*.txt")
    log_files = glob.glob(search_pattern)
    return max(log_files, key=os.path.getmtime) if log_files else None


# ── Boss kills ────────────────────────────────────────────────────────────────


def load_boss_kills(path=BOSS_KILLS_PATH) -> list:
    """Return list of boss kill dicts from the sidecar JSONL file."""
    if not os.path.exists(path):
        return []
    kills = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    kills.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        pass
    return kills


# ── Hidden encounters ─────────────────────────────────────────────────────────


def load_hidden(path=HIDDEN_PATH) -> set:
    """Return set of combat_ids that should be hidden from the encounter list."""
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(int(x) for x in data)
    except Exception:
        return set()


def save_hidden(hidden: set, path=HIDDEN_PATH):
    """Persist the set of hidden combat_ids."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted(hidden), f)
    except Exception:
        pass


def toggle_hidden(combat_id: int, path=HIDDEN_PATH):
    """Add combat_id to hidden set, or remove it if already hidden."""
    hidden = load_hidden(path)
    if combat_id in hidden:
        hidden.discard(combat_id)
    else:
        hidden.add(combat_id)
    save_hidden(hidden, path)


# ── Formatting helper ─────────────────────────────────────────────────────────


def _fmt_compact_amount(n: float) -> str:
    """Return a compact HTML string for amounts, e.g. '152 <strong>K</strong>'"""
    try:
        v = float(n)
    except Exception:
        return "0"
    if v >= 1_000_000:
        return f"{int(round(v / 1_000_000)):d}&nbsp;<strong>M</strong>"
    if v >= 1000:
        return f"{int(round(v / 1000)):d}&nbsp;<strong>K</strong>"
    return f"{int(round(v)):d}"


# ── CSV loader ────────────────────────────────────────────────────────────────


@st.cache_data(ttl=3)
def load_csv(path=CSV_PATH):
    """Load the pre-processed CSV.  TTL=3s so new tail-mode encounters are
    visible on the next auto-refresh cycle without needing a manual cache clear."""
    df = pd.read_csv(path)
    df["timestamp_dt"] = pd.to_datetime(df["timestamp"], format="%m/%d/%Y %H:%M:%S.%f", errors="coerce")
    # Backward-compat: older CSVs lack zone columns
    if "zone_id" not in df.columns:
        df["zone_id"] = 0
    if "zone_name" not in df.columns:
        df["zone_name"] = ""
    return df


# ── Notes ─────────────────────────────────────────────────────────────────────


def load_notes(path=NOTES_PATH):
    """Return {combat_id: note_str} from the sidecar JSONL file."""
    notes = {}
    if not os.path.exists(path):
        return notes
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                    notes[int(row["combat_id"])] = row.get("note", "")
                except Exception:
                    pass
    except Exception:
        pass
    return notes


def save_note(combat_id, note, path=NOTES_PATH):
    """Persist a note for a combat_id, replacing any existing entry."""
    notes = load_notes(path)
    if note:
        notes[int(combat_id)] = note
    else:
        notes.pop(int(combat_id), None)
    try:
        with open(path, "w", encoding="utf-8") as f:
            for cid, n in sorted(notes.items()):
                f.write(json.dumps({"combat_id": cid, "note": n}) + "\n")
    except Exception:
        pass


# ── Character counts ──────────────────────────────────────────────────────────


@st.cache_data(ttl=30)
def compute_character_counts(path=CSV_PATH):
    """Return a DataFrame of (source, combats) sorted by encounter count desc."""
    df = load_csv(path)
    if df.empty:
        return pd.DataFrame()
    counts = df[~df["source"].isnull() & (df["source"] != "")].groupby("source")["combat_id"].nunique()
    res = counts.reset_index().rename(columns={"combat_id": "combats"}).sort_values("combats", ascending=False)
    return res
