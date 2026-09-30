# Option B Migration Plan — Streamlit → FastAPI + SQLite + SPA

> **Purpose of this file:** durable, cross-session plan of record. Update the
> status table and checkbox items as work lands. Each session: read this file,
> pick the next `[ ]` item in the current phase, do it, update statuses, commit.

**Target architecture**

```
WoW Logs ──> wow-parser.py (unchanged; tail mode stays the live ingester)
                │  CSV (parsed_combat_data.csv) + sidecar JSONL files
                ▼
         storage/ (SQLite layer: sync DB from CSV + sidecars, query API)
                │  plain pandas/SQLite, NO streamlit imports
                ▼
         api/ (FastAPI: REST /api/* + SSE /api/events)
                ▼
         web/ (small SPA: tables + time-series charts + SSE live follow)
```

- The parser is the crown jewel and is **not rewritten**; it keeps producing
  CSV + sidecars exactly as today.
- `utils/data_engine.py` compute functions are the aggregation source of truth;
  they get decoupled from Streamlit, not reimplemented.
- Streamlit app is left running (frozen) until the SPA reaches parity (Phase 5),
  then retired.

**Key constraints on this machine**

- **Real data IS available** in `wow-data/` (gitignored): the full-history
  CSV (130k rows / 884 encounters / 7 chars) + the newest raw log (~20
  encounters, 15.7k lines). `make devdata` places it where the app expects
  (repo-root CSV + `data/logs/*.txt.gz` for replay). Anything that needs real
  data is tagged `[REAL-DATA]` — the parity pass and replay can now be tested
  for the ~20 newest encounters; full-history replay still needs more archived
  logs.
- **Synthetic fixture** (Phase 0) remains the hermetic, deterministic test
  dataset: `make fixture` / `make fixture-large`, and `tests/test_schema.py`
  runs the real parser over it in `tmp_path` (no real data required).
- **No subagents** in this harness. This file + small, self-contained phase
  items is the coordination mechanism; keep every item independently shippable.

**Real-data validation (done 2026-09-28):** raw log field layouts verified
identical to the synthetic templates (SPELL_DAMAGE=41, SPELL_HEAL=35,
SWING_DAMAGE=37 rest-fields); `detect_encounters` + `export_csv` run clean on
the real log (20 encounters, melee all > 0). `tests/test_real_data.py`
skip-if-absent guards both the CSV contract and the log field layouts
(Blizzard log-format change detector).

**Known coupling to remove (verified in code)**

- `utils/data_io.py`, `utils/data_engine.py`, `utils/replay_engine.py` all do
  `import streamlit as st` and use `@st.cache_data` / `@st.cache_resource`
  directly. FastAPI cannot use these; they must move behind a caching seam.
- `streamlit_app.py` still contains inlined Totals/banner logic (old P5.5) —
  irrelevant to the new backend, just don't port it; call `compute_totals_summary`.

**CSV schema (from `wow-parser.py:export_csv`) — the storage contract:**

`combat_id, timestamp, event, source, target, spell_name, amount,
effective_amount, type, zone_id, zone_name, spell_id`
(`timestamp` format `%m/%d/%Y %H:%M:%S.%f`)

Sidecars: `boss_kills.jsonl`, `encounter_notes.jsonl`, `hidden_combats.json`,
`healer_spells.json` (all under `data/sidecar/`).

---

## Status

| Phase | Description                          | Status        |
|-------|--------------------------------------|---------------|
| 0     | Synthetic data + schema pinning      | `[x]` done (2026-09-28, incl. real-data validation) |
| 1     | SQLite storage layer                 | `[x]` done (2026-09-28) |
| 2     | Decouple `utils/` from Streamlit     | `[x]` done (2026-09-28) |
| 3     | FastAPI service (REST + SSE)         | `[x]` done (2026-09-28) |
| 4     | Frontend SPA                         | `[x]` done (2026-09-28; replay viewer 4.6 stubbed — `[REAL-DATA]`) |
| 5     | Parity, cutover, retire Streamlit    | `[x]` done (2026-09-28) |

