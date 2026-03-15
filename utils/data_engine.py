"""
utils/data_engine.py
────────────────────
The 5 most complex pure data-processing functions.
All functions are Streamlit-agnostic (no rendering commands).
@st.cache_data decorators are allowed here — they only wrap functions,
they do not trigger page rendering.
"""

import pandas as pd
import streamlit as st

from utils.data_io import CSV_PATH, load_boss_kills, load_csv, load_healer_spells

# ── 1. Time-series builder ────────────────────────────────────────────────────


def combat_time_series(combat_df, resample_s=1, spell_filter=None):
    """Return a per-second (or resample_s) time series DataFrame with DPS and HPS for the combat."""
    if combat_df.empty:
        return pd.DataFrame()

    ts = combat_df.set_index("timestamp_dt").sort_index()
    dmg = ts[ts["type"] == "damage"]["effective_amount"].resample(f"{resample_s}s").sum()
    heal = ts[ts["type"].isin(["heal", "absorb"])]["effective_amount"].resample(f"{resample_s}s").sum()
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
                    ts[(ts["spell_name"] == spell_name) & (ts["type"].isin(["heal", "absorb"]))]["effective_amount"]
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
                    ts[(ts["spell_name"] == spell_name) & (ts["type"].isin(["heal", "absorb"]))]["effective_amount"]
                    .resample(f"{resample_s}s")
                    .sum()
                )
                df_ts["Selected_DPS"] = sel.reindex(df_ts.index).fillna(0)
                df_ts["Selected_HPS"] = sel_h.reindex(df_ts.index).fillna(0)
        except Exception:
            df_ts["Selected_DPS"] = 0
            df_ts["Selected_HPS"] = 0

    return df_ts


# ── 2. Spell aggregates (used everywhere) ─────────────────────────────────────


def spell_aggregates(combat_df, event_type, top_n=10):
    """Return a DataFrame with per-spell aggregates: count, total, avg, pct.

    *event_type* may be a single string (e.g. ``"damage"``) or a list of
    strings (e.g. ``["heal", "absorb"]``) to aggregate multiple types together.
    When a list is passed a ``kind`` column is included showing the dominant
    type for each spell (useful for colour-coding in charts).
    """
    if combat_df.empty:
        return pd.DataFrame()
    multi = not isinstance(event_type, str)
    types = list(event_type) if multi else [event_type]
    df = combat_df[combat_df["type"].isin(types)]
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
    if multi:
        # Dominant type per spell (highest total wins — in practice each spell
        # is always one type, so this is deterministic).
        kind_map = (
            df.groupby(["spell_name", "type"])["effective_amount"]
            .sum()
            .reset_index()
            .sort_values("effective_amount", ascending=False)
            .drop_duplicates(subset="spell_name")
            .rename(columns={"spell_name": "spell", "type": "kind"})
            .set_index("spell")["kind"]
        )
        agg["kind"] = agg["spell"].map(kind_map).fillna(types[0])
        return agg[["spell", "kind", "count", "total", "avg", "pct"]].head(top_n)
    return agg[["spell", "count", "total", "avg", "pct"]].head(top_n)


# ── 3. Totals summary (per-target rolled up across all combats) ───────────────


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
            return pd.DataFrame(), {}

    # per-combat durations
    combats = df.groupby("combat_id")["timestamp_dt"].agg(["min", "max"]).reset_index()
    combats["duration_s"] = (combats["max"] - combats["min"]).dt.total_seconds().clip(lower=0)

    total_duration_s = combats["duration_s"].sum()
    total_combats = combats["combat_id"].nunique()

    # per-target aggregates
    target_rows = []
    targets = df[~df["target"].isnull() & (df["target"] != "")]["target"].unique()
    for t in sorted(targets):
        sub = df[df["target"] == t]
        encounters = int(sub["combat_id"].nunique())
        spans = []
        for cid, g in sub.groupby("combat_id"):
            s = g["timestamp_dt"].min()
            e = g["timestamp_dt"].max()
            if pd.notna(s) and pd.notna(e) and e > s:
                spans.append((e - s).total_seconds())
        total_time = sum(spans)
        total_damage = float(sub[sub["type"] == "damage"]["effective_amount"].sum())
        total_heal = float(sub[sub["type"].isin(["heal", "absorb"])]["effective_amount"].sum())
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


# ── 4. All-encounters stats (spell + encounter aggregates) ────────────────────


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
    heal_per_enc = (
        df[df["type"].isin(["heal", "absorb"])].groupby("combat_id")["effective_amount"].sum().rename("total_heal")
    )
    enc_df = enc_times.join(dmg_per_enc).join(heal_per_enc).fillna(0).reset_index()
    enc_df["dps"] = enc_df.apply(lambda r: r["total_damage"] / r["duration_s"] if r["duration_s"] > 0 else 0, axis=1)
    enc_df["hps"] = enc_df.apply(lambda r: r["total_heal"] / r["duration_s"] if r["duration_s"] > 0 else 0, axis=1)

    total_damage = float(df[df["type"] == "damage"]["effective_amount"].sum())
    total_heal = float(df[df["type"].isin(["heal", "absorb"])]["effective_amount"].sum())
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
        types = [event_type] if isinstance(event_type, str) else list(event_type)
        d = df[df["type"].isin(types)]
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

    return meta, enc_df, _spell_agg("damage"), _spell_agg(["heal", "absorb"]), top_targets


