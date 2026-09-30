import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { Chart, baseAxis, tooltipBase } from '../components/Chart';
import { DataTable, type Col } from '../components/DataTable';
import type { BossRow } from '../types';
import { fmt1, fmtCompact, fmtInt } from '../lib/format';

const cols: Col<BossRow>[] = [
  { id: 'name', header: 'Boss', value: (r) => r.name, render: (r) => r.name.split('-')[0] },
  { id: 'zone_name', header: 'Zone', value: (r) => r.zone_name },
  { id: 'n_encounters', header: 'Encounters', right: true, value: (r) => r.n_encounters },
  { id: 'best_dps', header: 'Best DPS', right: true, value: (r) => r.best_dps, render: (r) => fmt1(r.best_dps) },
  { id: 'avg_dps', header: 'Avg DPS', right: true, value: (r) => r.avg_dps, render: (r) => fmt1(r.avg_dps) },
  { id: 'worst_dps', header: 'Worst DPS', right: true, value: (r) => r.worst_dps, render: (r) => fmt1(r.worst_dps) },
  { id: 'total_damage', header: 'Total damage', right: true, value: (r) => r.total_damage, render: (r) => fmtCompact(r.total_damage) },
];

export function BossComparisonView() {
  const [bosses, setBosses] = useState<BossRow[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    let on = true;
    api.comparisonBosses()
      .then((r) => on && setBosses(r.bosses))
      .catch((e) => on && setError(String(e)));
    return () => {
      on = false;
    };
  }, []);

  const sorted = [...bosses].sort((a, b) => b.avg_dps - a.avg_dps);
  const option = {
    grid: { left: 180, right: 60, top: 10, bottom: 25 },
    tooltip: tooltipBase,
    xAxis: { type: 'value', name: 'Avg DPS', ...baseAxis },
    yAxis: {
      type: 'category',
      data: sorted.map((b) => b.name.split('-')[0]).reverse(),
      ...baseAxis,
    },
    series: [
      {
        type: 'bar',
        data: sorted.map((b) => b.avg_dps).reverse(),
        itemStyle: { color: '#ba68c8' },
        label: { show: true, position: 'right', color: '#bbb', formatter: (p: { value: number }) => fmt1(p.value) },
      },
    ],
  };

  return (
    <div className="pad">
      <h2>Boss Comparison</h2>
      {error && <div className="error">{error}</div>}
      {bosses.length === 0 ? (
        <p className="dim small">No boss encounters found.</p>
      ) : (
        <>
          <p className="dim small">
            {fmtInt(bosses.length)} bosses (top 20 by total damage, all
            encounters).
          </p>
          <DataTable
            columns={cols}
            rows={bosses}
            initialSort={{ id: 'total_damage' }}
            maxHeight={420}
          />
          <h3>Average DPS by boss</h3>
          <Chart option={option} height={Math.max(160, 28 * bosses.length)} />
        </>
      )}
    </div>
  );
}
