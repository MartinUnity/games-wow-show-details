"""
Phase 5.1 parity: the SQLite-backed API must return the same numbers the
Streamlit app produced from the pandas/CSV path.

The two paths share the bare compute functions in ``utils/data_engine.py``;
what can differ is the *frame* they run on (SQLite rows vs ``pd.read_csv``)
and the endpoint wiring. These tests pin both:

1. frame identity — every row/column of the SQLite frame matches the CSV
   frame (the documented ``""``→NaN text alignment included);
2. payload parity — for every view (encounters, runs, totals, comparison,
   combat summary/timeline/events, abilities) the API JSON equals the same
   computation run on the CSV frame.

Hermetic on the synthetic fixture; the real-data variant (repo-root CSV,
``make devdata``) runs the same comparisons at full scale when present.
"""

import importlib.util
import json
import os
import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import make_fixture_log as fx  # noqa: E402

CSV_COLS = ["combat_id", "timestamp", "event", "source", "target",
            "spell_name", "amount", "effective_amount", "type",
            "zone_id", "zone_name", "spell_id"]
NUM_COLS = ["combat_id", "amount", "effective_amount", "zone_id", "spell_id"]
TXT_COLS = [c for c in CSV_COLS if c not in NUM_COLS]


def _load_parser():
    spec = importlib.util.spec_from_file_location(
        "wow_parser_parity_test", ROOT / "wow-parser.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def fixture_csv(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("parity")
    wp = _load_parser()
    log = tmp / "log.txt"
    log.write_text(
        "\n".join(fx.session_zethrok() + fx.session_lumiara()) + "\n",
        encoding="utf-8",
    )
    csv_path = tmp / "fixture.csv"
    original = wp._write_boss_kills
    wp._write_boss_kills = lambda *a, **k: None
    try:
        wp.export_csv(str(log), csv_path=str(csv_path))
    finally:
        wp._write_boss_kills = original
    return csv_path


def _make_client(csv_path: str, db_path: str, tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "CSV_PATH", str(csv_path))
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(config, "NOTES_PATH", str(tmp_path / "notes.jsonl"))
    monkeypatch.setattr(config, "HIDDEN_PATH", str(tmp_path / "hidden.json"))
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path / "nologs"))
    monkeypatch.setenv("WOW_USE_SQLITE", "1")
    monkeypatch.setenv("WOW_SSE_POLL_S", "0.2")

    import api.deps as deps

    deps.reset_cache()
    from starlette.testclient import TestClient

    from api.main import create_app

    with TestClient(create_app()) as c:
        yield c
    deps.reset_cache()


@pytest.fixture()
def client(fixture_csv, tmp_path, monkeypatch):
    from storage.sync import sync_from_csv

    db_path = tmp_path / "parity.db"
    sync_from_csv(str(fixture_csv), str(db_path))
    yield from _make_client(fixture_csv, db_path, tmp_path, monkeypatch)


def csv_frame(path: str) -> pd.DataFrame:
    """The reference frame: pandas straight off the CSV file.

    Forces WOW_USE_SQLITE=0 so it always reads the file, even though SQLite
    is the default path now and the fixtures have a synced DB.
    """
    from utils import data_io

    old = os.environ.get("WOW_USE_SQLITE")
    try:
        os.environ["WOW_USE_SQLITE"] = "0"
        return data_io.load_csv(path=path)
    finally:
        if old is None:
            os.environ.pop("WOW_USE_SQLITE", None)
        else:
            os.environ["WOW_USE_SQLITE"] = old


