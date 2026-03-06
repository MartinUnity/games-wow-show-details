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
HIDDEN_PATH = "hidden_combats.json"
BOSS_KILLS_PATH = "boss_kills.jsonl"


def load_boss_kills(path=BOSS_KILLS_PATH) -> list:
    """Return list of boss kill dicts from the sidecar JSONL file."""
    if not os.path.exists(path):
        return []
    kills = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    kills.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        pass
    return kills


def load_hidden(path=HIDDEN_PATH) -> set:
    """Return set of combat_ids that should be hidden from the encounter list."""
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(int(x) for x in data)
    except Exception:
        return set()


def save_hidden(hidden: set, path=HIDDEN_PATH):
    """Persist the set of hidden combat_ids."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted(hidden), f)
    except Exception:
        pass


def toggle_hidden(combat_id: int, path=HIDDEN_PATH):
    """Add combat_id to hidden set, or remove it if already hidden."""
    hidden = load_hidden(path)
    if combat_id in hidden:
        hidden.discard(combat_id)
    else:
        hidden.add(combat_id)
    save_hidden(hidden, path)


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
    # Backward-compat: older CSVs lack zone columns
    if "zone_id" not in df.columns:
        df["zone_id"] = 0
    if "zone_name" not in df.columns:
        df["zone_name"] = ""
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


@st.cache_data(ttl=3)
def compute_runs(path=CSV_PATH, gap_minutes=20):
    """Group encounters into 'runs' based on zone name and time continuity.

    A new run begins when the zone name changes OR when the gap between
    consecutive encounter start times exceeds *gap_minutes*.

    Returns
    -------
    runs_df : DataFrame
        One row per run with columns: run_id, zone_name, zone_id,
        start_dt, end_dt, n_encounters, total_damage, total_heal,
        duration_s, avg_dps, avg_hps.
    enc_summary : DataFrame
        One row per encounter with run_id stamped, plus start/end times.
    """
    try:
        df = load_csv(path)
    except Exception:
        return pd.DataFrame(), pd.DataFrame()

    if df.empty:
        return pd.DataFrame(), pd.DataFrame()

    enc_df = df[df["combat_id"] > 0].copy()
    if enc_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    # Drop rows from fully-unknown zones (zone_name is empty or UNKNOWN AREA)
    enc_df = enc_df[enc_df["zone_name"].notna() & (enc_df["zone_name"] != "") & (enc_df["zone_name"] != "UNKNOWN AREA")]
    if enc_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    # Per-encounter aggregates
    enc_times = (
        enc_df.groupby("combat_id")["timestamp_dt"]
        .agg(["min", "max"])
        .rename(columns={"min": "start_dt", "max": "end_dt"})
    )
    enc_times["duration_s"] = (enc_times["end_dt"] - enc_times["start_dt"]).dt.total_seconds().clip(lower=0)
    enc_dmg = enc_df[enc_df["type"] == "damage"].groupby("combat_id")["effective_amount"].sum().rename("total_damage")
    enc_heal = enc_df[enc_df["type"] == "heal"].groupby("combat_id")["effective_amount"].sum().rename("total_heal")
    # Zone per encounter — most frequent value in that combat
    enc_zone = enc_df.groupby("combat_id")["zone_name"].agg(lambda x: x.mode().iloc[0] if not x.mode().empty else "")
    enc_zone_id = enc_df.groupby("combat_id")["zone_id"].agg(lambda x: x.mode().iloc[0] if not x.mode().empty else 0)
    # Top damage target per encounter
    try:
        _player = enc_df["source"].mode().iloc[0] if not enc_df["source"].mode().empty else None
        _tgt_df = enc_df[(enc_df["type"] == "damage") & enc_df["target"].notna() & (enc_df["target"] != "")]
        if _player:
            _tgt_df = _tgt_df[_tgt_df["target"] != _player]
        # Keep the full target string (e.g. "192-Name-..." or "Name-Server") so
        # the Runs view displays the same human-friendly target text as
        # the Combat Viewer (`target`) column instead of only the prefix.
        enc_target = _tgt_df.groupby("combat_id")["target"].agg(lambda x: x.value_counts().index[0] if len(x) else "")
    except Exception:
        enc_target = pd.Series(dtype=str)

    enc_summary = (
        enc_times.join(enc_dmg)
        .join(enc_heal)
        .join(enc_zone.rename("zone_name"))
        .join(enc_zone_id.rename("zone_id"))
        .fillna(0)
        .reset_index()
    )
    if not enc_target.empty:
        enc_summary = enc_summary.merge(enc_target.rename("main_target").reset_index(), on="combat_id", how="left")
    else:
        enc_summary["main_target"] = ""

    enc_summary["zone_name"] = enc_summary["zone_name"].astype(str)
    enc_summary = enc_summary.sort_values("start_dt").reset_index(drop=True)

    # Assign run_id: increment on zone change or time gap
    gap_threshold = pd.Timedelta(minutes=gap_minutes)
    run_ids = []
    run_id = 0
    prev_end = None
    prev_zone = None
    for _, row in enc_summary.iterrows():
        zone = row["zone_name"]
        start = row["start_dt"]
        if prev_end is None or zone != prev_zone or (start - prev_end) > gap_threshold:
            run_id += 1
        run_ids.append(run_id)
        prev_end = row["end_dt"]
        prev_zone = zone
    enc_summary["run_id"] = run_ids

    # Aggregate by run
    runs = (
        enc_summary.groupby("run_id")
        .agg(
            zone_name=("zone_name", "first"),
            zone_id=("zone_id", "first"),
            start_dt=("start_dt", "min"),
            end_dt=("end_dt", "max"),
            n_encounters=("combat_id", "count"),
            total_damage=("total_damage", "sum"),
            total_heal=("total_heal", "sum"),
            duration_s=("duration_s", "sum"),
        )
        .reset_index()
    )
    runs["avg_dps"] = runs.apply(lambda r: r["total_damage"] / r["duration_s"] if r["duration_s"] > 0 else 0.0, axis=1)
    runs["avg_hps"] = runs.apply(lambda r: r["total_heal"] / r["duration_s"] if r["duration_s"] > 0 else 0.0, axis=1)

    # ── Join boss kills from sidecar ──────────────────────────────────────
    runs["has_boss_kill"] = False
    runs["boss_names"] = ""
    for bk in load_boss_kills():
        if bk.get("kill_flag", 0) != 1:
            continue
        try:
            end_dt = pd.Timestamp(bk["end_ts"])
        except Exception:
            continue
        bk_zone_id = int(bk.get("zone_id", 0))
        for idx, run_row in runs.iterrows():
            try:
                if int(run_row["zone_id"]) == bk_zone_id and pd.Timestamp(
                    run_row["start_dt"]
                ) <= end_dt <= pd.Timestamp(run_row["end_dt"]) + pd.Timedelta(minutes=10):
                    runs.at[idx, "has_boss_kill"] = True
                    existing = runs.at[idx, "boss_names"]
                    runs.at[idx, "boss_names"] = (existing + ", " + bk["boss_name"]) if existing else bk["boss_name"]
                    break
            except Exception:
                continue

    runs = runs.sort_values("run_id", ascending=False).reset_index(drop=True)
    return runs, enc_summary


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
    h1, h2, h3, h4 = st.columns(4)
    h1.metric("Total Runs", total_runs)
    h2.metric("Boss Runs", boss_run_count)
    h3.metric("Unique Zones", unique_zones)
    h4.metric("Total Encounters", total_enc)

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
        table_rows.append(
            {
                "run_id": int(r["run_id"]),
                "Zone": str(r["zone_name"]),
                "Boss": str(r.get("boss_names", "")) or "",
                "Date": r["start_dt"].strftime("%m/%d %H:%M") if pd.notna(r["start_dt"]) else "",
                "Enc": int(r["n_encounters"]),
                "Duration": _dur_label(r["duration_s"]),
                "Dmg": int(r["total_damage"]),
                "Heal": int(r["total_heal"]),
                "Avg DPS": round(r["avg_dps"], 1),
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

            gb = GridOptionsBuilder.from_dataframe(table_df)
            gb.configure_column("run_id", header_name="#", width=40, minWidth=40, maxWidth=40, suppressSizeToFit=True)
            gb.configure_column("Zone", width=160, minWidth=110)
            gb.configure_column("Boss", width=150, minWidth=100, cellStyle={"color": "#FFD700", "fontStyle": "italic"})
            gb.configure_column("Date", width=110, minWidth=90, maxWidth=120)
            gb.configure_column("Enc", width=55, minWidth=45, maxWidth=65, suppressSizeToFit=True)
            gb.configure_column("Duration", width=80, minWidth=70, maxWidth=90, suppressSizeToFit=True)
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
            gb.configure_column("Avg DPS", width=80, minWidth=70, maxWidth=100, suppressSizeToFit=True)
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

            # Build encounter rows — rendered at the bottom of this detail section
            enc_rows = []
            for _, er in run_encs.iterrows():
                enc_rows.append(
                    {
                        "#": int(er["combat_id"]),
                        "Target": str(er.get("main_target", "")) or "—",
                        "Start": er["start_dt"].strftime("%H:%M:%S") if pd.notna(er["start_dt"]) else "",
                        "Duration": _dur_label(er["duration_s"]),
                        "Dmg": int(er.get("total_damage", 0)),
                        "DPS": round(er["total_damage"] / er["duration_s"], 1) if er["duration_s"] > 0 else 0.0,
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
                    total_heal = float(grp[grp["type"] == "heal"]["effective_amount"].sum())
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
                                    .properties(height=260, title="Damage share"),
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
                                    .properties(height=260, title="Healing share"),
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
                                    _sh = float(_sgrp[_sgrp["type"] == "heal"]["effective_amount"].sum())
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

            # ── Encounter list (at the bottom) ────────────────────────────
            st.subheader("Encounters")
            enc_df_disp = pd.DataFrame(enc_rows)
            st.dataframe(
                enc_df_disp.style.format({"Dmg": "{:,}", "DPS": "{:.1f}"}),
                hide_index=True,
                width="stretch",
            )
            if len(enc_rows) > 1:
                try:
                    chart_df = pd.DataFrame(enc_rows).assign(cid_str=lambda d: d["#"].astype(str))
                    st.altair_chart(
                        alt.Chart(chart_df)
                        .mark_bar(color="#7CFC00", opacity=0.85)
                        .encode(
                            x=alt.X("DPS:Q", title="DPS"),
                            y=alt.Y("cid_str:N", title="Combat ID", sort=None),
                            tooltip=[
                                alt.Tooltip("cid_str:N", title="Combat"),
                                "Target",
                                alt.Tooltip("DPS:Q", format=".1f"),
                                "Duration",
                            ],
                        )
                        .properties(height=len(enc_rows) * 30, title=f"DPS per encounter — {zone_label}"),
                        width="stretch",
                    )
                except Exception as e:
                    # Fallback selectbox - this should already be above when AgGrid fails
                    pass
            st.caption("Navigate to **Combat Viewer** to inspect any individual encounter in full detail.")


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
            styled_h = ha.style.format({"total": "{:,.0f}", "avg": "{:.1f}", "pct": "{:.1f}%"}).apply(
                lambda row: [("background-color: #2f2f2f" if row.name % 2 == 0 else "color: #ffffff") for _ in row],
                axis=1,
            )
            st.dataframe(styled_h, height=TABLE_HEIGHT)
            try:
                _lw_h = max(100, min(400, max((len(s) for s in ha["spell"]), default=10) * 7))
                st.altair_chart(
                    alt.Chart(ha)
                    .mark_bar(color="#00CED1")
                    .encode(
                        x=alt.X("total:Q", title="Total Healing"),
                        y=alt.Y("spell:N", sort="-x", title="", axis=alt.Axis(labelLimit=_lw_h, labelFontSize=10)),
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
                _split_chart = (
                    alt.Chart(_tgt_totals)
                    .mark_bar(height=28)
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
                    .properties(height=50)
                )
                st.altair_chart(_split_chart, width="stretch")
                # compact label row beneath the bar
                _label_parts = [
                    f"<span style='color:#aaa'>{r.label}:</span> "
                    f"<b>{_fmt_compact_amount(r.damage)}</b> ({r.pct:.0f}%)"
                    for r in _tgt_totals.head(8).itertuples()
                ]
                st.markdown(
                    "<p style='font-size:0.78rem;margin:2px 0 8px 0'>" + " &nbsp;|&nbsp; ".join(_label_parts) + "</p>",
                    unsafe_allow_html=True,
                )
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

    with st.expander("Recent events", expanded=False):
        st.dataframe(
            combat_df.tail(200)[["timestamp", "event", "source", "spell_name", "amount", "effective_amount", "type"]]
        )


def _encounter_fingerprints(df: pd.DataFrame, char: str) -> dict:
    """Return {combat_id: frozenset_of_targets} for one character, excluding themselves as a target."""
    char_df = df[(df["source"] == char) & (df["combat_id"] > 0)]
    result = {}
    for cid, grp in char_df.groupby("combat_id"):
        targets = frozenset(t for t in grp["target"].dropna().unique() if t and t != char)
        result[int(cid)] = targets
    return result


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
                heal = float(sub[sub["type"] == "heal"]["effective_amount"].sum())
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

    # ── Session summary banner ──────────────────────────────────────────
    try:
        _today = datetime.now().date()
        _banner_df = df_chars[df_chars["combat_id"] > 0].copy() if not df_chars.empty else pd.DataFrame()
        if not _banner_df.empty and "timestamp_dt" in _banner_df.columns:
            _banner_df = _banner_df[_banner_df["timestamp_dt"].dt.date == _today]
        if not _banner_df.empty:
            _t_cids = _banner_df["combat_id"].unique()
            _n_enc = len(_t_cids)
            # durations & dps per encounter
            _enc_times = _banner_df.groupby("combat_id")["timestamp_dt"].agg(["min", "max"])
            _enc_times["dur"] = (_enc_times["max"] - _enc_times["min"]).dt.total_seconds().clip(lower=0)
            _total_played_s = int(_enc_times["dur"].sum())
            _played_str = (
                f"{_total_played_s // 3600}h {(_total_played_s % 3600) // 60}m"
                if _total_played_s >= 3600
                else f"{_total_played_s // 60}m {_total_played_s % 60}s"
            )
            # best DPS encounter
            _dmg = _banner_df[_banner_df["type"] == "damage"].groupby("combat_id")["effective_amount"].sum()
            _enc_dps = (_dmg / _enc_times["dur"].clip(lower=1)).dropna()
            _best_cid = int(_enc_dps.idxmax()) if not _enc_dps.empty else None
            _best_dps_val = _enc_dps.max() if not _enc_dps.empty else 0
            # target for best encounter
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

    # If Runs view requested, render and exit early
    if view == "Runs":
        runs_view()
        return

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
