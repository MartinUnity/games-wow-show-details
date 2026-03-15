# TODO

- Add support for clicking on a specific encounter in "Views" and having it open in "Combat Viewer" for that particular run so one can see all details of a single encounter battle

1. Implementation details

   - UI (views/runs.py): make run rows or a small action button clickable and call
     `st.experimental_set_query_params(combat_id=<combat_id>)` when activated. Use a clear affordance (link / icon) so users know the row is interactive.
   - Navigation surface (Combat Viewer): on load, read `st.experimental_get_query_params()` (or `st.session_state['selected_combat']`) and, if `combat_id` is present, pre-filter or scroll to that encounter and show the full detailed view. Keep existing behavior when no param is present.
   - Data access: reuse `compute_runs()` / the existing CSV loader to fetch the events for the chosen `combat_id` and render the same charts/tables currently used for live encounters.
   - Behavior: prefer query-params for a sharable URL (e.g., `?combat_id=12345`) so linking/bookmarking a single encounter becomes possible; fallback to `st.session_state` if you need UI-only state without URL changes.
   - Tests / verification: manual test checklist — click a run in "Views", confirm the app navigates to Combat Viewer and displays only that run's events; verify back/refresh keeps selection via query param.

   - Files to touch: `views/runs.py` (click handler), `streamlit_app.py` or `views/combat_viewer.py` (param reading and pre-load logic). Keep changes small and guarded (no behavior change when param absent).
