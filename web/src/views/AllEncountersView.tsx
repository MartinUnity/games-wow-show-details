import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import { Chart, baseAxis, tooltipBase } from '../components/Chart';
import { DataTable, type Col } from '../components/DataTable';
import { useLive } from '../live/LiveContext';
import type { EncounterMeta, EncounterRow, SpellRow, TargetTotal } from '../types';
import {
  fmt1,
  fmtCompact,
  fmtDuration,
  fmtInt,
  fmtWhen,
  hourOfDay,
} from '../lib/format';

const encCols: Col<EncounterRow>[] = [
  {
    id: 'combat_id',
    header: 'Combat',
    value: (r) => r.combat_id,
    render: (r) => `#${r.combat_id}${r.hidden ? ' 🙈' : ''}`,
  },
  {
    id: 'start_dt',
    header: 'Date',
    value: (r) => r.start_dt,
    render: (r) => fmtWhen(r.start_dt),
  },
  { id: 'zone_name', header: 'Zone', value: (r) => r.zone_name },
  {
    id: 'duration_s',
    header: 'Duration',
    right: true,
    value: (r) => r.duration_s,
    render: (r) => fmtDuration(r.duration_s),
  },
  {
    id: 'total_damage',
    header: 'Damage',
    right: true,
    value: (r) => r.total_damage,
    render: (r) => fmtCompact(r.total_damage),
  },
  { id: 'dps', header: 'DPS', right: true, value: (r) => r.dps, render: (r) => fmt1(r.dps) },
  { id: 'hps', header: 'HPS', right: true, value: (r) => r.hps, render: (r) => fmt1(r.hps) },
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

export function AllEncountersView({
  character,
  openCombat,
}: {
  character: string;
  openCombat: (combatId: number) => void;
}) {
  const live = useLive();
  const [meta, setMeta] = useState<EncounterMeta | null>(null);
  const [encounters, setEncounters] = useState<EncounterRow[]>([]);
  const [abilities, setAbilities] = useState<{ damage: SpellRow[]; healing: SpellRow[] }>({ damage: [], healing: [] });
  const [totals, setTotals] = useState<TargetTotal[]>([]);
  const [error, setError] = useState('');

  const charParam = character && character !== 'All' ? character : undefined;

  useEffect(() => {
    let on = true;
    Promise.all([
      api.encounters(charParam, 'include'),
      api.allEncounterAbilities(charParam),
      api.totals(charParam),
    ])
      .then(([enc, ab, tot]) => {
        if (!on) return;
        setMeta(enc.meta);
        setEncounters(enc.encounters);
        setAbilities(ab);
        setTotals(tot.totals);
      })
      .catch((e) => on && setError(String(e)));
    return () => {
      on = false;
    };
  }, [charParam, live.dataVersion]);

  const dpsHpsOption = useMemo(() => {
    const rows = [...encounters].sort((a, b) => a.start_dt.localeCompare(b.start_dt));
    return {
      grid: { left: 55, right: 20, top: 30, bottom: 25 },
      legend: { top: 0, textStyle: { color: '#bbb' } },
      tooltip: tooltipBase,
      xAxis: {
        type: 'category',
        data: rows.map((r) => `#${r.combat_id}`),
        ...baseAxis,
        axisLabel: { color: '#bbb', interval: Math.max(0, Math.floor(rows.length / 20) - 1) },
      },
      yAxis: { type: 'value', ...baseAxis },
      series: [
        { name: 'DPS', type: 'line', showSymbol: rows.length <= 80, data: rows.map((r) => Math.round(r.dps * 10) / 10), itemStyle: { color: '#e05555' } },
        { name: 'HPS', type: 'line', showSymbol: rows.length <= 80, data: rows.map((r) => Math.round(r.hps * 10) / 10), itemStyle: { color: '#4CAF50' } },
      ],
    };
  }, [encounters]);

  const hourOption = useMemo(() => {
    const bins = Array.from({ length: 24 }, () => 0);
    for (const r of encounters) {
      const h = hourOfDay(r.start_dt);
      if (h >= 0) bins[h] += 1;
    }
    return {
      grid: { left: 40, right: 20, top: 10, bottom: 25 },
      tooltip: tooltipBase,
      xAxis: { type: 'category', data: bins.map((_, i) => `${i}h`), ...baseAxis },
      yAxis: { type: 'value', minInterval: 1, ...baseAxis },
      series: [{ type: 'bar', data: bins, itemStyle: { color: '#42A5F5' } }],
    };
  }, [encounters]);

  const topTargets = useMemo(
    () => [...totals].sort((a, b) => b.total_damage - a.total_damage).slice(0, 10),
    [totals],
  );

  const mostCast = useMemo(
    () =>
      [...abilities.damage, ...abilities.healing]
        .sort((a, b) => b.count - a.count)
        .slice(0, 10),
    [abilities],
  );

  return (
    <div className="pad">
      <h2>All Encounters — Aggregated</h2>
      {error && <div className="error">{error}</div>}
      {meta && (
        <div className="metrics">
          <div className="metric"><div className="metric-label">Encounters</div><div className="metric-value">{meta.n_encounters}</div></div>
          <div className="metric"><div className="metric-label">Total time</div><div className="metric-value">{fmtDuration(meta.total_duration_s)}</div></div>
          <div className="metric"><div className="metric-label">Avg DPS</div><div className="metric-value">{fmt1(meta.avg_dps)}</div></div>
          <div className="metric"><div className="metric-label">Avg HPS</div><div className="metric-value">{fmt1(meta.avg_hps)}</div></div>
        </div>
      )}

      <h3>DPS &amp; HPS per encounter</h3>
      <Chart option={dpsHpsOption} height={280} />

      <h3>Session activity by hour of day</h3>
      <Chart option={hourOption} height={180} />

      {topTargets.length > 0 && (
        <>
          <h3>Top targets (by damage dealt)</h3>
          <div className="two-col even">
            <DataTable
              columns={[
                { id: 'target', header: 'Target', value: (r) => r.target, render: (r) => r.target.split('-')[0] },
                { id: 'encounters', header: 'Encounters', right: true, value: (r) => r.encounters },
                { id: 'total_damage', header: 'Damage', right: true, value: (r) => r.total_damage, render: (r) => fmtCompact(r.total_damage) },
                { id: 'dps', header: 'DPS', right: true, value: (r) => r.dps, render: (r) => fmt1(r.dps) },
              ]}
              rows={topTargets}
              maxHeight={300}
            />
            <Chart
              height={Math.max(100, 26 * topTargets.length)}
              option={{
                grid: { left: 170, right: 50, top: 5, bottom: 5 },
                tooltip: { ...tooltipBase, trigger: 'item' },
                xAxis: { type: 'value', ...baseAxis, axisLabel: { color: '#bbb', formatter: (v: number) => fmtCompact(v) } },
                yAxis: { type: 'category', data: topTargets.map((t) => t.target.split('-')[0]).reverse(), ...baseAxis },
                series: [{ type: 'bar', data: topTargets.map((t) => Math.round(t.total_damage)).reverse(), itemStyle: { color: '#e05555' } }],
              }}
            />
          </div>
        </>
      )}

      <h3>Ability breakdown (all encounters)</h3>
      <div className="two-col even">
        <AbilityPanel title="Damage by ability" spells={abilities.damage} color="#e05555" />
        <AbilityPanel title="Healing by ability" spells={abilities.healing} color="#4CAF50" />
      </div>

      {mostCast.length > 0 && (
        <>
          <h3>Most-cast abilities</h3>
          <DataTable
            columns={[
              { id: 'spell', header: 'Spell', value: (r) => r.spell },
              { id: 'count', header: 'Casts', right: true, value: (r) => r.count },
              { id: 'total', header: 'Total', right: true, value: (r) => r.total, render: (r) => fmtCompact(r.total) },
            ]}
            rows={mostCast}
            maxHeight={300}
            initialSort={{ id: 'count' }}
          />
        </>
      )}

      <h3>Encounters</h3>
      <p className="dim small">Click a row to open the combat viewer ({fmtInt(encounters.length)} rows).</p>
      <DataTable
        columns={encCols}
        rows={encounters}
        onRowClick={(r) => openCombat(r.combat_id)}
        initialSort={{ id: 'start_dt', desc: true }}
        maxHeight={420}
      />
    </div>
  );
}