**Migration complete** — only Phase 4.6 (replay viewer, `[REAL-DATA]`) is
left, and it is a feature addition, not a migration debt: the API endpoint
exists and returns 404 with instructions until archived raw logs are dropped
into `data/logs/`.

---

## Phase 0 — Synthetic data & schema pinning

Goal: a repeatable, realistic dataset on this machine so nothing blocks on
real logs. Everything downstream is built and tested against it.

- [x] **0.1** `tests/make_fixture_log.py`: emit a synthetic raw combat log
      (`tests/testdata/synthetic_combat.txt`) with: ≥3 encounters separated by
      >8 s gaps (exercises `detect_encounters` timeout), 2 characters
      (`Name-Server` format), `SPELL_DAMAGE` (41 fields), `SPELL_HEAL`
      (35 fields), `SWING_DAMAGE` (37 fields) lines with correct field
      layouts (copy real line shapes from `tests/test_parser.py`), boss kill
      markers that trip `BossKillTracker`. Field offsets MUST match the map in
      `docs/IMPROVEMENT_PLAN.md` P1.
- [x] **0.2** `Makefile` targets `make fixture` (+ `fixture-large`, + `devdata`
      for the real `wow-data/` set): gunzip-safe run of
      `python wow-parser.py` (single-file path, `export_csv`) against the
      synthetic log into `parsed_combat_data.csv` + sidecars; also generate
      a second, larger synthetic log (~50–100k events) for perf sanity.
- [x] **0.3** `tests/test_schema.py`: assert CSV header equals the 12-column
      contract above; assert parser output on the fixture has melee
      `amount > 0`, heals with `effective_amount = amount - overheal`,
      ≥3 distinct `combat_id`s. This locks the storage contract.
- [x] **0.4** Document the fixture in `docs/MIGRATION_PLAN.md` footer:
      how to regenerate, what it does/doesn't cover (no real zone ids,
      no archived `.txt.gz` logs → replay feature stays `[REAL-DATA]`).

**Exit criteria:** `pytest -q` green on this machine with zero real data;
`parsed_combat_data.csv` + sidecars exist and look plausible.

## Phase 1 — SQLite storage layer

Goal: query layer that replaces repeated `load_csv()` of a growing file.
Parser is untouched; SQLite is a *derived* store, rebuilt/synced from the
CSV + sidecars (CSV stays the source of truth — cheap insurance).

- [x] **1.1** `storage/__init__.py`, `storage/db.py`: schema
      (`events` table mirroring the 12 CSV columns with `timestamp_dt`
      computed column or index-friendly TEXT; indexes on `combat_id`,
      `(combat_id, timestamp)`, `source`, `type`; plus `boss_kills`,
      `notes`, `hidden_combats`, `healer_spells` tables from sidecars).
      `data/combat.db` path in `config.py`.
- [x] **1.2** `storage/sync.py`: `sync_from_csv(csv_path, db_path)` —
      (watermark is byte-offset based — simpler and exact for an
      append-only CSV; shrink/replace → full rebuild).
      idempotent full rebuild (transactional: build in temp file, swap) and
      `incremental_append(db_path, csv_path)` keyed on a stored
      `(mtime, row_count, max combat_id, last timestamp)` watermark for the
      tail-mode case. Rebuild on any inconsistency.
- [x] **1.3** Hook: `wow-parser.py` tail-mode flush calls
      `storage.sync.incremental_append` after each CSV append (guarded,
      non-fatal if DB missing — parser must never break because of the DB).
      `--full-import` / `--export-csv` trigger a full rebuild.
- [x] **1.4** `storage/queries.py`: thin pandas-returning functions
      (`load_events(combat_id=None, character=None)`,
      `encounter_summary()`, `latest_combat_id()`, …) — the only API the
      FastAPI layer and (optionally) Streamlit will use.
- [x] **1.5** Tests: `tests/test_storage.py` — sync idempotency, incremental
      append after a simulated tail flush, shrink → rebuild, query results
      frame-equal to the old `load_csv()` results on the fixture.

