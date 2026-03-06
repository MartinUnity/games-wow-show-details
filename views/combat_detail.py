"""
views/combat_detail.py
──────────────────────
Right-column single-encounter detail panel:
header metrics, damage-by-target split bar, ability tables/charts,
DPS/HPS time-series with spell filter, and rotation timeline.
"""

import os

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from utils.data_engine import combat_time_series, spell_aggregates
from utils.data_io import (
    _fmt_compact_amount,
    load_hidden,
    load_notes,
    save_note,
    toggle_hidden,
)
from utils.export_share import register_share_ui
from utils.replay_engine import generate_replay_manuscript, render_replay_viewer


def combat_detail_view(df, combat_id, resample_s=1, smooth_s=0, top_n=5, show_replay=False):
    # Show combat header with first target name if available
    combat_df = df[df["combat_id"] == combat_id].sort_values("timestamp_dt")

    if "target" in combat_df.columns:
        try:
            nonempty = combat_df[~combat_df["target"].isnull() & (combat_df["target"] != "")]
            if not nonempty.empty:
                target_name = str(nonempty.iloc[0]["target"]).strip()
        except Exception:
            target_name = ""
    else:
        target_name = ""

    st.header(f"Combat {combat_id}")

    # Hide / unhide button
    _hidden = load_hidden()
    _is_hidden = int(combat_id) in _hidden
    _hide_label = "🔴 Unhide this encounter" if _is_hidden else "🙈 Hide this encounter"
    if st.button(_hide_label, key=f"hide_btn_{combat_id}"):
        toggle_hidden(int(combat_id))
        st.rerun()

    notes = load_notes()
    existing_note = notes.get(int(combat_id), "")
    new_note = st.text_input(
        "Note",
        value=existing_note,
        key=f"note_{combat_id}",
        placeholder="e.g. elite pack near the cave",
        label_visibility="collapsed",
    )
    if new_note != existing_note:
        save_note(combat_id, new_note)

    if combat_df.empty:
        st.write("No events for this combat.")
        return

    # Summary metrics
    total_damage = combat_df[combat_df["type"] == "damage"]["effective_amount"].sum()
    total_dmg_raw = combat_df[combat_df["type"] == "damage"]["amount"].sum()
    total_heal = combat_df[combat_df["type"] == "heal"]["effective_amount"].sum()
    start = combat_df["timestamp_dt"].min()
    end = combat_df["timestamp_dt"].max()
    duration = (end - start).total_seconds() if pd.notna(start) and pd.notna(end) and end > start else 0.0

    # Time-to-first-kill: earliest UNIT_DIED event targeting a non-player unit
    try:
        player_name = combat_df["source"].mode().iloc[0] if not combat_df["source"].mode().empty else None
    except Exception:
        player_name = None
    ttk = None
    try:
        died_rows = combat_df[
            (combat_df["event"] == "UNIT_DIED")
            & combat_df["target"].notna()
            & (combat_df["target"] != "")
            & (combat_df["target"] != player_name)
        ]
        if not died_rows.empty and pd.notna(start):
            first_kill_dt = died_rows["timestamp_dt"].min()
            if pd.notna(first_kill_dt):
                ttk = (first_kill_dt - start).total_seconds()
    except Exception:
        ttk = None

    # Overkill: damage absorbed into already-zero HP (amount - effective_amount)
    overkill_pct = None
    try:
        if total_dmg_raw > 0:
            overkill_total = total_dmg_raw - total_damage
            overkill_pct = overkill_total / total_dmg_raw * 100
    except Exception:
        overkill_pct = None

    # Show DPS/HPS like the Live panel for a uniform layout
    dps = total_damage / duration if duration > 0 else 0.0
    hps = total_heal / duration if duration > 0 else 0.0

    cols = st.columns(5)
    cols[0].metric("DPS", f"{dps:.1f}")
    cols[1].metric("HPS", f"{hps:.1f}")
    cols[2].metric("Duration (s)", f"{duration:.1f}")
    cols[3].metric("Time to 1st kill", f"{ttk:.1f}s" if ttk is not None else "—")
    cols[4].metric("Overkill", f"{overkill_pct:.1f}%" if overkill_pct is not None else "—")

    st.write(f"Total damage: {int(total_damage)} — Total healing: {int(total_heal)}")

    # ── Damage split by target ───────────────────────────────────────────
    try:
        _tgt_dmg = combat_df[combat_df["type"] == "damage"].pipe(lambda d: d[d["target"].notna() & (d["target"] != "")])
        if player_name:
            _tgt_dmg = _tgt_dmg[_tgt_dmg["target"] != player_name]
        if not _tgt_dmg.empty:
            _tgt_totals = (
                _tgt_dmg.groupby("target")["effective_amount"].sum().sort_values(ascending=False).reset_index()
            )
            _tgt_totals.columns = ["target", "damage"]
            _total = _tgt_totals["damage"].sum()
            _tgt_totals["pct"] = _tgt_totals["damage"] / _total * 100
            _tgt_totals["label"] = _tgt_totals["target"].str.split("-").str[0]
            if len(_tgt_totals) > 1:
                st.subheader("Damage by target")
                # Make the stacked bar a bit taller so small label text fits and
                # so multiple targets are easier to distinguish visually.
                _split_chart = (
                    alt.Chart(_tgt_totals)
                    .mark_bar(height=36)
                    .encode(
                        x=alt.X("damage:Q", stack="normalize", title="Share of damage", axis=alt.Axis(format="%")),
                        color=alt.Color("label:N", title="Target", scale=alt.Scale(scheme="tableau10")),
                        order=alt.Order("damage:Q", sort="descending"),
                        tooltip=[
                            alt.Tooltip("label:N", title="Target"),
                            alt.Tooltip("damage:Q", format=",.0f", title="Damage"),
                            alt.Tooltip("pct:Q", format=".1f", title="%"),
                        ],
                    )
                    .properties(height=90)
                )
                # Layout the bar and the compact target list side-by-side (60/40)
                col_bar, col_labels = st.columns([3, 2])
                with col_bar:
                    st.altair_chart(_split_chart, width="stretch")

                # Build compact per-target lines for the right-hand column
                max_labels = 20
                parts = []
                for r in _tgt_totals.head(max_labels).itertuples():
                    parts.append(
                        f"<div style='font-size:0.75rem;margin:2px 0'><span style='color:#aaa'>{r.label}:</span> <b>{_fmt_compact_amount(r.damage)}</b> <span style='color:#9aa3b2'>({r.pct:.0f}%)</span></div>"
                    )
                with col_labels:
                    st.markdown("<div style='padding-left:8px'>" + "".join(parts) + "</div>", unsafe_allow_html=True)
    except Exception:
        pass

    # Aggregated per-spell tables/charts (damage & healing)
    dmg_agg = spell_aggregates(combat_df, "damage", top_n=top_n)
    heal_agg = spell_aggregates(combat_df, "heal", top_n=top_n)
    if not dmg_agg.empty or not heal_agg.empty:
        st.subheader("By ability")
        # Use dynamic table height based on number of abilities so both columns align
        ROW_HEIGHT = 28
        TABLE_PADDING = 60
        TABLE_HEIGHT = min(600, TABLE_PADDING + ROW_HEIGHT * max(3, top_n))
        BAR_ROW_HEIGHT = 28  # px per bar row; enough for label text
        BAR_MIN_HEIGHT = 120
        BAR_LABEL_LIMIT = 300  # max px for y-axis label before truncation
        a1, a2 = st.columns([1, 1])
        with a1:
            st.markdown("**Damage by ability**")
            if not dmg_agg.empty:
                # alternating row colors
                da = dmg_agg.reset_index(drop=True)
                styled = da.style.format({"total": "{:,}", "avg": "{:.1f}", "pct": "{:.1f}%"}).apply(
                    lambda row: [("background-color: #2f2f2f" if row.name % 2 == 0 else "color: #ffffff") for _ in row],
                    axis=1,
                )
                st.dataframe(styled, height=TABLE_HEIGHT)
                # spacer between table and its ability bar chart
                st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
                try:
                    dmg_chart_h = max(BAR_MIN_HEIGHT, BAR_ROW_HEIGHT * len(da))
                    _lw2 = max(100, min(400, max((len(s) for s in da["spell"]), default=10) * 7))
                    chart = (
                        alt.Chart(da)
                        .mark_bar()
                        .encode(
                            x=alt.X("total:Q"),
                            y=alt.Y("spell:N", sort="-x", axis=alt.Axis(labelLimit=_lw2, labelFontSize=10)),
                            tooltip=["spell", "total", "count", "avg", "pct"],
                        )
                    )
                    st.altair_chart(chart.properties(height=dmg_chart_h), width="stretch")
                except Exception:
                    pass
            else:
                st.write("No damage events")
            # (filter UI moved below the chart)
        with a2:
            st.markdown("**Healing by ability**")
            if not heal_agg.empty:
                ha = heal_agg.reset_index(drop=True)
                styled_h = ha.style.format({"total": "{:,}", "avg": "{:.1f}", "pct": "{:.1f}%"}).apply(
                    lambda row: [("background-color: #2f2f2f" if row.name % 2 == 0 else "color: #ffffff") for _ in row],
                    axis=1,
                )
                st.dataframe(styled_h, height=TABLE_HEIGHT)
                # spacer between table and its ability bar chart
                st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
                try:
                    heal_chart_h = max(BAR_MIN_HEIGHT, BAR_ROW_HEIGHT * len(ha))
                    _lw2h = max(100, min(400, max((len(s) for s in ha["spell"]), default=10) * 7))
                    chart_h = (
                        alt.Chart(ha)
                        .mark_bar()
                        .encode(
                            x=alt.X("total:Q"),
                            y=alt.Y("spell:N", sort="-x", axis=alt.Axis(labelLimit=_lw2h, labelFontSize=10)),
                            tooltip=["spell", "total", "count", "avg", "pct"],
                        )
                    )
                    st.altair_chart(chart_h.properties(height=heal_chart_h), width="stretch")
                except Exception:
                    pass
            else:
                st.write("No healing events")
            # (filter UI moved below the chart)
    spell_filter = st.session_state.get("spell_filter", "")
    df_ts = combat_time_series(combat_df, resample_s, spell_filter=spell_filter)
    if not df_ts.empty and smooth_s and smooth_s > 0:
        window = max(1, int(smooth_s / resample_s))
        df_ts = df_ts.rolling(window=window, min_periods=1, center=True).mean()
    if not df_ts.empty:
        st.subheader("DPS / HPS (per second)")
        st.line_chart(df_ts)

        # Spell filter dropdown + clear button beneath the chart
        dmg_spells = dmg_agg["spell"].tolist() if not dmg_agg.empty else []
        heal_spells = heal_agg["spell"].tolist() if not heal_agg.empty else []
        options = [""] + [f"{s} [Damage]" for s in dmg_spells] + [f"{s} [Healing]" for s in heal_spells]
        # ensure session key exists
        if "spell_filter" not in st.session_state:
            st.session_state["spell_filter"] = ""
        if st.session_state.get("spell_filter") not in options:
            st.session_state["spell_filter"] = ""

        sel_col, clear_col = st.columns([4, 1])
        with sel_col:
            st.selectbox(
                "Filter by spell",
                options=options,
                index=options.index(st.session_state.get("spell_filter", "")),
                key="spell_filter",
            )
        with clear_col:
            if st.button("Clear filter", key="clear_spell_filter"):
                st.session_state["spell_filter"] = ""

    # ── Rotation timeline (swimlane scatter) ──────────────────────────────
    try:
        tl_df = combat_df[
            combat_df["spell_name"].notna()
            & (combat_df["spell_name"] != "")
            & combat_df["type"].isin(["damage", "heal"])
        ].copy()
        if not tl_df.empty and pd.notna(start):
            tl_df["elapsed_s"] = (tl_df["timestamp_dt"] - start).dt.total_seconds()
            # Limit to top-20 spells by usage so the chart stays readable
            top_spells = tl_df["spell_name"].value_counts().head(20).index.tolist()
            tl_df = tl_df[tl_df["spell_name"].isin(top_spells)]
            n_spells = tl_df["spell_name"].nunique()
            timeline_height = max(200, min(600, 30 * n_spells + 60))
            st.subheader("Rotation timeline")
            try:
                tl_chart = (
                    alt.Chart(tl_df)
                    .mark_circle(opacity=0.75)
                    .encode(
                        x=alt.X("elapsed_s:Q", title="Elapsed (s)"),
                        y=alt.Y("spell_name:N", title="", sort=alt.EncodingSortField(field="elapsed_s", op="min")),
                        color=alt.Color(
                            "type:N",
                            scale=alt.Scale(domain=["damage", "heal"], range=["#7CFC00", "#00CED1"]),
                            legend=alt.Legend(title="Type"),
                        ),
                        size=alt.Size("effective_amount:Q", scale=alt.Scale(range=[40, 400]), legend=None),
                        tooltip=[
                            alt.Tooltip("spell_name:N", title="Spell"),
                            alt.Tooltip("elapsed_s:Q", format=".2f", title="At (s)"),
                            alt.Tooltip("effective_amount:Q", format=",", title="Eff. amount"),
                            alt.Tooltip("amount:Q", format=",", title="Raw amount"),
                            alt.Tooltip("type:N", title="Type"),
                        ],
                    )
                    .properties(height=timeline_height)
                )
                st.altair_chart(tl_chart, width="stretch")
            except Exception:
                pass
    except Exception:
        pass

    # Share / Export (CSV + optional GIF replay)
    try:
        register_share_ui(combat_df, combat_id)
    except Exception:
        pass

    with st.expander("Recent events", expanded=False):
        st.dataframe(
            combat_df.tail(200)[["timestamp", "event", "source", "spell_name", "amount", "effective_amount", "type"]]
        )

    # ── Replay Viewer (Positional Data) ─────────────────────────────────
    from utils.data_io import LOG_DIR

    # Hard-linked to the test boss fight log as requested
    latest_log = os.path.join(os.getcwd(), "testdata/WoWCombatLog-030526_164213.txt")

    if show_replay and os.path.exists(latest_log):
        st.subheader("2D Replay (Positional)")
        with st.spinner("Building replay manuscript..."):
            try:
                # We need the raw log to get the X,Y data that isn't in the CSV
                manuscript = generate_replay_manuscript(combat_df, latest_log)
                if manuscript:
                    replay_html = render_replay_viewer(manuscript)
                    components.html(replay_html, height=520)
                else:
                    st.info("No positional data found for this combat in the latest log.")
            except Exception as e:
                st.error(f"Failed to load replay: {e}")
