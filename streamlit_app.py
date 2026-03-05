import json
import os
from datetime import datetime

import altair as alt
import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

# Use wide layout so the UI isn't compressed on desktop/1440p
st.set_page_config(page_title="WoW Combat Viewer", layout="wide")

CSV_PATH = "parsed_combat_data.csv"
NOTES_PATH = "encounter_notes.jsonl"


def _fmt_compact_amount(n: float) -> str:
    """Return a compact HTML string for amounts, e.g. '152 <strong>K</strong>'"""
    try:
        v = float(n)
    except Exception:
        return "0"
    if v >= 1_000_000:
        return f"{int(round(v / 1_000_000)):d}&nbsp;<strong>M</strong>"
    if v >= 1000:
        return f"{int(round(v / 1000)):d}&nbsp;<strong>K</strong>"
    return f"{int(round(v)):d}"


@st.cache_data(ttl=3)
def load_csv(path=CSV_PATH):
    """Load the pre-processed CSV.  TTL=3s so new tail-mode encounters are
    visible on the next auto-refresh cycle without needing a manual cache clear."""
    df = pd.read_csv(path)
    df["timestamp_dt"] = pd.to_datetime(df["timestamp"], format="%m/%d/%Y %H:%M:%S.%f", errors="coerce")
    return df


def load_notes(path=NOTES_PATH):
    """Return {combat_id: note_str} from the sidecar JSONL file."""
    notes = {}
    if not os.path.exists(path):
        return notes
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                    notes[int(row["combat_id"])] = row.get("note", "")
                except Exception:
                    pass
    except Exception:
        pass
    return notes


def save_note(combat_id, note, path=NOTES_PATH):
    """Persist a note for a combat_id, replacing any existing entry."""
    notes = load_notes(path)
    if note:
        notes[int(combat_id)] = note
    else:
        notes.pop(int(combat_id), None)
    try:
        with open(path, "w", encoding="utf-8") as f:
            for cid, n in sorted(notes.items()):
                f.write(json.dumps({"combat_id": cid, "note": n}) + "\n")
    except Exception:
        pass


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

        st.subheader("Combats (top by damage)")
        top = agg.sort_values("total_damage", ascending=False).head(num_combats)
        # Snap 'now' to the current minute so the "X ago" label is stable across
        # auto-refresh ticks and AgGrid won't see stale-looking data every 3 s.
        _now_min = datetime.now().replace(second=0, microsecond=0)
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
                    # Append a human-readable "time ago" hint from encounter start
                    if pd.notna(s):
                        try:
                            ago_secs = int(
                                (
                                    _now_min - s.to_pydatetime().replace(tzinfo=None, second=0, microsecond=0)
                                ).total_seconds()
                            )
                            if ago_secs <= 0:
                                ago_str = "< 1m ago"
                            elif ago_secs < 60:
                                ago_str = "< 1m ago"
                            elif ago_secs < 3600:
                                ago_str = f"{ago_secs // 60}m ago"
                            elif ago_secs < 86400:
                                ago_str = f"{ago_secs // 3600}h ago"
                            elif ago_secs < 7 * 86400:
                                ago_str = f"{ago_secs // 86400}d ago"
                            elif ago_secs < 30 * 86400:
                                ago_str = f"{ago_secs // (7 * 86400)}w ago"
                            else:
                                ago_str = f"{ago_secs // (30 * 86400)}mo ago"
                            if ago_str:
                                duration_label = f"{duration_label} {ago_str}" if duration_label else ago_str
                        except Exception:
                            pass
            except Exception:
                target_name = ""
                duration_label = ""

            rows_data.append(
                {
                    "combat_id": int(cid),
                    "target": target_name,
                    "duration": duration_label,
                    "total_damage": int(row.get("total_damage", 0)),
                    "total_heal": int(row.get("total_heal", 0)),
                }
            )

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
                    header_name="Damage",
                    valueFormatter=damage_formatter_js,
                    cellStyle={"color": "#7CFC00", "fontFamily": "monospace"},
                    width=90,
                    minWidth=90,
                    maxWidth=90,
                    suppressSizeToFit=True,
                )
                gb.configure_column(
                    "total_heal",
                    header_name="Heal",
                    valueFormatter=heal_formatter_js,
                    cellStyle={"color": "#00CED1", "fontFamily": "monospace"},
                    width=90,
                    minWidth=90,
                    maxWidth=90,
                    suppressSizeToFit=True,
                )
                gb.configure_column(
                    "combat_id",
                    header_name="#",
                    width=58,
                    minWidth=58,
                    maxWidth=58,
                    suppressSizeToFit=True,
                )
                gb.configure_column(
                    "duration",
                    header_name="Duration",
                    width=110,
                    minWidth=110,
                    suppressSizeToFit=True,
                )
                gb.configure_selection(selection_mode="single", use_checkbox=False)
                gb.configure_pagination(paginationAutoPageSize=False, paginationPageSize=25)
                gb.configure_default_column(sortable=True, filter=True)
                grid_opts = gb.build()
                try:
                    # Make AgGrid taller so it can show ~20-25 rows without scrolling.
                    # Compute desired rows based on the requested `num_combats` (fall back to 20)
                    desired_rows = min(max(int(st.session_state.get("num_combats", 20)), 20), 25)
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


