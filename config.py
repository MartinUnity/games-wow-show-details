"""
config.py
─────────
Single source of truth for all shared paths and configuration values.
Both wow-parser.py and the Streamlit app (via utils/) import from here.
"""

import os

# ── WoW combat log source directory ──────────────────────────────────────────
# Allow overriding the local WoW Logs path via the environment for privacy and
# portability. If not set, fall back to the previous developer-local default.
_DEFAULT_HOME = os.path.expanduser("~")
# Construct a sensible Linux default under $HOME but allow full override via
# the WOW_LOG_DIR environment variable (useful on Windows or non-standard setups).
LOG_DIR = os.environ.get(
    "WOW_LOG_DIR",
    os.path.join(
        _DEFAULT_HOME,
        ".local",
        "share",
        "Steam",
        "steamapps",
        "compatdata",
        "4076040504",
        "pfx",
        "drive_c",
        "Program Files (x86)",
        "World of Warcraft",
        "_retail_",
        "Logs",
    ),
)

# ── Local data directory for archived (compressed) log files ─────────────────
# Old log files are moved here and stored as .txt.gz to save space.
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "logs")

# ── Output / sidecar file paths ───────────────────────────────────────────────
CSV_PATH = "parsed_combat_data.csv"
# Sidecar directory under repo data/sidecar — convenience paths for services and UIs
SIDECAR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sidecar")
BOSS_KILLS_PATH = os.path.join(SIDECAR_DIR, "boss_kills.jsonl")
NOTES_PATH = os.path.join(SIDECAR_DIR, "encounter_notes.jsonl")
HIDDEN_PATH = os.path.join(SIDECAR_DIR, "hidden_combats.json")
HEALER_SPELLS_PATH = os.path.join(SIDECAR_DIR, "healer_spells.json")

# ── CSV backup directory ─────────────────────────────────────────────────────
# Timestamped backups of parsed_combat_data.csv are stored here instead of
# cluttering the repo root.
CSV_BACKUP_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "csv-backups"
)

# ── Log archive settings ──────────────────────────────────────────────────────
# How many parsed_combat_data.csv.bak.* files to keep before pruning.
MAX_CSV_BACKUPS = 10

# ── Streamlit UI Settings ─────────────────────────────────────────────────────
# Default number of combats to display (e.g., 25, 50, 100, or 1000 for "all")
DEFAULT_NUM_COMBATS = 25

# Default number of top abilities to show in the detailed breakdown
DEFAULT_TOP_N_ABILITIES = 7

# Minimum number of combats a source must have to be shown in the character list
MIN_SOURCE_COMBATS = 3
