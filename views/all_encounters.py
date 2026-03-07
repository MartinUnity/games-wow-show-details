"""
views/all_encounters.py
───────────────────────
Aggregated view across every encounter: headline metrics, top-targets table,
per-encounter DPS/HPS bars + rolling trend, session activity chart,
and ability breakdown with cast-count comparison.
"""

import altair as alt
import pandas as pd
import streamlit as st

from utils.data_engine import compute_all_encounters_stats
from utils.data_io import _fmt_compact_amount


def all_encounters_view(df_full, character=None):
    """Full-width aggregated view across all encounters."""
    char_arg = None if (not character or character == "All") else character
    meta, enc_df, dmg_spells, heal_spells, top_targets = compute_all_encounters_stats(character=char_arg)

    if not meta:
        st.write("No data yet. Run: python wow-parser.py --full-import")
        return

    # ── Headline metrics ───────────────────────────────────────────────────
    st.header("All Encounters — Aggregated")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Encounters", meta["n_encounters"])
    c2.metric("Total time (s)", f"{int(meta['total_duration_s'])}")
    c3.metric(
        "Total damage",
        _fmt_compact_amount(meta["total_damage"])
        .replace("&nbsp;", " ")
        .replace("<strong>", "")
        .replace("</strong>", ""),
    )
    c4.metric(
        "Total healing",
        _fmt_compact_amount(meta["total_heal"]).replace("&nbsp;", " ").replace("<strong>", "").replace("</strong>", ""),
    )
    c5.metric("Avg DPS", f"{meta['avg_dps']:.1f}")
    c6.metric("Avg HPS", f"{meta['avg_hps']:.1f}")

    # ── Top targets ────────────────────────────────────────────────────────
    if not top_targets.empty:
        st.subheader("Top targets (by damage dealt)")
        col_tbl, col_chart = st.columns([1, 2])
        with col_tbl:
            fmt_cols = {"total_damage": "{:,.0f}", "avg_damage": "{:,.0f}"}
            if "best_dps" in top_targets.columns:
                fmt_cols.update({"best_dps": "{:.1f}", "worst_dps": "{:.1f}", "avg_dps": "{:.1f}"})
            styled_t = top_targets.style.format(fmt_cols).apply(
                lambda row: ["background-color: #2f2f2f" if row.name % 2 == 0 else "color: #ffffff" for _ in row],
                axis=1,
            )
            st.dataframe(styled_t, hide_index=True)
        with col_chart:
            try:
                base = alt.Chart(top_targets).encode(
                    y=alt.Y("target:N", sort="-x", title=""),
                )
                bars = base.mark_bar().encode(
                    x=alt.X("total_damage:Q", title="Total damage dealt"),
                    color=alt.value("#FF6347"),
                    tooltip=[
                        "target",
                        alt.Tooltip("encounters:Q", title="encounters"),
                        alt.Tooltip("total_damage:Q", format=",", title="total dmg"),
                        alt.Tooltip("avg_damage:Q", format=",.0f", title="avg dmg/enc"),
                    ],
                )
                text = base.mark_text(
                    align="right",
                    baseline="middle",
                    dx=-5,
                    color="#000000",
                    fontWeight="bold",
                ).encode(
                    x=alt.X("total_damage:Q"),
                    text=alt.Text("total_damage:Q", format=",.0f"),
                )
                chart = (bars + text).properties(height=max(100, 35 * len(top_targets)))
                st.altair_chart(chart, width="stretch")
            except Exception:
                pass

    # ── Per-encounter DPS / HPS bar chart with rolling-average trend line ────
    if not enc_df.empty:
        st.subheader("DPS & HPS per encounter")
        enc_sorted = enc_df.sort_values("combat_id").copy()
        roll_window = max(2, min(5, len(enc_sorted) // 3 or 1))
        enc_sorted["dps_roll"] = enc_sorted["dps"].rolling(roll_window, min_periods=1, center=True).mean()
        enc_sorted["hps_roll"] = enc_sorted["hps"].rolling(roll_window, min_periods=1, center=True).mean()
        enc_sorted["combat_id_s"] = enc_sorted["combat_id"].astype(str)

        chart_df = enc_sorted[["combat_id_s", "dps", "hps"]].melt(
            id_vars="combat_id_s", var_name="metric", value_name="value"
        )
        bars = (
            alt.Chart(chart_df)
            .mark_bar(opacity=0.75)
            .encode(
                x=alt.X("combat_id_s:N", title="Encounter", sort=None),
                y=alt.Y("value:Q", title="DPS / HPS"),
                color=alt.Color(
                    "metric:N",
                    scale=alt.Scale(domain=["dps", "hps"], range=["#7CFC00", "#00CED1"]),
                    legend=alt.Legend(title="Metric"),
                ),
                xOffset="metric:N",
                tooltip=["combat_id_s:N", "metric:N", alt.Tooltip("value:Q", format=".1f")],
            )
        )
        # Rolling mean lines — one per metric
        roll_df = enc_sorted[["combat_id_s", "dps_roll", "hps_roll"]].melt(
            id_vars="combat_id_s", var_name="metric", value_name="roll_value"
        )
        roll_df["metric"] = roll_df["metric"].str.replace("_roll", "")  # match colour domain
        lines = (
            alt.Chart(roll_df)
            .mark_line(strokeWidth=2.5, strokeDash=[4, 2], point=False)
            .encode(
                x=alt.X("combat_id_s:N", sort=None),
                y=alt.Y("roll_value:Q"),
                color=alt.Color(
                    "metric:N",
                    scale=alt.Scale(domain=["dps", "hps"], range=["#7CFC00", "#00CED1"]),
                    legend=None,
                ),
                tooltip=["combat_id_s:N", "metric:N", alt.Tooltip("roll_value:Q", format=".1f", title="rolling avg")],
            )
        )
        try:
            st.altair_chart(
                alt.layer(bars, lines).resolve_scale(y="shared").properties(height=270),
                width="stretch",
            )
        except Exception:
            st.altair_chart(bars.properties(height=250), width="stretch")

        # ── Time-of-session chart ──────────────────────────────────────────
        st.subheader("Session activity by hour of day")
        try:
            session_df = enc_sorted.copy()
            session_df["hour"] = session_df["min"].dt.hour
            hour_agg = (
                session_df.groupby("hour").agg(encounters=("combat_id", "count"), avg_dps=("dps", "mean")).reset_index()
            )
            # Fill any missing hours with zeros so the x-axis is continuous
            all_hours = pd.DataFrame({"hour": range(24)})
            hour_agg = all_hours.merge(hour_agg, on="hour", how="left").fillna(0)

            enc_bars = (
                alt.Chart(hour_agg)
                .mark_bar(color="#778899", opacity=0.8)
                .encode(
                    x=alt.X("hour:O", title="Hour of day", axis=alt.Axis(labelAngle=0)),
                    y=alt.Y("encounters:Q", title="Encounters", axis=alt.Axis(titleColor="#778899")),
                    tooltip=["hour:O", "encounters:Q", alt.Tooltip("avg_dps:Q", format=".1f", title="avg DPS")],
                )
            )
            dps_line = (
                alt.Chart(hour_agg[hour_agg["encounters"] > 0])
                .mark_line(color="#7CFC00", strokeWidth=2.5, point=alt.OverlayMarkDef(color="#7CFC00", size=60))
                .encode(
                    x=alt.X("hour:O"),
                    y=alt.Y("avg_dps:Q", title="Avg DPS", axis=alt.Axis(titleColor="#7CFC00")),
                    tooltip=["hour:O", alt.Tooltip("avg_dps:Q", format=".1f", title="avg DPS"), "encounters:Q"],
                )
            )
            st.altair_chart(
                alt.layer(enc_bars, dps_line).resolve_scale(y="independent").properties(height=220),
                width="stretch",
            )
        except Exception:
            pass

    # ── Ability breakdown ─────────────────────────────────────────────────
    st.subheader("Ability breakdown (all encounters)")
    ROW_HEIGHT = 28
    TABLE_PADDING = 60
    TABLE_HEIGHT = min(
        700,
        TABLE_PADDING
        + ROW_HEIGHT
        * max(
            5,
            min(
                40,
                max(
                    len(dmg_spells) if not dmg_spells.empty else 0,
                    len(heal_spells) if not heal_spells.empty else 0,
                ),
            ),
        ),
    )
    CHART_BAR_ROW = 28
    CHART_MIN_H = 160
    CHART_LABEL_LIMIT = 300

    col_dmg, col_heal = st.columns(2)

    with col_dmg:
        st.markdown("**Damage by ability**")
        if not dmg_spells.empty:
            da = dmg_spells.reset_index(drop=True)
            styled = da.style.format({"total": "{:,.0f}", "avg": "{:.1f}", "pct": "{:.1f}%"}).apply(
                lambda row: [("background-color: #2f2f2f" if row.name % 2 == 0 else "color: #ffffff") for _ in row],
                axis=1,
            )
            st.dataframe(styled, height=TABLE_HEIGHT)
            try:
                _lw = max(100, min(400, max((len(s) for s in da["spell"]), default=10) * 7))
                st.altair_chart(
                    alt.Chart(da)
                    .mark_bar()
                    .encode(
                        x=alt.X("total:Q", title="Total Damage"),
                        y=alt.Y("spell:N", sort="-x", title="", axis=alt.Axis(labelLimit=_lw, labelFontSize=10)),
                        tooltip=[
                            "spell",
                            alt.Tooltip("total:Q", format=","),
                            alt.Tooltip("avg:Q", format=".1f"),
                            alt.Tooltip("pct:Q", format=".1f"),
                        ],
                    )
                    .properties(height=max(CHART_MIN_H, CHART_BAR_ROW * len(da))),
                    width="stretch",
                )
            except Exception:
                pass
        else:
            st.write("No damage events.")

    with col_heal:
        st.markdown("**Healing by ability**")
        if not heal_spells.empty:
            ha = heal_spells.reset_index(drop=True)
            ha_display = ha.copy()
            if "kind" in ha_display.columns:
                ha_display["Kind"] = (
                    ha_display["kind"].map({"heal": "Heal", "absorb": "Absorb"}).fillna(ha_display["kind"])
                )
                ha_display = ha_display.drop(columns=["kind"])
            styled_h = ha_display.style.format({"total": "{:,.0f}", "avg": "{:.1f}", "pct": "{:.1f}%"}).apply(
                lambda row: [("background-color: #2f2f2f" if row.name % 2 == 0 else "color: #ffffff") for _ in row],
                axis=1,
            )
            st.dataframe(styled_h, height=TABLE_HEIGHT)
            try:
                _lw_h = max(100, min(400, max((len(s) for s in ha["spell"]), default=10) * 7))
                _color_enc = (
                    alt.Color(
                        "kind:N",
                        scale=alt.Scale(domain=["heal", "absorb"], range=["#4CAF50", "#42A5F5"]),
                        legend=alt.Legend(title="Kind"),
                    )
                    if "kind" in ha.columns
                    else alt.value("#00CED1")
                )
                st.altair_chart(
                    alt.Chart(ha)
                    .mark_bar()
                    .encode(
                        x=alt.X("total:Q", title="Total Healing"),
                        y=alt.Y("spell:N", sort="-x", title="", axis=alt.Axis(labelLimit=_lw_h, labelFontSize=10)),
                        color=_color_enc,
                        tooltip=[
                            "spell",
                            alt.Tooltip("total:Q", format=","),
                            alt.Tooltip("avg:Q", format=".1f"),
                            alt.Tooltip("pct:Q", format=".1f"),
                        ],
                    )
                    .properties(height=max(CHART_MIN_H, CHART_BAR_ROW * len(ha))),
                    width="stretch",
                )
            except Exception:
                pass
        else:
            st.write("No healing events.")

    # ── Cast-count comparison (top abilities by casts) ────────────────────
    all_spells = []
    if not dmg_spells.empty:
        all_spells.append(dmg_spells.assign(type="damage"))
    if not heal_spells.empty:
        # Use the kind column (heal/absorb) if available, otherwise fall back to "heal".
        _hs = heal_spells.copy()
        _hs["type"] = _hs["kind"] if "kind" in _hs.columns else "heal"
        all_spells.append(_hs)
    if all_spells:
        combined = pd.concat(all_spells).sort_values("count", ascending=False).head(25).reset_index(drop=True)
        st.subheader("Most-cast abilities (by use count)")
        try:
            st.altair_chart(
                alt.Chart(combined)
                .mark_bar()
                .encode(
                    x=alt.X("count:Q", title="Total casts"),
                    y=alt.Y("spell:N", sort="-x", title=""),
                    color=alt.Color(
                        "type:N",
                        scale=alt.Scale(
                            domain=["damage", "heal", "absorb"],
                            range=["#7CFC00", "#4CAF50", "#42A5F5"],
                        ),
                        legend=alt.Legend(title="Type"),
                    ),
                    tooltip=[
                        "spell",
                        "type",
                        "count",
                        alt.Tooltip("avg:Q", format=".1f"),
                        alt.Tooltip("pct:Q", format=".1f"),
                    ],
                )
                .properties(height=max(200, 18 * len(combined))),
                width="stretch",
            )
        except Exception:
            st.dataframe(combined)