def combat_time_series(combat_df, resample_s=1, spell_filter=None):
    """Return a per-second (or resample_s) time series DataFrame with DPS and HPS for the combat."""
    if combat_df.empty:
        return pd.DataFrame()

    ts = combat_df.set_index("timestamp_dt").sort_index()
    dmg = ts[ts["type"] == "damage"]["effective_amount"].resample(f"{resample_s}s").sum()
    heal = ts[ts["type"] == "heal"]["effective_amount"].resample(f"{resample_s}s").sum()
    df_ts = pd.DataFrame({"DPS": dmg, "HPS": heal}).fillna(0)

    # If a spell filter is provided, compute per-spell series and attach
    if spell_filter:
        # spell_filter can be in format 'Spell [Damage]' or 'Spell [Healing]'
        try:
            if spell_filter.endswith("]") and "[" in spell_filter:
                spell_name, kind = spell_filter.rsplit(" [", 1)
                kind = kind.rstrip("]").lower()
            else:
                spell_name = spell_filter
                kind = None

            if kind == "damage":
                sel = (
                    ts[(ts["spell_name"] == spell_name) & (ts["type"] == "damage")]["effective_amount"]
                    .resample(f"{resample_s}s")
                    .sum()
                )
                df_ts["Selected_DPS"] = sel.reindex(df_ts.index).fillna(0)
                df_ts["Selected_HPS"] = 0
            elif kind == "healing":
                sel_h = (
                    ts[(ts["spell_name"] == spell_name) & (ts["type"] == "heal")]["effective_amount"]
                    .resample(f"{resample_s}s")
                    .sum()
                )
                df_ts["Selected_HPS"] = sel_h.reindex(df_ts.index).fillna(0)
                df_ts["Selected_DPS"] = 0
            else:
                sel = (
                    ts[(ts["spell_name"] == spell_name) & (ts["type"] == "damage")]["effective_amount"]
                    .resample(f"{resample_s}s")
                    .sum()
                )
                sel_h = (
                    ts[(ts["spell_name"] == spell_name) & (ts["type"] == "heal")]["effective_amount"]
                    .resample(f"{resample_s}s")
                    .sum()
                )
                df_ts["Selected_DPS"] = sel.reindex(df_ts.index).fillna(0)
                df_ts["Selected_HPS"] = sel_h.reindex(df_ts.index).fillna(0)
        except Exception:
            df_ts["Selected_DPS"] = 0
            df_ts["Selected_HPS"] = 0

    return df_ts


def spell_aggregates(combat_df, event_type, top_n=10):
    """Return a DataFrame with per-spell aggregates: count, total, avg, pct."""
    if combat_df.empty:
        return pd.DataFrame()
    df = combat_df[combat_df["type"] == event_type]
    if df.empty:
        return pd.DataFrame()
    agg = (
        df.groupby("spell_name")["effective_amount"]
        .agg([("total", "sum"), ("count", "count")])
        .sort_values("total", ascending=False)
    )
    agg["avg"] = agg["total"] / agg["count"]
    total_all = agg["total"].sum()
    agg["pct"] = agg["total"] / total_all * 100
    agg = agg.reset_index().rename(columns={"spell_name": "spell"})
    return agg[["spell", "count", "total", "avg", "pct"]].head(top_n)


