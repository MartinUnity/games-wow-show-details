**Agent Instructions**
- Purpose: provide concise developer/agent guidance for building, testing, linting and coding style in this repository.
- Location: repository root - use these commands relative to the project root.
- Files referenced: `requirements.txt`, `wow-parser.py`, `streamlit_app.py`, `testdata/`.

- **Repository quick facts:** small Python Streamlit app that parses WoW combat logs and exposes a web UI; main scripts are `wow-parser.py` and `streamlit_app.py`.

**Commands**
- Install deps: `pip install -r requirements.txt` (use a venv).
- Run web UI: `streamlit run streamlit_app.py`.
- Parser one-off: `python wow-parser.py --export-csv` or `python wow-parser.py --full-import`.
- Parser interactive / tail mode: `python wow-parser.py` (tails the most recent combat log).

- Linting / formatting (recommended tools; not currently part of repo):

```bash
# install formatter/linter (recommended):
apt-get install -y python3-pip # or use your OS package manager
pip install black ruff isort flake8

# format repository with black/isort:
black .
isort .

# quick lint with ruff/flake8 (ruff is faster):
ruff check .
flake8 .
```

- Running tests (this repo contains no formal tests folder by default, but follow this guidance):

```bash
# Run full test suite (pytest):
pytest

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
- - Use absolute imports for project modules: `from views.runs import foo` not relative unless inside a package where relative is clearer.
- - Keep imports at top of file; avoid heavy imports inside module scope only if they are expensive — then document why.

- Types
- - Prefer type hints on public functions and methods. Use `from typing import Optional, List, Dict, Any`.
- - Don't insist on full coverage for private/internal helper functions, but add types where they improve readability.

- Naming
- - Modules / files: short, lowercase, underscores (already used in repo: `data_engine.py`, `streamlit_app.py`).
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
- - For Streamlit UI code, prefer `st.error()`/`st.warning()` for user-facing errors and still log to `logging` for diagnostics.

- Configuration & secrets
- - Keep configuration in `config.py` or environment variables. Do NOT commit secrets. Use `os.environ` for runtime secrets.

- Files & IO
- - When reading large logs, stream/iterate rather than load everything into memory when possible.
- - When writing output files (CSV backups), follow the repo pattern: write a timestamped backup rather than clobbering existing files.

- Concurrency & long-running tasks
- - Tail mode in `wow-parser.py` watches log files — keep polling intervals configurable and document the default.
- - Use small, well-documented timeouts and defensively handle partial/incomplete input lines.

**Testing Practices**
- Unit tests: small, isolated, fast. Mock external dependencies (file system, streamlit runtime) with `monkeypatch` or `unittest.mock`.
- Integration tests: if you need to run parser on realistic logs, use trimmed fixtures in `testdata/` and assert key outputs only.
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
- Primary entrypoints: `wow-parser.py` (parser CLI) and `streamlit_app.py` (UI).
- Data: `parsed_combat_data.csv` and `hidden_combats.json` are persisted artifacts; tests should use copies, not the live file.
- Test fixtures: use `testdata/` files shipped with repo for deterministic parsing tests.

**Cursor / Copilot Rules**
- Cursor rules location lookup: `.cursor/rules/` or top-level `.cursorrules` — none found in repository.
- GitHub Copilot instructions: look for `.github/copilot-instructions.md` — none found in repository.
- If such files are added later, agents should respect them and include their directives in code generation.

**If you are blocked**
- Check for missing dependencies: run `pip install -r requirements.txt`.
- Inspect `README.md` and `README` sections for runtime flags and expected file locations.
- Ask one targeted question listing the exact file or behavior you need clarification on.

**Next Steps (suggested for automation agents)**
1. Run `pip install -r requirements.txt` in a fresh venv and launch `streamlit run streamlit_app.py` to validate runtime.
2. Add `pytest` and a small smoke test under `tests/` that parses a `testdata/` file and asserts a few columns.
3. Optionally add `pyproject.toml` with `black`/`ruff`/`isort` configuration for consistent formatting.

---
Generated for agents operating in this repository. Update this file when tooling or repo layout changes.
