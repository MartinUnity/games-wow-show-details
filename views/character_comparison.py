"""
views/character_comparison.py
──────────────────────────────
Side-by-side comparison of two or more player characters:
headline metrics, per-encounter DPS/HPS bars, top spell breakdowns,
and Jaccard-similarity matched encounter pairing.
"""

import altair as alt
import pandas as pd
import streamlit as st

from utils.data_engine import compute_all_encounters_stats
from utils.data_io import _fmt_compact_amount, load_csv

# ── Private helper ────────────────────────────────────────────────────────────


def _encounter_fingerprints(df: pd.DataFrame, char: str) -> dict:
    """Return {combat_id: frozenset_of_targets} for one character, excluding themselves as a target."""
    char_df = df[(df["source"] == char) & (df["combat_id"] > 0)]
    result = {}
    for cid, grp in char_df.groupby("combat_id"):
        targets = frozenset(t for t in grp["target"].dropna().unique() if t and t != char)
        result[int(cid)] = targets
    return result


# ── Public view ───────────────────────────────────────────────────────────────


def character_comparison_view(available_chars: list):
    """Side-by-side comparison of two or more characters across all their encounters."""
    st.header("Character Comparison")

    if len(available_chars) < 2:
        st.info(
            "Need at least 2 player-like characters in the data to compare. Play some encounters with another character first."
        )
        return

    selected = st.multiselect(
        "Characters to compare",
        options=available_chars,
        default=available_chars[:2],
        key="cmp_chars",
    )
    if len(selected) < 2:
        st.info("Select at least 2 characters.")
        return

    # Gather stats per character
    char_stats = {}  # char -> (meta, enc_df, dmg_spells, heal_spells, top_targets)
    for char in selected:
        char_stats[char] = compute_all_encounters_stats(character=char)

    # ── Headline metrics ──────────────────────────────────────────────────
    def _cmp_stat(label: str, value, color: str = "inherit"):
        """Render a compact labeled stat with optional color."""
        st.markdown(
            f"<div style='margin:2px 0'>"
            f"<span style='font-size:0.75rem;color:#888'>{label}</span><br>"
            f"<span style='font-size:1.1rem;font-weight:600;color:{color}'>{value}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

    cols = st.columns(len(selected))
    for i, char in enumerate(selected):
        meta, enc_df, _, _, _ = char_stats[char]
        short = char.split("-")[0]
        best_dps = float(enc_df["dps"].max()) if not enc_df.empty else 0.0
        with cols[i]:
            st.subheader(short)
            m1, m2 = st.columns(2)
            with m1:
                st.metric("Encounters", meta.get("n_encounters", 0))
            with m2:
                _cmp_stat("Avg DPS", _fmt_compact_amount(meta.get("avg_dps", 0)), "#7CFC00")
            m3, m4 = st.columns(2)
            with m3:
                _cmp_stat("Avg HPS", _fmt_compact_amount(meta.get("avg_hps", 0)), "#00CED1")
            with m4:
                _cmp_stat("Best DPS", _fmt_compact_amount(best_dps), "#7CFC00")
            m5, m6 = st.columns(2)
            with m5:
                _cmp_stat("Total Healing", _fmt_compact_amount(meta.get("total_heal", 0)), "#00CED1")
            with m6:
                _cmp_stat("Total Damage", _fmt_compact_amount(meta.get("total_damage", 0)), "#7CFC00")

    st.markdown("---")

    # ── Per-encounter DPS / HPS grouped bar ───────────────────────────────
    max_enc = st.number_input(
        "Max recent encounters per character",
        min_value=5,
        max_value=500,
        value=75,
        step=5,
        key="cmp_max_enc",
        help="Only the N most recent encounters (by combat ID) are shown in the per-encounter charts.",
    )
    enc_rows = []
    for char in selected:
        _, enc_df, _, _, _ = char_stats[char]
        if enc_df.empty:
            continue
        short = char.split("-")[0]
        tmp = enc_df.sort_values("combat_id").tail(int(max_enc)).copy()
        tmp["character"] = short
        tmp["enc_seq"] = range(1, len(tmp) + 1)
        enc_rows.append(tmp)

    if enc_rows:
        combined_enc = pd.concat(enc_rows, ignore_index=True)
        tab_dps, tab_hps = st.tabs(["DPS per encounter", "HPS per encounter"])

        with tab_dps:
            try:
                dps_chart = (
                    alt.Chart(combined_enc)
                    .mark_bar(opacity=0.85)
                    .encode(
                        x=alt.X("enc_seq:O", title="Encounter #"),
                        y=alt.Y("dps:Q", title="DPS", stack=None),
                        color=alt.Color("character:N", title="Character"),
                        xOffset=alt.XOffset("character:N"),
                        tooltip=[
                            alt.Tooltip("character:N", title="Character"),
                            alt.Tooltip("enc_seq:Q", title="Encounter #"),
                            alt.Tooltip("dps:Q", format=",.0f", title="DPS"),
                            alt.Tooltip("duration_s:Q", format=".0f", title="Duration (s)"),
                        ],
                    )
                    .properties(height=320)
                )
                st.altair_chart(dps_chart, width="stretch")
            except Exception:
                pass

        with tab_hps:
            try:
                hps_chart = (
                    alt.Chart(combined_enc)
                    .mark_bar(opacity=0.85)
                    .encode(
                        x=alt.X("enc_seq:O", title="Encounter #"),
                        y=alt.Y("hps:Q", title="HPS", stack=None),
                        color=alt.Color("character:N", title="Character"),
                        xOffset=alt.XOffset("character:N"),
                        tooltip=[
                            alt.Tooltip("character:N", title="Character"),
                            alt.Tooltip("enc_seq:Q", title="Encounter #"),
                            alt.Tooltip("hps:Q", format=",.0f", title="HPS"),
                            alt.Tooltip("duration_s:Q", format=".0f", title="Duration (s)"),
                        ],
                    )
                    .properties(height=320)
                )
                st.altair_chart(hps_chart, width="stretch")
            except Exception:
                pass

    st.markdown("---")

    # ── Ability breakdown: damage ─────────────────────────────────────────
    st.subheader("Top damage spells")
    dmg_cols = st.columns(len(selected))
    for i, char in enumerate(selected):
        _, _, dmg_spells, _, _ = char_stats[char]
        short = char.split("-")[0]
        with dmg_cols[i]:
            st.markdown(f"**{short}**")
            if not dmg_spells.empty:
                top = dmg_spells.head(10).copy()
                try:
                    bar = (
                        alt.Chart(top)
                        .mark_bar()
                        .encode(
                            x=alt.X("total:Q", title="Total damage"),
                            y=alt.Y("spell:N", sort="-x", title=""),
                            tooltip=[
                                "spell",
                                alt.Tooltip("total:Q", format=","),
                                alt.Tooltip("pct:Q", format=".1f", title="% of total"),
                                alt.Tooltip("count:Q"),
                            ],
                        )
                        .properties(height=max(180, 22 * len(top)))
                    )
                    st.altair_chart(bar, width="stretch")
                except Exception:
                    pass
                st.dataframe(
                    top.style.format({"total": "{:,.0f}", "avg": "{:.0f}", "pct": "{:.1f}%"}),
                    hide_index=True,
                )
            else:
                st.write("No damage data.")

    st.markdown("---")

    # ── Ability breakdown: healing ────────────────────────────────────────
    st.subheader("Top healing spells")
    heal_cols = st.columns(len(selected))
    for i, char in enumerate(selected):
        _, _, _, heal_spells, _ = char_stats[char]
        short = char.split("-")[0]
        with heal_cols[i]:
            st.markdown(f"**{short}**")
            if not heal_spells.empty:
                top = heal_spells.head(10).copy()
                try:
                    bar = (
                        alt.Chart(top)
                        .mark_bar(color="#00CED1")
                        .encode(
                            x=alt.X("total:Q", title="Total healing"),
                            y=alt.Y("spell:N", sort="-x", title=""),
                            tooltip=[
                                "spell",
                                alt.Tooltip("total:Q", format=","),
                                alt.Tooltip("pct:Q", format=".1f", title="% of total"),
                                alt.Tooltip("count:Q"),
                            ],
                        )
                        .properties(height=max(180, 22 * len(top)))
                    )
                    st.altair_chart(bar, width="stretch")
                except Exception:
                    pass
                st.dataframe(
                    top.style.format({"total": "{:,.0f}", "avg": "{:.0f}", "pct": "{:.1f}%"}),
                    hide_index=True,
                )
            else:
                st.write("No healing data.")

    st.markdown("---")
    st.subheader("Paired encounters")
    st.caption(
        "Encounters are matched across characters by the set of targets they fought. "
        "Adjust the similarity threshold to allow partial overlaps (e.g. same boss with different trash)."
    )

    jaccard_thresh = st.slider(
        "Similarity threshold (1.0 = exact target set, 0.5 = half the targets in common)",
        min_value=0.5,
        max_value=1.0,
        value=1.0,
        step=0.05,
        key="cmp_jaccard",
    )

    df_full = load_csv()
    df_full = df_full[df_full["combat_id"] > 0]

    # Per-source per-encounter durations for DPS/HPS calculation
    enc_times_full = df_full.groupby(["source", "combat_id"])["timestamp_dt"].agg(["min", "max"]).reset_index()
    enc_times_full["duration_s"] = (enc_times_full["max"] - enc_times_full["min"]).dt.total_seconds().clip(lower=0)

    # Build fingerprints per selected character, capped to most recent max_enc encounters
    _cap = int(st.session_state.get("cmp_max_enc", 75))

    def _cap_fingerprints(fp: dict, n: int) -> dict:
        recent_ids = sorted(fp.keys())[-n:]
        return {cid: fp[cid] for cid in recent_ids}

    fingerprints = {char: _cap_fingerprints(_encounter_fingerprints(df_full, char), _cap) for char in selected}

    # Use the character with fewest encounters as the reference to iterate over
    ref_char = min(selected, key=lambda c: len(fingerprints[c]))
    other_chars = [c for c in selected if c != ref_char]

    matched_groups = []  # [{char: combat_id, "label": str}, ...]
    used = {c: set() for c in selected}

    for ref_cid, ref_fp in fingerprints[ref_char].items():
        if not ref_fp:
            continue
        group = {ref_char: ref_cid}
        all_matched = True
        for other_char in other_chars:
            best_sim, best_cid = 0.0, None
            for other_cid, other_fp in fingerprints[other_char].items():
                if other_cid in used[other_char] or not other_fp:
                    continue
                union = len(ref_fp | other_fp)
                sim = len(ref_fp & other_fp) / union if union else 0.0
                if sim > best_sim:
                    best_sim, best_cid = sim, other_cid
            if best_sim >= jaccard_thresh and best_cid is not None:
                group[other_char] = best_cid
            else:
                all_matched = False
                break
        if all_matched:
            for c, cid in group.items():
                used[c].add(cid)
            label = ", ".join(sorted(ref_fp)[:4])
            if len(ref_fp) > 4:
                label += f" +{len(ref_fp) - 4}"
            matched_groups.append({"group": group, "label": label})

    unmatched = len(fingerprints[ref_char]) - len(matched_groups)
    if not matched_groups:
        st.info(
            f"No encounters matched at {jaccard_thresh:.0%} similarity. "
            f"Try lowering the threshold, or play more encounters with both characters "
            f"against the same targets."
        )
    else:
        st.caption(
            f"**{len(matched_groups)}** paired group(s) found "
            f"({'exact' if jaccard_thresh == 1.0 else f'≥{jaccard_thresh:.0%} similarity'}). "
            f"{unmatched} encounter(s) from {ref_char.split('-')[0]} had no match."
        )

        # Build a tidy DataFrame for charting
        rows = []
        for grp_info in matched_groups:
            group = grp_info["group"]
            label = grp_info["label"]
            for char, cid in group.items():
                sub = df_full[(df_full["source"] == char) & (df_full["combat_id"] == cid)]
                enc_row = enc_times_full[(enc_times_full["source"] == char) & (enc_times_full["combat_id"] == cid)]
                dur = float(enc_row["duration_s"].iloc[0]) if not enc_row.empty else 0.0
                dmg = float(sub[sub["type"] == "damage"]["effective_amount"].sum())
                heal = float(sub[sub["type"].isin(["heal", "absorb"])]["effective_amount"].sum())
                rows.append(
                    {
                        "character": char.split("-")[0],
                        "combat_id": cid,
                        "label": label,
                        "dps": dmg / dur if dur > 0 else 0.0,
                        "hps": heal / dur if dur > 0 else 0.0,
                        "duration_s": dur,
                    }
                )

        paired_df = pd.DataFrame(rows)

        tab_pdps, tab_phps = st.tabs(["DPS (paired)", "HPS (paired)"])
        for tab, metric, title_label in [
            (tab_pdps, "dps", "DPS"),
            (tab_phps, "hps", "HPS"),
        ]:
            with tab:
                try:
                    chart = (
                        alt.Chart(paired_df)
                        .mark_bar(opacity=0.85)
                        .encode(
                            x=alt.X(
                                "label:N",
                                title="Encounter (targets)",
                                sort=None,
                                axis=alt.Axis(labelAngle=-30, labelLimit=200),
                            ),
                            y=alt.Y(f"{metric}:Q", title=title_label, stack=None),
                            color=alt.Color("character:N", title="Character"),
                            xOffset=alt.XOffset("character:N"),
                            tooltip=[
                                alt.Tooltip("character:N"),
                                alt.Tooltip("label:N", title="Targets"),
                                alt.Tooltip(f"{metric}:Q", format=",.0f", title=title_label),
                                alt.Tooltip("duration_s:Q", format=".0f", title="Duration (s)"),
                                alt.Tooltip("combat_id:Q", title="Combat ID"),
                            ],
                        )
                        .properties(height=320)
                    )
                    st.altair_chart(chart, width="stretch")
                except Exception:
                    pass

        with st.expander("Paired encounters detail", expanded=False):
            pair_table = []
            for grp_info in matched_groups:
                row = {"Targets": grp_info["label"]}
                for char, cid in grp_info["group"].items():
                    row[f"{char.split('-')[0]} combat_id"] = cid
                pair_table.append(row)
            st.dataframe(pd.DataFrame(pair_table), hide_index=True)
