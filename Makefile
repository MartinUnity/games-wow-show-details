# Development data fixtures — this machine has no real combat logs.
# See docs/MIGRATION_PLAN.md (Phase 0). The parser is stdlib-only, so plain
# python3 is fine; pandas/pytest need the venv (see runme.sh).

FIXTURE_LOG_DIR := build/fixture-logs

.PHONY: fixture fixture-large devdata db clean-fixture

# Point the app at the REAL data in wow-data/ (see docs/MIGRATION_PLAN.md):
#  - parsed_combat_data.csv  → repo root (where CSV_PATH expects it)
#  - newest raw log          → data/logs/*.txt.gz (replay feature source)
devdata:
	cp wow-data/parsed_combat_data.csv parsed_combat_data.csv
	mkdir -p data/logs
	for f in wow-data/WoWCombatLog-*.txt; do \
		gzip -c -9 $$f > data/logs/$$(basename $$f).gz; \
	done
	@echo "real data in place: parsed_combat_data.csv + data/logs/"
	@$(MAKE) db

# Small fixture: two character sessions → 6 encounters. Produces
# parsed_combat_data.csv + data/sidecar/boss_kills.jsonl in the repo root.
fixture:
	python3 tests/make_fixture_log.py tests/testdata
	rm -rf $(FIXTURE_LOG_DIR) && mkdir -p $(FIXTURE_LOG_DIR)
	cp tests/testdata/WoWCombatLog-*.txt $(FIXTURE_LOG_DIR)/
	WOW_LOG_DIR=$(abspath $(FIXTURE_LOG_DIR)) python3 wow-parser.py --full-import

# Large fixture: one ~60k-line log (~40 encounters) for performance sanity.
# Overwrites parsed_combat_data.csv with the large dataset.
fixture-large:
	rm -rf $(FIXTURE_LOG_DIR) && mkdir -p $(FIXTURE_LOG_DIR)
	python3 tests/make_fixture_log.py --stress $(FIXTURE_LOG_DIR)
	WOW_LOG_DIR=$(abspath $(FIXTURE_LOG_DIR)) python3 wow-parser.py --full-import

# Rebuild the derived SQLite store from the repo-root CSV + sidecars.
# (Also runs automatically after any parser export: --full-import / --export-csv.)
db:
	python3 -c "from storage.sync import sync_from_csv; from config import CSV_PATH, DB_PATH; print(f'Synced {sync_from_csv(CSV_PATH)} rows -> {DB_PATH}')"

clean-fixture:
	rm -rf $(FIXTURE_LOG_DIR) tests/testdata/WoWCombatLog-*.txt

# ── Phase 4: SPA (web/, Vite + React + TypeScript) ──────────────────────
# ~/.npm is read-only on this machine, so npm uses a repo-local cache.
export npm_config_cache := $(CURDIR)/.npm-cache

.PHONY: web-install web-dev web-build web-test

web-install:
	cd web && npm install

# Dev server on :5173, proxying /api → 127.0.0.1:8000 (run `runme.sh start api` first).
web-dev:
	cd web && npm run dev

# Production bundle → web/dist (served by the API at / once built).
web-build:
	cd web && npm run build

web-test:
	cd web && npm test

