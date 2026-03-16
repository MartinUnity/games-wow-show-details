"""
views/runs.py
─────────────
Runs page: encounters grouped by zone + time gap into sessions,
with per-run summaries, boss-kill badges, and participant breakdown.
"""

import altair as alt
import pandas as pd
import streamlit as st

from utils.data_engine import compute_runs, spell_aggregates
from utils.data_io import (
    DEFAULT_TOP_N_ABILITIES,
    _fmt_compact_amount,
    load_csv,
    load_healer_spells,
)


@st.dialog("Combat Preview", width="large")
def _show_combat_dialog(combat_id: int) -> None:
    """Modal dialog showing the right-hand combat detail for a given combat_id."""
    from views.combat_detail import combat_detail_view

    df = load_csv()
    if df is None or df.empty:
        st.warning("No parsed data available.")
        return
    if combat_id not in df["combat_id"].values:
        st.warning(f"Combat #{combat_id} not found in loaded data.")
        return
    combat_detail_view(df, combat_id, resample_s=2, smooth_s=3, top_n=DEFAULT_TOP_N_ABILITIES)


def runs_view():
    """Runs page: encounters grouped by zone into runs, with per-run summaries."""
    st.header("Runs")

    gap_min = st.sidebar.slider(
        "Run gap threshold (min)",
        min_value=5,
        max_value=60,
        value=20,
        step=5,
        key="runs_gap_min",
        help="A new run is created when the gap between encounters exceeds this value.",
    )

    runs_df, enc_summary = compute_runs(gap_minutes=gap_min)

    if runs_df.empty:
        st.info(
            "No zone data found — re-import your log with the updated parser to stamp zone info.\n\n"
            "Run: `python wow-parser.py --full-import`"
        )
        return

    # ── Headline ──────────────────────────────────────────────────────────
    has_boss_col = "has_boss_kill" in runs_df.columns
    boss_run_count = int(runs_df["has_boss_kill"].sum()) if has_boss_col else 0
    unique_zones = runs_df["zone_name"].nunique()
    total_runs = len(runs_df)
    total_enc = int(runs_df["n_encounters"].sum())
    # Additional top-line metrics: total damage/heal and average DPS/HPS (compact)
    total_damage = int(runs_df["total_damage"].sum()) if "total_damage" in runs_df.columns else 0
    total_heal = int(runs_df["total_heal"].sum()) if "total_heal" in runs_df.columns else 0
    avg_dps = round(runs_df["avg_dps"].mean(), 1) if "avg_dps" in runs_df.columns else 0.0
    avg_hps = round(runs_df["avg_hps"].mean(), 1) if "avg_hps" in runs_df.columns else 0.0

    # Show damage before healing to match other pages, with color-coding
    h1, h2, h3, h4, h5, h6, h7, h8 = st.columns(8)

    # small helper to render a colored metric (value on top, label below)
    def _col_metric(col, label, value_html, value_color="#CCCCCC"):
        col.markdown(
            f"<div style='line-height:1.05'>"
            f"<div style='font-size:2.1rem;color:{value_color};font-weight:700'>{value_html}</div>"
            f"<div style='font-size:0.78rem;color:#999;margin-top:2px'>{label}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    _col_metric(h1, "Total Runs", f"{total_runs}")
    _col_metric(h2, "Boss Runs", f"{boss_run_count}")
    _col_metric(h3, "Unique Zones", f"{unique_zones}")
    _col_metric(h4, "Total Encounters", f"{total_enc}")

    dmg_html = _fmt_compact_amount(total_damage).replace("&nbsp;", " ").replace("<strong>", "").replace("</strong>", "")
    heal_html = _fmt_compact_amount(total_heal).replace("&nbsp;", " ").replace("<strong>", "").replace("</strong>", "")
    _col_metric(h5, "Total Damage", dmg_html, value_color="#7CFC00")
    _col_metric(h6, "Total Heal", heal_html, value_color="#00CED1")
    _col_metric(h7, "Avg DPS", f"{avg_dps:.1f}", value_color="#7CFC00")
    _col_metric(h8, "Avg HPS", f"{avg_hps:.1f}", value_color="#00CED1")

    st.markdown("---")

    # ── Filters ───────────────────────────────────────────────────────────
    boss_only = False
    if has_boss_col and boss_run_count > 0:
        boss_only = st.sidebar.checkbox(
            "Boss kills only",
            value=True,
            key="runs_boss_only",
            help="Only show runs that ended with a boss kill (ENCOUNTER_END with kill_flag=1).",
        )
    filtered_runs = runs_df[runs_df["has_boss_kill"]] if (has_boss_col and boss_only) else runs_df
    all_zones = sorted(filtered_runs["zone_name"].unique().tolist())
    selected_zones = st.multiselect(
        "Filter by zone",
        options=all_zones,
        default=[],
        key="runs_zone_filter",
        placeholder="All zones",
    )
    display_runs = filtered_runs[filtered_runs["zone_name"].isin(selected_zones)] if selected_zones else filtered_runs

    # ── Runs table ────────────────────────────────────────────────────────
    def _dur_label(s):
        s = int(s)
        return f"{s // 60}:{s % 60:02d}"

    table_rows = []
    for _, r in display_runs.iterrows():
        # Determine a short player/character name for this run (split on '-' like other views)
        try:
            _cids = enc_summary[enc_summary["run_id"] == int(r["run_id"])]["combat_id"].tolist()
            _raw = load_csv()
            if _raw is not None and not _raw.empty and _cids:
                _sub = _raw[_raw["combat_id"].isin(_cids)]
                try:
                    _player_mode = _sub["source"].mode()
                    player_short = str(_player_mode.iloc[0]).split("-")[0] if not _player_mode.empty else ""
                except Exception:
                    player_short = ""
            else:
                player_short = ""
        except Exception:
            player_short = ""
        # Prefer precomputed run_role from compute_runs() (fast). Fall back to
        # default DPS when not present. compute_runs() will already have
        # scanned the sidecar and CSV to stamp `run_role` and `is_healer_run`.
        run_role = str(r.get("run_role", "DPS")) if "run_role" in r else "DPS"
        run_spec = str(r.get("run_spec", "")) if "run_spec" in r else ""
        run_class = str(r.get("run_class", "")) if "run_class" in r else ""

        # run_spec/run_class are precomputed in compute_runs(); fall back to
        # deriving class from spec if present for older runs.
        if not run_class and run_spec:
            spec_to_class = {
                "Mistweaver": "Monk",
                "Restoration_Shaman": "Shaman",
                "Restoration_Druid": "Druid",
                "Holy_Paladin": "Paladin",
                "Preservation_Evoker": "Evoker",
                "Holy_Priest": "Priest",
                "Discipline_Priest": "Priest",
            }
            run_class = spec_to_class.get(run_spec, "")

        table_rows.append(
            {
                "run_id": int(r["run_id"]),
                "Zone": str(r["zone_name"]),
                "Player": player_short,
                "PlayerClass": run_class,
                "Role": run_role,
                "Boss": str(r.get("boss_names", "")) or "",
                "Date": r["start_dt"].strftime("%m/%d %H:%M") if pd.notna(r["start_dt"]) else "",
                "Enc": int(r["n_encounters"]),
                "Duration": _dur_label(r["duration_s"]),
                "Dmg": int(r["total_damage"]),
                "Heal": int(r["total_heal"]),
                "Avg DPS": round(r["avg_dps"], 1),
                "Avg HPS": round(r["avg_hps"], 1),
            }
        )
    table_df = pd.DataFrame(table_rows)

    selected_run_id = None
    if not table_df.empty:
        try:
            from st_aggrid import (
                AgGrid,
                DataReturnMode,
                GridOptionsBuilder,
                GridUpdateMode,
                JsCode,
            )

            compact_fmt = JsCode(
                """
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
            )

            # JS function to color Player name based on detected class
            player_style_js = JsCode(
                """
            function(params) {
              var cls = (params.data && params.data.PlayerClass) ? params.data.PlayerClass : '';
              var map = {
                'Druid': '#FF7C0A',
                'Evoker': '#33937F',
                'Monk': '#00FF98',
                'Paladin': '#F48CBA',
                'Priest': '#FFFFFF',
                'Shaman': '#0070DD'
              };
              var c = map[cls] || '#9D89C9';
              return {color: c, fontWeight: 'bold'};
            }
            """
            )

            gb = GridOptionsBuilder.from_dataframe(table_df)
            gb.configure_column("run_id", header_name="#", width=40, minWidth=40, maxWidth=40, suppressSizeToFit=True)
            gb.configure_column(
                "Zone", width=160, minWidth=110, maxWidth=400, cellStyle={"color": "#7CFC00", "fontWeight": "bold"}
            )
            gb.configure_column(
                "Player",
                header_name="Player",
                width=100,
                minWidth=80,
                maxWidth=140,
                suppressSizeToFit=True,
                cellStyle=player_style_js,
            )
            # role/class helper columns
            gb.configure_column("Role", header_name="Role", width=90, minWidth=80, suppressSizeToFit=True)
            gb.configure_column("PlayerClass", header_name="Class", hide=True)
            gb.configure_column("Boss", width=150, minWidth=100, cellStyle={"color": "#FFD700", "fontStyle": "italic"})
            gb.configure_column("Date", width=110, minWidth=90, maxWidth=120)
            gb.configure_column("Enc", width=55, minWidth=65, maxWidth=75, suppressSizeToFit=True)
            gb.configure_column("Duration", width=80, minWidth=100, maxWidth=150, suppressSizeToFit=True)
            gb.configure_column(
                "Dmg",
                width=70,
                minWidth=60,
                maxWidth=80,
                suppressSizeToFit=True,
                valueFormatter=compact_fmt,
                cellStyle={"color": "#7CFC00", "fontFamily": "monospace"},
            )
            gb.configure_column(
                "Heal",
                width=70,
                minWidth=60,
                maxWidth=80,
                suppressSizeToFit=True,
                valueFormatter=compact_fmt,
                cellStyle={"color": "#00CED1", "fontFamily": "monospace"},
            )
            gb.configure_column("Avg DPS", width=120, minWidth=125, maxWidth=150, suppressSizeToFit=True)
            gb.configure_column("Avg HPS", width=120, minWidth=125, maxWidth=150, suppressSizeToFit=True)
            gb.configure_selection(selection_mode="single", use_checkbox=False)
            gb.configure_default_column(sortable=True, filter=True)
            grid_opts = gb.build()

            row_px, header_px = 32, 48
            grid_h = min(900, header_px + row_px * max(10, min(20, len(table_df))))

            resp = AgGrid(
                table_df,
                gridOptions=grid_opts,
                height=grid_h,
                fit_columns_on_grid_load=False,
                data_return_mode=DataReturnMode.FILTERED_AND_SORTED,
                update_mode="SELECTION_CHANGED",
                theme="streamlit",
                allow_unsafe_jscode=True,
            )
            sel = resp.get("selected_rows", [])
            if isinstance(sel, pd.DataFrame) and not sel.empty:
                selected_run_id = int(sel.iloc[0]["run_id"])
            elif isinstance(sel, list) and sel:
                selected_run_id = int(sel[0]["run_id"])
            # If a run is selected, navigate to Combat Viewer and pass the
            # selected combat id via query params so the Combat Viewer can
            # pre-load that encounter. We also set the sidebar view state to
            # "Combat Viewer" so the app switches pages immediately.
            if selected_run_id is not None:
                try:
                    st.experimental_set_query_params(combat=str(selected_run_id))
                    # The radio in streamlit_app.py stores its selection under
                    # the session state key 'View' (the label), so set that
                    # value to switch pages programmatically.
                    st.session_state["View"] = "Combat Viewer"
                    st.experimental_rerun()
                except Exception:
                    # If navigation fails for any reason, fall back to leaving
                    # the selection in-place without navigating.
                    pass
        except Exception:
            # Fallback selectbox
            run_options = [
                (f"#{int(r.run_id)} — {r.Zone}" + (f" [{r.Boss}]" if r.Boss else "") + f" — {r.Date}")
                for r in table_df.itertuples()
            ]
            sel_str = st.selectbox("Select a run", run_options, key="runs_fallback_sel")
            try:
                selected_run_id = int(sel_str.split(" ")[0].lstrip("#"))
            except Exception:
                selected_run_id = None

    # ── Per-zone damage bar chart ─────────────────────────────────────────
    if not runs_df.empty:
        zone_agg = (
            runs_df.groupby("zone_name")
            .agg(runs=("run_id", "count"), total_damage=("total_damage", "sum"), avg_dps=("avg_dps", "mean"))
            .reset_index()
            .sort_values("total_damage", ascending=False)
        )
        try:
            base = alt.Chart(zone_agg).encode(
                y=alt.Y("zone_name:N", sort="-x", title=""),
            )
            bars = base.mark_bar().encode(
                x=alt.X("total_damage:Q", title="Total Damage"),
                color=alt.value("#7CFC00"),
                tooltip=[
                    alt.Tooltip("zone_name:N", title="Zone"),
                    alt.Tooltip("runs:Q", title="Runs"),
                    alt.Tooltip("total_damage:Q", format=",", title="Total Dmg"),
                    alt.Tooltip("avg_dps:Q", format=".1f", title="Avg DPS"),
                ],
            )
            text = base.mark_text(
                align="right",
                baseline="middle",
                dx=-5,  # Slightly inside the bar from the right
                color="#000000",  # Dark color for better contrast against lime-green bar
                fontWeight="bold",
            ).encode(
                x=alt.X("total_damage:Q"),
                text=alt.Text("total_damage:Q", format=",.0f"),
            )
            chart = (bars + text).properties(height=max(120, 28 * len(zone_agg)), title="Damage by zones (all runs)")
            st.altair_chart(chart, width="stretch", height=len(zone_agg) * 28 + 100)
        except Exception:
            pass

    # ── Selected run detail ───────────────────────────────────────────────
    if selected_run_id is not None and not enc_summary.empty:
        run_meta = runs_df[runs_df["run_id"] == selected_run_id]
        run_encs = enc_summary[enc_summary["run_id"] == selected_run_id].sort_values("start_dt")

        if not run_meta.empty and not run_encs.empty:
            zone_label = run_meta.iloc[0]["zone_name"]
            date_label = (
                run_meta.iloc[0]["start_dt"].strftime("%Y-%m-%d %H:%M")
                if pd.notna(run_meta.iloc[0]["start_dt"])
                else ""
            )
            st.markdown("---")
            st.subheader(f"Run #{selected_run_id} — {zone_label}  ·  {date_label}")

            # Boss kill badge
            run_boss = str(run_meta.iloc[0].get("boss_names", "") or "")
            if run_boss:
                st.markdown(
                    f"<p style='margin:0 0 8px 0;font-size:0.9rem;'>🏆 <b style='color:#FFD700'>{run_boss}</b> killed</p>",
                    unsafe_allow_html=True,
                )

            rm = run_meta.iloc[0]
            rc1, rc2, rc3, rc4 = st.columns(4)
            rc1.metric("Encounters", int(rm["n_encounters"]))
            rc2.metric("Duration", _dur_label(rm["duration_s"]))
            rc3.metric(
                "Total Damage",
                _fmt_compact_amount(rm["total_damage"])
                .replace("&nbsp;", " ")
                .replace("<strong>", "")
                .replace("</strong>", ""),
            )
            rc4.metric("Avg DPS", f"{rm['avg_dps']:.1f}")

            # ── Cross-reference boss kills for these encounters ──────────
            boss_kill_ids = []
            try:
                from utils.data_io import load_boss_kills

                _kills = load_boss_kills()
                # Use end_ts from boss_kills to match with encounter timestamps
                # (since boss_kills.jsonl does not contain combat_id)
                for _k in _kills:
                    if _k.get("kill_flag") == 1:
                        try:
                            bk_end = pd.to_datetime(_k.get("end_ts"))
                            # Look for an encounter in this run that ends +/- 20 seconds from this boss kill
                            for _, er in run_encs.iterrows():
                                enc_end = er["end_dt"]
                                # Compare timestamps
                                if abs((enc_end - bk_end).total_seconds()) < 20:
                                    boss_kill_ids.append(int(er["combat_id"]))
                        except Exception:
                            continue
            except Exception:
                pass

            # Build encounter rows — rendered at the bottom of this detail section
            enc_rows = []
            # Load raw CSV once to compute per-encounter absorb amounts
            _enc_raw = load_csv()
            for _, er in run_encs.iterrows():
                _cid = int(er["combat_id"])
                _is_boss = _cid in boss_kill_ids
                _star = "\u2b50 " if _is_boss else ""  # Golden Star
                _target = str(er.get("main_target", "")) or "\u2014"
                _disp_target = f"{_star}{_target}"

                # Compute per-encounter absorb (if present in raw data)
                try:
                    _abs_amt = (
                        int(
                            _enc_raw[(_enc_raw["combat_id"] == _cid) & (_enc_raw["type"] == "absorb")][
                                "effective_amount"
                            ].sum()
                        )
                        if (_enc_raw is not None and not _enc_raw.empty)
                        else 0
                    )
                except Exception:
                    _abs_amt = 0

                enc_rows.append(
                    {
                        "#": _cid,
                        "Target": _disp_target,
                        "Start": er["start_dt"].strftime("%H:%M:%S") if pd.notna(er["start_dt"]) else "",
                        "Duration": _dur_label(er["duration_s"]),
                        "Dmg": int(er.get("total_damage", 0)),
                        "Heal": int(er.get("total_heal", 0)),
                        "Absorb": int(_abs_amt),
                        "DPS": round(er["total_damage"] / er["duration_s"], 1) if er["duration_s"] > 0 else 0.0,
                        "HPS": round(er.get("total_heal", 0) / er["duration_s"], 1) if er["duration_s"] > 0 else 0.0,
                        "is_boss": _is_boss,
                    }
                )

            # ── Participant breakdown ──────────────────────────────────────
            try:
                _run_cids = run_encs["combat_id"].tolist()
                _enc_dur = dict(zip(run_encs["combat_id"], run_encs["duration_s"]))
                _raw_df = load_csv()
                _run_df = _raw_df[_raw_df["combat_id"].isin(_run_cids)].copy()

                def _classify(name: str):
                    """Return (role_label, short_name) for a source name."""
                    parts = str(name).split("-")
                    if len(parts) == 3 and all(parts):
                        return "Player", parts[0]
                    return "Follower NPC", parts[0]

                participants = []
                for src, grp in _run_df.groupby("source"):
                    total_dmg = float(grp[grp["type"] == "damage"]["effective_amount"].sum())
                    total_heal = float(grp[grp["type"].isin(["heal", "absorb"])]["effective_amount"].sum())
                    if total_dmg == 0 and total_heal == 0:
                        continue
                    # Active time = sum of encounter durations this source appears in
                    active_dur = sum(_enc_dur.get(cid, 0) for cid in grp["combat_id"].unique())
                    dps = total_dmg / active_dur if active_dur > 0 else 0.0
                    hps = total_heal / active_dur if active_dur > 0 else 0.0
                    role, short = _classify(src)
                    participants.append(
                        {
                            "Source": short,
                            "Role": role,
                            "Damage": int(total_dmg),
                            "DPS": round(dps, 1),
                            "Healing": int(total_heal),
                            "HPS": round(hps, 1),
                        }
                    )

                if participants:
                    part_df = pd.DataFrame(participants).sort_values("Damage", ascending=False).reset_index(drop=True)
                    st.subheader("Participant breakdown")

                    # Role badge legend
                    _roles = part_df["Role"].unique().tolist()
                    _badge_parts = []
                    for _r in _roles:
                        _col = "#7CFC00" if _r == "Player" else "#FF8C00"
                        _badge_parts.append(
                            f"<span style='display:inline-block;width:10px;height:10px;"
                            f"background:{_col};border-radius:2px;margin-right:4px'></span>"
                            f"<span style='color:#ccc;font-size:0.78rem'>{_r}</span>"
                        )
                    st.markdown(
                        "<p style='margin:0 0 6px 0'>" + " &nbsp;&nbsp; ".join(_badge_parts) + "</p>",
                        unsafe_allow_html=True,
                    )

                    st.dataframe(
                        part_df.style.format(
                            {"Damage": "{:,}", "DPS": "{:.1f}", "Healing": "{:,}", "HPS": "{:.1f}"}
                        ).apply(
                            lambda row: [
                                (
                                    "color: #7CFC00"
                                    if row["Role"] == "Player"
                                    else "color: #FF8C00" if col in ("Source", "Role") else ""
                                )
                                for col in part_df.columns
                            ],
                            axis=1,
                        ),
                        hide_index=True,
                        width="stretch",
                    )

                    # DPS / HPS pie charts side-by-side
                    _has_dmg = part_df["Damage"].sum() > 0
                    _has_heal = part_df["Healing"].sum() > 0
                    _color_scale = alt.Scale(domain=["Player", "Follower NPC"], range=["#7CFC00", "#FF8C00"])

                    _pie_cols = st.columns(2 if (_has_dmg and _has_heal) else 1)

                    if _has_dmg:
                        _dmg_df = part_df[part_df["Damage"] > 0].copy()
                        _dmg_df["pct"] = (_dmg_df["Damage"] / _dmg_df["Damage"].sum() * 100).round(1)
                        try:
                            with _pie_cols[0]:
                                st.altair_chart(
                                    alt.Chart(_dmg_df)
                                    .mark_arc(outerRadius=110)
                                    .encode(
                                        theta=alt.Theta("Damage:Q"),
                                        color=alt.Color(
                                            "Source:N",
                                            scale=alt.Scale(
                                                domain=_dmg_df["Source"].tolist(),
                                                range=[
                                                    "#7CFC00" if r == "Player" else "#FF8C00"
                                                    for r in _dmg_df["Role"].tolist()
                                                ],
                                            ),
                                            legend=alt.Legend(title="Source"),
                                        ),
                                        tooltip=[
                                            alt.Tooltip("Source:N"),
                                            alt.Tooltip("Role:N"),
                                            alt.Tooltip("Damage:Q", format=",", title="Damage"),
                                            alt.Tooltip("DPS:Q", format=".1f"),
                                            alt.Tooltip("pct:Q", format=".1f", title="%"),
                                        ],
                                    )
                                    .properties(height=300, title="Damage share"),
                                    width="stretch",
                                )
                        except Exception:
                            pass

                    if _has_heal:
                        _heal_df = part_df[part_df["Healing"] > 0].copy()
                        _heal_df["pct"] = (_heal_df["Healing"] / _heal_df["Healing"].sum() * 100).round(1)
                        _pie_heal_col = _pie_cols[1] if (_has_dmg and len(_pie_cols) > 1) else _pie_cols[0]
                        try:
                            with _pie_heal_col:
                                st.altair_chart(
                                    alt.Chart(_heal_df)
                                    .mark_arc(outerRadius=110)
                                    .encode(
                                        theta=alt.Theta("Healing:Q"),
                                        color=alt.Color(
                                            "Source:N",
                                            scale=alt.Scale(
                                                domain=_heal_df["Source"].tolist(),
                                                range=[
                                                    "#7CFC00" if r == "Player" else "#FF8C00"
                                                    for r in _heal_df["Role"].tolist()
                                                ],
                                            ),
                                            legend=alt.Legend(title="Source"),
                                        ),
                                        tooltip=[
                                            alt.Tooltip("Source:N"),
                                            alt.Tooltip("Role:N"),
                                            alt.Tooltip("Healing:Q", format=",", title="Healing"),
                                            alt.Tooltip("HPS:Q", format=".1f"),
                                            alt.Tooltip("pct:Q", format=".1f", title="%"),
                                        ],
                                    )
                                    .properties(height=300, title="Healing share"),
                                    width="stretch",
                                )
                        except Exception:
                            pass

                    # Per-encounter DPS trend per participant (only if >1 encounter)
                    if len(_run_cids) > 1 and len(participants) > 1:
                        try:
                            _trend_rows = []
                            for _cid in _run_cids:
                                _enc_slice = _run_df[_run_df["combat_id"] == _cid]
                                _dur = _enc_dur.get(_cid, 0)
                                for _src, _sgrp in _enc_slice.groupby("source"):
                                    _sd = float(_sgrp[_sgrp["type"] == "damage"]["effective_amount"].sum())
                                    _sh = float(_sgrp[_sgrp["type"].isin(["heal", "absorb"])]["effective_amount"].sum())
                                    if _sd == 0 and _sh == 0:
                                        continue
                                    _role, _short = _classify(_src)
                                    _trend_rows.append(
                                        {
                                            "Combat": str(_cid),
                                            "Source": _short,
                                            "Role": _role,
                                            "DPS": round(_sd / _dur, 1) if _dur > 0 else 0.0,
                                            "HPS": round(_sh / _dur, 1) if _dur > 0 else 0.0,
                                        }
                                    )
                            if _trend_rows:
                                _trend_df = pd.DataFrame(_trend_rows)
                                # Natural sort order for combat IDs
                                _cid_order = [str(c) for c in _run_cids]
                                tab_tdps, tab_thps = st.tabs(
                                    ["DPS by encounter (per participant)", "HPS by encounter (per participant)"]
                                )
                                with tab_tdps:
                                    st.altair_chart(
                                        alt.Chart(_trend_df)
                                        .mark_line(point=True, strokeWidth=2)
                                        .encode(
                                            x=alt.X("Combat:N", sort=_cid_order, title="Combat"),
                                            y=alt.Y("DPS:Q", title="DPS"),
                                            color=alt.Color("Source:N", legend=alt.Legend(title="Source")),
                                            strokeDash=alt.StrokeDash(
                                                "Role:N",
                                                scale=alt.Scale(
                                                    domain=["Player", "Follower NPC"],
                                                    range=[[1, 0], [4, 2]],
                                                ),
                                                legend=None,
                                            ),
                                            tooltip=["Combat", "Source", "Role", alt.Tooltip("DPS:Q", format=".1f")],
                                        )
                                        .properties(height=240),
                                        width="stretch",
                                    )
                                with tab_thps:
                                    _hps_trend = _trend_df[_trend_df["HPS"] > 0]
                                    if not _hps_trend.empty:
                                        st.altair_chart(
                                            alt.Chart(_hps_trend)
                                            .mark_line(point=True, strokeWidth=2)
                                            .encode(
                                                x=alt.X("Combat:N", sort=_cid_order, title="Combat"),
                                                y=alt.Y("HPS:Q", title="HPS"),
                                                color=alt.Color("Source:N", legend=alt.Legend(title="Source")),
                                                strokeDash=alt.StrokeDash(
                                                    "Role:N",
                                                    scale=alt.Scale(
                                                        domain=["Player", "Follower NPC"],
                                                        range=[[1, 0], [4, 2]],
                                                    ),
                                                    legend=None,
                                                ),
                                                tooltip=[
                                                    "Combat",
                                                    "Source",
                                                    "Role",
                                                    alt.Tooltip("HPS:Q", format=".1f"),
                                                ],
                                            )
                                            .properties(height=240),
                                            width="stretch",
                                        )
                                    else:
                                        st.caption("No healing recorded.")
                        except Exception:
                            pass
            except Exception:
                pass

            # ── Abilities breakdown for this run (damage & healing) ─────
            try:
                _run_cids = run_encs["combat_id"].tolist()
                _raw_df = load_csv()
                _run_df = _raw_df[_raw_df["combat_id"].isin(_run_cids)].copy()
                dmg_agg_run = spell_aggregates(_run_df, "damage", top_n=200)
                heal_agg_run = spell_aggregates(_run_df, ["heal", "absorb"], top_n=200)
                st.subheader("Abilities in this run")
                ad, ah = st.columns(2)
                with ad:
                    st.markdown("**Damage abilities**")
                    if not dmg_agg_run.empty:
                        st.dataframe(
                            dmg_agg_run.style.format({"total": "{:,.0f}", "avg": "{:.1f}", "pct": "{:.1f}%"}),
                            height=min(400, 28 * len(dmg_agg_run.head(12))),
                            width=900,
                        )
                    else:
                        st.write("No damage abilities recorded for this run.")
                with ah:
                    st.markdown("**Healing abilities**")
                    if not heal_agg_run.empty:
                        st.dataframe(
                            heal_agg_run.style.format({"total": "{:,.0f}", "avg": "{:.1f}", "pct": "{:.1f}%"}),
                            height=min(400, 28 * len(heal_agg_run.head(12))),
                            width=900,
                        )
                    else:
                        st.write("No healing abilities recorded for this run.")
            except Exception:
                pass

            # ── Abilities pies (Damage / Healing) — use the ability tables above
            try:
                # dmg_agg_run and heal_agg_run are computed in the abilities section above.
                if "dmg_agg_run" in locals() and not dmg_agg_run.empty:
                    dmg_pie_df = dmg_agg_run.rename(columns={"spell": "Spell", "total": "Total", "pct": "Pct"}).copy()
                    # Add % to legend labels - we use a specific suffix to split on in the expression if needed,
                    # but here we'll just format the string.
                    dmg_pie_df["Spell_Lbl"] = dmg_pie_df.apply(
                        lambda r: f"{r['Spell']} \u00bb {r['Pct']:>4.1f}%", axis=1
                    )
                else:
                    dmg_pie_df = pd.DataFrame()

                if "heal_agg_run" in locals() and not heal_agg_run.empty:
                    heal_pie_df = heal_agg_run.rename(columns={"spell": "Spell", "total": "Total", "pct": "Pct"}).copy()
                    heal_pie_df["Spell_Lbl"] = heal_pie_df.apply(
                        lambda r: f"{r['Spell']} \u00bb {r['Pct']:>4.1f}%", axis=1
                    )
                else:
                    heal_pie_df = pd.DataFrame()

                if not dmg_pie_df.empty or not heal_pie_df.empty:
                    col_a, col_b = st.columns(2)
                    with col_a:
                        if not dmg_pie_df.empty:
                            try:
                                st.altair_chart(
                                    alt.Chart(dmg_pie_df)
                                    .mark_arc(outerRadius=180)
                                    .encode(
                                        theta=alt.Theta("Total:Q"),
                                        color=alt.Color(
                                            "Spell_Lbl:N",
                                            legend=alt.Legend(
                                                title="Spell",
                                                orient="right",
                                                labelLimit=400,
                                                labelFont="monospace",
                                                labelFontSize=12,
                                                labelColor="#CCCCCC",
                                            ),
                                            sort=alt.SortField("Total", order="descending"),
                                        ),
                                        tooltip=[
                                            alt.Tooltip("Spell:N"),
                                            alt.Tooltip("Total:Q", format=",", title="Total"),
                                            alt.Tooltip("Pct:Q", format=".1f", title="%"),
                                        ],
                                    )
                                    .properties(title="Damage by ability (this run)", height=560),
                                    width="stretch",
                                )
                            except Exception:
                                pass
                    with col_b:
                        if not heal_pie_df.empty:
                            try:
                                st.altair_chart(
                                    alt.Chart(heal_pie_df)
                                    .mark_arc(outerRadius=180)
                                    .encode(
                                        theta=alt.Theta("Total:Q"),
                                        color=alt.Color(
                                            "Spell_Lbl:N",
                                            legend=alt.Legend(
                                                title="Spell",
                                                orient="right",
                                                labelLimit=400,
                                                labelFont="monospace",
                                                labelFontSize=11,
                                                labelColor="#CCCCCC",
                                            ),
                                            sort=alt.SortField("Total", order="descending"),
                                        ),
                                        tooltip=[
                                            alt.Tooltip("Spell:N"),
                                            alt.Tooltip("Total:Q", format=",", title="Total"),
                                            alt.Tooltip("Pct:Q", format=".1f", title="%"),
                                        ],
                                    )
                                    .properties(title="Healing by ability (this run)", height=560),
                                    width="stretch",
                                )
                            except Exception:
                                pass
            except Exception:
                pass

            # ── Encounter list (at the bottom) ────────────────────────────
            st.subheader("Encounters")
            enc_df_full = pd.DataFrame(enc_rows)

            show_bosses_only = st.checkbox("Show Bosses only", value=False)
            if show_bosses_only:
                enc_df_disp = enc_df_full[enc_df_full["is_boss"] == True].copy()
            else:
                enc_df_disp = enc_df_full.copy()

            if enc_df_disp.empty:
                st.info("No boss encounters found in this run.")
            else:
                # ── Clickable encounter table (st.dataframe with row selection) ─
                # Drop is_boss from display; use it only for styling.
                # Rename "#" → "ID  ↗" so the column next to the selection
                # checkbox makes the click-to-preview intent clear.
                display_cols = ["#", "Target", "Start", "Duration", "Dmg", "Heal", "DPS", "HPS"]
                enc_display = enc_df_disp[display_cols].copy()
                enc_display = enc_display.rename(columns={"#": "ID  ↗"})

                def _style_enc_row(row):
                    """Gold text for boss rows (identified by ⭐ prefix), dim grey otherwise."""
                    is_boss_row = str(row["Target"]).startswith("\u2b50")
                    color = "#FFD700" if is_boss_row else ""
                    return [f"color:{color}" if color else "" for _ in row]

                styled_enc = enc_display.style.apply(_style_enc_row, axis=1).format(
                    {"Dmg": "{:,}", "Heal": "{:,}", "DPS": "{:.1f}", "HPS": "{:.1f}"}
                )

                st.caption("Click a row to open the combat preview.")
                row_px = 35
                tbl_h = min(600, 38 + row_px * max(3, len(enc_display)))
                sel_event = st.dataframe(
                    styled_enc,
                    hide_index=True,
                    height=tbl_h,
                    use_container_width=True,
                    on_select="rerun",
                    selection_mode="single-row",
                    key="enc_table_sel",
                )
                # Open the dialog immediately when a row is selected
                sel_rows = sel_event.selection.get("rows", []) if sel_event and sel_event.selection else []
                if sel_rows:
                    _clicked_cid = int(enc_display.iloc[sel_rows[0]]["ID  ↗"])
                    _show_combat_dialog(_clicked_cid)

            if not enc_df_full.empty:
                try:
                    # 1. Prepare data for "Dual Bar" format
                    # We need a long format where 'Value' is the amount and 'Type' defines the bar
                    chart_df = enc_df_full.copy()
                    chart_df["Label"] = chart_df.apply(lambda r: f"#{r['#']} {r['Target']}".replace("**", ""), axis=1)

                    # Melt DPS, HPS, and Absorb into rows
                    # Ensure your enc_df_full actually has columns named 'HPS' and 'Absorb'
                    melted_df = chart_df.melt(
                        id_vars=["Label", "#", "Target", "is_boss", "Duration"],
                        value_vars=["DPS", "HPS", "Absorb"],
                        var_name="StatType",
                        value_name="Value",
                    )

                    # Map StatType to a "Row Group" so DPS is one bar, HPS+Absorb is the other
                    melted_df["BarGroup"] = melted_df["StatType"].map(
                        {"DPS": "Damage", "HPS": "Support", "Absorb": "Support"}
                    )

                    # 2. Define Colors
                    color_scale = alt.Scale(
                        domain=["DPS", "HPS", "Absorb"],
                        range=["#FFD700", "#7CFC00", "#00BFFF"],  # Gold, Green, DeepSkyBlue
                    )

                    # 3. Build the Chart
                    base = alt.Chart(melted_df).encode(
                        y=alt.Y("Label:N", title="Encounter", sort=chart_df["Label"].tolist()),
                        x=alt.X("sum(Value):Q", title="Value (DPS / HPS)"),
                        color=alt.Color("StatType:N", scale=color_scale, title="Stat"),
                        tooltip=["Target", "StatType:N", alt.Tooltip("sum(Value):Q", format=".1f", title="Value")],
                    )

                    # yOffset creates the "split" bar effect within one row
                    bars = base.mark_bar(opacity=0.85).encode(
                        yOffset="BarGroup:N",
                        # This splits the row into two half-height bars
                    )

                    # Add text labels for each bar
                    text = base.mark_text(align="left", baseline="middle", dx=5, fontSize=10, fontWeight="bold").encode(
                        yOffset="BarGroup:N", text=alt.Text("sum(Value):Q", format=".1f")
                    )

                    final_chart = (
                        (bars + text)
                        .properties(
                            height=len(chart_df) * 50,  # Slightly taller rows to accommodate dual bars
                            title=f"Performance per encounter — {zone_label}",
                        )
                        .configure_view(strokeOpacity=0)  # Cleans up the grid look
                    )

                    st.altair_chart(final_chart, width="stretch")

                except Exception as e:
                    st.error(f"Chart Error: {e}")

