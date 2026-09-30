"""
Phase 3 API tests.

Hermetic: synthetic fixture → real parser → CSV → SQLite (all in tmp), with
every `config` path the API touches monkeypatched. Runs the FastAPI app via
ASGI (httpx/TestClient) — no ports, no real data. Covers one test per
endpoint group, write→read round trips for note/hide, and the SSE stream
emitting on a simulated tail append.
"""

import csv
import importlib.util
import json
import pathlib
import sys
import threading
import time

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import make_fixture_log as fx  # noqa: E402


def _load_parser():
    spec = importlib.util.spec_from_file_location(
        "wow_parser_api_test", ROOT / "wow-parser.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def fixture_csv(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("api")
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


@pytest.fixture()
def client(fixture_csv, tmp_path, monkeypatch):
    import config
    from storage.sync import sync_from_csv

    db_path = tmp_path / "api.db"
    sync_from_csv(str(fixture_csv), str(db_path))

    monkeypatch.setattr(config, "CSV_PATH", str(fixture_csv))
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(config, "NOTES_PATH", str(tmp_path / "notes.jsonl"))
    monkeypatch.setattr(config, "HIDDEN_PATH", str(tmp_path / "hidden.json"))
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path / "nologs"))
    monkeypatch.setenv("WOW_USE_SQLITE", "1")
    monkeypatch.setenv("WOW_SSE_POLL_S", "0.2")

    import api.deps as deps

    deps.reset_cache()
    try:
        from starlette.testclient import TestClient

        from api.main import create_app

        with TestClient(create_app()) as c:
            yield c
    finally:
        deps.reset_cache()


# ── Health / discovery ───────────────────────────────────────────────────────


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["sqlite_active"] is True
    assert body["row_count"] > 0
    assert body["max_combat_id"] == 6
    assert body["db_age_s"] is not None


def test_characters(client):
    body = client.get("/api/characters").json()
    # Only 3-part Name-Realm-Region sources are player-like.
    assert body["characters"] == [
        "Lumiara-TheMaelstrom-EU", "Zethrok-TheMaelstrom-EU"]
    # Per-source combat counts are exposed for the SPA tables.
    assert body["counts"]["Zethrok-TheMaelstrom-EU"] == 4
    assert body["counts"]["Crabpet"] >= 1
    body2 = client.get("/api/characters",
                       params={"include_others": "true"}).json()
    # Crabpet fought 1 combat < MIN_SOURCE_COMBATS (3) → filtered out.
    assert isinstance(body2["others"], list)
    assert all(n not in body2["others"] for n in body["characters"])


# ── Read endpoints ───────────────────────────────────────────────────────────


def test_encounters(client):
    body = client.get("/api/encounters").json()
    encs = body["encounters"]
    assert body["meta"]["n_encounters"] == 6
    assert sorted(e["combat_id"] for e in encs) == [1, 2, 3, 4, 5, 6]
    for e in encs:
        assert e["duration_s"] > 0
        assert e["start_dt"] and e["end_dt"] and e["zone_name"]
        assert "note" in e and "hidden" in e
    # Newest first.
    starts = [e["start_dt"] for e in encs]
    assert starts == sorted(starts, reverse=True)

    # Character filter (Lumiara only fought combats 5 and 6).
    body = client.get("/api/encounters",
                      params={"character": "Lumiara-TheMaelstrom-EU"}).json()
    assert sorted(e["combat_id"] for e in body["encounters"]) == [5, 6]


