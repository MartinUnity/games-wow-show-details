**Agent Instructions**
- Purpose: provide concise developer/agent guidance for building, testing, linting and coding style in this repository.
- Location: repository root - use these commands relative to the project root.
- Files referenced: `requirements.txt`, `wow-parser.py`, `api/`, `storage/`, `web/`, `tests/`.

- **Repository quick facts:** `wow-parser.py` parses WoW combat logs into a CSV +
  sidecar files; a FastAPI service (`api/`) serves a derived SQLite store
  (`storage/`) to a React/TypeScript SPA (`web/`). The Streamlit app was
  retired in the Phase 5 cutover (2026-09-28).

**Commands**
- Install deps: `pip install -r requirements.txt` (venv at `.venv/`). SPA: `make web-install` (note: `~/.npm` is read-only here — npm uses the repo-local `.npm-cache/`; the Makefile/runme.sh already set `npm_config_cache`).
- Run everything: `./runme.sh start` (parser tail mode + FastAPI on :8000, which serves the built SPA from `web/dist`). SPA dev mode: `make web-build` once, then `./runme.sh start web` (vite :5173, proxies `/api`).
- Parser one-off: `python wow-parser.py --export-csv` or `python wow-parser.py --full-import`.
- Parser interactive / tail mode: `python wow-parser.py` (tails the most recent combat log).
- Data: `make fixture` (synthetic 6-encounter set), `make devdata` (real data from `wow-data/`), `make db` (rebuild SQLite).

- Linting / formatting:

```bash
# ruff is configured via ruff.toml (scope: the new api/ + storage/ packages;
# the legacy parser/utils code predates the config and is left as-is).
ruff check api/ storage/

# black/isort/flake8 remain available for the rest of the tree:
black .
isort .
flake8 .
```

- Running tests:

```bash
# Run full test suite (pytest) — from the repo root, via the venv:
.venv/bin/python -m pytest tests/ -q

# SPA tests:
make web-test   # vitest (web/)

# Run a single test file:
.venv/bin/python -m pytest tests/test_xxx.py -q

# Run a single test function in a file:
.venv/bin/python -m pytest tests/test_xxx.py::test_function_name -q

# Run by test name pattern (fast when you remember the name):
.venv/bin/python -m pytest tests/ -k "substring_of_test_name" -q

# Use -q for concise output, -k to filter by expression, and -x to stop on first failure.
```

**Where to add tests**
- Python: `tests/` (pytest, `test_*.py` / `test_*` conventions). Hermetic by default: build the dataset in `tmp_path` (the `fixture_csv` pattern in `tests/test_api.py` runs the real parser over the synthetic generator `tests/make_fixture_log.py`).
- `tests/testdata/` and the repo-root `parsed_combat_data.csv` are gitignored personal data — tests must not depend on them except via explicit skip-guards (see `tests/test_real_data.py`, `tests/test_parity.py`).
- SPA: vitest + Testing Library under `web/src/` (`make web-test`).

# Run a single test file:
pytest tests/test_xxx.py -q

# Run a single test function in a file:
pytest tests/test_xxx.py::test_function_name -q

# Run by test name pattern (fast when you remember the name):
pytest -k "substring_of_test_name" -q