def _norm_txt(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else v


def _sorted_rows(df: pd.DataFrame) -> tuple:
    """(text tuples, numeric list) sorted by text content — order-insensitive
    full-frame comparison."""
    txt = sorted(
        tuple(_norm_txt(v) for v in row)
        for row in df[TXT_COLS].itertuples(index=False, name=None)
    )
    num = sorted(
        tuple(float(v) for v in row)
        for row in df[NUM_COLS].itertuples(index=False, name=None)
    )
    return txt, num


def _close(a, b, tol=1e-6):
    """Tolerant equality for numbers; plain equality for anything else."""
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    if a is None or b is None:
        return a is b
    a, b = float(a), float(b)
    if a == 0 and b == 0:
        return True
    return abs(a - b) <= tol * max(abs(a), abs(b), 1.0)


# ── 1. Frame identity ────────────────────────────────────────────────────────


def test_frames_match(client, fixture_csv):
    from utils import data_io

    sqlite_df = data_io.load_csv(path=str(fixture_csv))  # WOW_USE_SQLITE=1
    csv_df = csv_frame(str(fixture_csv))
    assert len(sqlite_df) == len(csv_df) > 0
    t1, n1 = _sorted_rows(sqlite_df)
    t2, n2 = _sorted_rows(csv_df)
    assert t1 == t2, "text columns diverge between SQLite and CSV frames"
    assert all(_close(a, b) for pair in zip(n1, n2) for a, b in zip(*pair))


# ── 2. Payload parity, per view ──────────────────────────────────────────────


def test_encounters_parity(client, fixture_csv):
    from utils import data_engine

    csv_df = csv_frame(str(fixture_csv))
    meta, enc_df, _, _, _ = data_engine.compute_all_encounters_stats(
        path=str(fixture_csv), df=csv_df)

    body = client.get("/api/encounters").json()
    assert body["meta"]["n_encounters"] == meta["n_encounters"]
    assert _close(body["meta"]["total_damage"], meta["total_damage"])
    assert _close(body["meta"]["total_heal"], meta["total_heal"])
    assert _close(body["meta"]["avg_dps"], meta["avg_dps"])

    ref = enc_df.set_index("combat_id")
    api = {e["combat_id"]: e for e in body["encounters"]}
    assert set(api) == set(ref.index)
    for cid, e in api.items():
        r = ref.loc[cid]
        assert _close(e["duration_s"], r["duration_s"])
        assert _close(e["total_damage"], r["total_damage"])
        assert _close(e["total_heal"], r["total_heal"])
        assert _close(e["dps"], r["dps"])
        assert _close(e["hps"], r["hps"])


def test_runs_parity(client, fixture_csv):
    from utils import data_engine

    csv_df = csv_frame(str(fixture_csv))
    runs_ref, enc_ref = data_engine.compute_runs(
        path=str(fixture_csv), gap_minutes=20, df=csv_df)

    body = client.get("/api/runs", params={"gap_minutes": 20}).json()
    api_runs = {r["run_id"]: r for r in body["runs"]}
    ref_runs = runs_ref.set_index("run_id")
    assert set(api_runs) == set(ref_runs.index)
    for rid, r in api_runs.items():
        ref = ref_runs.loc[rid]
        for col in ref_runs.columns:
            if col in r and col != "run_id":
                if isinstance(ref[col], pd.Timestamp):
                    assert pd.Timestamp(r[col]) == ref[col], \
                        f"run {rid} col {col}: {r[col]} != {ref[col]}"
                else:
                    assert _close(r[col], ref[col]), \
                        f"run {rid} col {col}: {r[col]} != {ref[col]}"
    assert len(body["encounters"]) == len(enc_ref)


def test_totals_parity(client, fixture_csv):
    from utils import data_engine

    csv_df = csv_frame(str(fixture_csv))
    totals_ref, meta_ref = data_engine.compute_totals_summary(
        path=str(fixture_csv), df=csv_df)

    body = client.get("/api/totals").json()
    assert body["meta"] == meta_ref or (
        all(_close(body["meta"].get(k), meta_ref.get(k))
            for k in set(body["meta"]) | set(meta_ref)))
    api = {r["target"]: r for r in body["totals"]}
    ref = totals_ref.set_index("target")
    assert set(api) == set(ref.index)
    for t, r in api.items():
        for col in totals_ref.columns:
            if col in r and col != "target":
                assert _close(r[col], ref.loc[t, col]), \
                    f"totals {t} col {col}: {r[col]} != {ref.loc[t, col]}"


def _summary_ref(csv_df, combat_id: int) -> dict:
    """Reference combat-header numbers straight off the CSV frame (the same
    formulas the Streamlit combat_detail header used)."""
    df = csv_df[csv_df["combat_id"] == combat_id]
    start, end = df["timestamp_dt"].min(), df["timestamp_dt"].max()
    dur = max(0.0, (end - start).total_seconds())
    dmg = float(df.loc[df["type"] == "damage", "effective_amount"].sum())
    heal = float(df.loc[df["type"].isin(["heal", "absorb"]),
                        "effective_amount"].sum())
    return {
        "duration_s": round(dur, 2),
        "total_damage": int(dmg),
        "total_heal": int(heal),
        "dps": round(dmg / dur, 1) if dur > 0 else 0.0,
        "hps": round(heal / dur, 1) if dur > 0 else 0.0,
    }


def test_combat_summary_parity(client, fixture_csv):
    from utils import data_engine

    csv_df = csv_frame(str(fixture_csv))
    combat_id = 1
    df = csv_df[csv_df["combat_id"] == combat_id]

    body = client.get(f"/api/combat/{combat_id}/summary").json()
    ref = _summary_ref(csv_df, combat_id)
    for k, v in ref.items():
        assert _close(body["bounds"][k], v), \
            f"bounds.{k}: {body['bounds'][k]} != {v}"

    # Per-target damage split vs the CSV frame.
    player = df["source"].mode().iloc[0]
    tdf = df[(df["type"] == "damage") & df["target"].notna()
             & (df["target"] != "") & (df["target"] != player)]
    ref_tot = tdf.groupby("target")["effective_amount"].sum()
    api_tot = {t["target"]: t["total"] for t in body["targets"]}
    for t, tot in ref_tot.items():
        assert _close(api_tot.get(t), tot), f"target {t}: {api_tot.get(t)} != {tot}"

    # Top spells vs the CSV frame.
    ref_spells = data_engine.spell_aggregates(df, "damage", 10)
    api_spells = {s["spell"]: s for s in body["top_damage_spells"]}
    for _, r in ref_spells.iterrows():
        assert _close(api_spells[r["spell"]]["total"], r["total"]), \
            f"spell {r['spell']} total"


def test_timeline_parity(client, fixture_csv):
    from utils import data_engine

    csv_df = csv_frame(str(fixture_csv))
    df = csv_df[csv_df["combat_id"] == 1]

    def ref_points(resample_s, spell=None, smooth_s=0):
        ts = data_engine.combat_time_series(df, resample_s=resample_s,
                                            spell_filter=spell)
        for col in ("DPS", "HPS", "Selected_DPS", "Selected_HPS"):
            if col in ts.columns and smooth_s > 1:
                ts[col] = ts[col].rolling(window=smooth_s, center=True,
                                          min_periods=1).mean()
        t0 = ts.index[0]
        out = []
        for idx, row in ts.iterrows():
            p = {"t": int((idx - t0).total_seconds()),
                 "dps": round(float(row["DPS"]), 1),
                 "hps": round(float(row["HPS"]), 1)}
            if "Selected_DPS" in ts.columns:
                p["selected_dps"] = round(float(row["Selected_DPS"]), 1)
                p["selected_hps"] = round(float(row["Selected_HPS"]), 1)
            out.append(p)
        return out

    for params, spell, smooth in [
        ({"resample_s": 1}, None, 0),
        ({"resample_s": 5}, None, 0),
        ({"resample_s": 1, "smooth_s": 5}, None, 5),
        ({"resample_s": 1, "spell": "Melee [Damage]"}, "Melee [Damage]", 0),
    ]:
        api = client.get("/api/combat/1/timeline",
                         params=params).json()["points"]
        ref = ref_points(params.get("resample_s", 1), spell, smooth)
        assert len(api) == len(ref), f"{params}: {len(api)} != {len(ref)}"
        for a, r in zip(api, ref):
            for k in r:
                assert _close(a.get(k), r[k]), f"{params} t={r['t']} {k}"


def test_events_parity(client, fixture_csv):
    csv_df = csv_frame(str(fixture_csv))
    df = csv_df[csv_df["combat_id"] == 1].sort_values("timestamp_dt")
    api = client.get("/api/combat/1/events",
                     params={"limit": 20}).json()["events"]
    ref = df.tail(20)
    assert len(api) == len(ref)
    for a, (_, r) in zip(api, ref.iterrows()):
        assert a["timestamp"] == r["timestamp"]
        assert a["source"] == _norm_txt(r["source"])
        assert a["target"] == _norm_txt(r["target"])
        assert _close(a["amount"], r["amount"])
        assert _close(a["effective_amount"], r["effective_amount"])


def test_abilities_parity(client, fixture_csv):
    from utils import data_engine

    csv_df = csv_frame(str(fixture_csv))
    in_combat = csv_df[csv_df["combat_id"] > 0]

    body = client.get("/api/all-encounters/abilities",
                      params={"top_n": 10}).json()
    ref = data_engine.spell_aggregates(in_combat, "damage", 10)
    assert [s["spell"] for s in body["damage"][:len(ref)]] == \
        list(ref["spell"])
    api_tot = {s["spell"]: s["total"] for s in body["damage"]}
    for _, r in ref.iterrows():
        assert _close(api_tot[r["spell"]], r["total"])

    z = client.get("/api/all-encounters/abilities",
                   params={"character": "Zethrok-TheMaelstrom-EU",
                           "top_n": 10}).json()
    refz = data_engine.spell_aggregates(
        in_combat[in_combat["source"] == "Zethrok-TheMaelstrom-EU"],
        "damage", 10)
    assert [s["spell"] for s in z["damage"][:len(refz)]] == list(refz["spell"])


def test_comparison_characters_parity(client, fixture_csv):
    from utils import data_engine

    csv_df = csv_frame(str(fixture_csv))
    body = client.get("/api/comparison/characters").json()["characters"]
    for entry in body:
        meta, _, _, _, _ = data_engine.compute_all_encounters_stats(
            path=str(fixture_csv), character=entry["character"], df=csv_df)
        for k in ("n_encounters", "total_damage", "total_heal",
                  "total_duration_s", "avg_dps", "avg_hps"):
            assert _close(entry[k], meta[k]), \
                f"{entry['character']}.{k}: {entry[k]} != {meta[k]}"


# ── Real data at full scale [REAL-DATA] ──────────────────────────────────────

REAL_CSV = ROOT / "parsed_combat_data.csv"


def _real_data_ready() -> bool:
    if not REAL_CSV.exists():
        return False
    # The repo-root CSV is the synthetic fixture after `make fixture` —
    # only treat a large CSV as the real dataset.
    with open(REAL_CSV, encoding="utf-8", errors="replace") as f:
        for i, _ in enumerate(f):
            if i >= 5000:
                return True
    return False


@pytest.mark.skipif(not _real_data_ready(),
                    reason="real data not in place (make devdata)")
def test_real_data_parity(tmp_path, monkeypatch):
    from storage.sync import sync_from_csv
    from utils import data_engine

    db_path = tmp_path / "real_parity.db"
    n = sync_from_csv(str(REAL_CSV), str(db_path))
    assert n > 10000

    yield_client = _make_client(REAL_CSV, db_path, tmp_path, monkeypatch)
    with next(yield_client) as client:
        csv_df = csv_frame(str(REAL_CSV))
        assert len(csv_df) == n

        # Frame identity at full scale.
        from utils import data_io

        sqlite_df = data_io.load_csv(path=str(REAL_CSV))
        t1, n1 = _sorted_rows(sqlite_df)
        t2, n2 = _sorted_rows(csv_df)
        assert t1 == t2
        assert all(_close(a, b) for pair in zip(n1, n2) for a, b in zip(*pair))

        # Encounters meta + per-combat numbers.
        meta, enc_df, _, _, _ = data_engine.compute_all_encounters_stats(
            path=str(REAL_CSV), df=csv_df)
        body = client.get("/api/encounters").json()
        assert body["meta"]["n_encounters"] == meta["n_encounters"]
        assert _close(body["meta"]["total_damage"], meta["total_damage"])
        ref = enc_df.set_index("combat_id")
        api = {e["combat_id"]: e for e in body["encounters"]}
        assert set(api) == set(ref.index)
        for cid, e in api.items():
            r = ref.loc[cid]
            assert _close(e["duration_s"], r["duration_s"])
            assert _close(e["total_damage"], r["total_damage"])
            assert _close(e["dps"], r["dps"])

        # Totals.
        totals_ref, meta_ref = data_engine.compute_totals_summary(
            path=str(REAL_CSV), df=csv_df)
        body = client.get("/api/totals").json()
        api_tot = {r["target"]: r for r in body["totals"]}
        ref_tot = totals_ref.set_index("target")
        assert set(api_tot) == set(ref_tot.index)
        for t, r in api_tot.items():
            for col in totals_ref.columns:
                if col in r and col != "target":
                    assert _close(r[col], ref_tot.loc[t, col]), \
                        f"totals {t}.{col}"

        # One combat's summary + timeline (the largest in-combat one).
        cids = sorted(set(api))
        biggest = max(cids, key=lambda c: api[c]["total_damage"])
        df = csv_df[csv_df["combat_id"] == biggest]
        body = client.get(f"/api/combat/{biggest}/summary").json()
        ref_s = _summary_ref(csv_df, biggest)
        for k, v in ref_s.items():
            assert _close(body["bounds"][k], v), f"bounds.{k}"
        api_tl = client.get(f"/api/combat/{biggest}/timeline",
                            params={"resample_s": 1}).json()["points"]
        ts = data_engine.combat_time_series(df, resample_s=1)
        assert len(api_tl) == len(ts)
        t0 = ts.index[0]
        for p, (idx, row) in zip(api_tl, ts.iterrows()):
            assert p["t"] == int((idx - t0).total_seconds())
            assert _close(p["dps"], round(float(row["DPS"]), 1))
            assert _close(p["hps"], round(float(row["HPS"]), 1))
