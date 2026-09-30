import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import { DataTable, type Col } from '../components/DataTable';
import type { RunRow } from '../types';
import { fmt1, fmtCompact, fmtDuration, fmtWhen } from '../lib/format';

const runCols: Col<RunRow>[] = [
  { id: 'run_id', header: 'Run', value: (r) => r.run_id, render: (r) => `#${r.run_id}` },
  { id: 'zone_name', header: 'Zone', value: (r) => r.zone_name },
  {
    id: 'start_dt',
    header: 'Date',
    value: (r) => r.start_dt,
    render: (r) => fmtWhen(r.start_dt),
  },
  { id: 'n_encounters', header: 'Encounters', right: true, value: (r) => r.n_encounters },
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
  {
    id: 'dps',
    header: 'Avg DPS',
    right: true,
    value: (r) => (r.duration_s > 0 ? r.total_damage / r.duration_s : 0),
    render: (r) =>
      r.duration_s > 0 ? fmt1(r.total_damage / r.duration_s) : '—',
  },
];

export function RunsView({
  openCombat,
}: {
  openCombat: (combatId: number) => void;
}) {
  const [gap, setGap] = useState(20);
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [encounters, setEncounters] = useState<RunRow[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [bossesOnly, setBossesOnly] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let on = true;
    api.runs(gap)
      .then((r) => {
        if (!on) return;
        setRuns(r.runs);
        setEncounters(r.encounters);
      })
      .catch((e) => on && setError(String(e)));
    return () => {
      on = false;
    };
  }, [gap]);

  const runEncounters = useMemo(() => {
    if (selected === null) return [];
    let rows = encounters.filter((e) => e.run_id === selected);
    if (bossesOnly) rows = rows.filter((e) => nonEmpty(e.main_target));
    return rows;
  }, [encounters, selected, bossesOnly]);

  const selRun = runs.find((r) => r.run_id === selected);

  return (
    <div className="pad">
      <div className="row gap wrap">
        <h2>Runs</h2>
        <label className="small dim">Gap (min)</label>
        <select className="input" value={gap} onChange={(e) => setGap(Number(e.target.value))}>
          {[10, 20, 30, 60].map((g) => (
            <option key={g} value={g}>
              {g}
            </option>
          ))}
        </select>
      </div>
      {error && <div className="error">{error}</div>}
      <p className="dim small">
        A run is a sequence of encounters in the same zone with gaps under the
        threshold.
      </p>

      <DataTable
        columns={runCols}
        rows={runs}
        onRowClick={(r) => setSelected(r.run_id)}
        initialSort={{ id: 'start_dt', desc: true }}
        maxHeight={360}
      />

      {selRun && (
        <>
          <h3>
            Run #{selRun.run_id} — {selRun.zone_name} · {fmtWhen(selRun.start_dt)}
          </h3>
          <label className="check">
            <input
              type="checkbox"
              checked={bossesOnly}
              onChange={(e) => setBossesOnly(e.target.checked)}
            />
            Bosses only
          </label>
          <div className="pad-top">
            <DataTable
              columns={[
                { id: 'combat_id', header: 'Combat', value: (r) => r.combat_id as number, render: (r) => `#${r.combat_id}` },
                { id: 'start_dt', header: 'Date', value: (r) => r.start_dt as string, render: (r) => fmtWhen(r.start_dt as string) },
                {
                  id: 'duration_s',
                  header: 'Duration',
                  right: true,
                  value: (r) => r.duration_s as number,
                  render: (r) => fmtDuration(r.duration_s as number),
                },
                {
                  id: 'dps',
                  header: 'DPS',
                  right: true,
                  value: (r) =>
                    (r.duration_s as number) > 0
                      ? (r.total_damage as number) / (r.duration_s as number)
                      : 0,
                  render: (r) =>
                    (r.duration_s as number) > 0
                      ? fmt1((r.total_damage as number) / (r.duration_s as number))
                      : '—',
                },
                { id: 'main_target', header: 'Target', value: (r) => (r.main_target as string) ?? '' },
              ]}
              rows={runEncounters}
              onRowClick={(r) => openCombat(r.combat_id as number)}
              maxHeight={360}
            />
          </div>
        </>
      )}
      {selRun && runEncounters.length === 0 && (
        <div className="dim small pad-top">
          No encounters {bossesOnly ? 'with a boss target ' : ''}in this run.
        </div>
      )}
    </div>
  );
}

function nonEmpty(v: unknown): boolean {
  return typeof v === 'string' && v.trim() !== '';
}
