"""
views/boss_comparison.py
────────────────────────
Boss Comparison page: pick a boss, then compare two runs side-by-side.

Flow:
  1. User picks a boss name (derived from boss_kills sidecar joined to enc_summary).
  2. Two selectors (Left / Right) show available runs for that boss with date + duration.
     A run is identified by (character_short_name, combat_id).
  3. Side-by-side panels render headline metrics, ability breakdown, and DPS/HPS
     time-series for each chosen combat_id — reusing spell_aggregates() and
     combat_time_series() from utils/data_engine.
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from utils.data_engine import combat_time_series, compute_runs, spell_aggregates
from utils.data_io import (
    DEFAULT_TOP_N_ABILITIES,
    _fmt_compact_amount,
    load_boss_kills,
    load_csv,
)


# ── Private helpers ───────────────────────────────────────────────────────────


def _dur_label(s: float) -> str:
    """Format seconds as M:SS."""
    s = int(s)
    return f"{s // 60}:{s % 60:02d}"


def _compact(v) -> str:
    """Return a plain-text compact amount (strip HTML tags from _fmt_compact_amount)."""
    raw = _fmt_compact_amount(v)
    return raw.replace("&nbsp;", " ").replace("<strong>", "").replace("</strong>", "")


def _build_boss_enc_table(gap_minutes: int = 20) -> pd.DataFrame:
    """Return a DataFrame with one row per boss-kill encounter.

    Columns: boss_name, combat_id, character, start_dt, end_dt, duration_s,
             total_damage, total_heal, dps, hps, run_role, run_spec.

    The join strategy:
    - Load boss_kills sidecar (has boss_name, end_ts, kill_flag, zone_id).
    - Load enc_summary from compute_runs() (has combat_id, start_dt, end_dt, zone_id).
    - Match each kill to the enc_summary row whose end_dt is within ±20 s.
    - Derive character short name from the mode source in the raw CSV for that combat_id.
    """
    try:
        runs_df, enc_summary = compute_runs(gap_minutes=gap_minutes)
    except Exception:
        return pd.DataFrame()

    if enc_summary.empty:
        return pd.DataFrame()

    kills = [bk for bk in load_boss_kills() if bk.get("kill_flag", 0) == 1]
    if not kills:
        return pd.DataFrame()

    # Attach run_role per combat_id from enc_summary → runs_df join
    run_role_map: dict[int, str] = {}
    run_spec_map: dict[int, str] = {}
    if "run_id" in enc_summary.columns and "run_role" in runs_df.columns:
        rid_to_role = runs_df.set_index("run_id")["run_role"].to_dict()
        rid_to_spec = runs_df.set_index("run_id").get("run_spec", pd.Series(dtype=str)).to_dict()
        for _, er in enc_summary.iterrows():
            rid = int(er.get("run_id", 0))
            cid = int(er["combat_id"])
            run_role_map[cid] = rid_to_role.get(rid, "DPS")
            run_spec_map[cid] = rid_to_spec.get(rid, "") if rid_to_spec else ""

    # Build a lookup: combat_id → enc row
    enc_idx = enc_summary.set_index("combat_id")

    # Load raw CSV for character resolution (once)
    raw_df = load_csv()
    char_cache: dict[int, str] = {}

    def _char_for(cid: int) -> str:
        if cid in char_cache:
            return char_cache[cid]
        try:
            sub = raw_df[raw_df["combat_id"] == cid]["source"]
            mode = sub.mode()
            name = str(mode.iloc[0]) if not mode.empty else ""
            char_cache[cid] = name
            return name
        except Exception:
            char_cache[cid] = ""
            return ""

    rows = []
    for bk in kills:
        boss_name = str(bk.get("boss_name", "Unknown"))
        try:
            bk_end = pd.Timestamp(bk["end_ts"])
        except Exception:
            continue
        bk_zone_id = int(bk.get("zone_id", 0))

        # Find matching enc_summary row (end_dt within ±20 s, same zone_id)
        for cid, er in enc_idx.iterrows():
            try:
                ez = int(er.get("zone_id", 0))
                if bk_zone_id and ez and bk_zone_id != ez:
                    continue
                enc_end = er["end_dt"]
                if abs((enc_end - bk_end).total_seconds()) <= 20:
                    dur = float(er.get("duration_s", 0))
                    char_full = _char_for(int(cid))
                    char_short = char_full.split("-")[0] if char_full else f"#{cid}"
                    dmg = float(er.get("total_damage", 0))
                    heal = float(er.get("total_heal", 0))
                    dps = dmg / dur if dur > 0 else 0.0
                    hps = heal / dur if dur > 0 else 0.0
                    rows.append(
                        {
                            "boss_name": boss_name,
                            "combat_id": int(cid),
                            "character": char_full,
                            "character_short": char_short,
                            "start_dt": er["start_dt"],
                            "end_dt": enc_end,
                            "duration_s": dur,
                            "total_damage": dmg,
                            "total_heal": heal,
                            "dps": dps,
                            "hps": hps,
                            "run_role": run_role_map.get(int(cid), "DPS"),
                            "run_spec": run_spec_map.get(int(cid), "") if run_spec_map else "",
                        }
                    )
                    break  # one encounter per boss kill
            except Exception:
                continue

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows).drop_duplicates(subset="combat_id").reset_index(drop=True)
    result = result.sort_values(["boss_name", "start_dt"], ascending=[True, False]).reset_index(drop=True)
    return result


def _run_label(row: pd.Series) -> str:
    """Human-readable label for a boss run picker option."""
    date_str = row["start_dt"].strftime("%m/%d %H:%M") if pd.notna(row["start_dt"]) else "?"
    dur_str = _dur_label(row["duration_s"])
    char = row["character_short"]
    role = row["run_role"]
    if row["dps"] > row["hps"]:
        perf = f"DPS {_compact(row['dps'])}"
    else:
        perf = f"HPS {_compact(row['hps'])}"
    return f"{char} ({role}) — {date_str} — {dur_str} — {perf}"


# ── Comparison panel ──────────────────────────────────────────────────────────


def _render_side(
    df_raw: pd.DataFrame,
    cid: int,
    row: pd.Series,
    dmg_agg: pd.DataFrame,
    heal_agg: pd.DataFrame,
    dmg_table_h: int,
    heal_table_h: int,
    dmg_chart_h: int,
    heal_chart_h: int,
    resample_s: int = 2,
) -> None:
    """Render headline metrics + ability breakdown + time-series for one combat_id.

    The caller pre-computes *dmg_agg* / *heal_agg* for both sides and passes
    uniform fixed pixel heights so the two columns stay vertically aligned
    regardless of how many rows each side has.
    """
    sub = df_raw[df_raw["combat_id"] == cid].sort_values("timestamp_dt")

    if sub.empty:
        st.warning(f"No events found for combat #{cid}.")
        return

    dur = float(row["duration_s"])
    dmg = float(row["total_damage"])
    heal = float(row["total_heal"])
    dps = row["dps"]
    hps = row["hps"]

    char_short = row["character_short"]
    role = row["run_role"]
    spec = str(row.get("run_spec", "")) or ""
    date_str = row["start_dt"].strftime("%Y-%m-%d %H:%M") if pd.notna(row["start_dt"]) else "?"

    # Header
    spec_label = f" · {spec}" if spec else ""
    st.markdown(
        f"<div style='font-size:1.05rem;font-weight:700;margin-bottom:4px'>"
        f"{char_short}<span style='color:#888;font-weight:400'>{spec_label} · {role}</span>"
        f"</div>"
        f"<div style='font-size:0.8rem;color:#888;margin-bottom:8px'>{date_str} · {_dur_label(dur)}</div>",
        unsafe_allow_html=True,
    )

    # Headline metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("DPS", f"{dps:,.0f}")
    m2.metric("HPS", f"{hps:,.0f}")
    m3.metric("Total Dmg", _compact(dmg))
    m4.metric("Total Heal", _compact(heal))

    # Ability breakdown — fixed heights passed in from caller so both columns
    # always occupy the same vertical space regardless of row count.
    st.markdown("**Abilities**")
    tab_d, tab_h = st.tabs(["Damage", "Healing"])

    with tab_d:
        if dmg_agg.empty:
            st.caption("No damage events.")
            # Reserve the same vertical space as the other side would use.
            st.markdown(
                f"<div style='height:{dmg_table_h + dmg_chart_h}px'></div>",
                unsafe_allow_html=True,
            )
        else:
            st.dataframe(
                dmg_agg[["spell", "count", "total", "avg", "pct"]].style.format(
                    {"total": "{:,.0f}", "avg": "{:.1f}", "pct": "{:.1f}%"}
                ),
                hide_index=True,
                use_container_width=True,
                height=dmg_table_h,
            )
            try:
                st.altair_chart(
                    alt.Chart(dmg_agg.reset_index(drop=True))
                    .mark_bar(color="#7CFC00")
                    .encode(
                        x=alt.X("total:Q", title="Total damage"),
                        y=alt.Y("spell:N", sort="-x", title=""),
                        tooltip=[
                            "spell",
                            alt.Tooltip("total:Q", format=","),
                            alt.Tooltip("count:Q"),
                            alt.Tooltip("avg:Q", format=".1f"),
                            alt.Tooltip("pct:Q", format=".1f"),
                        ],
                    )
                    .properties(height=dmg_chart_h),
                    use_container_width=True,
                )
            except Exception:
                pass

    with tab_h:
        if heal_agg.empty:
            st.caption("No healing events.")
            st.markdown(
                f"<div style='height:{heal_table_h + heal_chart_h}px'></div>",
                unsafe_allow_html=True,
            )
        else:
            st.dataframe(
                heal_agg[["spell", "count", "total", "avg", "pct"]].style.format(
                    {"total": "{:,.0f}", "avg": "{:.1f}", "pct": "{:.1f}%"}
                ),
                hide_index=True,
                use_container_width=True,
                height=heal_table_h,
            )
            try:
                st.altair_chart(
                    alt.Chart(heal_agg.reset_index(drop=True))
                    .mark_bar(color="#00CED1")
                    .encode(
                        x=alt.X("total:Q", title="Total healing"),
                        y=alt.Y("spell:N", sort="-x", title=""),
                        tooltip=[
                            "spell",
                            alt.Tooltip("total:Q", format=","),
                            alt.Tooltip("count:Q"),
                            alt.Tooltip("avg:Q", format=".1f"),
                            alt.Tooltip("pct:Q", format=".1f"),
                        ],
                    )
                    .properties(height=heal_chart_h),
                    use_container_width=True,
                )
            except Exception:
                pass

    # DPS / HPS time-series
    st.markdown("**DPS / HPS over time**")
    try:
        ts = combat_time_series(sub, resample_s=resample_s)
        if not ts.empty:
            ts_reset = ts.reset_index().rename(columns={"index": "time", "timestamp_dt": "time"})
            ts_long = ts_reset.melt(id_vars="time", value_vars=["DPS", "HPS"], var_name="Metric", value_name="Value")
            ts_long = ts_long[ts_long["Value"] > 0]
            color_scale = alt.Scale(domain=["DPS", "HPS"], range=["#7CFC00", "#00CED1"])
            st.altair_chart(
                alt.Chart(ts_long)
                .mark_line(interpolate="monotone", strokeWidth=1.5)
                .encode(
                    x=alt.X("time:T", title="Time"),
                    y=alt.Y("Value:Q", title=""),
                    color=alt.Color("Metric:N", scale=color_scale),
                    tooltip=["time:T", "Metric:N", alt.Tooltip("Value:Q", format=".0f")],
                )
                .properties(height=180),
                use_container_width=True,
            )
    except Exception:
        st.caption("Time-series unavailable.")


# ── Public view ───────────────────────────────────────────────────────────────


def boss_comparison_view() -> None:
    """Boss Comparison page: pick a boss, pick two runs, compare side-by-side."""
    st.header("Boss Comparison")

    # Load boss encounter table (cached implicitly via compute_runs cache)
    with st.spinner("Loading boss kill data…"):
        boss_enc = _build_boss_enc_table()

    if boss_enc.empty:
        st.info(
            "No boss kills found.\n\n"
            "Boss kills are recorded when the WoW combat log emits an `ENCOUNTER_END` event "
            "with `kill_flag=1`. Make sure you have run `python wow-parser.py --full-import` "
            "after a session where you killed a boss."
        )
        return

    # ── Boss selector ─────────────────────────────────────────────────────
    boss_names = sorted(boss_enc["boss_name"].unique().tolist())
    selected_boss = st.selectbox(
        "Boss",
        options=boss_names,
        key="bcomp_boss",
        help="Select the boss encounter to compare.",
    )

    boss_runs = boss_enc[boss_enc["boss_name"] == selected_boss].reset_index(drop=True)

    if boss_runs.empty:
        st.warning("No kill records for this boss.")
        return

    # ── Kill table overview ───────────────────────────────────────────────
    with st.expander(f"All kills for {selected_boss} ({len(boss_runs)})", expanded=False):
        disp = boss_runs[["character_short", "run_role", "run_spec", "start_dt", "duration_s", "dps", "hps"]].copy()
        disp["start_dt"] = disp["start_dt"].dt.strftime("%Y-%m-%d %H:%M")
        disp["duration_s"] = disp["duration_s"].apply(_dur_label)
        disp = disp.rename(
            columns={
                "character_short": "Character",
                "run_role": "Role",
                "run_spec": "Spec",
                "start_dt": "Date",
                "duration_s": "Duration",
                "dps": "DPS",
                "hps": "HPS",
            }
        )
        st.dataframe(
            disp.style.format({"DPS": "{:,.0f}", "HPS": "{:,.0f}"}),
            hide_index=True,
            use_container_width=True,
        )

    # ── Run pickers ───────────────────────────────────────────────────────
    # Build option labels
    options_map: dict[str, int] = {}  # label → combat_id
    for _, row in boss_runs.iterrows():
        label = _run_label(row)
        # Deduplicate labels if needed (same char + same timestamp is unusual but possible)
        base = label
        suffix = 1
        while label in options_map:
            label = f"{base} [{suffix}]"
            suffix += 1
        options_map[label] = int(row["combat_id"])

    option_labels = list(options_map.keys())

    if len(option_labels) < 1:
        st.warning("Not enough kill records to compare.")
        return

    st.markdown("---")
    col_l_pick, col_r_pick = st.columns(2)

    with col_l_pick:
        st.markdown("**Left run**")
        left_label = st.selectbox(
            "Left",
            options=option_labels,
            index=0,
            key="bcomp_left",
            label_visibility="collapsed",
        )

    # Right picker: if only one option, allow same run (self-comparison)
    with col_r_pick:
        st.markdown("**Right run**")
        right_default = 1 if len(option_labels) > 1 else 0
        right_label = st.selectbox(
            "Right",
            options=option_labels,
            index=right_default,
            key="bcomp_right",
            label_visibility="collapsed",
        )

    left_cid = options_map[left_label]
    right_cid = options_map[right_label]

    left_row = boss_runs[boss_runs["combat_id"] == left_cid].iloc[0]
    right_row = boss_runs[boss_runs["combat_id"] == right_cid].iloc[0]

    # ── Load raw data once, filter to the two combats ────────────────────
    df_raw = load_csv()
    if df_raw is None or df_raw.empty:
        st.error("No parsed CSV data available.")
        return

    needed_cids = list({left_cid, right_cid})
    df_filtered = df_raw[df_raw["combat_id"].isin(needed_cids)]

    st.markdown("---")

    # ── Self-comparison guard / delta header ─────────────────────────────
    if left_cid == right_cid:
        st.info("Both sides show the same combat — select different runs to compare.")

    # Show delta summary row when both sides differ
    if left_cid != right_cid:
        d_dps = left_row["dps"] - right_row["dps"]
        d_hps = left_row["hps"] - right_row["hps"]
        d_dur = left_row["duration_s"] - right_row["duration_s"]
        delta_col1, delta_col2, delta_col3 = st.columns(3)
        delta_col1.metric(
            "ΔDPS (Left − Right)",
            f"{d_dps:+,.0f}",
            delta=f"{d_dps:+,.0f}",
            delta_color="normal",
        )
        delta_col2.metric(
            "ΔHPS (Left − Right)",
            f"{d_hps:+,.0f}",
            delta=f"{d_hps:+,.0f}",
            delta_color="normal",
        )
        delta_col3.metric(
            "ΔDuration (s)",
            f"{d_dur:+.0f}s",
            delta=f"{d_dur:+.0f}",
            delta_color="inverse",  # shorter is better
        )
        st.markdown("---")

    # ── Side-by-side panels ───────────────────────────────────────────────
    resample_s = st.sidebar.selectbox(
        "Resample interval (s)",
        options=[1, 2, 3, 5],
        index=1,
        key="bcomp_resample",
    )

    # Pre-compute spell aggregates for both sides so we can derive uniform
    # fixed heights before entering the two columns.  This ensures the table
    # and bar-chart blocks are identical in pixel height on both sides even
    # when one side has fewer spells than the other.
    ROW_PX = 35       # approximate height per data row in st.dataframe
    HEADER_PX = 38    # fixed header overhead for st.dataframe
    BAR_PX = 22       # height per bar in the Altair chart
    MIN_TABLE_H = 120 # minimum pixel height for table
    MIN_CHART_H = 120 # minimum pixel height for chart

    left_sub = df_filtered[df_filtered["combat_id"] == left_cid]
    right_sub = df_filtered[df_filtered["combat_id"] == right_cid]

    left_dmg_agg = spell_aggregates(left_sub, "damage", top_n=DEFAULT_TOP_N_ABILITIES)
    right_dmg_agg = spell_aggregates(right_sub, "damage", top_n=DEFAULT_TOP_N_ABILITIES)
    left_heal_agg = spell_aggregates(left_sub, ["heal", "absorb"], top_n=DEFAULT_TOP_N_ABILITIES)
    right_heal_agg = spell_aggregates(right_sub, ["heal", "absorb"], top_n=DEFAULT_TOP_N_ABILITIES)

    max_dmg_rows = max(len(left_dmg_agg), len(right_dmg_agg), 1)
    max_heal_rows = max(len(left_heal_agg), len(right_heal_agg), 1)

    dmg_table_h = max(MIN_TABLE_H, HEADER_PX + ROW_PX * max_dmg_rows)
    heal_table_h = max(MIN_TABLE_H, HEADER_PX + ROW_PX * max_heal_rows)
    dmg_chart_h = max(MIN_CHART_H, BAR_PX * max_dmg_rows)
    heal_chart_h = max(MIN_CHART_H, BAR_PX * max_heal_rows)

    col_left, col_right = st.columns(2)
    with col_left:
        _render_side(
            df_filtered, left_cid, left_row,
            dmg_agg=left_dmg_agg, heal_agg=left_heal_agg,
            dmg_table_h=dmg_table_h, heal_table_h=heal_table_h,
            dmg_chart_h=dmg_chart_h, heal_chart_h=heal_chart_h,
            resample_s=resample_s,
        )
    with col_right:
        _render_side(
            df_filtered, right_cid, right_row,
            dmg_agg=right_dmg_agg, heal_agg=right_heal_agg,
            dmg_table_h=dmg_table_h, heal_table_h=heal_table_h,
            dmg_chart_h=dmg_chart_h, heal_chart_h=heal_chart_h,
            resample_s=resample_s,
        )
