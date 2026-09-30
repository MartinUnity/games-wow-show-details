import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import { Chart, baseAxis, tooltipBase } from '../components/Chart';
import { DataTable, type Col } from '../components/DataTable';
import type { CharacterEntry, SpellRow } from '../types';
import { fmt1, fmtCompact, fmtDuration } from '../lib/format';

const PALETTE = ['#e05555', '#42A5F5', '#4CAF50', '#ffb74d', '#ba68c8', '#4dd0e1', '#f06292'];

function SpellTable({ spells }: { spells: SpellRow[] }) {
  const cols: Col<SpellRow>[] = [
    { id: 'spell', header: 'Spell', value: (r) => r.spell },
    { id: 'total', header: 'Total', right: true, value: (r) => r.total, render: (r) => fmtCompact(r.total) },
    { id: 'count', header: 'Count', right: true, value: (r) => r.count },
    { id: 'avg', header: 'Avg', right: true, value: (r) => r.avg, render: (r) => fmt1(r.avg) },
  ];
  return <DataTable columns={cols} rows={spells} maxHeight={200} />;
}

export function CharacterComparisonView() {
  const [all, setAll] = useState<string[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [entries, setEntries] = useState<CharacterEntry[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    let on = true;
    Promise.all([api.characters(), api.comparisonCharacters()])
      .then(([ch, cmp]) => {
        if (!on) return;
        setAll(ch.characters);
        setEntries(cmp.characters);
        // Default: first two characters, like the Streamlit app.
        setSelected(ch.characters.slice(0, 2));
      })
      .catch((e) => on && setError(String(e)));
    return () => {
      on = false;
    };
  }, []);

  const toggle = (name: string) =>
    setSelected((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name],
    );

  const rows = useMemo(
    () => entries.filter((e) => selected.includes(e.character)),
    [entries, selected],
  );

  const names = rows.map((r) => r.character.split('-')[0]);
  const dpsOption = {
    grid: { left: 55, right: 20, top: 30, bottom: 25 },
    legend: { top: 0, textStyle: { color: '#bbb' } },
    tooltip: tooltipBase,
    xAxis: { type: 'category', data: names, ...baseAxis },
    yAxis: { type: 'value', ...baseAxis },
    series: [
      { name: 'Avg DPS', type: 'bar', data: rows.map((r, i) => ({ value: Math.round(r.avg_dps * 10) / 10, itemStyle: { color: PALETTE[i % PALETTE.length] } })), itemStyle: { color: '#e05555' } },
      { name: 'Avg HPS', type: 'bar', data: rows.map((r, i) => ({ value: Math.round(r.avg_hps * 10) / 10, itemStyle: { color: PALETTE[(i + 3) % PALETTE.length] } })), itemStyle: { color: '#4CAF50' } },
    ],
  };

  return (
    <div className="pad">
      <h2>Character Comparison</h2>
      {error && <div className="error">{error}</div>}
      <div className="row gap wrap">
        {all.map((c) => (
          <label key={c} className={`check ${selected.includes(c) ? 'picked' : ''}`}>
            <input
              type="checkbox"
              checked={selected.includes(c)}
              onChange={() => toggle(c)}
            />
            {c.split('-')[0]}
          </label>
        ))}
      </div>
      {selected.length < 2 && (
        <p className="dim small">Select at least 2 characters.</p>
      )}

      {rows.length >= 2 && (
        <>
          <div className="cards">
            {rows.map((r, i) => (
              <div key={r.character} className="card">
                <div className="card-title" style={{ color: PALETTE[i % PALETTE.length] }}>
                  {r.character.split('-')[0]}
                </div>
                <div className="small">
                  Encounters: <b>{r.n_encounters}</b>
                  <br />
                  Total time: <b>{fmtDuration(r.total_duration_s)}</b>
                  <br />
                  Avg DPS: <b>{fmt1(r.avg_dps)}</b>
                  <br />
                  Avg HPS: <b>{fmt1(r.avg_hps)}</b>
                </div>
              </div>
            ))}
          </div>

          <h3>DPS / HPS</h3>
          <Chart option={dpsOption} height={280} />

          <h3>Top damage spells</h3>
          <div className="two-col even">
            {rows.map((r) => (
              <div key={r.character}>
                <div className="sub">{r.character.split('-')[0]}</div>
                {r.top_damage_spells.length > 0 ? (
                  <SpellTable spells={r.top_damage_spells} />
                ) : (
                  <div className="dim small">No damage data.</div>
                )}
              </div>
            ))}
          </div>

          <h3>Top healing spells</h3>
          <div className="two-col even">
            {rows.map((r) => (
              <div key={r.character}>
                <div className="sub">{r.character.split('-')[0]}</div>
                {r.top_heal_spells.length > 0 ? (
                  <SpellTable spells={r.top_heal_spells} />
                ) : (
                  <div className="dim small">No healing data.</div>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