@st.cache_data(ttl=30)
def compute_totals_summary(path=CSV_PATH, character=None):
    """Compute aggregated statistics across all combats from CSV."""
    try:
        df = load_csv(path)
    except Exception:
        return pd.DataFrame(), {}

    if df.empty:
        return pd.DataFrame(), {}

    # If a specific character is requested, filter by the source/character column
    if character:
        char_col = None
        for cand in ("source", "player", "character", "name"):
            if cand in df.columns:
                char_col = cand
                break
        if char_col:
            df = df[df[char_col] == character]
        else:
            # no character-like column found, return empty
            return pd.DataFrame(), {}

    # per-combat durations
    combats = df.groupby("combat_id")["timestamp_dt"].agg(["min", "max"]).reset_index()
    combats["duration_s"] = (combats["max"] - combats["min"]).dt.total_seconds().clip(lower=0)

    # total duration across combats
    total_duration_s = combats["duration_s"].sum()
    total_combats = combats["combat_id"].nunique()

    # per-target aggregates
    target_rows = []
    targets = df[~df["target"].isnull() & (df["target"] != "")]["target"].unique()
    for t in sorted(targets):
        sub = df[df["target"] == t]
        encounters = int(sub["combat_id"].nunique())
        # sum span per combat for this target
        spans = []
        for cid, g in sub.groupby("combat_id"):
            s = g["timestamp_dt"].min()
            e = g["timestamp_dt"].max()
            if pd.notna(s) and pd.notna(e) and e > s:
                spans.append((e - s).total_seconds())
        total_time = sum(spans)
        total_damage = float(sub[sub["type"] == "damage"]["effective_amount"].sum())
        total_heal = float(sub[sub["type"] == "heal"]["effective_amount"].sum())
        dps = total_damage / total_time if total_time > 0 else 0.0
        hps = total_heal / total_time if total_time > 0 else 0.0
        target_rows.append(
            {
                "target": t,
                "encounters": encounters,
                "total_time_s": total_time,
                "total_damage": total_damage,
                "total_heal": total_heal,
                "dps": dps,
                "hps": hps,
            }
        )

    totals_df = pd.DataFrame(target_rows)
    totals_df = totals_df.sort_values(["encounters", "total_damage"], ascending=[False, False])

    meta = {
        "total_combats": int(total_combats),
        "total_duration_s": float(total_duration_s),
        "unique_targets": int(len(targets)),
    }

    return totals_df, meta


