# Improvement Plan

A prioritized, dependency-ordered plan to revive and stabilize the WoW Combat
Viewer. Items are grouped into phases (P1–P6). Each item lists the files to
touch, the reason, and its status.

> Status legend: `[ ]` pending · `[~]` in progress · `[x]` done

## Why this order (dependencies & synergy)

1. **P1 (data correctness) is the foundation.** The CSV is the single source of
   truth every view reads. Fixing the one real parser bug (melee damage stored
   as `-1`) first means every downstream metric is built on correct data, and the
   P6 parser tests lock the fix in.
2. **P6 (parser tests) ships with P1** — the regression tests exist to protect
   the P1 fix and are the cheapest, highest-value tests in the repo (they run on
   `parse_combat_line` with inline raw lines, no file I/O).
3. **P2 (broken UI) next.** Once data is right, make the app actually usable:
   migrate the removed `st.experimental_*` APIs and fix the Runs → Combat Viewer
   navigation (this also delivers the feature described in `docs/TODO.md`).
4. **P3 (replay) after P6.** The dead replay feature needs a valid log source;
   the `testdata/` fixtures created under P6 give it a deterministic file to read.
5. **P4 (performance) after P1/P2** so optimizations are verified against known-
   correct behavior.
6. **P5 (cleanup) last** — low-risk, no behavior change.

---

## P1 — Data correctness (parser) `[x]`

The previous review's "all damage/heal amounts are wrong" finding was a **false
positive** (field miscount: 41/35/37, not 42/36/38; target-remaining-health was
misread as damage). Verified field map:

| Event | Fields | Amount | Parser | Status |
|---|---|---|---|---|
| `SPELL_DAMAGE` / `SPELL_PERIODIC_DAMAGE` | 41 | damage `[30]` | `[-11]`→`[30]` (L239) | correct |
| `SPELL_HEAL` / `SPELL_PERIODIC_HEAL` | 35 | amount `[30]`, overheal `[32]` | `[-5]`, `[-3]` (L200-201) | correct |
| `SWING_DAMAGE` | 37 | damage `[28]` | `[-8]`→`[29]` = `-1` (L251) | **BUG** |
| `SWING_DAMAGE_LANDED` | 37 | damage `[28]` | not extracted | duplicate (OK) |

- `[x]` **P1.1** Fix `wow-parser.py:251`: `rest_parts[-8]` → `rest_parts[-9]`
  so melee reads the real damage field (index 28). `SWING_DAMAGE` and
  `SWING_DAMAGE_LANDED` are the same hit (identical damage, 0–2 ms apart), so
  extracting only `SWING_DAMAGE` avoids double-count — leave the duplicate alone.
- `[x]` **P1.2** Regenerate data: `python wow-parser.py --full-import`.
  All 3,488 melee rows currently store `amount=-1`.

## P6 — Tests `[~]`

- `[x]` **P6.1** `tests/test_parser.py`: unit tests on `parse_combat_line` using
  inline real raw lines — assert melee damage > 0 (the P1 regression guard),
  spell damage matches field `[30]`, and heal `effective_amount = amount -
  overheal` (incl. full-overheal → 0).
- `[ ]` **P6.2** (optional) trimmed `tests/testdata/` fixtures + an
  integration test that runs the encounter-detection state machine on a small log
  and asserts combat boundaries. Only if time allows; parser unit tests already
  cover the fix.

## P2 — Broken UI (Streamlit 1.55) `[x]`

Streamlit 1.55 removed the `st.experimental_*` namespace; these calls were wrapped
in `try/except` and silently no-op'd, so the features were dead.

- `[x]` **P2.1** Migrated `st.experimental_set_query_params` /
  `st.experimental_get_query_params` → `st.query_params` at:
  - `streamlit_app.py` (read block) + new `_first_query_param()` helper (the modern
    API returns a single `str` for a single-valued key, not a list, so the helper
    normalises both `str` and `list` shapes)
  - `views/summary_sidebar.py` (selection writes)
  - `views/runs.py` (navigation)
- `[x]` **P2.2** Fixed **Runs → Combat Viewer** navigation (the `docs/TODO.md`
  feature). The old code wrote `st.session_state["View"]` (wrong key; the radio is
  keyed `app_view`) and passed `run_id` as the combat id. Verified directly:
  setting a radio's session key after it is instantiated raises
  `StreamlitAPIException` (caught by the surrounding `except` → silent no-op).
  Fix: `runs.py` resolves the selected run to a concrete `combat_id` (its last
  encounter) via `enc_summary`, then writes `?view=Combat Viewer` +
  `?combat=<id>` and `st.rerun()`. The app now reads `?view` **before** the radio
  renders (so it can switch pages) and `?combat` inside the Combat Viewer. URL
  stays shareable.
- `[x]` **P2.3** Fixed `views/combat_detail.py`: `show_replay` can be left
  undefined by the try/except → `NameError`. Initialized `show_replay = False`
  before the guarded block.

> Verified via `streamlit.testing.v1.AppTest`: all six views boot/switch with no
> exceptions, `?view=Runs` deep-link switches pages, and `?combat=<id>` sets the
> selected combat.

## P3 — Dead replay feature `[x]`

