"""
views/summary_sidebar.py
────────────────────────
Left-column encounter list with AgGrid selection.
Calls summary_view(df, num_combats) → returns selected_override combat_id or None.
"""

import pandas as pd
import streamlit as st

from utils.data_io import load_hidden, toggle_hidden


def summary_view(df, num_combats=10):
    st.header("Summary")
    # return value: if user selected a combat via AgGrid this function
    # will return that combat_id to allow immediate rendering without a full rerun.
    selected_override = None
    if "combat_id" in df.columns:
        total_combats = int(df["combat_id"].max())
    else:
        total_combats = 0

    # Show total combats and a 'Show Live' button to quickly revert selection
    if "combat_select" not in st.session_state:
        st.session_state["combat_select"] = 0

    sc, sb = st.columns([3, 1])
    sc.markdown(f"- **Total Combats:** {total_combats}")
    if sb.button("Show Live", key="show_live_button"):
        st.session_state["combat_select"] = 0

    if "combat_id" in df.columns:
        agg = (
            df[df["type"] == "damage"]
            .groupby("combat_id")["effective_amount"]
            .sum()
            .rename("total_damage")
            .to_frame()
            .join(df[df["type"] == "heal"].groupby("combat_id")["effective_amount"].sum().rename("total_heal"))
            .fillna(0)
        )

        st.subheader("Combats (most recent first)")
        top = agg.sort_values("combat_id", ascending=False).head(num_combats)
        # Build a small rows list with metadata so we can optionally render a scrollable table
        rows_data = []
        for cid, row in top.iterrows():
            # find first target and duration for this combat (if available)
            target_name = ""
            duration_label = ""
            try:
                sub = df[df["combat_id"] == cid]
                most_dmg_target = None
                longest_target = None
                # Compute most-damaged target
                if not sub.empty and "target" in sub.columns:
                    dmg_sub = sub[sub["type"] == "damage"]
                    dmg_sub = dmg_sub[~dmg_sub["target"].isnull() & (dmg_sub["target"] != "")]
                    if not dmg_sub.empty:
                        grouped = dmg_sub.groupby("target")["effective_amount"].sum()
                        try:
                            player_name = sub["source"].mode().iloc[0] if not sub["source"].mode().empty else None
                        except Exception:
                            player_name = None
                        if player_name in grouped.index:
                            grouped = grouped.drop(player_name)
                        if not grouped.empty:
                            most_dmg_target = str(grouped.idxmax())

                    # fallback: first non-empty target
                    if not most_dmg_target:
                        nonempty = sub[~sub["target"].isnull() & (sub["target"] != "")]
                        if not nonempty.empty:
                            most_dmg_target = str(nonempty.iloc[0]["target"])

                # Compute longest-lived target by appearance span, excluding the player
                if not sub.empty and "target" in sub.columns and "timestamp_dt" in sub.columns:
                    durations = {}
                    try:
                        player_name = sub["source"].mode().iloc[0] if not sub["source"].mode().empty else None
                    except Exception:
                        player_name = None
                    for t, grp in sub[~sub["target"].isnull() & (sub["target"] != "")].groupby("target"):
                        try:
                            if player_name and t == player_name:
                                continue
                            s = grp["timestamp_dt"].min()
                            e = grp["timestamp_dt"].max()
                            if pd.notna(s) and pd.notna(e) and e > s:
                                durations[t] = (e - s).total_seconds()
                        except Exception:
                            continue
                    if durations:
                        longest_target = max(durations, key=durations.get)

                if most_dmg_target:
                    target_name = f"{most_dmg_target}"
                elif longest_target:
                    target_name = longest_target

                if not sub.empty and "timestamp_dt" in sub.columns:
                    s = sub["timestamp_dt"].min()
                    e = sub["timestamp_dt"].max()
                    if pd.notna(s) and pd.notna(e) and e > s:
                        secs = int((e - s).total_seconds())
                        mm = secs // 60
                        ss = secs % 60
                        duration_label = f"[{mm:02d}:{ss:02d}]"
            except Exception:
                target_name = ""
                duration_label = ""

            rows_data.append(
                {
                    "combat_id": int(cid),
                    "player": (
                        str(df[df["combat_id"] == cid]["source"].mode().iloc[0]).split("-")[0]
                        if not df[df["combat_id"] == cid].empty
                        else ""
                    ),
                    "target": target_name,
                    "duration": duration_label,
                    "total_damage": int(row.get("total_damage", 0)),
                    "total_heal": int(row.get("total_heal", 0)),
                }
            )

        # Filter out hidden encounters before building the grid
        _hidden = load_hidden()
        rows_data = [r for r in rows_data if r["combat_id"] not in _hidden]

        # Render a scrollable dataframe and provide a selectbox (use AgGrid for all sizes)
        if True:
            # Try to use AgGrid for a client-side selection experience. Only fall back
            # to the HTML table if the AgGrid package is not available.
            try:
                from st_aggrid import (
                    AgGrid,
                    DataReturnMode,
                    GridOptionsBuilder,
                    GridUpdateMode,
                    JsCode,
                )

                aggrid_ok = True
            except Exception:
                aggrid_ok = False

            if aggrid_ok:
                df_rows = pd.DataFrame(rows_data)
                gb = GridOptionsBuilder.from_dataframe(df_rows)
                # Format damage/heal columns with compact formatter and colored monospace style
                damage_formatter = """
function(params) {
  var v = params.value;
  if (v == null) return '';
  var n = Number(v);
  if (isNaN(n)) return v;
  if (n >= 1000000) return Math.round(n/1000000) + 'M';
  if (n >= 1000) return Math.round(n/1000) + 'K';
  return n.toString();
}
"""
                # Wrap JS in JsCode so st_aggrid recognizes it as executable JS
                try:
                    damage_formatter_js = JsCode(damage_formatter)
                except Exception:
                    damage_formatter_js = damage_formatter
                heal_formatter = damage_formatter
                heal_formatter_js = damage_formatter_js
                gb.configure_column(
                    "total_damage",
                    header_name="Dmg",
                    valueFormatter=damage_formatter_js,
                    cellStyle={"color": "#7CFC00", "fontFamily": "monospace"},
                    width=70,
                    minWidth=70,
                    maxWidth=70,
                    suppressSizeToFit=True,
                )
                gb.configure_column(
                    "total_heal",
                    header_name="Heal",
                    valueFormatter=heal_formatter_js,
                    cellStyle={"color": "#00CED1", "fontFamily": "monospace"},
                    width=70,
                    minWidth=70,
                    maxWidth=70,
                    suppressSizeToFit=True,
                )
                gb.configure_column(
                    "combat_id",
                    header_name="#",
                    width=40,
                    minWidth=40,
                    maxWidth=40,
                    suppressSizeToFit=True,
                )
                gb.configure_column(
                    "duration",
                    header_name="Dur",
                    width=70,
                    minWidth=70,
                    maxWidth=70,
                    suppressSizeToFit=True,
                )
                gb.configure_column(
                    "player",
                    header_name="Player",
                    width=90,
                    minWidth=90,
                    suppressSizeToFit=True,
                )
                gb.configure_selection(selection_mode="single", use_checkbox=False)
                gb.configure_pagination(paginationAutoPageSize=False, paginationPageSize=25)
                gb.configure_default_column(sortable=True, filter=True)
                grid_opts = gb.build()
                try:
                    # Make AgGrid taller so it can show ~20-25 rows without scrolling.
                    # Compute desired rows based on the requested `num_combats` (fall back to 20)
                    desired_rows = min(max(int(st.session_state.get("num_combats", 25)), 20), 25)
                    row_px = 32
                    header_px = 48
                    height_px = min(1000, header_px + desired_rows * row_px)
                    grid_resp = AgGrid(
                        df_rows,
                        gridOptions=grid_opts,
                        height=height_px,
                        fit_columns_on_grid_load=False,
                        data_return_mode=DataReturnMode.FILTERED_AND_SORTED,
                        update_mode="SELECTION_CHANGED",
                        theme="streamlit",
                        allow_unsafe_jscode=True,
                    )
                    selected = grid_resp.get("selected_rows", [])
                    sel_id = None
                    try:
                        # selected can be a list of dicts, a pandas DataFrame, or other.
                        if isinstance(selected, pd.DataFrame):
                            if not selected.empty:
                                sel_row = selected.iloc[0]
                                sel_id = int(sel_row.get("combat_id", 0))
                        elif isinstance(selected, (list, tuple)):
                            if len(selected) > 0 and isinstance(selected[0], dict):
                                sel_id = int(selected[0].get("combat_id", 0))
                        elif isinstance(selected, dict):
                            sel_id = int(selected.get("combat_id", 0))
                    except Exception:
                        sel_id = None

                    if sel_id and st.session_state.get("combat_select", 0) != sel_id:
                        st.session_state["combat_select"] = sel_id
                        selected_override = sel_id
                        try:
                            st.experimental_set_query_params(
                                combat=str(sel_id), num_combats=str(st.session_state.get("num_combats", 10))
                            )
                        except Exception:
                            pass
                except Exception as e:
                    # AgGrid rendered but something in selection handling raised — show a warning
                    st.warning(f"AgGrid selection handling error: {e}")
            else:
                # Fallback: simple selectbox list so selection still works without st_aggrid
                st.warning(
                    "`st_aggrid` not installed — showing a simple selector. Install `st_aggrid` for the interactive grid."
                )
                opts = [f"#{r['combat_id']} — {r.get('target','')} {r.get('duration','')}" for r in rows_data]
                if not opts:
                    st.write("No combats to show")
                else:
                    sel_display = st.selectbox("Select combat", opts, key="fallback_select")
                    try:
                        selected_cid = int(sel_display.split()[0].lstrip("#"))
                        if selected_cid and st.session_state.get("combat_select", 0) != selected_cid:
                            st.session_state["combat_select"] = selected_cid
                            selected_override = selected_cid
                    except Exception:
                        pass
        # Legacy per-row UI removed; AgGrid is used for all sizes when available
    else:
        st.write("No combat segmentation available yet.")
    return selected_override
