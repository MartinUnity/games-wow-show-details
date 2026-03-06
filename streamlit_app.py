"""
streamlit_app.py
────────────────
Thin orchestrator: page config, global CSS, sidebar controls,
view routing, and session banner.  All business logic lives in
utils/ and views/ — this file should stay under ~150 lines.
"""

import os
from datetime import datetime

import altair as alt
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from utils.data_engine import (
    compute_all_encounters_stats,
    compute_totals_summary,
    spell_aggregates,
)

# ── Imports from the modular packages ────────────────────────────────────────
from utils.data_io import (
    CSV_PATH,
    _fmt_compact_amount,
    compute_character_counts,
    load_csv,
    load_hidden,
    toggle_hidden,
)
from views.all_encounters import all_encounters_view
from views.character_comparison import character_comparison_view
from views.combat_detail import combat_detail_view
from views.runs import runs_view
from views.summary_sidebar import summary_view

# ── Page config (must be the first Streamlit call) ────────────────────────────
st.set_page_config(page_title="WoW Combat Viewer", layout="wide")


def main():
    # Tight header to reduce vertical padding at top
    st.markdown(
        """
        <style>
        .block-container { padding-top: 0.5rem !important; padding-bottom: 0.5rem !important; }
        header[data-testid="stHeader"] { height: 2rem !important; min-height: 2rem !important; }
        section[data-testid="stSidebar"] > div:first-child { padding-top: 0.5rem !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        "<p style='margin:2rem 0 4px 0; font-size:1.25rem; font-weight:700; line-height:1.2;'>⚔️ WoW Combat Viewer</p>",
        unsafe_allow_html=True,
    )

    # --- Live-follow controls (top of sidebar) ---
    follow_live = st.sidebar.checkbox("Follow live", value=True, key="follow_live")
    if follow_live:
        st_autorefresh(interval=3000, key="live_autorefresh")  # rerun every 3 s

    if st.sidebar.button("Show Latest", help="Reset selection to the most recent encounter."):
        st.session_state["combat_select"] = 0
        st.rerun()

    # CSV age indicator
    try:
        age_s = int(datetime.now().timestamp() - os.path.getmtime(CSV_PATH))
        if age_s < 60:
            st.sidebar.caption(f"CSV last updated: {age_s}s ago")
        else:
            st.sidebar.caption(f"CSV last updated: {age_s // 60}m {age_s % 60}s ago")
    except Exception:
        pass

    st.sidebar.markdown("---")

    # Chart controls: resample and smoothing (seconds)
    resample_s = st.sidebar.selectbox("Resample interval (s)", options=[1, 2, 3, 5], index=1)
    smooth_s = st.sidebar.selectbox("Smoothing window (s, 0=off)", options=[0, 2, 3, 5, 10], index=2)
    show_replay = st.sidebar.checkbox("Enable 2D Replay", value=False, help="Requires Advanced Combat Logging logs.")
    view = st.sidebar.radio(
        "View", options=["Combat Viewer", "Runs", "All Encounters", "Totals", "Character Comparison"], index=0
    )

    # Load CSV to populate character selector
    df_chars = load_csv()
    char_col = None
    if not df_chars.empty:
        for cand in ("source", "player", "character", "name"):
            if cand in df_chars.columns:
                char_col = cand
                break

    if char_col:
        raw_names = [n for n in df_chars[char_col].dropna().unique() if n and str(n).strip() != ""]

        def is_player_like(n: str) -> bool:
            try:
                s = str(n).strip()
                parts = s.split("-")
                return len(parts) == 3 and all(p for p in parts)
            except Exception:
                return False

        player_like = sorted([n for n in raw_names if is_player_like(n)])
        others = sorted([n for n in raw_names if not is_player_like(n)])
        include_non_player = st.sidebar.checkbox("Include non-player sources", value=False)
        min_source_combats = st.sidebar.slider("Min combats to show (sources)", min_value=1, max_value=10, value=3)
        try:
            counts_df = compute_character_counts()
            counts_map = dict(zip(counts_df["source"], counts_df["combats"]))
        except Exception:
            counts_map = {}

        if include_non_player:
            others_filtered = [n for n in others if counts_map.get(n, 0) >= min_source_combats]
            options = ["All"] + player_like + others_filtered
        else:
            options = ["All"] + player_like
    else:
        options = ["All"]

    if "character_select" not in st.session_state:
        st.session_state["character_select"] = "All"

    selected_character = st.sidebar.selectbox("Character", options=options, index=0, key="character_select")

    # ── Session summary banner ──────────────────────────────────────────
    try:
        _today = datetime.now().date()
        _banner_df = df_chars[df_chars["combat_id"] > 0].copy() if not df_chars.empty else None
        if _banner_df is not None and not _banner_df.empty and "timestamp_dt" in _banner_df.columns:
            _banner_df = _banner_df[_banner_df["timestamp_dt"].dt.date == _today]
        if _banner_df is not None and not _banner_df.empty:
            _t_cids = _banner_df["combat_id"].unique()
            _n_enc = len(_t_cids)
            _enc_times = _banner_df.groupby("combat_id")["timestamp_dt"].agg(["min", "max"])
            _enc_times["dur"] = (_enc_times["max"] - _enc_times["min"]).dt.total_seconds().clip(lower=0)
            _total_played_s = int(_enc_times["dur"].sum())
            _played_str = (
                f"{_total_played_s // 3600}h {(_total_played_s % 3600) // 60}m"
                if _total_played_s >= 3600
                else f"{_total_played_s // 60}m {_total_played_s % 60}s"
            )
            _dmg = _banner_df[_banner_df["type"] == "damage"].groupby("combat_id")["effective_amount"].sum()
            _enc_dps = (_dmg / _enc_times["dur"].clip(lower=1)).dropna()
            _best_cid = int(_enc_dps.idxmax()) if not _enc_dps.empty else None
            _best_dps_val = _enc_dps.max() if not _enc_dps.empty else 0
            _best_target = ""
            if _best_cid:
                _bsub = _banner_df[_banner_df["combat_id"] == _best_cid]
                _dmg_tgts = _bsub[_bsub["type"] == "damage"].groupby("target")["effective_amount"].sum()
                try:
                    _pn = _bsub["source"].mode().iloc[0]
                    _dmg_tgts = _dmg_tgts.drop(_pn, errors="ignore")
                except Exception:
                    pass
                if not _dmg_tgts.empty:
                    _best_target = str(_dmg_tgts.idxmax()).split("-")[0]
            _best_str = f"{_fmt_compact_amount(_best_dps_val)} DPS" + (f" on {_best_target}" if _best_target else "")
            st.markdown(
                f"<p style='margin:0 0 6px 0;font-size:0.82rem;color:#aaa;'>"
                f"📅 Today &nbsp;·&nbsp; <b style='color:#fff'>{_n_enc}</b> encounters"
                f" &nbsp;·&nbsp; Best <b style='color:#7CFC00'>{_best_str}</b>"
                f" &nbsp;·&nbsp; <b style='color:#fff'>{_played_str}</b> played"
                f"</p>",
                unsafe_allow_html=True,
            )
    except Exception:
        pass

    # ── View routing ──────────────────────────────────────────────────────

    if view == "Runs":
        runs_view()
        return

    if view == "Totals":
        char = st.session_state.get("character_select", "All")
        char_arg = None if char == "All" else char
        totals_df, meta = compute_totals_summary(character=char_arg)

        st.header("Totals Summary")
        if totals_df.empty:
            st.write("No parsed data available (parsed_combat_data.csv).")
            return

        st.markdown(
            f"- **Total combats:** {meta.get('total_combats', 0)}  \n"
            f"- **Total duration (s):** {int(meta.get('total_duration_s', 0))}  \n"
            f"- **Unique targets:** {meta.get('unique_targets', 0)}"
        )
        if char and char != "All":
            st.markdown(f"- **Character:** {char}")

        tab_target, tab_ability = st.tabs(["By Target", "By Ability"])

        with tab_target:
            try:
                char_counts = compute_character_counts()
                if not char_counts.empty:
                    st.subheader("Characters (by combat count)")
                    st.dataframe(char_counts.head(50).reset_index(drop=True))
            except Exception:
                pass
            st.subheader("Targets")
            sort_opt = st.selectbox(
                "Sort by",
                ["encounters", "total_damage", "total_time_s", "dps"],
                key="totals_target_sort",
            )
            st.dataframe(
                totals_df.sort_values(sort_opt, ascending=False)
                .reset_index(drop=True)
                .head(50)
                .style.format(
                    {
                        "total_damage": "{:,.0f}",
                        "total_heal": "{:,.0f}",
                        "dps": "{:.1f}",
                        "hps": "{:.1f}",
                        "total_time_s": "{:.0f}",
                    }
                )
            )
            csv_bytes = totals_df.sort_values(sort_opt, ascending=False).to_csv(index=False).encode()
            st.download_button("Download targets CSV", csv_bytes, file_name="totals_targets.csv", mime="text/csv")

        with tab_ability:
            _, _, dmg_spells, heal_spells, _ = compute_all_encounters_stats(character=char_arg)
            col_d, col_h = st.columns(2)
            with col_d:
                st.subheader("Damage abilities")
                if not dmg_spells.empty:
                    st.dataframe(
                        dmg_spells.style.format({"total": "{:,.0f}", "avg": "{:.1f}", "pct": "{:.1f}%"}),
                        hide_index=True,
                    )
                    try:
                        st.altair_chart(
                            alt.Chart(dmg_spells.reset_index(drop=True))
                            .mark_bar()
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
                            .properties(height=max(180, 22 * len(dmg_spells))),
                            width="stretch",
                        )
                    except Exception:
                        pass
                    csv_dmg = dmg_spells.to_csv(index=False).encode()
                    st.download_button(
                        "Download damage abilities CSV",
                        csv_dmg,
                        file_name="totals_dmg_abilities.csv",
                        mime="text/csv",
                        key="dl_dmg_spells",
                    )
                else:
                    st.write("No damage events.")
            with col_h:
                st.subheader("Healing abilities")
                if not heal_spells.empty:
                    st.dataframe(
                        heal_spells.style.format({"total": "{:,.0f}", "avg": "{:.1f}", "pct": "{:.1f}%"}),
                        hide_index=True,
                    )
                    try:
                        st.altair_chart(
                            alt.Chart(heal_spells.reset_index(drop=True))
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
                            .properties(height=max(180, 22 * len(heal_spells))),
                            width="stretch",
                        )
                    except Exception:
                        pass
                    csv_heal = heal_spells.to_csv(index=False).encode()
                    st.download_button(
                        "Download healing abilities CSV",
                        csv_heal,
                        file_name="totals_heal_abilities.csv",
                        mime="text/csv",
                        key="dl_heal_spells",
                    )
                else:
                    st.write("No healing events.")
        return

    if view == "All Encounters":
        char = st.session_state.get("character_select", "All")
        all_encounters_view(df_chars, character=(None if char == "All" else char))
        return

    if view == "Character Comparison":
        player_like_chars = sorted(
            [
                n
                for n in (df_chars["source"].dropna().unique() if not df_chars.empty else [])
                if len(str(n).split("-")) == 3 and all(str(n).split("-"))
            ]
        )
        character_comparison_view(player_like_chars)
        return

    # ── Combat Viewer ─────────────────────────────────────────────────────
    df = load_csv()

    if df.empty:
        st.write("No parsed data available. Run: python wow-parser.py --full-import")
        return

    # Auto-select latest encounter when a new one arrives (Follow live)
    try:
        current_max_cid = int(df["combat_id"].max()) if "combat_id" in df.columns else 0
    except Exception:
        current_max_cid = 0
    last_max_cid = st.session_state.get("_last_max_cid", current_max_cid)
    if follow_live and current_max_cid > last_max_cid:
        st.session_state["combat_select"] = 0  # 0 → show latest
    st.session_state["_last_max_cid"] = current_max_cid

    # Show encounter count in sidebar
    try:
        _n_enc = int(df["combat_id"].nunique()) if "combat_id" in df.columns else 0
        _has_zero = 0 in df["combat_id"].values if "combat_id" in df.columns else False
        _real_enc = _n_enc - (1 if _has_zero else 0)
        st.sidebar.success(f"{_real_enc} encounters loaded")
    except Exception:
        pass

    # Apply character filter if selected
    if selected_character and selected_character != "All":
        char_col = None
        for cand in ("source", "player", "character", "name"):
            if cand in df.columns:
                char_col = cand
                break
        if char_col:
            df = df[df[char_col] == selected_character]

    if df.empty:
        st.write("No events after applying filters.")
        return

    # Compute available max_combats now (used for query param handling)
    try:
        max_combats = int(df["combat_id"].max()) if "combat_id" in df.columns else 1
    except Exception:
        max_combats = 1
    if max_combats < 1:
        max_combats = 1

    # Respect `?combat=<id>` and `?num_combats=` query params early
    try:
        params = st.experimental_get_query_params()
        if "combat" in params and params["combat"]:
            try:
                st.session_state["combat_select"] = int(params["combat"][0])
            except Exception:
                pass
        if "num_combats" in params and params["num_combats"]:
            try:
                val = int(params["num_combats"][0])
                st.session_state["num_combats"] = min(max_combats, max(1, val))
            except Exception:
                pass
    except Exception:
        pass

    # Decide which combat to show now (used for sidebar controls)
    selected_cid = int(st.session_state.get("combat_select", 0))
    if selected_cid == 0:
        try:
            combat_to_show = int(df["combat_id"].max())
        except Exception:
            combat_to_show = 0
    else:
        combat_to_show = selected_cid

    # Compute available abilities for this combat so the slider can be dynamic
    combat_df_preview = df[df["combat_id"] == combat_to_show]
    dmg_agg_preview = spell_aggregates(combat_df_preview, "damage", top_n=1000)
    heal_agg_preview = spell_aggregates(combat_df_preview, "heal", top_n=1000)
    max_abilities = max(len(dmg_agg_preview), len(heal_agg_preview))

    if max_abilities <= 1:
        st.sidebar.markdown(f"**Abilities to show:** {max_abilities if max_abilities>0 else 0}")
        top_n = max(1, max_abilities)
    else:
        min_val = 1
        max_val = max_abilities + 1
        if "abilities_n" not in st.session_state:
            st.session_state["abilities_n"] = min(7, max_val)
        top_n = st.sidebar.slider(
            "Abilities to show",
            min_value=min_val,
            max_value=max_val,
            value=st.session_state["abilities_n"],
            key="abilities_n",
        )

    # Show-last-N-combats slider
    try:
        max_combats = int(df["combat_id"].max()) if "combat_id" in df.columns else 1
    except Exception:
        max_combats = 1
    if max_combats < 1:
        max_combats = 1
    st.sidebar.markdown("---")
    st.sidebar.markdown("**Combats to display (filtered by Character)**")
    _nc_options = [10, 25, 50, 100, 250, "All"]
    _nc_default = min(25, max_combats)
    if "num_combats" not in st.session_state:
        st.session_state["num_combats"] = _nc_default
    _nc_select = st.sidebar.selectbox(
        "Show last N combats",
        options=_nc_options,
        index=_nc_options.index(25) if 25 in _nc_options else 0,
        key="num_combats_select",
    )
    num_combats_val = max_combats if _nc_select == "All" else int(_nc_select)
    st.session_state["num_combats"] = num_combats_val

    # Hidden encounters management
    _hidden = load_hidden()
    if _hidden:
        with st.sidebar.expander(f"Hidden encounters ({len(_hidden)})", expanded=False):
            st.caption("Click a button to restore an encounter to the list.")
            for _hid in sorted(_hidden):
                if st.button(f"Unhide #{_hid}", key=f"unhide_sidebar_{_hid}"):
                    toggle_hidden(_hid)
                    st.rerun()

    # Render summary (left) and combat details (right) in a 1:3 split
    col_left, col_right = st.columns([1, 3])
    with col_left:
        sel_override = summary_view(df, num_combats=st.session_state.get("num_combats", 25))

    with col_right:
        if sel_override:
            combat_detail_view(
                df, sel_override, resample_s=resample_s, smooth_s=smooth_s, top_n=top_n, show_replay=show_replay
            )
        else:
            combat_detail_view(
                df, combat_to_show, resample_s=resample_s, smooth_s=smooth_s, top_n=top_n, show_replay=show_replay
            )


if __name__ == "__main__":
    main()
