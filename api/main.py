"""
api.main — FastAPI app for the WoW Combat Log viewer (Phase 3).

Run with:  uvicorn api.main:app --host 127.0.0.1 --port 8000
(runme.sh has an `api` target for this.)

Read endpoints are thin wrappers over the bare Phase 2 compute functions;
writes go through api.deps, which keeps the JSONL sidecars (still read by
the frozen Streamlit app) and the SQLite tables in sync.
"""

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from api import deps
from utils import data_engine, export_share, replay_engine

# ── Helpers ──────────────────────────────────────────────────────────────────


def _records(df: pd.DataFrame) -> list:
    """DataFrame → JSON-safe list of dicts (Timestamps → ISO strings)."""
    if df is None or df.empty:
        return []
    return json.loads(df.to_json(orient="records", date_format="iso"))


def _mode(s: pd.Series, default: str = "") -> str:
    """Modal value of a series (display helper; never raises)."""
    if s is None or s.empty:
        return default
    m = s.mode()
    return str(m.iloc[0]) if not m.empty else default


_DECOMPRESSED: dict = {}


def _decompress_cached(log_path: str) -> str:
    """gunzip → plain text, cached per file (replays re-read the same log)."""
    hit = _DECOMPRESSED.get(log_path)
    if hit is None:
        hit = replay_engine.decompress_log(log_path)
        _DECOMPRESSED[log_path] = hit
    return hit


def _combat_frame(combat_id: int) -> pd.DataFrame:
    df = deps.load_events(combat_id=combat_id)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"combat {combat_id} not found")
    return df


def _combat_bounds(df: pd.DataFrame) -> dict:
    start, end = df["timestamp_dt"].min(), df["timestamp_dt"].max()
    dur = max(0.0, (end - start).total_seconds())
    dmg = float(df.loc[df["type"] == "damage", "effective_amount"].sum())
    heal = float(df.loc[df["type"].isin(["heal", "absorb"]), "effective_amount"].sum())
    return {
        "start": str(start),
        "end": str(end),
        "duration_s": round(dur, 2),
        "total_damage": int(dmg),
        "total_heal": int(heal),
        "dps": round(dmg / dur, 1) if dur > 0 else 0.0,
        "hps": round(heal / dur, 1) if dur > 0 else 0.0,
        "events": len(df),
    }


# ── App factory ──────────────────────────────────────────────────────────────


