# WoW Combat Viewer

A tool for parsing World of Warcraft combat logs and visualizing your gameplay statistics with detailed encounter analysis.

## 🎯 Overview

This project parses World of Warcraft combat log files to extract player damage, healing, and ability usage data. It provides an interactive Streamlit web interface that lets you review your encounters, analyze your DPS/HPS performance, compare characters, and track boss kills across multiple zones.

## 📁 Project Structure

```
games-wow-show-details/
├── wow-parser.py        # Combat log parser script
├── streamlit_app.py     # Streamlit web application
├── parsed_combat_data.csv  # Output CSV with parsed combat data
├── boss_kills.jsonl     # Boss kill records (sidecar file)
├── hidden_combats.json  # Hidden encounter IDs
├── requirements.txt      # Python dependencies
└── README.md            # This file
```

## 🚀 Quick Start

### Prerequisites
- Python 3.8+
- Steam World of Warcraft installed (for combat log access)
- Write access to: `/home/martin/.local/share/Steam/steamapps/compatdata/4076040504/pfx/drive_c/Program Files (x86)/World of Warcraft/_retail_/Logs`

### Installation

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Initial setup (parse all historical logs):**
   ```bash
   python wow-parser.py --full-import
   ```

3. **Start the web interface:**
   ```bash
   streamlit run streamlit_app.py
   ```

## 📊 Features

### Combat Log Parsing

- **Real-time encounter detection** using GUID-based state machine tracking active enemies
- **Player action extraction** for damage, healing, and spell usage
- **Encounter boundaries** automatically detected (enemy deaths, wipes, flee, timeout)
- **Boss kill tracking** via ENCOUNTER_START/END events
- **Zone change monitoring** for run grouping

### Web Dashboard Views

| View                     | Description                                                                        |
| ------------------------ | ---------------------------------------------------------------------------------- |
| **Combat Viewer**        | Live encounter details with DPS/HPS charts, ability breakdowns, rotation timelines |
| **Runs**                 | Encounters grouped by zone with boss kill tracking                                 |
| **All Encounters**       | Aggregated stats across all combats with rolling averages                          |
| **Totals**               | Summary statistics per target and ability                                          |
| **Character Comparison** | Side-by-side comparison of multiple characters                                     |

### Key Capabilities

- ⏱️ **Real-time live following** - Automatically updates when you play
- 🎯 **DPS/HPS tracking** - Per-second granularity with smoothing options
- ✨ **Ability analytics** - Top spells by damage/healing with percentage breakdowns
- 👥 **Character comparison** - Compare multiple characters' performance
- 🏆 **Boss kill tracking** - Track which bosses were defeated in each run
- 🙈 **Hidden encounters** - Optionally hide specific combats from the list
- 📍 **Zone/run grouping** - Automatically groups encounters by zone and time gaps

## 🛠️ Usage

### Parser Commands

```bash
# Parse latest combat log and export to CSV
python wow-parser.py --export-csv

# Parse all historical logs (with backup)
python wow-parser.py --full-import

# Test parser output (summary or debug mode)
python wow-parser.py --test-parser summary
python wow-parser.py --test-parser debug

# Live tail mode (continuous monitoring)
python wow-parser.py
```

### Streamlit Views

- Launch with: `streamlit run streamlit_app.py`
- Access at default port 8501 in browser
- Sidebar controls for character filtering, resampling, and view selection

## 📈 Visualizations

The application includes:
- **Line charts** for DPS/HPS over time (configurable resample intervals)
- **Bar charts** for ability breakdowns by damage/healing type
- **Pie charts** showing damage/heal share per participant
- **Scatter swimlanes** for rotation timeline visualization
- **AgGrid tables** with client-side filtering and sorting

## ⚙️ Configuration

Key configuration locations:
- `config.py` → `LOG_DIR` is now environment-configurable via `WOW_LOG_DIR` (preferred)
- `streamlit_app.py` → various file paths (CSV, hidden combats, boss kills)

Default combat log directory (developer fallback):
```
/home/martin/.local/share/Steam/steamapps/compatdata/4076040504/pfx/drive_c/Program Files (x86)/World of Warcraft/_retail_/Logs
```

Environment configuration:

1. Export your WoW logs directory (recommended):

```bash
export WOW_LOG_DIR="$HOME/.local/share/Steam/steamapps/compatdata/4076040504/pfx/drive_c/Program Files (x86)/World of Warcraft/_retail_/Logs"
```

2. Run the parser or UI as usual:

```bash
python wow-parser.py --full-import
streamlit run streamlit_app.py
```

Privacy note
------------

This repository previously included locally parsed combat CSVs and sidecar files derived from gameplay logs (timestamps, player/server identifiers). To avoid accidentally publishing personal gameplay data, those files are now ignored by the repository by default. Keep any raw or exported logs out of the tracked tree — the project expects you to run the parser locally to generate `parsed_combat_data.csv` and any sidecar artifacts.

## 📝 Data Format

### Combat Log Parser Output (`parsed_combat_data.csv`)

| Column           | Description                                  |
| ---------------- | -------------------------------------------- |
| combat_id        | Unique encounter identifier                  |
| timestamp        | Event timestamp (MM/DD/YYYY HH:MM:SS.ffffff) |
| event            | Action type (damage, heal, swing, etc.)      |
| source           | Player/NPC that performed action             |
| target           | Target of the action                         |
| spell_name       | Spell/ability used (if applicable)           |
| amount           | Raw damage/healing value                     |
| effective_amount | Actual damage dealt after absorbs/misses     |
| type             | Category (damage, heal, other)               |
| zone_id          | Zone identifier                              |
| zone_name        | Zone name                                    |

### Boss Kills (`boss_kills.jsonl`)

```json
{"boss_name": "Ragnaros", "start_ts": "...", "end_ts": "...", "kill_flag": 1, "zone_id": 123}
```

## 🔧 Advanced Features

### Encounter Detection Algorithm

The parser uses a GUID-based state machine to track combat:
- Opens when friendly unit damages enemy or vice versa
- Grows as additional enemies join
- Closes when all enemies die, player dies, flee occurs, or timeout expires (8s default)

### Live Mode

In tail mode (`python wow-parser.py` without flags):
- Watches the latest combat log for new events
- Flushes completed encounters to CSV
- Streamlit refreshes every 3 seconds to show live updates
- Ctrl+C to stop monitoring

## 📋 Requirements

```txt
altair>=4.0.0
pandas>=1.0.0
streamlit>=1.0.0
streamlit_autorefresh
st_aggrid
```

## ⚠️ Notes

- Combat logs are stored in your Steam directory
- The parser handles multiple log files (e.g., when you restart the game)
- Hidden encounters are persisted totum/hidden_combats.json
- CSV backups are created automatically during full imports

## 📄 License

This tool is for personal use with your own World of Warcraft combat logs.

---

Built with ❤️ for WoW players who want detailed encounter analysis.
