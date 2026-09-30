import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import { Chart, baseAxis, tooltipBase } from '../components/Chart';
import { DataTable, type Col } from '../components/DataTable';
import { useLive } from '../live/LiveContext';
import type { SpellRow, TargetTotal } from '../types';
import {
  fmt1,
  fmtCompact,
  fmtDuration,
  fmtInt,
} from '../lib/format';

const targetCols: Col<TargetTotal>[] = [
  { id: 'target', header: 'Target', value: (r) => r.target, render: (r) => r.target.split('-')[0] },
  { id: 'encounters', header: 'Encounters', right: true, value: (r) => r.encounters },
  { id: 'total_time_s', header: 'Total time', right: true, value: (r) => r.total_time_s, render: (r) => fmtDuration(r.total_time_s) },
  { id: 'total_damage', header: 'Damage', right: true, value: (r) => r.total_damage, render: (r) => fmtCompact(r.total_damage) },
  { id: 'total_heal', header: 'Healing', right: true, value: (r) => r.total_heal, render: (r) => fmtCompact(r.total_heal) },
  { id: 'dps', header: 'DPS', right: true, value: (r) => r.dps, render: (r) => fmt1(r.dps) },
  { id: 'hps', header: 'HPS', right: true, value: (r) => r.hps, render: (r) => fmt1(r.hps) },
];

const SORTS: { id: string; label: string }[] = [
  { id: 'encounters', label: 'Encounters' },
  { id: 'total_time_s', label: 'Total time' },
  { id: 'total_damage', label: 'Damage' },
  { id: 'total_heal', label: 'Healing' },
  { id: 'dps', label: 'DPS' },
  { id: 'hps', label: 'HPS' },
];

function AbilityPanel({ title, spells, color }: { title: string; spells: SpellRow[]; color: string }) {
  const cols: Col<SpellRow>[] = [
    { id: 'spell', header: 'Spell', value: (r) => r.spell },
    { id: 'total', header: 'Total', right: true, value: (r) => r.total, render: (r) => fmtCompact(r.total) },
    { id: 'count', header: 'Count', right: true, value: (r) => r.count },
    { id: 'avg', header: 'Avg', right: true, value: (r) => r.avg, render: (r) => fmt1(r.avg) },
    { id: 'pct', header: '%', right: true, value: (r) => r.pct, render: (r) => `${r.pct.toFixed(1)}%` },
  ];
  return (
    <div>
      <div className="sub">{title}</div>
      {spells.length === 0 ? (
        <div className="dim small">No events.</div>
      ) : (
        <>
          <DataTable columns={cols} rows={spells} maxHeight={220} />
          <Chart
            height={Math.max(100, 24 * Math.min(spells.length, 12))}
            option={{
              grid: { left: 150, right: 40, top: 5, bottom: 5 },
              tooltip: { ...tooltipBase, trigger: 'item' },
              xAxis: { type: 'value', ...baseAxis, axisLabel: { color: '#bbb', formatter: (v: number) => fmtCompact(v) } },
              yAxis: { type: 'category', data: spells.slice(0, 12).map((s) => s.spell).reverse(), ...baseAxis },
              series: [{ type: 'bar', data: spells.slice(0, 12).map((s) => Math.round(s.total)).reverse(), itemStyle: { color } }],
            }}
          />
        </>
      )}
    </div>
  );
}

export function TotalsView({ character }: { character: string }) {
  const live = useLive();
  const [totals, setTotals] = useState<TargetTotal[]>([]);
  const [meta, setMeta] = useState<Record<string, unknown> | null>(null);
  const [abilities, setAbilities] = useState<{ damage: SpellRow[]; healing: SpellRow[] }>({ damage: [], healing: [] });
  const [charCounts, setCharCounts] = useState<Record<string, number>>({});
  const [sortBy, setSortBy] = useState('encounters');
  const [error, setError] = useState('');

  const charParam = character && character !== 'All' ? character : undefined;

  useEffect(() => {
    let on = true;
    Promise.all([
      api.totals(charParam),
      api.allEncounterAbilities(charParam),
      api.characters(),
    ])
      .then(([t, ab, ch]) => {
        if (!on) return;
        setTotals(t.totals);
        setMeta(t.meta);
        setAbilities(ab);
        setCharCounts(ch.counts);
      })
      .catch((e) => on && setError(String(e)));
    return () => {
      on = false;
    };
  }, [charParam, live.dataVersion]);

  const sortedTargets = useMemo(
    () =>
      [...totals].sort((a, b) => (b[sortBy as keyof TargetTotal] as number) - (a[sortBy as keyof TargetTotal] as number)),
    [totals, sortBy],
  );

  const charTable = useMemo(
    () =>
      Object.entries(charCounts)
        .map(([name, n]) => ({ name, combats: n }))
        .sort((a, b) => b.combats - a.combats)
        .slice(0, 50),
    [charCounts],
  );

  return (
    <div className="pad">
      <h2>Totals Summary</h2>
      {error && <div className="error">{error}</div>}
      {meta && (
        <p className="dim small">
          {String(meta.total_combats)} combats ·{' '}
          {fmtDuration(Number(meta.total_duration_s))} total ·{' '}
          {fmtInt(Number(meta.unique_targets))} unique targets
          {character !== 'All' ? ` · character: ${character}` : ''}
        </p>
      )}

      {character === 'All' && charTable.length > 0 && (
        <>
          <h3>Characters (by combat count)</h3>
          <DataTable
            columns={[
              { id: 'name', header: 'Character', value: (r: { name: string; combats: number }) => r.name },
              { id: 'combats', header: 'Combats', right: true, value: (r: { name: string; combats: number }) => r.combats },
            ]}
            rows={charTable}
            maxHeight={300}
          />
        </>
      )}

      <h3>Targets</h3>
      <div className="row gap">
        <label className="small dim">Sort by</label>
        <select className="input" value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
          {SORTS.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>
      </div>
      <div className="pad-top">
        <DataTable columns={targetCols} rows={sortedTargets} maxHeight={420} />
      </div>

      <h3>Abilities</h3>
      <div className="two-col even">
        <AbilityPanel title="Damage abilities" spells={abilities.damage} color="#e05555" />
        <AbilityPanel title="Healing abilities" spells={abilities.healing} color="#4CAF50" />
      </div>
    </div>
  );
}
