# WoW Combat Viewer

A tool for parsing World of Warcraft combat logs and visualizing your gameplay
statistics with detailed encounter analysis.

## 🎯 Overview

The parser (`wow-parser.py`) reads raw WoW combat logs into a CSV plus small
sidecar files. A **FastAPI + SQLite + React SPA** stack serves an interactive
web UI: review encounters, analyze DPS/HPS, compare characters and bosses,
and follow live combat over Server-Sent Events.

```
WoW Logs ──> wow-parser.py (tail mode = live ingester)
                │  parsed_combat_data.csv + data/sidecar/*.jsonl|json
                ▼
         storage/   (SQLite — derived, always rebuildable from the CSV)
                ▼
         api/       (FastAPI: REST /api/* + SSE /api/events; serves web/dist)
                ▼
         web/       (React + TypeScript SPA: ECharts + TanStack Table)
```

The CSV stays the source of truth; the SQLite store is derived from it
(rebuildable at any time with `make db`) and is the default read path once
it exists (`WOW_USE_SQLITE=0` forces the CSV).

## 📁 Project Structure

```
games-wow-show-details/
├── wow-parser.py            # Combat log parser (stdlib-only; the "crown jewel")
├── api/                     # FastAPI app: REST + SSE endpoints
├── storage/                 # SQLite schema, CSV→DB sync, query layer
├── web/                     # React SPA (Vite + TypeScript)
├── utils/                   # Streamlit-free data compute + IO helpers
├── config.py                # Paths & tunables (WOW_* env overrides)
├── runme.sh                 # Process supervisor (parser / api / web)
├── Makefile                 # fixture / devdata / db / web targets
├── parsed_combat_data.csv   # Output CSV (gitignored — personal data)
└── data/sidecar/            # boss_kills.jsonl, notes, hidden, healer spells
```

## 🚀 Quick Start

### Prerequisites
- Python 3.10+ and Node 18+
- World of Warcraft installed (for combat log access)
- Write access to your WoW Logs directory (see Environment configuration)

### Installation

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
make web-install        # npm install for the SPA
make web-build          # production bundle → web/dist
```

### One-off import + run

```bash
python wow-parser.py --full-import   # parse all historical logs (→ CSV + SQLite)
./runme.sh start                     # starts parser (tail mode) + api
```

Open **http://127.0.0.1:8000** — the API serves the built SPA.

### Development (hot-reload SPA)

```bash
./runme.sh start parser api          # backend + parser
./runme.sh start web                 # vite dev server on :5173 (proxies /api)
# → http://127.0.0.1:5173
```

No real logs on this machine? `make fixture` builds a small deterministic
dataset (6 encounters); `make devdata` uses the real data in `wow-data/`.

## 📊 Features

### Combat Log Parsing

- **Real-time encounter detection** using a GUID-based state machine
- **Player action extraction** for damage, healing, and spell usage
- **Encounter boundaries** (enemy deaths, wipes, flee, timeout)
- **Boss kill tracking** via ENCOUNTER_START/END events
- **Zone change monitoring** for run grouping

### Web Dashboard Views

| View                     | Description                                                    |
| ------------------------ | -------------------------------------------------------------- |
| **Combat Viewer**        | DPS/HPS timeline, ability breakdowns, rotation timeline, TTK/overkill, notes, hide, CSV/GIF export |
| **Runs**                 | Encounters grouped by zone + time gaps, per-participant stats  |
| **All Encounters**       | Every combat with session activity and ability breakdown       |
| **Totals**               | Summary statistics per target and ability                      |
| **Character Comparison** | Side-by-side comparison of characters                          |
| **Boss Comparison**      | Per-boss DPS across encounters                                 |

### Key Capabilities

- ⏱️ **Live follow** — SSE stream pushes `encounter_closed`; the SPA auto-selects the new combat
- 🎯 **DPS/HPS tracking** — per-second granularity, resample + smoothing controls
- ✨ **Ability analytics** — top spells by damage/healing with percentage breakdowns
- 🏆 **Boss kill tracking** — which bosses fell in which run
- 🙈 **Hidden encounters** & per-encounter notes (persisted sidecars + SQLite)
- 🔗 **Deep links** — `?view=…&combat=…` URLs are shareable
- 📤 **Exports** — per-combat CSV and animated GIF damage replay

## 🛠️ Commands

```bash
# Parser
python wow-parser.py                  # tail mode (live)
python wow-parser.py --export-csv     # parse newest log
python wow-parser.py --full-import    # parse all historical logs

