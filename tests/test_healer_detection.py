import os
from datetime import datetime

import pandas as pd


def test_single_healer_spell_marks_run(tmp_path, monkeypatch):
    """A single occurrence of a healer-unique spell id in a run marks it Healer."""
    # Prepare a minimal CSV with one combat and one healer spell event
    csv_path = tmp_path / "parsed_combat_data.csv"
    ts = datetime.now().strftime("%m/%d/%Y %H:%M:%S.%f")
    header = "combat_id,timestamp,event,source,target,spell_name,amount,effective_amount,type,zone_id,zone_name,spell_id\n"
    # Use a made-up healer spell id
    healer_spell_id = 123456
    row = f"1,{ts},SPELL_HEAL,HealerOne-Server,HealerOne-Server,Test Heal,100,100,heal,1,TestZone,{healer_spell_id}\n"
    csv_path.write_text(header + row, encoding="utf-8")

    # Monkeypatch the sidecar loader to report this spell id as a healer spell
    import sys
    import pathlib

    # Ensure repo root is on sys.path so top-level modules (utils/) can be imported
    ROOT = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(ROOT))

    import utils.data_io as data_io


    def _fake_sidecar():
        return {"Holy_Priest": [healer_spell_id]}


    monkeypatch.setattr(data_io, "load_healer_spells", _fake_sidecar)

    # Now run compute_runs against our temporary CSV
    from utils.data_engine import compute_runs

    runs, enc = compute_runs(path=str(csv_path), gap_minutes=20)

    assert not runs.empty
    # The run should be marked Healer and the spec/class should map
    assert runs.iloc[0]["run_role"] == "Healer"
    assert runs.iloc[0].get("run_spec", "") == "Holy_Priest"
    assert runs.iloc[0].get("run_class", "") == "Priest"