@st.cache_data(ttl=3)
def compute_all_encounters_stats(path=CSV_PATH, character=None):
    """Aggregate spell usage and per-encounter metrics across all combat encounters."""
    try:
        df = load_csv(path)
    except Exception:
        return {}, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    if df.empty:
        return {}, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    # Drop out-of-combat rows
    df = df[df["combat_id"] > 0].copy()

    if character and character != "All":
        for cand in ("source", "player", "character", "name"):
            if cand in df.columns:
                df = df[df[cand] == character]
                break

    if df.empty:
        return {}, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    # Per-encounter duration / damage / heal
    enc_times = df.groupby("combat_id")["timestamp_dt"].agg(["min", "max"])
    enc_times["duration_s"] = (enc_times["max"] - enc_times["min"]).dt.total_seconds().clip(lower=0)
    dmg_per_enc = df[df["type"] == "damage"].groupby("combat_id")["effective_amount"].sum().rename("total_damage")
    heal_per_enc = df[df["type"] == "heal"].groupby("combat_id")["effective_amount"].sum().rename("total_heal")
    enc_df = enc_times.join(dmg_per_enc).join(heal_per_enc).fillna(0).reset_index()
    enc_df["dps"] = enc_df.apply(lambda r: r["total_damage"] / r["duration_s"] if r["duration_s"] > 0 else 0, axis=1)
    enc_df["hps"] = enc_df.apply(lambda r: r["total_heal"] / r["duration_s"] if r["duration_s"] > 0 else 0, axis=1)

    total_damage = float(df[df["type"] == "damage"]["effective_amount"].sum())
    total_heal = float(df[df["type"] == "heal"]["effective_amount"].sum())
    total_duration = float(enc_times["duration_s"].sum())
    n_encounters = int(enc_times.shape[0])

    meta = {
        "n_encounters": n_encounters,
        "total_duration_s": total_duration,
        "total_damage": total_damage,
        "total_heal": total_heal,
        "avg_dps": total_damage / total_duration if total_duration > 0 else 0.0,
        "avg_hps": total_heal / total_duration if total_duration > 0 else 0.0,
    }

    def _spell_agg(event_type, top_n=40):
        d = df[df["type"] == event_type]
        if d.empty:
            return pd.DataFrame()
        agg = (
            d.groupby("spell_name")["effective_amount"]
            .agg([("total", "sum"), ("count", "count")])
            .sort_values("total", ascending=False)
        )
        agg["avg"] = agg["total"] / agg["count"]
        agg["pct"] = agg["total"] / agg["total"].sum() * 100
        return (
            agg.reset_index()
            .rename(columns={"spell_name": "spell"})[["spell", "count", "total", "avg", "pct"]]
            .head(top_n)
        )

    # Top targets by damage received, with per-encounter DPS stats
    try:
        player_name = df["source"].mode().iloc[0] if not df["source"].mode().empty else None
    except Exception:
        player_name = None
    dmg_df = df[(df["type"] == "damage") & df["target"].notna() & (df["target"] != "")]
    if player_name:
        dmg_df = dmg_df[dmg_df["target"] != player_name]
    if not dmg_df.empty:
        tgt_dmg = dmg_df.groupby("target")["effective_amount"].sum().rename("total_damage")
        tgt_enc = dmg_df.groupby("target")["combat_id"].nunique().rename("encounters")
        top_targets = (
            pd.concat([tgt_dmg, tgt_enc], axis=1).sort_values("total_damage", ascending=False).head(10).reset_index()
        )
        top_targets["avg_damage"] = top_targets["total_damage"] / top_targets["encounters"]
        # Per-target × per-encounter DPS (damage / encounter duration)
        try:
            tgt_enc_dmg = dmg_df.groupby(["target", "combat_id"])["effective_amount"].sum().reset_index()
            enc_dur = enc_times[["duration_s"]].reset_index()  # combat_id, duration_s
            tgt_enc_dmg = tgt_enc_dmg.merge(enc_dur, on="combat_id", how="left")
            tgt_enc_dmg["enc_dps"] = tgt_enc_dmg.apply(
                lambda r: r["effective_amount"] / r["duration_s"] if r["duration_s"] > 0 else 0.0, axis=1
            )
            tgt_dps_stats = (
                tgt_enc_dmg.groupby("target")["enc_dps"]
                .agg(best_dps="max", worst_dps="min", avg_dps="mean")
                .reset_index()
            )
            top_targets = top_targets.merge(tgt_dps_stats, on="target", how="left")
        except Exception:
            pass
    else:
        top_targets = pd.DataFrame()

    return meta, enc_df, _spell_agg("damage"), _spell_agg("heal"), top_targets


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
                st.altair_chart(
                    alt.Chart(top_targets)
                    .mark_bar()
                    .encode(
                        x=alt.X("total_damage:Q", title="Total damage dealt"),
                        y=alt.Y("target:N", sort="-x", title=""),
                        color=alt.value("#FF6347"),
                        tooltip=[
                            "target",
                            alt.Tooltip("encounters:Q", title="encounters"),
                            alt.Tooltip("total_damage:Q", format=",", title="total dmg"),
                            alt.Tooltip("avg_damage:Q", format=",.0f", title="avg dmg/enc"),
                        ],
                    )
                    .properties(height=max(180, 28 * len(top_targets))),
                    width="stretch",
                )
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
    CHART_HEIGHT = 260

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
                st.altair_chart(
                    alt.Chart(da)
                    .mark_bar()
                    .encode(
                        x=alt.X("total:Q", title="Total Damage"),
                        y=alt.Y("spell:N", sort="-x", title=""),
                        tooltip=[
                            "spell",
                            alt.Tooltip("total:Q", format=","),
                            alt.Tooltip("avg:Q", format=".1f"),
                            alt.Tooltip("pct:Q", format=".1f"),
                        ],
                    )
                    .properties(height=CHART_HEIGHT),
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
            styled_h = ha.style.format({"total": "{:,.0f}", "avg": "{:.1f}", "pct": "{:.1f}%"}).apply(
                lambda row: [("background-color: #2f2f2f" if row.name % 2 == 0 else "color: #ffffff") for _ in row],
                axis=1,
            )
            st.dataframe(styled_h, height=TABLE_HEIGHT)
            try:
                st.altair_chart(
                    alt.Chart(ha)
                    .mark_bar(color="#00CED1")
                    .encode(
                        x=alt.X("total:Q", title="Total Healing"),
                        y=alt.Y("spell:N", sort="-x", title=""),
                        tooltip=[
                            "spell",
                            alt.Tooltip("total:Q", format=","),
                            alt.Tooltip("avg:Q", format=".1f"),
                            alt.Tooltip("pct:Q", format=".1f"),
                        ],
                    )
                    .properties(height=CHART_HEIGHT),
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
        all_spells.append(heal_spells.assign(type="heal"))
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
                        scale=alt.Scale(domain=["damage", "heal"], range=["#7CFC00", "#00CED1"]),
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


def combat_detail_view(df, combat_id, resample_s=1, smooth_s=0, top_n=5):
    # Show combat header with first target name if available
    combat_df = df[df["combat_id"] == combat_id].sort_values("timestamp_dt")
    target_name = ""
    try:
        if not combat_df.empty and "target" in combat_df.columns:
            nonempty = combat_df[~combat_df["target"].isnull() & (combat_df["target"] != "")]
            if not nonempty.empty:
                target_name = str(nonempty.iloc[0]["target"]).strip()
    except Exception:
        target_name = ""

    if target_name:
        st.header(f"Combat {combat_id} — {target_name}")
    else:
        st.header(f"Combat {combat_id}")

    # Encounter note
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

    # Aggregated per-spell tables/charts (damage & healing)
    dmg_agg = spell_aggregates(combat_df, "damage", top_n=top_n)
    heal_agg = spell_aggregates(combat_df, "heal", top_n=top_n)
    if not dmg_agg.empty or not heal_agg.empty:
        st.subheader("By ability")
        # Use dynamic table height based on number of abilities so both columns align
        ROW_HEIGHT = 28
        TABLE_PADDING = 60
        TABLE_HEIGHT = min(600, TABLE_PADDING + ROW_HEIGHT * max(3, top_n))
        CHART_HEIGHT = 180
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
                    chart = (
                        alt.Chart(da)
                        .mark_bar()
                        .encode(
                            x=alt.X("total:Q"),
                            y=alt.Y("spell:N", sort="-x"),
                            tooltip=["spell", "total", "count", "avg", "pct"],
                        )
                    )
                    st.altair_chart(chart.properties(height=CHART_HEIGHT), width="stretch")
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
                    chart_h = (
                        alt.Chart(ha)
                        .mark_bar()
                        .encode(
                            x=alt.X("total:Q"),
                            y=alt.Y("spell:N", sort="-x"),
                            tooltip=["spell", "total", "count", "avg", "pct"],
                        )
                    )
                    st.altair_chart(chart_h.properties(height=CHART_HEIGHT), width="stretch")
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

    with st.expander("Recent events", expanded=False):
        st.dataframe(
            combat_df.tail(200)[["timestamp", "event", "source", "spell_name", "amount", "effective_amount", "type"]]
        )


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
    cols = st.columns(len(selected))
    for i, char in enumerate(selected):
        meta, enc_df, _, _, _ = char_stats[char]
        short = char.split("-")[0]
        with cols[i]:
            st.subheader(short)
            m1, m2 = st.columns(2)
            m1.metric("Encounters", meta.get("n_encounters", 0))
            m2.metric("Avg DPS", f"{meta.get('avg_dps', 0):,.0f}")
            m3, m4 = st.columns(2)
            m3.metric("Avg HPS", f"{meta.get('avg_hps', 0):,.0f}")
            best_dps = float(enc_df["dps"].max()) if not enc_df.empty else 0.0
            m4.metric("Best DPS", f"{best_dps:,.0f}")
            m5, m6 = st.columns(2)
            m5.metric("Total Damage", f"{meta.get('total_damage', 0):,.0f}")
            m6.metric("Total Healing", f"{meta.get('total_heal', 0):,.0f}")

    st.markdown("---")

    # ── Per-encounter DPS / HPS grouped bar ───────────────────────────────
    enc_rows = []
    for char in selected:
        _, enc_df, _, _, _ = char_stats[char]
        if enc_df.empty:
            continue
        short = char.split("-")[0]
        tmp = enc_df.sort_values("combat_id").copy()
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


def main():
    # Tight header to reduce vertical padding at top
    st.markdown(
        """
        <style>
        /* Collapse Streamlit's default top padding on the main content area */
        .block-container { padding-top: 0.5rem !important; padding-bottom: 0.5rem !important; }
        /* Shrink the fixed top header bar */
        header[data-testid="stHeader"] { height: 2rem !important; min-height: 2rem !important; }
        /* Pull the sidebar down less so it doesn't feel disconnected */
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
    view = st.sidebar.radio(
        "View", options=["Combat Viewer", "All Encounters", "Totals", "Character Comparison"], index=0
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

        # Prefer canonical player names like Name-Server-Region and filter out noisy entries
        def is_player_like(n: str) -> bool:
            try:
                s = str(n).strip()
                parts = s.split("-")
                return len(parts) == 3 and all(p for p in parts)
            except Exception:
                return False

        player_like = sorted([n for n in raw_names if is_player_like(n)])
        others = sorted([n for n in raw_names if not is_player_like(n)])
        # Allow user to include non-player sources explicitly and filter tiny sources
        include_non_player = st.sidebar.checkbox("Include non-player sources", value=False)
        min_source_combats = st.sidebar.slider("Min combats to show (sources)", min_value=1, max_value=10, value=3)
        # get counts to filter tiny sources
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

    # If Totals view requested, render totals summary and exit early
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
            # Show per-character summary
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

    # If All Encounters view requested, render aggregated ability/encounter stats and exit early
    if view == "All Encounters":
        char = st.session_state.get("character_select", "All")
        all_encounters_view(df_chars, character=(None if char == "All" else char))
        return

    # If Character Comparison view requested
    if view == "Character Comparison":
        # Build list of player-like characters, sorted by encounter count descending
        player_like_chars = sorted(
            [
                n
                for n in (df_chars["source"].dropna().unique() if not df_chars.empty else [])
                if len(str(n).split("-")) == 3 and all(str(n).split("-"))
            ]
        )
        character_comparison_view(player_like_chars)
        return

    # --- Combat Viewer ---
    df = load_csv()

    if df.empty:
        st.write("No parsed data available. Run: python wow-parser.py --full-import")
        return

    # --- Auto-select latest encounter when a new one arrives (Follow live) ---
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

    # Respect `?combat=<id>` and `?num_combats=` query params early so they affect defaults
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

    # Abilities control in the sidebar: if only 0-1 abilities, show a static label; else show slider 1..(max+1)
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

    # Show-last-N-combats slider: default to 10 on fresh start, max is number of combats available
    try:
        max_combats = int(df["combat_id"].max()) if "combat_id" in df.columns else 1
    except Exception:
        max_combats = 1
    if max_combats < 1:
        max_combats = 1
    if "num_combats" not in st.session_state:
        st.session_state["num_combats"] = min(20, max_combats)
    st.sidebar.markdown("---")
    st.sidebar.markdown("**Combats to display (filtered by Character)**")
    # Create the slider widget; do not assign its return directly into session_state
    # (Streamlit manages the session state for widgets with `key`)
    num_combats_val = st.sidebar.slider(
        "Show last N combats",
        min_value=1,
        max_value=max_combats,
        value=st.session_state["num_combats"],
        key="num_combats",
    )

    # Render summary (left) and combat details (right) in a 1:3 split
    col_left, col_right = st.columns([1, 3])
    with col_left:
        sel_override = summary_view(df, num_combats=st.session_state.get("num_combats", 10))

    # Always render the details panel inside the right column; if an immediate
    # selection override exists, prefer that combat id so the UI responds
    # without requiring an extra click.
    with col_right:
        if sel_override:
            combat_detail_view(df, sel_override, resample_s=resample_s, smooth_s=smooth_s, top_n=top_n)
        else:
            combat_detail_view(df, combat_to_show, resample_s=resample_s, smooth_s=smooth_s, top_n=top_n)


@st.cache_data(ttl=30)
def compute_character_counts(path=CSV_PATH):
    df = load_csv(path)
    if df.empty:
        return pd.DataFrame()
    # count unique combat ids per source
    counts = df[~df["source"].isnull() & (df["source"] != "")].groupby("source")["combat_id"].nunique()
    res = counts.reset_index().rename(columns={"combat_id": "combats"}).sort_values("combats", ascending=False)
    return res


if __name__ == "__main__":
    main()