**Exit criteria (met 2026-09-28):** repeated full syncs produce identical DB
*contents* (the `meta.synced_at` wall-clock value is the only exception);
incremental append catches up a simulated tail flush; queries match
pandas-from-CSV. Real-data smoke: 130k rows / 883 encounters synced, encounter
summary ≈ 55 ms vs ≈ 1.8 s for a full pandas load. `make db` rebuilds the DB
from the repo-root CSV; `make devdata` runs it automatically.

**Deviations from the plan text (deliberate):**
- Timestamps stored as original TEXT + `ts` REAL (UTC epoch via `timegm` —
  deterministic across machines); `timestamp_dt` is computed in
  `queries.load_events`, matching `data_io.load_csv` output exactly.
- `queries.load_events` returns `""` for empty fields; `pd.read_csv` (old
  `load_csv`) returns `NaN`. The one documented frame difference.

## Phase 2 — Decouple `utils/` from Streamlit

Goal: `utils/data_engine.py` + `utils/data_io.py` importable without
Streamlit installed, callable from FastAPI. Streamlit app still works.

- [x] **2.1** Introduce a caching seam: bare compute functions
      (`compute_runs`, `compute_totals_summary`, `compute_all_encounters_stats`,
      `combat_time_series`, `spell_aggregates`, `load_csv`,
      `compute_character_counts`) with **no** decorators; move
      `@st.cache_data` to a thin `utils/st_compat.py` wrapper module that
      the Streamlit views import instead. TTLs preserved.
- [x] **2.2** Same for `utils/replay_engine.py`
      (`@st.cache_resource`/`@st.cache_data` → plain functions; caching at
      call site per framework). `utils/export_share.py` already guards its
      `import streamlit` — keep that pattern as the reference.
- [x] **2.3** Point `load_csv` at `storage.queries.load_events`
      (feature flag / env `WOW_USE_SQLITE=1`) so both UIs can share Phase 1.
      Keep CSV fallback.
- [x] **2.4** Verify: `tests/test_phase2.py` — subprocess import with
      `sys.modules['streamlit'] = None`, bare compute results on the
      fixture, flag-vs-CSV frame equality, `AppTest` run of
      `streamlit_app.py` (no exceptions, also with `WOW_USE_SQLITE=1`).

**Exit criteria (met 2026-09-28):** the only module-level
`import streamlit` left under `utils/` is `utils/st_compat.py` (the single
caching seam); `views/` + `streamlit_app.py` still import Streamlit
(they get replaced in Phase 5). `utils/export_share.py` keeps its guarded
function-level import (the reference pattern). 37 tests green.

**Deviations (deliberate):**
- `storage.queries.load_events` now selects exactly the 12 CSV columns (+
  computed `timestamp_dt`); a `load_events_raw` variant exposes the internal
  `rowid`/`ts` columns for tooling.
- The flag path aligns SQLite `""` → `NaN` so downstream filters behave
  identically to the `pd.read_csv` path.

## Phase 3 — FastAPI service

Goal: the real `/api` surface + live event stream that Streamlit never had.
Replace the unused `Flask` in `requirements.txt` with
`fastapi` + `uvicorn` (+ `sse-starlette`).

- [x] **3.1** `api/main.py` (app factory `create_app()`), `api/deps.py`
      (live config, shared event-frame memo, sidecar+DB write helpers,
      watermark, TTL memo). `runme.sh` gains an `api` target
      (uvicorn, 127.0.0.1:8000, `./runme.sh start api`).
      `requirements.txt`: `Flask` (unused) removed; `fastapi`, `uvicorn`,
      `sse-starlette`, `httpx` added.