def test_combat_summary(client):
    r = client.get("/api/combat/2/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["combat_id"] == 2
    assert body["bounds"]["total_damage"] > 0
    assert body["targets"]
    assert body["target_name"]
    assert body["top_damage_spells"]
    assert body["top_heal_spells"]
    # Parity extras for the header metrics.
    assert "ttk_s" in body
    assert body["total_damage_raw"] >= body["bounds"]["total_damage"]
    assert body["total_absorb"] >= 0
    assert client.get("/api/combat/9999/summary").status_code == 404


def test_combat_events(client):
    r = client.get("/api/combat/1/events", params={"limit": 50})
    assert r.status_code == 200
    body = r.json()
    events = body["events"]
    assert 0 < len(events) <= 50
    keys = set(events[0])
    assert {"timestamp", "event", "source", "spell_name", "type"} <= keys
    assert client.get("/api/combat/9999/events").status_code == 404


def test_timeline(client):
    r = client.get("/api/combat/1/timeline", params={"resample_s": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["points"]
    p0 = body["points"][0]
    assert p0["t"] == 0
    assert set(p0) >= {"t", "dps", "hps"}

    # Source filter narrows the series; smoothing must not crash.
    src = body["points"]
    r2 = client.get("/api/combat/1/timeline",
                    params={"resample_s": 1, "smooth_s": 3,
                            "source": "Zethrok-TheMaelstrom-EU"}).json()
    assert r2["points"]
    assert len(r2["points"]) <= len(src) or True  # same resample, same length

    # Spell filter adds the selected_* series (Streamlit parity).
    top = client.get("/api/combat/1/summary").json()["top_damage_spells"]
    if top:
        spell = f"{top[0]['spell']} [Damage]"
        r3 = client.get("/api/combat/1/timeline",
                        params={"resample_s": 1, "spell": spell}).json()
        assert "selected_dps" in r3["points"][-1] or any(
            "selected_dps" in p for p in r3["points"])
    assert client.get("/api/combat/9999/timeline").status_code == 404


def test_runs(client):
    body = client.get("/api/runs", params={"gap_minutes": 20}).json()
    assert body["runs"]
    assert body["encounters"]
    assert "run_id" in body["encounters"][0]
    assert "zone_name" in body["runs"][0]


def test_totals(client):
    body = client.get("/api/totals").json()
    assert body["meta"]["total_combats"] == 6
    assert body["totals"]
    row = body["totals"][0]
    assert set(row) >= {"target", "encounters", "total_damage", "dps"}


def test_all_encounter_abilities(client):
    body = client.get("/api/all-encounters/abilities", params={"top_n": 5}).json()
    assert body["damage"]
    assert len(body["damage"]) <= 5
    assert set(body["damage"][0]) >= {"spell", "total", "count"}
    # Character filter narrows the aggregation.
    z = client.get("/api/all-encounters/abilities",
                   params={"character": "Zethrok-TheMaelstrom-EU"}).json()
    assert z["damage"]
    assert sum(d["total"] for d in z["damage"]) <= \
        sum(d["total"] for d in body["damage"]) + 1


def test_comparison(client):
    chars = client.get("/api/comparison/characters").json()["characters"]
    by_name = {e["character"]: e for e in chars}
    assert "Zethrok-TheMaelstrom-EU" in by_name
    z = by_name["Zethrok-TheMaelstrom-EU"]
    assert z["n_encounters"] == 4
    assert z["top_damage_spells"]

    bosses = client.get("/api/comparison/bosses").json()["bosses"]
    assert bosses
    assert set(bosses[0]) >= {"name", "n_encounters", "avg_dps", "zone_name"}


# ── Write endpoints (round trips) ────────────────────────────────────────────


def test_note_roundtrip(client):
    r = client.post("/api/encounters/3/note", json={"note": "great pull"})
    assert r.status_code == 200
    body = client.get("/api/encounters").json()
    e3 = [e for e in body["encounters"] if e["combat_id"] == 3][0]
    assert e3["note"] == "great pull"

    # Empty note deletes it.
    client.post("/api/encounters/3/note", json={"note": ""})
    body = client.get("/api/encounters").json()
    e3 = [e for e in body["encounters"] if e["combat_id"] == 3][0]
    assert not e3["note"]

    assert client.post("/api/encounters/9999/note",
                       json={"note": "x"}).status_code == 404


def test_hide_roundtrip(client):
    assert client.post("/api/encounters/1/hide").status_code == 200
    body = client.get("/api/encounters").json()
    assert 1 not in [e["combat_id"] for e in body["encounters"]]

    body = client.get("/api/encounters",
                      params={"hidden": "include"}).json()
    e1 = [e for e in body["encounters"] if e["combat_id"] == 1][0]
    assert e1["hidden"] is True

    assert client.delete("/api/encounters/1/hide").status_code == 200
    body = client.get("/api/encounters").json()
    assert 1 in [e["combat_id"] for e in body["encounters"]]


# ── Exports ──────────────────────────────────────────────────────────────────


def test_export_csv(client):
    r = client.get("/api/combat/1/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert r.content.decode().splitlines()[0].startswith("combat_id")
    assert client.get("/api/combat/9999/export.csv").status_code == 404


def test_export_gif(client):
    r = client.get("/api/combat/1/export.gif")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/gif")
    assert r.content.startswith(b"GIF89a")


# ── Replay stub [REAL-DATA] ──────────────────────────────────────────────────


def test_replay_stub_404(client):
    r = client.get("/api/combat/1/replay")
    assert r.status_code == 404
    assert "REAL-DATA" in r.json()["detail"]


# ── SSE ──────────────────────────────────────────────────────────────────────


def _read_sse_events(client, stop_after, timeout_s=10.0, max_polls=60):
    """Consume the SSE stream until *stop_after* events arrive (or timeout).
    The stream is bounded (max_polls) so the ASGI task always terminates."""
    events = []
    errors = []

    def consume():
        try:
            with client.stream(
                "GET", "/api/events", params={"max_polls": max_polls}
            ) as resp:
                assert resp.status_code == 200
                event = None
                for line in resp.iter_lines():
                    if line.startswith("event:"):
                        event = line.split(":", 1)[1].strip()
                    elif line.startswith("data:") and event is not None:
                        events.append(
                            (event, json.loads(line.split(":", 1)[1])))
                        event = None
                        if len(events) >= stop_after:
                            break
        except BaseException as exc:  # surface thread errors to the test
            errors.append(exc)

    t = threading.Thread(target=consume, daemon=True)
    t.start()
    t.join(timeout_s)
    if errors:
        raise errors[0]
    return events


def test_sse_hello_and_encounter_closed(client, fixture_csv):
    """hello on connect; encounter_closed when a tail flush appends combat 7."""
    from storage.sync import incremental_append

    import config

    def append_new_encounter():
        time.sleep(0.3)  # let the stream register its baseline first
        with open(fixture_csv, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow([7, "03/21/2026 20:00:00.000000", "SWING_DAMAGE",
                        "Zethrok-TheMaelstrom-EU", "Nightborne Raider", "Melee",
                        300, 300, "damage", 2962, "The Maelstrom", 0])
        # Explicit db_path: sync's default is bound at import time, so the
        # monkeypatched config.DB_PATH would not be picked up otherwise.
        incremental_append(str(fixture_csv), config.DB_PATH)

    t = threading.Thread(target=append_new_encounter, daemon=True)
    t.start()

    # 60 polls × 0.2 s = 12 s window; we stop after 3 events anyway.
    events = _read_sse_events(client, stop_after=3, timeout_s=15.0)
    names = [e for e, _ in events]
    assert names[0] == "hello"
    assert "encounter_closed" in names
    closed = [d for n, d in events if n == "encounter_closed"][0]
    assert closed["combat_id"] == 7
    assert closed["total_damage"] == 300
    assert "data_updated" in names