- `[x]` **P3.1** `views/combat_detail.py` hardcoded a nonexistent
  `testdata/WoWCombatLog-030526_164213.txt`. Now wired to the real data:
  - `utils/replay_engine.find_log_for_combat(start, end, DATA_DIR)` picks the
    archived session log (filenames encode the `MMDDYY_HHMMSS` session start) whose
    start time is the most recent one at or before the combat began.
  - `utils/replay_engine.decompress_log(gz)` gunzips the `.txt.gz` to a temp
    `.txt` (`@st.cache_resource`), so the ~20 MB gunzip runs once per file/process,
    not on every 3 s rerun.
  - `generate_replay_manuscript` now takes `(start_dt, end_dt, log_file_path)`
    (it only ever used the df for that window) and is `@st.cache_data`, so the
    expensive log scan is cached too.
  - `combat_detail.py` shows a clear `st.info` when no archived log covers the
    encounter. Verified: combat 650 → 20 units / 728 positional events.

## P4 — Performance `[~]`

- `[x]` **P4.1** `utils/data_engine.py`:
  - `compute_totals_summary` was O(N×targets) (a `for t: sub = df[df.target==t]`
    loop). Vectorized to `groupby`/`agg` + `np.where` division. On the live CSV
    (121,833 rows, 316 targets): **2.24 s → ~0.06 s**. Output verified identical
    (frame-equal) to the old loop.
  - `compute_runs` healer pass was a per-event `iterrows` scan; replaced with
    `pd.to_numeric`/`Series.map` (numeric id match wins over name) + a
    `groupby("run_id").first()` on the earliest healer event. Role/spec/class
    maps verified identical (0 diffs).
  - The three slow `DataFrame.apply(axis=1)` dps/hps rows
    (`compute_runs`, `compute_all_encounters_stats`, and the per-target enc_dps)
    replaced with vectorized `np.where` division. `compute_runs` and
    `compute_all_encounters_stats` outputs verified frame-equal across all
    columns.
  - The boss-kill join (O(kills×runs)) was left as-is: kills and runs are both
    small, and its "stamp the first matching run per kill" semantics make a
    vectorized rewrite error-prone for negligible gain.
- `[x]` **P4.2** `views/runs.py`: `load_csv()` was called once per displayed run
  row inside the table loop. Hoisted to a single `_raw = load_csv()` before the
  loop.
- `[~]` **P4.3** `wow-parser.py` — **deferred (see rationale)**:
  `export_csv_from_files` streams each log 3× (encounter detect → parse → boss
  kills). Collapsing to one pass would require materializing the whole log set in
  memory (~250 MB decompressed → ~1–2 GB of Python strings) *or* refactoring the
  core `detect_encounters` state machine into a feedable object — both high-risk
  for a path that is **CLI-only** (`--full-import`/`--export-csv`) and never runs
  in the live web app (which reads the CSV). The 3 streaming passes are the
  memory-safe design, so they are left as-is.
  `run_tail_mode` does maintain a separate *incremental* encounter state machine
  (it flushes each encounter the moment it closes, in real time) rather than
  calling the *batch* `detect_encounters()`. That is intentional, not accidental
  drift: sharing it would mean turning `detect_encounters` into a reusable
  streaming state machine (higher risk, CLI-only benefit). Left as-is.
- `[x]` **P4.4** `st_autorefresh(interval=3000)` triggered a full app rerun every
  3 s. Interval is now a documented constant `LIVE_REFRESH_INTERVAL_MS = 10_000`
  in `config.py` (re-exported via `utils/data_io.py`, consumed in
  `streamlit_app.py`), cutting full-rerun frequency ~3× while still surfacing new
  tail-mode encounters promptly.

## P5 — Cleanup / maintainability `[~]`

- `[x]` **P5.1** `wow-parser.py`: removed the dead "robust" spell-ID extraction
  blocks (HEAL + DAMAGE branches) — each was unconditionally overwritten by the
  correct `rest_parts[8]` read a few lines later. Verified a no-op: 4000 sampled
  real damage/heal lines produce **0** `spell_id` diffs vs. before. Also removed
  the two write-only `src_map = {}` vars (in `export_csv` and
  `export_csv_from_files`).
- `[x]` **P5.2** `runme.sh`: fixed the broken interpreter selection —
  `.venv/bin/python` dangles to the system `python3` (now 3.14) while the packages
  are installed for 3.13, so the old `if -x` check silently picked a broken
  interpreter. Now it only trusts the venv python when it really is 3.13, else
  falls back to a real `python3.13`, and always puts
  `.venv/lib/python3.13/site-packages` on `PYTHONPATH`. Verified: it resolves to
  `/usr/bin/python3.13` and that combo imports `pandas`+`streamlit`. Also removed
  the stale header comments (`extraction.py`, `save-game-cleanup.py`,
  `show-data.py`) and renamed the foreign `TERRA_INV_DIR` override to
  `WOW_BASE_DIR`.
- `[x]` **P5.3** `.gitignore`: added `runme.logs/` and `runme.pids/` (both created
  by `runme.sh`).
- `[x]` **P5.4** `README.md`: fixed `st_aggrid` → `streamlit-aggrid` (matches
  `requirements.txt`) and the `totum/hidden_combats.json` typo →
  `data/sidecar/hidden_combats.json`. Also corrected the now-stale
  "refreshes every 3 seconds" note to the new 10 s / `LIVE_REFRESH_INTERVAL_MS`
  behaviour.
- `[ ]` **P5.5** (defer) `streamlit_app.py` is 535 lines (docstring claims ~150);
  the inlined Totals logic (L252-395) could move to a view module. Optional and
  higher-risk — do only if the others land cleanly.

---

## Verification

- `pytest -q` (P6.1 regression + existing `test_healer_detection.py`).
- `python3.13 -m py_compile` on every edited file.
- `python wow-parser.py --full-import` then spot-check melee rows are > 0.
- Streamlit smoke: launch with the 3.13 site-packages path and click through
  Runs → Combat Viewer.
