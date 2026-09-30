"""
storage/ — SQLite query layer over the parser's CSV + sidecar files.

Phase 1 of docs/MIGRATION_PLAN.md. The CSV remains the source of truth; the
database is a derived, always-rebuildable store that gives the FastAPI layer
(and optionally Streamlit) fast indexed queries instead of re-reading a
growing CSV.

Modules:
    db       schema + connection helper (stdlib only)
    sync     full rebuild + byte-offset incremental append (stdlib only)
    queries  pandas-returning query functions (requires pandas)

``sync``/``db`` are importable from the stdlib-only parser; ``queries`` is
for the app/server layers.
"""