# Use -q for concise output, -k to filter by expression, and -x to stop on first failure.
```

**Where to add tests**
- Put tests under `tests/` using pytest. Use `test_*.py` file and `test_*` function conventions.
- Use `testdata/` directory present in the repo for read-only fixtures; tests should not modify these files in-place.
- Prefer pytest `tmp_path` fixture for temporary files.

**Code Style Guidelines**
- Follow PEP8/PEP257 as baseline. Keep functions small and single-purpose.
- Use `black` for formatting; set line length 88 (black default) unless project owner requests otherwise.

- Imports
- - Order: stdlib, third-party, local (three groups separated by a blank line).
- - Use absolute imports for project modules: `from utils.data_engine import foo`.
- - `utils/` must stay Streamlit-free and import cleanly from plain Python (pinned by `tests/test_phase2.py`).

- Types
- - Prefer type hints on public functions and methods. Use `from typing import Optional, List, Dict, Any`.
- - Don't insist on full coverage for private/internal helper functions, but add types where they improve readability.

- Naming
- - Modules / files: short, lowercase, underscores (already used in repo: `data_engine.py`, `storage/sync.py`).
- - Functions & variables: snake_case.
- - Classes: PascalCase.
- - Constants: UPPER_SNAKE (e.g., `LOG_DIR`, `DEFAULT_TIMEOUT`).

- Docstrings & comments
- - Every public module, class and function should have a short docstring describing purpose and important parameters/returns.
- - Use triple-quoted strings (PEP257). One-line docstring when trivial; multi-line for details.
- - Comments only for explaining non-obvious behaviour, algorithm choices, or references to external sources.

- Error handling & logging
- - Prefer exceptions to silent failures. Raise specific exceptions (ValueError, FileNotFoundError, RuntimeError) rather than generic Exception when appropriate.
- - Do not swallow exceptions; if you must, log them with context and re-raise or return an explicit error result.
- - Use the `logging` module for library code (module-level logger: `logger = logging.getLogger(__name__)`).
- - In the API, return proper HTTP errors (404/400 via `HTTPException`) and log to `logging`; the SPA surfaces them.

- Configuration & secrets
- - Keep configuration in `config.py` or environment variables. Do NOT commit secrets. Use `os.environ` for runtime secrets.

- Files & IO
- - When reading large logs, stream/iterate rather than load everything into memory when possible.
- - When writing output files (CSV backups), follow the repo pattern: write a timestamped backup rather than clobbering existing files.

- Concurrency & long-running tasks
- - Tail mode in `wow-parser.py` watches log files — keep polling intervals configurable and document the default.
- - Use small, well-documented timeouts and defensively handle partial/incomplete input lines.

**Testing Practices**
- Unit tests: small, isolated, fast. Mock external dependencies (file system, network) with `monkeypatch` or `unittest.mock`.
- API tests: ASGI `TestClient` against `api.main.create_app()` with every `config` path monkeypatched into `tmp_path` (pattern in `tests/test_api.py`). Remember `api.deps` caches frames — call `deps.reset_cache()` around fixture setup/teardown, and pass explicit paths to `storage.sync` functions (their `db_path` defaults bind at import time).
- Parity: `tests/test_parity.py` pins the SQLite-backed API numbers against the pandas/CSV reference for every view — extend it when you change compute functions or endpoints.
- Integration tests: parser runs on the synthetic fixture via the real `wow-parser.py` in `tmp_path`; real-data tests are skip-guarded.
- Avoid relying on network or external APIs in tests.

**Pull Request / Commit Guidance for Agents**
- Create a topic branch per logical change: `agent/<short-desc>`.
- Commit messages: one-line prefix describing why, not how. Example: `fix: handle empty log files when parsing` or `feat: add --export-csv flag to parser`.
- Keep changes small and atomic. Run linters and tests locally before proposing a PR.

**Agent Etiquette / Operational Rules**
- Non-destructive by default: do not modify unrelated files. If repository is dirty, avoid committing until user asks.
- When asked to commit: create a single commit with a concise message and describe the change in the PR body.
- NEVER push force to shared branches without explicit consent.

**Repository-specific notes**
- **Architecture:** `docs/MIGRATION_PLAN.md` documents the completed
  Streamlit → FastAPI + SQLite + SPA migration (Option B, Phases 0–5). Keep
  its session log current for any further architecture work.
- **The parser is the crown jewel** (`wow-parser.py`, stdlib-only) — do not
  rewrite it; the only allowed hooks are the fail-soft SQLite sync calls.
- **CSV is the source of truth.** SQLite (`data/combat.db`) is derived and
  always rebuildable (`make db`); it is the *default* read path once it
  exists (`WOW_USE_SQLITE=0` forces the CSV — used by the parity tests). Never
  let the DB become a second source of truth. Sidecar files under
  `data/sidecar/` are owned by the parser; the API keeps them and the SQLite
  mirror in sync on writes.
- **Real data IS available** in `wow-data/` (gitignored, personal): `make
  devdata` places it where the app expects; `make fixture` is the hermetic
  alternative.
- Primary entrypoints: `wow-parser.py` (parser CLI), `api/main.py` (FastAPI +
  SPA hosting), `web/` (SPA).
- Data: `parsed_combat_data.csv`, `data/combat.db` and the sidecars are
  persisted personal artifacts (gitignored); tests must use `tmp_path` copies.

**Cursor / Copilot Rules**
- Cursor rules location lookup: `.cursor/rules/` or top-level `.cursorrules` — none found in repository.
- GitHub Copilot instructions: look for `.github/copilot-instructions.md` — none found in repository.
- If such files are added later, agents should respect them and include their directives in code generation.

**If you are blocked**
- Check for missing dependencies: run `pip install -r requirements.txt` (and `make web-install` for the SPA).
- Inspect `README.md` and `docs/MIGRATION_PLAN.md` for runtime flags, env vars and expected file locations.
- Ask one targeted question listing the exact file or behavior you need clarification on.

**Post-migration backlog (suggestions)**
1. SPA polish: code-split the 1.4 MB bundle (dynamic view imports); incremental chart append on live-follow instead of refetch.
2. Replay viewer (4.6, `[REAL-DATA]`): port the replay manuscript JSON to a web component; needs archived raw logs in `data/logs/`.
3. Boss Comparison v2: per-boss run-detail drill-down (parity with the retired Streamlit view).

---
Generated for agents operating in this repository. Update this file when tooling or repo layout changes.
