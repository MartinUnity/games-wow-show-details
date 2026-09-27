"""
views/totals.py
───────────────
Totals Summary view: headline combat/duration/target counts plus the
"By Target" and "By Ability" breakdowns (damage/healing ability tables and
charts). Extracted verbatim from streamlit_app.py so the orchestrator stays thin.
"""

import altair as alt
import streamlit as st

from utils.data_engine import compute_all_encounters_stats, compute_totals_summary
from utils.data_io import compute_character_counts


def totals_view():
    """Render the Totals Summary view for the currently selected character."""
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
        csv_bytes = (
            totals_df.sort_values(sort_opt, ascending=False)
            .to_csv(index=False)
            .encode()
        )
        st.download_button(
            "Download targets CSV",
            csv_bytes,
            file_name="totals_targets.csv",
            mime="text/csv",
        )

    with tab_ability:
        _, _, dmg_spells, heal_spells, _ = compute_all_encounters_stats(
            character=char_arg
        )
        col_d, col_h = st.columns(2)
        with col_d:
            st.subheader("Damage abilities")
            if not dmg_spells.empty:
                st.dataframe(
                    dmg_spells.style.format(
                        {"total": "{:,.0f}", "avg": "{:.1f}", "pct": "{:.1f}%"}
                    ),
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
                    heal_spells.style.format(
                        {"total": "{:,.0f}", "avg": "{:.1f}", "pct": "{:.1f}%"}
                    ),
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