# Processes (parser / api / web)
./runme.sh start|stop|restart|status|logs [name]

# Data
make fixture        # synthetic 6-encounter dataset (hermetic)
make devdata        # place real data from wow-data/ where the app expects
make db             # rebuild the derived SQLite store

# SPA
make web-dev        # vite dev server (:5173)
make web-build      # production bundle (served by the API at /)
make web-test       # vitest

# Tests
.venv/bin/python -m pytest tests/ -q
```

## ⚙️ Configuration

- `WOW_LOG_DIR` — your World of Warcraft `Logs` folder (parser)
- `WOW_USE_SQLITE` — `0` forces the plain CSV; otherwise (default) the
  derived SQLite store is used once it exists (`make db`)
- `WOW_BASE_DIR` — override the data directory (default: repo root)
- `WOW_SSE_POLL_S` — SSE watermark poll interval (default 1.5 s)
- All paths/tunables live in `config.py`

Environment examples for `WOW_LOG_DIR`:

Linux (bash):

```bash
export WOW_LOG_DIR="$HOME/.local/share/Steam/steamapps/compatdata/4076040504/pfx/drive_c/Program Files (x86)/World of Warcraft/_retail_/Logs"
```

Windows (PowerShell):

```powershell
$env:WOW_LOG_DIR = 'C:\Program Files (x86)\World of Warcraft\_retail_\Logs'
```

Privacy note
------------

Parsed CSVs, sidecar files and `wow-data/` are **gitignored** — they contain
personal gameplay data (names, timestamps, server identifiers). Keep raw and
exported logs out of the tracked tree; run the parser locally.

## 📝 Data Format

### `parsed_combat_data.csv`

| Column           | Description                                  |
| ---------------- | -------------------------------------------- |
| combat_id        | Unique encounter identifier (0 = out of combat) |
| timestamp        | Event timestamp (MM/DD/YYYY HH:MM:SS.ffffff) |
| event            | Action type (SPELL_DAMAGE, SWING_HEAL, …)    |
| source           | Unit that performed the action               |
| target           | Target of the action                         |
| spell_name       | Spell/ability used (if applicable)           |
| amount           | Raw damage/healing value                     |
| effective_amount | Damage/healing after absorbs/misses          |
| type             | damage / heal / absorb / other               |
| zone_id          | Zone identifier                              |
| zone_name        | Zone name                                    |
| spell_id         | Spell identifier                             |

### Sidecars (`data/sidecar/`)

- `boss_kills.jsonl` — one JSON object per boss encounter
- `encounter_notes.jsonl` — per-combat notes
- `hidden_combats.json` — list of hidden combat ids
- `healer_spells.json` — spec → spell-id mapping for HPS attribution

The parser owns these files (the SPA writes through the API, which keeps the
files and the SQLite mirror in sync — the sidecar files remain the canonical
sidecar store, SQLite is derived).

### Replay logs

Drop archived raw logs (`.txt` or `.txt.gz`) into `data/logs/` to enable the
positional replay feature for encounters covered by those logs.

## ⚠️ Notes

- The SQLite store is **derived, never canonical** — rebuild with `make db`.
- CSV backups are created automatically during full imports.
- The old Streamlit app was retired in the FastAPI/SPA migration
  (see `docs/MIGRATION_PLAN.md`).

## 📄 License

This tool is for personal use with your own World of Warcraft combat logs.