@asynccontextmanager
async def _lifespan(_: FastAPI):
    """Warm the memo cache at startup so the first request after a restart
    doesn't pay the full SQLite frame reload (~2 s over 130k rows)."""
    try:
        deps.get_events()
        deps.character_options()
    except Exception:
        logging.getLogger(__name__).warning("startup warmup failed", exc_info=True)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="WoW Combat Log API",
        description="REST + SSE surface over the parsed combat CSV / SQLite store.",
        version="0.3.0",
        lifespan=_lifespan,
    )

    # ── Health / discovery ────────────────────────────────────────────────

    @app.get("/api/health")
    def health():
        c = deps.cfg()
        out = {
            "sqlite_active": deps.use_sqlite(),
            "csv_path": c.CSV_PATH,
            "csv_exists": os.path.exists(c.CSV_PATH),
        }
        if out["csv_exists"]:
            out["csv_mtime"] = os.path.getmtime(c.CSV_PATH)
        if deps.use_sqlite():
            from storage import queries

            stats = queries.db_stats(c.DB_PATH)
            out.update(stats)
            out["encounter_count"] = queries.count_encounters(c.DB_PATH)
            if stats.get("synced_at"):
                out["db_age_s"] = round(time.time() - stats["synced_at"], 1)
        return out

    @app.get("/api/characters")
    def characters(include_others: bool = False):
        return deps.character_options(include_others=include_others)

    # ── Encounters ────────────────────────────────────────────────────────

    @app.get("/api/encounters")
    def encounters(
        character: str | None = None,
        hidden: str = Query("exclude", pattern="^(exclude|include)$"),
    ):
        c = deps.cfg()

        def build():
            full = deps.get_events()
            meta, enc_df, _, _, _ = data_engine.compute_all_encounters_stats(
                path=c.CSV_PATH, character=character, df=full)
            # Zone per combat (mode of zone_name) — ~1 s over 130k rows, so
            # it is memoized with the stats rather than recomputed per call.
            zone = (
                full[full["combat_id"] > 0]
                .groupby("combat_id")["zone_name"]
                .agg(lambda x: x.mode().iloc[0] if not x.mode().empty else "")
            )
            return meta, enc_df, zone

        meta, enc_df, zone = deps.memo(
            f"enc:{character or 'all'}:h{hidden}", 10.0, build)
        rows = _records(enc_df)
        if rows:
            # enc_df carries the time bounds as min/max — rename for the API.
            for r in rows:
                r["start_dt"] = r.pop("min")
                r["end_dt"] = r.pop("max")
        if not rows:
            return {"meta": meta, "encounters": []}

        # Note/hidden stamps (sidecar reads, cheap and write-sensitive).
        notes = deps.load_notes()
        hidden_set = deps.load_hidden()

        out = []
        for r in rows:
            cid = int(r["combat_id"])
            r["zone_name"] = str(zone.get(cid, ""))
            r["note"] = notes.get(cid)
            r["hidden"] = cid in hidden_set
            if hidden == "exclude" and r["hidden"]:
                continue
            out.append(r)
        out.sort(key=lambda r: r["start_dt"], reverse=True)
        return {"meta": meta, "encounters": out}

    @app.get("/api/combat/{combat_id}/summary")
    def combat_summary(combat_id: int, top_n: int = Query(10, ge=1, le=50)):
        df = _combat_frame(combat_id)
        player = _mode(df["source"])
        tdf = df[(df["type"] == "damage") & df["target"].notna()
                 & (df["target"] != "")]
        if player:
            tdf = tdf[tdf["target"] != player]
        targets = []
        if not tdf.empty:
            agg = (tdf.groupby("target")["effective_amount"]
                   .agg(total="sum", count="count").reset_index()
                   .sort_values("total", ascending=False).head(top_n))
            agg["avg"] = (agg["total"] / agg["count"]).round(1)
            targets = _records(agg)
        # Combat-level extras (parity with the Streamlit header metrics).
        dmg = df[df["type"] == "damage"]
        total_damage = float(dmg["effective_amount"].sum())
        total_damage_raw = float(dmg["amount"].sum())
        overkill_pct = None
        if total_damage_raw > 0:
            overkill_pct = round(
                (total_damage_raw - total_damage) / total_damage_raw * 100, 1)
        total_absorb = float(df.loc[df["type"] == "absorb",
                                    "effective_amount"].sum())
        ttk_s = None
        died = df[(df["event"] == "UNIT_DIED") & df["target"].notna()
                  & (df["target"] != "")]
        if player:
            died = died[died["target"] != player]
        if not died.empty and not df["timestamp_dt"].isna().any():
            ttk_s = round(float(
                (died["timestamp_dt"].min() - df["timestamp_dt"].min())
                .total_seconds()), 1)
        target_name = str(targets[0]["target"]) if targets else ""
        return {
            "combat_id": combat_id,
            "target_name": target_name,
            "note": deps.load_notes().get(combat_id),
            "bounds": _combat_bounds(df),
            "ttk_s": ttk_s,
            "overkill_pct": overkill_pct,
            "total_damage_raw": int(total_damage_raw),
            "total_absorb": int(total_absorb),
            "targets": targets,
            "top_damage_spells": _records(
                data_engine.spell_aggregates(df, "damage", top_n)),
            "top_heal_spells": _records(
                data_engine.spell_aggregates(df, ["heal", "absorb"], top_n)),
        }

    @app.get("/api/combat/{combat_id}/events")
    def combat_events(combat_id: int,
                      limit: int = Query(200, ge=1, le=5000)):
        """Most recent raw events of the combat (Recent-events table and
        the client-side rotation timeline / uptime charts)."""
        df = _combat_frame(combat_id)
        cols = ["timestamp", "event", "source", "target", "spell_name",
                "amount", "effective_amount", "type"]
        sub = df.sort_values("timestamp_dt").tail(limit)[cols]
        return {"combat_id": combat_id, "events": _records(sub)}

    @app.get("/api/combat/{combat_id}/timeline")
    def combat_timeline(
        combat_id: int,
        resample_s: int = Query(1, ge=1, le=60),
        smooth_s: int = Query(0, ge=0, le=60),
        source: str | None = None,
        spell: str | None = None,
    ):
        """Per-resample_s DPS/HPS series. ``spell`` mirrors the Streamlit
        spell filter: plain name, or ``Name [Damage]`` / ``Name [Healing]``
        (adds selected_dps / selected_hps lines)."""
        df = _combat_frame(combat_id)
        if source:
            df = df[df["source"] == source]
        ts = data_engine.combat_time_series(
            df, resample_s=resample_s, spell_filter=spell or None)
        if ts.empty:
            return {"combat_id": combat_id, "resample_s": resample_s,
                    "points": []}
        if smooth_s > 1:
            for col in ("DPS", "HPS", "Selected_DPS", "Selected_HPS"):
                if col in ts.columns:
                    ts[col] = ts[col].rolling(
                        window=smooth_s, center=True, min_periods=1).mean()
        t0 = ts.index[0]
        points = [
            {
                "t": int((idx - t0).total_seconds()),
                "dps": round(float(row["DPS"]), 1),
                "hps": round(float(row["HPS"]), 1),
                **({"selected_dps": round(float(row["Selected_DPS"]), 1)}
                   if "Selected_DPS" in ts.columns else {}),
                **({"selected_hps": round(float(row["Selected_HPS"]), 1)}
                   if "Selected_HPS" in ts.columns else {}),
            }
            for idx, row in ts.iterrows()
        ]
        return {"combat_id": combat_id, "resample_s": resample_s,
                "smooth_s": smooth_s, "points": points}

    @app.get("/api/all-encounters/abilities")
    def all_encounter_abilities(character: str | None = None,
                                top_n: int = Query(15, ge=1, le=50)):
        """Ability breakdown across all (optionally character-filtered)
        combats — the 'Ability breakdown' section of the All Encounters
        view and the ability tables of Totals."""

        def build():
            df = deps.get_events()
            if character and character != "All":
                df = df[df["source"] == character]
            df = df[df["combat_id"] > 0]
            return {
                "damage": _records(
                    data_engine.spell_aggregates(df, "damage", top_n)),
                "healing": _records(
                    data_engine.spell_aggregates(df, ["heal", "absorb"], top_n)),
            }

        return deps.memo(f"abilities:{character or 'all'}:n{top_n}", 30.0, build)

    # ── Runs / totals / comparisons ───────────────────────────────────────

    @app.get("/api/runs")
    def runs(gap_minutes: int = Query(20, ge=1, le=1440)):
        c = deps.cfg()

        def build():
            runs_df, enc = data_engine.compute_runs(path=c.CSV_PATH,
                                                    gap_minutes=gap_minutes,
                                                    df=deps.get_events())
            return runs_df, enc

        runs_df, enc = deps.memo(f"runs:{gap_minutes}", 10.0, build)
        return {"runs": _records(runs_df), "encounters": _records(enc)}

    @app.get("/api/totals")
    def totals(character: str | None = None):
        c = deps.cfg()
        totals_df, meta = deps.memo(
            f"totals:{character or 'all'}", 10.0,
            lambda: data_engine.compute_totals_summary(
                path=c.CSV_PATH, character=character,
                df=deps.get_events()))
        return {"totals": _records(totals_df), "meta": meta}

    @app.get("/api/comparison/characters")
    def comparison_characters():
        c = deps.cfg()
        options = deps.character_options()
        entries = []
        for ch in options["characters"]:

            def build(ch=ch):
                meta, _, dmg_top, heal_top, _ = (
                    data_engine.compute_all_encounters_stats(
                        path=c.CSV_PATH, character=ch,
                        df=deps.get_events()))
                return {
                    "character": ch,
                    **meta,
                    "top_damage_spells": _records(dmg_top.head(10)),
                    "top_heal_spells": _records(heal_top.head(10)),
                }

            entries.append(deps.memo(f"cmp:char:{ch}", 30.0, build))
        return {"characters": entries}

    @app.get("/api/comparison/bosses")
    def comparison_bosses():
        def build():
            df = deps.get_events()
            df = df[df["combat_id"] > 0]
            if df.empty:
                return {"bosses": []}
            times = df.groupby("combat_id")["timestamp_dt"].agg(["min", "max"])
            times["duration_s"] = ((times["max"] - times["min"])
                                   .dt.total_seconds().clip(lower=0))
            times = times.rename(columns={"min": "start_dt", "max": "end_dt"})
            dmg = df[df["type"] == "damage"].groupby(
                "combat_id")["effective_amount"].sum()
            per = times.join(dmg.rename("total_damage")).fillna(0)
            per["dps"] = np_where_dur(per)
            player = _mode(df["source"])
            tdf = df[(df["type"] == "damage") & df["target"].notna()
                     & (df["target"] != "")]
            if player:
                tdf = tdf[tdf["target"] != player]
            boss = tdf.groupby("combat_id")["target"].agg(
                lambda x: x.value_counts().index[0] if len(x) else "")
            zone = df.groupby("combat_id")["zone_name"].agg(
                lambda x: x.mode().iloc[0] if not x.mode().empty else "")
            per = per.join(boss.rename("boss")).join(zone.rename("zone_name"))
            per = per[per["boss"] != ""]
            grp = per.groupby("boss").agg(
                n_encounters=("dps", "size"),
                zone_name=("zone_name", "first"),
                best_dps=("dps", "max"),
                worst_dps=("dps", "min"),
                avg_dps=("dps", "mean"),
                total_damage=("total_damage", "sum"),
            ).reset_index().rename(columns={"boss": "name"})
            grp["avg_dps"] = grp["avg_dps"].round(1)
            grp["best_dps"] = grp["best_dps"].round(1)
            grp["worst_dps"] = grp["worst_dps"].round(1)
            grp = grp.sort_values("total_damage", ascending=False).head(20)
            return {"bosses": _records(grp)}

        return deps.memo("cmp:bosses", 30.0, build)

    # ── Writes (sidecars + SQLite, single writer: this API) ───────────────

    class NoteBody(BaseModel):
        note: str = ""

    @app.post("/api/encounters/{combat_id}/note")
    def set_note(combat_id: int, body: NoteBody):
        if not deps.combat_exists(combat_id):
            raise HTTPException(status_code=404,
                                detail=f"combat {combat_id} not found")
        deps.save_note(combat_id, body.note)
        return {"combat_id": combat_id, "note": body.note or None}

    @app.post("/api/encounters/{combat_id}/hide")
    def hide_encounter(combat_id: int):
        if not deps.combat_exists(combat_id):
            raise HTTPException(status_code=404,
                                detail=f"combat {combat_id} not found")
        deps.set_hidden(combat_id, hidden=True)
        return {"combat_id": combat_id, "hidden": True}

    @app.delete("/api/encounters/{combat_id}/hide")
    def unhide_encounter(combat_id: int):
        deps.set_hidden(combat_id, hidden=False)
        return {"combat_id": combat_id, "hidden": False}

    # ── Exports ───────────────────────────────────────────────────────────

    @app.get("/api/combat/{combat_id}/export.csv")
    def export_csv(combat_id: int):
        df = _combat_frame(combat_id)
        data = export_share.combat_csv_bytes(df)
        return Response(
            content=data,
            media_type="text/csv",
            headers={"Content-Disposition":
                     f'attachment; filename="combat_{combat_id}.csv"'},
        )

    @app.get("/api/combat/{combat_id}/export.gif")
    def export_gif(combat_id: int, max_bars: int = Query(5, ge=1, le=20),
                   fps: int = Query(6, ge=1, le=30)):
        df = _combat_frame(combat_id)
        data = export_share.create_combat_gif_bytes(df, max_bars=max_bars,
                                                    fps=fps)
        if data is None:
            raise HTTPException(status_code=404,
                                detail="no gif data for this combat")
        return Response(
            content=data,
            media_type="image/gif",
            headers={"Content-Disposition":
                     f'attachment; filename="combat_{combat_id}.gif"'},
        )

    # ── Replay [REAL-DATA] ────────────────────────────────────────────────

    @app.get("/api/combat/{combat_id}/replay")
    def replay(combat_id: int):
        """Positional replay manuscript for a combat (needs the archived raw
        log that recorded it; 404 when none covers the time window)."""
        df = _combat_frame(combat_id)
        start, end = df["timestamp_dt"].min(), df["timestamp_dt"].max()
        log_path = replay_engine.find_log_for_combat(start, end, deps.cfg().LOG_DIR)
        if not log_path:
            raise HTTPException(
                status_code=404,
                detail="no archived raw log covers this combat "
                       "[REAL-DATA: drop more logs into data/logs/]")
        plain = _decompress_cached(log_path)
        manuscript = replay_engine.generate_replay_manuscript(start, end, plain)
        if manuscript is None:
            raise HTTPException(status_code=404,
                                detail="no positional events in the log")
        return json.loads(manuscript)

    # ── SSE live stream ───────────────────────────────────────────────────

    @app.get("/api/events")
    async def events(max_polls: int = Query(0, ge=0)):
        """Event stream: `hello` with the current watermark, then
        `encounter_closed` per new combat id and `data_updated` when the row
        count moves. Poll interval: WOW_SSE_POLL_S (default 1.5 s).

        ``max_polls>0`` bounds the stream (0 = unbounded) — useful for curl
        smoke tests and the API test-suite.
        """
        poll = float(os.environ.get("WOW_SSE_POLL_S", "1.5"))

        async def gen():
            wm = deps.watermark()
            yield {"event": "hello", "data": json.dumps(wm)}
            last_max, last_rows = wm["max_combat_id"], wm["row_count"]
            polls = 0
            while True:
                if max_polls and polls >= max_polls:
                    return
                polls += 1
                await asyncio.sleep(poll)
                wm = deps.watermark()
                new_max, rows = wm["max_combat_id"], wm["row_count"]
                if new_max > last_max:
                    fresh = deps.get_events()
                    for cid in range(last_max + 1, new_max + 1):
                        sub = fresh[fresh["combat_id"] == cid]
                        if sub.empty:
                            continue
                        summary = _combat_bounds(sub)
                        summary["combat_id"] = cid
                        summary["zone_name"] = _mode(sub["zone_name"])
                        yield {"event": "encounter_closed",
                               "data": json.dumps(summary)}
                    last_max = new_max
                if rows != last_rows:
                    last_rows = rows
                    yield {"event": "data_updated", "data": json.dumps(wm)}

        return EventSourceResponse(gen())

    # ── SPA hosting ──────────────────────────────────────────────────────
    # Serve the built frontend (web/dist, produced by `make web-build`) and
    # fall back to index.html for client-side routes. In dev, the Vite dev
    # server (make web-dev) proxies /api here instead.
    default_dist = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "web", "dist")
    dist = os.environ.get("WOW_DIST_DIR", default_dist)
    if os.path.isdir(dist):
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles

        app.mount("/assets",
                  StaticFiles(directory=os.path.join(dist, "assets")),
                  name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            full = os.path.normpath(os.path.join(dist, path))
            if path and os.path.isfile(full):
                return FileResponse(full)
            return FileResponse(os.path.join(dist, "index.html"))

    return app


def np_where_dur(per: pd.DataFrame) -> pd.Series:
    """dps = damage/duration, 0 where duration == 0 (helper for the boss
    comparison; isolated to keep the route readable)."""
    import numpy as np

    dur = per["duration_s"]
    return pd.Series(
        np.where(dur > 0, per["total_damage"] / dur.replace(0, np.nan), 0.0),
        index=per.index,
    )


app = create_app()
