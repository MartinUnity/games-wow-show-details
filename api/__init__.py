"""
api/ — FastAPI service (Phase 3 of docs/MIGRATION_PLAN.md).

Replaces the Streamlit GUI's data path with a real REST + SSE surface.
Every read endpoint is a thin wrapper over the bare Phase 2 compute
functions; the only new logic is JSON shaping, the SSE watermark loop, and
write endpoints that keep both the JSONL sidecars and the SQLite tables in
sync (single writer during the transition: this API).
"""