- [x] **3.2** Read endpoints (each = thin wrapper over Phase 1/2 functions;
      return JSON, params mirror the Streamlit view options):
      - `GET /api/encounters?character=&hidden=exclude` (list w/ duration,
        dps — from `compute_all_encounters_stats`)
      - `GET /api/combat/{id}/summary` (per-target damage, top abilities —
        `spell_aggregates`)
      - `GET /api/combat/{id}/timeline?resample_s=&smooth_s=&source=`
        (`combat_time_series` — the live-follow chart payload)
      - `GET /api/runs?gap_minutes=` (`compute_runs`)
      - `GET /api/totals?character=` (`compute_totals_summary`)
      - `GET /api/comparison/characters`, `GET /api/comparison/bosses`
      - `GET /api/characters` (character list + counts, player-like filter)
      - `GET /api/health` (DB age, row count, last combat ts)

      Perf notes (real 130k-row data, `WOW_USE_SQLITE=1`): full frame load
      ~1.9 s once per data generation (memoized in `deps.get_events`,
      keyed on DB watermark / CSV mtime); compute fns take an optional
      pre-loaded `df=` param so endpoints share one load; warm responses
      60–230 ms (encounters 77 ms, combat summary 75 ms, timeline 55 ms).
- [x] **3.3** Write endpoints (the "limited /api postings" pain, solved):
      - `POST /api/encounters/{id}/note` (body `{note}`; empty deletes)
      - `POST /api/encounters/{id}/hide` / `DELETE` (toggle hidden)
      - writes go through `storage` (SQLite tables) and keep the JSONL
        sidecars in sync during transition (single writer: the API).
- [x] **3.4** `GET /api/events` (SSE): watch DB watermark
      (`max(combat_id)`, row count) on a 1–2 s poll; emit
      `encounter_closed {combat_id, summary}` / `data_updated` events.
      This replaces `st_autorefresh`'s full rerun with a targeted push.
      `max_polls=` bounds the stream (0 = unbounded) for curl/test use; poll
      interval via `WOW_SSE_POLL_S` (default 1.5 s).