# ── 5. Run grouper (zone + time-gap clustering with boss-kill join) ───────────


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
    enc_heal = (
        enc_df[enc_df["type"].isin(["heal", "absorb"])]
        .groupby("combat_id")["effective_amount"]
        .sum()
        .rename("total_heal")
    )
    # Zone per encounter — most frequent value in that combat
    enc_zone = enc_df.groupby("combat_id")["zone_name"].agg(lambda x: x.mode().iloc[0] if not x.mode().empty else "")
    enc_zone_id = enc_df.groupby("combat_id")["zone_id"].agg(lambda x: x.mode().iloc[0] if not x.mode().empty else 0)
    # Top damage target per encounter
    try:
        _player = enc_df["source"].mode().iloc[0] if not enc_df["source"].mode().empty else None
        _tgt_df = enc_df[(enc_df["type"] == "damage") & enc_df["target"].notna() & (enc_df["target"] != "")]
        if _player:
            _tgt_df = _tgt_df[_tgt_df["target"] != _player]
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

    # ── Run-level role classification using healer sidecar ───────────────
    # Load healer-identifying spells (sidecar may contain numeric ids and/or names)
    try:
        healer_sidecar = load_healer_spells()
    except Exception:
        healer_sidecar = {}

    healer_ids = set()
    healer_names = set()
    for spec, spells in (healer_sidecar or {}).items():
        if not isinstance(spells, list):
            continue
        for s in spells:
            if isinstance(s, int):
                healer_ids.add(int(s))
            else:
                healer_names.add(str(s).lower())

    # If we have any healer indicators, stamp runs with role info.
    if healer_ids or healer_names:
        try:
            # Map combat_id -> run_id from enc_summary
            cid_to_run = enc_summary.set_index("combat_id")["run_id"].to_dict()
            # Add run_id to per-event dataframe (enc_df is the per-event subset we used earlier)
            per_event = enc_df.copy()
            per_event["run_id"] = per_event["combat_id"].map(cid_to_run)

            # Helper to decide if a run contains any healer spells
            run_roles = {}
            run_spec_map = {}
            run_class_map = {}

            # Reverse map: spell id/name -> spec for quick lookup
            id_to_spec = {}
            name_to_spec = {}
            for spec, spells in (healer_sidecar or {}).items():
                if not isinstance(spells, list):
                    continue
                for s in spells:
                    if isinstance(s, int):
                        id_to_spec.setdefault(int(s), set()).add(spec)
                    else:
                        name_to_spec.setdefault(str(s).lower(), set()).add(spec)

            # Simple spec -> class mapping used for coloring in views
            spec_to_class = {
                "Mistweaver": "Monk",
                "Restoration_Shaman": "Shaman",
                "Restoration_Druid": "Druid",
                "Holy_Paladin": "Paladin",
                "Preservation_Evoker": "Evoker",
                "Holy_Priest": "Priest",
                "Discipline_Priest": "Priest",
            }

            for rid, grp in per_event.groupby("run_id"):
                if pd.isna(rid):
                    continue
                try:
                    # Iterate events in chronological order and short-circuit on
                    # the first healer-identifying spell encountered.
                    ordered = grp.sort_values("timestamp_dt") if "timestamp_dt" in grp.columns else grp
                    found_spec = None
                    for _, ev in ordered.iterrows():
                        try:
                            sid = ev.get("spell_id", None)
                            # Prefer numeric id matches
                            if sid is not None:
                                try:
                                    sid_int = int(sid)
                                except Exception:
                                    sid_int = None
                                if sid_int and sid_int in id_to_spec:
                                    # Pick deterministic spec from the set
                                    specs = sorted(id_to_spec[sid_int])
                                    found_spec = specs[0]
                                    break
                            # Fallback to name match
                            sname = ev.get("spell_name", None)
                            if sname:
                                sname_l = str(sname).lower()
                                if sname_l in name_to_spec:
                                    specs = sorted(name_to_spec[sname_l])
                                    found_spec = specs[0]
                                    break
                        except Exception:
                            continue

                    if found_spec:
                        run_roles[int(rid)] = "Healer"
                        run_spec_map[int(rid)] = found_spec
                        run_class_map[int(rid)] = spec_to_class.get(found_spec, "")
                    else:
                        run_roles[int(rid)] = "DPS"
                        run_spec_map[int(rid)] = ""
                        run_class_map[int(rid)] = ""
                except Exception:
                    run_roles[int(rid)] = "DPS"
                    run_spec_map[int(rid)] = ""
                    run_class_map[int(rid)] = ""

            runs["run_role"] = runs["run_id"].map(run_roles).fillna("DPS")
            runs["is_healer_run"] = runs["run_role"] == "Healer"
            runs["run_spec"] = runs["run_id"].map(run_spec_map).fillna("")
            runs["run_class"] = runs["run_id"].map(run_class_map).fillna("")
        except Exception:
            runs["run_role"] = "DPS"
            runs["is_healer_run"] = False
    else:
        runs["run_role"] = "DPS"
        runs["is_healer_run"] = False
    return runs, enc_summary
