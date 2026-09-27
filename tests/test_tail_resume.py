"""
Tests for tail-mode CSV resumption (wow-parser.py).

Tail mode used to ``seek(0, 2)`` on startup, silently discarding every log line
written before it started.  These tests guard the new behaviour: on startup (and
on log rotation) we resume from the CSV's latest timestamp, so combat that
happened before tail mode started is recovered instead of lost.
"""

import importlib.util
import pathlib
from datetime import datetime

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

HEADER = (
    "combat_id,timestamp,event,source,target,spell_name,amount,"
    "effective_amount,type,zone_id,zone_name,spell_id"
)


@pytest.fixture(scope="module")
def parser():
    """Load wow-parser.py (hyphenated name) as a module, cached per test run."""
    spec = importlib.util.spec_from_file_location(
        "wow_parser_tail_resume", ROOT / "wow-parser.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_csv(tmp_path, ts_rows):
    """Write a CSV with the given timestamp column values; return its path str."""
    p = tmp_path / "out.csv"
    lines = [HEADER]
    for i, ts in enumerate(ts_rows):
        lines.append(f"0,{ts},SPELL_HEAL,a,a,x,1,1,heal,0,,{i}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


def _write_log(tmp_path, lines):
    p = tmp_path / "log.txt"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


def test_line_timestamp_parses(parser):
    dt = parser._line_timestamp(
        '9/27/2026 08:16:39.4242  SPELL_DAMAGE,Player-1-AAAA,"a",0x511'
    )
    assert dt is not None
    assert (dt.year, dt.month, dt.day) == (2026, 9, 27)
    assert (dt.hour, dt.minute, dt.second) == (8, 16, 39)


def test_line_timestamp_rejects_garbage(parser):
    assert parser._line_timestamp("") is None
    assert parser._line_timestamp(None) is None
    assert parser._line_timestamp("no timestamp here") is None


def test_read_max_timestamp_missing_file(parser, tmp_path):
    assert parser._read_max_timestamp(str(tmp_path / "nope.csv")) is None


def test_read_max_timestamp_uses_datetime_not_string(parser, tmp_path):
    # "3/7/2026" sorts AFTER "3/26/2026" as a string, so a naive string max
    # would pick the wrong one. The real (chronological) max is March 26.
    csv_path = _write_csv(
        tmp_path,
        ["3/7/2026 21:24:20.9451", "3/26/2026 19:00:14.6671"],
    )
    mx = parser._read_max_timestamp(csv_path)
    assert mx is not None
    assert (mx.month, mx.day) == (3, 26)


def test_open_log_resuming_skips_lines_already_in_csv(parser, tmp_path):
    csv_path = _write_csv(tmp_path, ["3/26/2026 19:00:14.6671"])
    log_path = _write_log(
        tmp_path,
        [
            '3/26/2026 19:00:14.6671  SPELL_HEAL,Player-1-AAAA,"a",0x511',
            '3/26/2026 19:00:13.0000  SPELL_HEAL,Player-1-AAAA,"a",0x511',
            '9/27/2026 08:16:39.4242  SPELL_DAMAGE,Player-1-AAAA,"a",0x511',
        ],
    )
    fh, pending = parser._open_log_resuming(log_path, csv_path)
    fh.close()
    # Only the Sep 27 line is strictly newer than the CSV max (Mar 26);
    # the equal and older Mar 26 lines are already captured.
    assert len(pending) == 1
    assert "9/27/2026" in pending[0]


def test_open_log_resuming_no_csv_catches_all(parser, tmp_path):
    # No prior CSV -> the whole log is treated as new.
    log_path = _write_log(
        tmp_path,
        [
            '9/27/2026 08:16:39.4242  SPELL_DAMAGE,Player-1-AAAA,"a",0x511',
            '9/27/2026 08:16:40.0000  SPELL_DAMAGE,Player-1-AAAA,"a",0x511',
        ],
    )
    fh, pending = parser._open_log_resuming(log_path, str(tmp_path / "nope.csv"))
    fh.close()
    assert len(pending) == 2


def _parsed(ts, source="Mythrul-TheMaelstrom-EU"):
    return {
        "timestamp": ts,
        "event": "SPELL_DAMAGE",
        "source": source,
        "target": "",
        "spell_name": "Heart Strike",
        "amount": 1234,
        "effective_amount": 1234,
        "type": "damage",
        "spell_id": 20403,
    }


def test_build_encounter_rows_keeps_in_window(parser):
    # Regression: in-window rows must be stamped with combat_id and kept
    # (previously the append lived in the wrong branch and everything was dropped).
    enc_start = datetime(2026, 9, 27, 8, 16, 39, 424200)
    close_dt = datetime(2026, 9, 27, 8, 17, 0, 0)
    rows = parser._build_encounter_rows(
        [
            _parsed("9/27/2026 08:16:40.0000"),
            None,  # unparseable -> ignored
            _parsed("9/27/2026 08:16:59.0000"),
            _parsed("9/27/2026 08:18:30.0000"),  # after close -> dropped
        ],
        enc_start,
        close_dt,
        872,
        1234,
        "Maelstrom",
    )
    assert len(rows) == 2
    assert all(r[0] == 872 for r in rows)
    assert all(r[9] == 1234 for r in rows)
    assert all(r[10] == "Maelstrom" for r in rows)


def test_build_encounter_rows_drops_all_out_of_window(parser):
    enc_start = datetime(2026, 9, 27, 8, 16, 39, 424200)
    close_dt = datetime(2026, 9, 27, 8, 17, 0, 0)
    rows = parser._build_encounter_rows(
        [_parsed("9/27/2026 08:16:00.0000"), _parsed("9/27/2026 08:18:00.0000")],
        enc_start,
        close_dt,
        872,
        0,
        "",
    )
    assert rows == []