- [x] **3.5** Export endpoints: `GET /api/combat/{id}/export.csv`,
      `GET /api/combat/{id}/export.gif` (port `utils/export_share.py`
      byte generators; they're already framework-free).
- [x] **3.6** API tests (`tests/test_api.py`, httpx `ASGITransport` against
      the fixture DB): one test per endpoint group, write-then-read round
      trips for note/hide, SSE emits on simulated tail append. 14 tests;
      hermetic (fixture CSV → tmp DB, all `config` paths monkeypatched).
      Note: `storage.sync.incremental_append`'s `db_path` default is bound
      at import time — tests must pass the patched path explicitly.
- [x] **3.7** `[REAL-DATA]` Replay: `GET /api/combat/{id}/replay`
      (`replay_engine.generate_replay_manuscript` — needs archived
      `data/logs/*.txt.gz`; defer until real data exists, stub 404 otherwise).

**Exit criteria:** full REST+SSE surface exercisable with `curl` against
fixture data; all API tests green; Streamlit app untouched and still green.

## Phase 4 — Frontend SPA

Goal: replace `views/*.py` (~2,900 lines) with a small SPA. Keep it boring:
**Vite + React + TypeScript**, TanStack Table (aggrid-web-react if we want
drop-in parity), ECharts for the time-series (incremental updates over SSE —
the main UX win vs Streamlit's re-render).

- [x] **4.1** Scaffold `web/` (Vite React-TS), dev proxy `/api` → :8000,
      `api/client.ts` typed client generated/hand-written from Phase 3
      shapes.
- [x] **4.2** Shell: sidebar view switcher + character selector +
      "Follow live" toggle (SSE subscription lives here, not per-view).
- [x] **4.3** Views in Streamlit-parity order (one PR-sized chunk each,
      compare side-by-side with the running Streamlit app on real data
      when available):
      - [x] Combat Viewer (timeline chart w/ resample/smooth controls,
            summary panel, top abilities, note + hide actions)
      - [x] Runs
      - [x] All Encounters
      - [x] Totals
      - [x] Character Comparison
      - [x] Boss Comparison (v1: table + per-boss DPS bars; the Streamlit
            per-boss run-detail drill-down is deferred to the Phase 5 parity
            pass)
- [x] **4.4** Live-follow: on `encounter_closed` SSE event → auto-select
      latest. (Incremental chart *append* not done — the SPA refetches the
      selected combat on switch; a single-combat fetch is ~100 ms warm,
      acceptable. Note in the Phase 5 polish list.)
- [x] **4.5** Deep links: `?view=…&combat=…` (parity with the Streamlit
      query-param scheme; shareable URLs keep working).
- [ ] **4.6** `[REAL-DATA]` Replay viewer (port `render_replay_viewer`
      manuscript JSON to a web component) — last, needs real logs. Stub:
      SPA shows "Replay available when archived logs exist" and handles the
      404 from `/api/combat/{id}/replay`.

**Exit criteria:** every Streamlit view has a working SPA equivalent on
fixture data; live-follow updates without full page refetch.

## Phase 5 — Parity, cutover, retire Streamlit

- [x] **5.1** `[REAL-DATA]` Side-by-side parity pass on real logs:
      numbers in SPA vs Streamlit for encounters/runs/totals (diff the
      JSON payloads, not pixels). → `tests/test_parity.py` (10 tests):
      frame identity (SQLite ≡ pandas/CSV, row-for-row) + per-view payload
      parity (encounters, runs, totals, comparison, combat summary/
      timeline/events, abilities) on the fixture, plus a full-scale real-data
      run (130,008 rows / 883 encounters — all green first try; the only
      divergence found was test-side representation, no number mismatch).
- [x] **5.2** Serve `web/dist` statically from FastAPI (`StaticFiles`);
      `runme.sh start` now = `parser` + `api` only (single-process
      production path at :8000); `web` target kept for vite dev mode.
- [x] **5.3** `README.md` rewritten for the new architecture (structure,
      commands, config, data format, replay logs); `AGENTS.md` updated
      (post-cutover commands, testing practice, repo notes, backlog).
      `docs/IMPROVEMENT_PLAN.md` + `docs/TODO.md` (Streamlit-era) bannered
      as superseded.
- [x] **5.4** Deleted `streamlit_app.py`, `views/`, `utils/st_compat.py`,
      `utils.export_share.register_share_ui`; dropped `streamlit*` from
      `requirements.txt`; removed the two Streamlit-dependent tests
      (`st_compat` wrappers, AppTest regression) from `tests/test_phase2.py`;
      zero `import streamlit` left in the tree; 62 pytest + 1 vitest green.
- [x] **5.5** Sidecar end-state decided: **keep dual-write.** The JSONL/
      JSON sidecars stay the canonical sidecar store, owned by the parser;
      SQLite mirrors them (derived, rebuildable). The API is the single
      writer for SPA-initiated changes and updates both stores atomically
      enough for this app. Documented in `README.md` + `AGENTS.md`.

---

## Risks / notes

- **Synthetic data fidelity:** field layouts come from the P1-verified map
  in `IMPROVEMENT_PLAN.md`; if the real parser rejects synthetic lines, fix
  the generator, not the parser.
- **SQLite vs CSV drift:** CSV stays canonical; DB is always rebuildable
  (`make fixture` / `--full-import`). Never let the DB become a second
  source of truth for parser output.
- **Tail-mode coupling:** the Phase 1.3 hook is the only parser change in
  the whole migration; it must be fail-soft.
- **Scope guard:** replay (3.7, 4.6) and anything tagged `[REAL-DATA]` is
  explicitly deferred until real logs/CSV are on this machine.

## Session log

| Date       | Session focus            | Result / next up                     |
|------------|--------------------------|--------------------------------------|
| 2026-09-28 | Plan written             | —                                    |
| 2026-09-28 | Phase 0 complete         | Synthetic fixture (6 combats, all close reasons) + hermetic schema tests + Makefile; real data arrived in `wow-data/`, validated (layouts 41/35/37, 20 encounters detected), `make devdata` + `test_real_data.py` added; 29 tests green. |
| 2026-09-28 | Phase 1 complete         | `storage/` package (db/sync/queries), byte-offset incremental watermark, fail-soft parser hooks (tail flush + full-import/export), `make db`, 4 new tests (33 total, green). Real-data smoke: 130k rows, encounter summary 55 ms vs 1.8 s pandas. |
| 2026-09-28 | Phase 2 complete         | `utils/` Streamlit-free: bare compute fns in data_io/data_engine/replay_engine, single caching seam `utils/st_compat.py` (original TTLs), 7 call sites switched, `WOW_USE_SQLITE=1` flag on `load_csv` (frame-verified vs CSV path), `AppTest` regression in-suite; 38 tests green, app runs exception-free on both data paths. **Next: Phase 3 — FastAPI service (REST + SSE)** |
| 2026-09-28 | Phase 3 complete         | `api/` package: FastAPI app (`create_app`) with full REST surface (health, characters, encounters, combat summary/timeline, runs, totals, comparison chars/bosses, note/hide writes syncing JSONL+SQLite, export CSV/GIF, replay stub) + SSE `/api/events` (watermark poll, `encounter_closed`/`data_updated`, `max_polls` bound). `runme.sh` `api` target; `Flask`→`fastapi`/`uvicorn`/`sse-starlette`/`httpx`. 14 hermetic API tests; 52 total green. Real-data smoke: warm endpoints 60–230 ms. Also fixed pre-existing test-ordering bug in `test_healer_detection` (data_engine now resolves `load_healer_spells` via module attr). **Next: Phase 4 — frontend SPA** |
| 2026-09-28 | Phase 4 SPA implemented  | `web/` Vite+React+TS SPA: typed `api/client.ts`, shell (sidebar nav, character/resample/smooth, follow-live toggle, encounter count, SSE via `LiveContext`), 6 views (Combat Viewer w/ TTK/overkill metrics, ability tables, spell-filtered timeline, rotation timeline, recent events, note/hide/exports; Runs; All Encounters w/ hour histogram + ability breakdown; Totals; Character Comparison; Boss Comparison v1), deep links `?view=…&combat=…` (custom `useQueryState`, no router), TanStack Table + ECharts. API additions: `/api/combat/{id}/events`, `…/abilities` (all-encounters), summary `ttk_s`/`overkill_pct`/`target_name`/`note`, timeline `spell=` filter, `character_options` counts, `count_encounters` in `/api/health`; `web/dist` static mount (ahead of 5.2). `ruff.toml` (new+storage packages lint-clean); `runme.sh` `web` target; Makefile `web-*` targets; `.npm-cache` gitignored. 54 pytest + 1 vitest green; `tsc -b && vite build` clean (1.4 MB bundle — code-split backlog). Real-data smoke: all new endpoints + SPA hosting + vite `/api` proxy verified. **Next: Phase 5 — parity pass on real data, cutover, retire Streamlit** |
| 2026-09-28 | **Phase 5 complete — migration done** | **Parity (5.1):** new `tests/test_parity.py` (10 tests) diffs API (SQLite) JSON payloads vs the pandas/CSV reference for every view, incl. full-scale real data (130k rows, 883 encounters) — zero number mismatches. **Cutover (5.2–5.4):** `streamlit_app.py` + `views/` + `utils/st_compat.py` + `register_share_ui` deleted; `streamlit*` out of requirements; zero `import streamlit` left; `runme.sh start` = parser + api only (API serves `web/dist` at :8000). **Docs (5.3/5.5):** README rewritten; AGENTS.md updated; sidecar end-state decided (dual-write: sidecars canonical, SQLite mirror). 62 pytest + 1 vitest green; `tsc -b && vite build` clean. **SQLite flipped to the default read path** (parity-proven): `WOW_USE_SQLITE=0` now forces the CSV; new `test_sqlite_is_default_when_db_exists` guards it. **Remaining (backlog, not migration debt):** 4.6 replay viewer `[REAL-DATA]`, SPA bundle code-split + incremental live append, Boss Comparison v2 drill-down. |
