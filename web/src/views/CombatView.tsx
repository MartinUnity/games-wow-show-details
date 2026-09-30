import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import { Chart, baseAxis, tooltipBase } from '../components/Chart';
import { DataTable, type Col } from '../components/DataTable';
import { useLive } from '../live/LiveContext';
import type {
  CombatEvent,
  CombatSummary,
  EncounterRow,
  SpellRow,
  TimelinePoint,
} from '../types';
import {
  fmt1,
  fmtCompact,
  fmtDuration,
  fmtInt,
  fmtWhen,
  parseCsvTs,
} from '../lib/format';

const eventCols: Col<CombatEvent>[] = [
  { id: 'timestamp', header: 'Time', value: (r) => r.timestamp },
  { id: 'event', header: 'Event', value: (r) => r.event },
  { id: 'source', header: 'Source', value: (r) => r.source },
  { id: 'target', header: 'Target', value: (r) => r.target },
  { id: 'spell_name', header: 'Spell', value: (r) => r.spell_name },
  {
    id: 'amount',
    header: 'Amount',
    right: true,
    value: (r) => r.amount,
    render: (r) => fmtCompact(r.amount),
  },
  {
    id: 'effective_amount',
    header: 'Effective',
    right: true,
    value: (r) => r.effective_amount,
    render: (r) => fmtCompact(r.effective_amount),
  },
  { id: 'type', header: 'Type', value: (r) => r.type },
];

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
    </div>
  );
}

function SpellTable({ spells }: { spells: SpellRow[] }) {
  const cols: Col<SpellRow>[] = [
    { id: 'spell', header: 'Spell', value: (r) => r.spell },
    {
      id: 'total',
      header: 'Total',
      right: true,
      value: (r) => r.total,
      render: (r) => fmtCompact(r.total),
    },
    { id: 'count', header: 'Count', right: true, value: (r) => r.count },
    {
      id: 'avg',
      header: 'Avg',
      right: true,
      value: (r) => r.avg,
      render: (r) => fmt1(r.avg),
    },
    {
      id: 'pct',
      header: '%',
      right: true,
      value: (r) => r.pct,
      render: (r) => `${r.pct.toFixed(1)}%`,
    },
  ];
  return <DataTable columns={cols} rows={spells} maxHeight={260} />;
}

function SpellBars({ spells, color }: { spells: SpellRow[]; color: string }) {
  const top = spells.slice(0, 15).reverse();
  return (
    <Chart
      height={Math.max(120, 26 * top.length)}
      option={{
        grid: { left: 140, right: 30, top: 10, bottom: 10 },
        tooltip: { ...tooltipBase, trigger: 'item' },
        xAxis: { type: 'value', ...baseAxis, axisLabel: { color: '#bbb', formatter: (v: number) => fmtCompact(v) } },
        yAxis: { type: 'category', data: top.map((s) => s.spell), ...baseAxis },
        series: [
          {
            type: 'bar',
            data: top.map((s) => Math.round(s.total)),
            itemStyle: { color },
            label: { show: true, position: 'right', color: '#bbb', formatter: (p: { value: number }) => fmtCompact(p.value) },
          },
        ],
      }}
    />
  );
}

export function CombatView({
  character,
  resampleS,
  smoothS,
  followLive,
  combat,
  setCombat,
}: {
  character: string;
  resampleS: number;
  smoothS: number;
  followLive: boolean;
  combat: string | undefined;
  setCombat: (id: string | null) => void;
}) {
  const live = useLive();
  const [filter, setFilter] = useState('');
  const [showHidden, setShowHidden] = useState(false);

  const combatId = combat ? Number(combat) : NaN;
  const [summary, setSummary] = useState<CombatSummary | null>(null);
  const [timeline, setTimeline] = useState<TimelinePoint[]>([]);
  const [events, setEvents] = useState<CombatEvent[]>([]);
  const [spellFilter, setSpellFilter] = useState('');
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const [list, setList] = useState<EncounterRow[]>([]);
  const current = list.find((e) => e.combat_id === combatId);
  const isHidden = current?.hidden ?? false;

  const charParam = character && character !== 'All' ? character : undefined;

  useEffect(() => {
    let on = true;
    api.encounters(charParam, showHidden ? 'include' : 'exclude')
      .then((r) => on && setList(r.encounters))
      .catch((e) => on && setError(String(e)));
    return () => {
      on = false;
    };
  }, [charParam, showHidden, live.dataVersion]);

  // Live follow: auto-select the newest closed encounter (Phase 4.4).
  useEffect(() => {
    if (!followLive || !live.lastEncounter) return;
    if (!combatId || live.lastEncounter.combat_id > combatId) {
      setCombat(String(live.lastEncounter.combat_id));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live.lastEncounter, followLive]);

  useEffect(() => {
    setSpellFilter('');
    if (!Number.isFinite(combatId) || combatId <= 0) {
      setSummary(null);
      setTimeline([]);
      setEvents([]);
      return;
    }
    let on = true;
    setError('');
    Promise.all([
      api.combatSummary(combatId),
      api.combatTimeline(combatId, { resampleS, smoothS }),
      api.combatEvents(combatId, 5000),
    ])
      .then(([s, t, ev]) => {
        if (!on) return;
        setSummary(s);
        setTimeline(t.points);
        setEvents(ev.events);
        setNote(s.note ?? '');
      })
      .catch((e) => on && setError(String(e)));
    return () => {
      on = false;
    };
    // dataVersion re-fetches the timeline so a closing encounter grows.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [combatId, resampleS, smoothS, live.dataVersion]);

  // Spell-filtered series (only when a filter is active).
  useEffect(() => {
    if (!spellFilter || !Number.isFinite(combatId)) return;
    let on = true;
    api.combatTimeline(combatId, { resampleS, smoothS, spell: spellFilter })
      .then((t) => on && setTimeline(t.points))
      .catch((e) => on && setError(String(e)));
    return () => {
      on = false;
    };
  }, [spellFilter, combatId, resampleS, smoothS]);

  const combatOptions = useMemo(() => {
    const f = filter.trim().toLowerCase();
    if (!f) return list;
    return list.filter(
      (e) =>
        String(e.combat_id).includes(f) ||
        e.zone_name.toLowerCase().includes(f) ||
        (e.note ?? '').toLowerCase().includes(f),
    );
  }, [list, filter]);

  const spellOptions = useMemo(() => {
    if (!summary) return [''];
    return [
      '',
      ...summary.top_damage_spells.map((s) => `${s.spell} [Damage]`),
      ...summary.top_heal_spells.map((s) => `${s.spell} [Healing]`),
    ];
  }, [summary]);

  // ── Derived chart data ────────────────────────────────────────────────
  const timelineOption = useMemo(() => {
    if (!timeline.length) return null;
    const t0 = timeline[0].t;
    const x = timeline.map((p) => Math.round((p.t - t0) * 10) / 10);
    const series: Record<string, unknown>[] = [
      { name: 'DPS', type: 'line', showSymbol: false, data: x.map((_, i) => Math.round(timeline[i].dps * 10) / 10), itemStyle: { color: '#e05555' } },
      { name: 'HPS', type: 'line', showSymbol: false, data: x.map((_, i) => Math.round(timeline[i].hps * 10) / 10), itemStyle: { color: '#4CAF50' } },
    ];
    if (timeline.some((p) => p.selected_dps !== undefined)) {
      series.push({ name: 'Selected DPS', type: 'line', showSymbol: false, data: x.map((_, i) => Math.round((timeline[i].selected_dps ?? 0) * 10) / 10), itemStyle: { color: '#ffb74d' } });
      series.push({ name: 'Selected HPS', type: 'line', showSymbol: false, data: x.map((_, i) => Math.round((timeline[i].selected_hps ?? 0) * 10) / 10), itemStyle: { color: '#42A5F5' } });
    }
    return {
      grid: { left: 55, right: 20, top: 30, bottom: 25 },
      legend: { top: 0, textStyle: { color: '#bbb' } },
      tooltip: tooltipBase,
      xAxis: { type: 'category', data: x, name: 's', ...baseAxis },
      yAxis: { type: 'value', ...baseAxis },
      series,
    };
  }, [timeline, spellFilter]);

  const { rotationOption, uptimeOption, recentEvents } = useMemo(() => {
    const rot: CombatEvent[] = events.filter(
      (e) => e.spell_name && ['damage', 'heal', 'absorb'].includes(e.type),
    );
    let rotOpt: Record<string, unknown> | null = null;
    let upOpt: Record<string, unknown> | null = null;
    if (rot.length) {
      const counts = new Map<string, number>();
      const firstSeen = new Map<string, number>();
      const t0 = parseCsvTs(rot[0].timestamp);
      for (const e of rot) {
        counts.set(e.spell_name, (counts.get(e.spell_name) ?? 0) + 1);
        if (!firstSeen.has(e.spell_name)) {
          firstSeen.set(e.spell_name, parseCsvTs(e.timestamp) - t0);
        }
      }
      const top = [...counts.entries()]
        .sort((a, b) => b[1] - a[1])
        .slice(0, 20)
        .map(([s]) => s);
      // Sort lanes by first appearance (Streamlit parity).
      top.sort((a, b) => (firstSeen.get(a) ?? 0) - (firstSeen.get(b) ?? 0));
      const pts = rot
        .filter((e) => top.includes(e.spell_name))
        .map((e) => ({
          value: [
            Math.round(((parseCsvTs(e.timestamp) - t0) * 100) / 100),
            e.spell_name,
          ],
          size: e.effective_amount,
          raw: e,
        }));
      rotOpt = {
        grid: { left: 150, right: 30, top: 10, bottom: 25 },
        tooltip: {
          trigger: 'item',
          formatter: (p: { value: [number, string] }) =>
            `${p.value[1]} @ ${p.value[0]}s`,
        },
        xAxis: { type: 'value', name: 'Elapsed (s)', ...baseAxis },
        yAxis: { type: 'category', data: top, ...baseAxis, inverse: true },
        series: [
          {
            type: 'scatter',
            data: pts,
            symbolSize: (v: { value: [number, string]; size: number }) =>
              Math.max(4, Math.min(22, Math.sqrt(v.size) / 4)),
            itemStyle: { color: '#7CFC00', opacity: 0.6 },
          },
        ],
      };
      // Uptime: fraction of distinct seconds each spell had activity.
      const totalSecs = Math.max(1, Math.ceil((parseCsvTs(rot[rot.length - 1].timestamp) - t0) / 1000));
      const secs = new Map<string, Set<number>>();
      for (const e of rot) {
        if (!top.includes(e.spell_name)) continue;
        const s = Math.floor((parseCsvTs(e.timestamp) - t0) / 1000);
        if (!secs.has(e.spell_name)) secs.set(e.spell_name, new Set());
        secs.get(e.spell_name)!.add(s);
      }
      const up = top.map((s) => ({
        spell: s,
        pct: ((secs.get(s)?.size ?? 0) / totalSecs) * 100,
      }));
      upOpt = {
        grid: { left: 150, right: 50, top: 10, bottom: 25 },
        tooltip: { ...tooltipBase, trigger: 'item' },
        xAxis: { type: 'value', max: 100, name: 'Uptime %', ...baseAxis },
        yAxis: { type: 'category', data: up.map((u) => u.spell), ...baseAxis, inverse: true },
        series: [
          {
            type: 'bar',
            data: up.map((u) => Math.round(u.pct * 10) / 10),
            itemStyle: { color: '#7CFC00' },
            label: { show: true, position: 'right', color: '#bbb', formatter: (p: { value: number }) => `${p.value.toFixed(1)}%` },
          },
        ],
      };
    }
    return { rotationOption: rotOpt, uptimeOption: upOpt, recentEvents: rot.slice(-200) };
  }, [events]);

  const saveNote = async () => {
    if (!Number.isFinite(combatId)) return;
    try {
      await api.saveNote(combatId, note);
    } catch (e) {
      setError(String(e));
    }
  };

  const toggleHide = async () => {
    if (!Number.isFinite(combatId) || !summary) return;
    try {
      await api.setHidden(combatId, !isHidden);
      // refresh list + state
      api.encounters(charParam, showHidden ? 'include' : 'exclude')
        .then((r) => setList(r.encounters))
        .catch(() => undefined);
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="two-col">
      <div className="col-side">
        <div className="row gap">
          <input
            className="input grow"
            placeholder="Filter combats…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          <label className="check">
            <input
              type="checkbox"
              checked={showHidden}
              onChange={(e) => setShowHidden(e.target.checked)}
            />
            hidden
          </label>
        </div>
        <div className="grow scroll">
          {combatOptions.map((e) => (
            <button
              key={e.combat_id}
              className={`combat-item ${e.combat_id === combatId ? 'active' : ''} ${e.hidden ? 'hidden-mark' : ''}`}
              onClick={() => setCombat(String(e.combat_id))}
            >
              <div className="combat-item-top">
                <span>
                  #{e.combat_id}
                  {e.hidden ? ' 🙈' : ''}
                </span>
                <span className="dim">{fmtWhen(e.start_dt)}</span>
              </div>
              <div className="dim small">
                {e.zone_name || '—'} · {fmtDuration(e.duration_s)} ·{' '}
                {fmt1(e.dps)} dps
              </div>
            </button>
          ))}
          {combatOptions.length === 0 && <div className="dim pad">No combats</div>}
        </div>
      </div>

      <div className="col-main scroll">
        {error && <div className="error">{error}</div>}
        {!Number.isFinite(combatId) && (
          <div className="dim pad center">Select an encounter →</div>
        )}
        {Number.isFinite(combatId) && !summary && (
          <div className="dim pad center">Loading…</div>
        )}
        {summary && (
          <>
            <div className="row gap wrap">
              <h2>
                Combat {summary.combat_id}
                {summary.target_name ? ` — ${summary.target_name}` : ''}
              </h2>
              <button className="btn" onClick={toggleHide}>
                {isHidden ? '🔴 Unhide' : '🙈 Hide'}
              </button>
              <a className="btn" href={api.exportCsvUrl(summary.combat_id)} target="_blank" rel="noreferrer">
                ⬇ CSV
              </a>
              <a className="btn" href={api.exportGifUrl(summary.combat_id)} target="_blank" rel="noreferrer">
                ⬇ GIF
              </a>
            </div>

            <div className="row gap wrap">
              <label className="small dim">Note</label>
              <input
                className="input grow"
                value={note}
                placeholder="e.g. elite pack near the cave"
                onChange={(e) => setNote(e.target.value)}
              />
              <button className="btn" onClick={saveNote}>
                Save
              </button>
            </div>

            <div className="metrics">
              <Metric label="DPS" value={fmt1(summary.bounds.dps)} />
              <Metric label="HPS" value={fmt1(summary.bounds.hps)} />
              <Metric label="Duration" value={`${fmt1(summary.bounds.duration_s)}s`} />
              <Metric label="Time to 1st kill" value={summary.ttk_s != null ? `${summary.ttk_s}s` : '—'} />
              <Metric label="Overkill" value={summary.overkill_pct != null ? `${summary.overkill_pct}%` : '—'} />
            </div>
            <div className="dim small pad-top">
              Total damage: {fmtInt(summary.bounds.total_damage)} · Total
              healing:{' '}
              {summary.total_absorb > 0
                ? `${fmtInt(summary.bounds.total_heal - summary.total_absorb)} healed + ${fmtInt(summary.total_absorb)} absorbed`
                : fmtInt(summary.bounds.total_heal)}
            </div>

            {summary.targets.length > 1 && (
              <>
                <h3>Damage by target</h3>
                <Chart
                  height={Math.max(90, 26 * Math.min(summary.targets.length, 10))}
                  option={{
                    grid: { left: 200, right: 40, top: 5, bottom: 5 },
                    tooltip: { ...tooltipBase, trigger: 'item' },
                    xAxis: { type: 'value', ...baseAxis, axisLabel: { color: '#bbb', formatter: (v: number) => fmtCompact(v) } },
                    yAxis: {
                      type: 'category',
                      data: summary.targets.slice(0, 10).map((t) => t.target.split('-')[0]).reverse(),
                      ...baseAxis,
                    },
                    series: [
                      {
                        type: 'bar',
                        data: summary.targets.slice(0, 10).map((t) => Math.round(t.total)).reverse(),
                        itemStyle: { color: '#e05555' },
                      },
                    ],
                  }}
                />
              </>
            )}

            {(summary.top_damage_spells.length > 0 ||
              summary.top_heal_spells.length > 0) && (
              <>
                <h3>By ability</h3>
                <div className="two-col even">
                  <div>
                    <div className="sub">Damage by ability</div>
                    {summary.top_damage_spells.length > 0 ? (
                      <>
                        <SpellTable spells={summary.top_damage_spells} />
                        <SpellBars spells={summary.top_damage_spells} color="#e05555" />
                      </>
                    ) : (
                      <div className="dim small">No damage events</div>
                    )}
                  </div>
                  <div>
                    <div className="sub">Healing by ability</div>
                    {summary.top_heal_spells.length > 0 ? (
                      <>
                        <SpellTable spells={summary.top_heal_spells} />
                        <SpellBars spells={summary.top_heal_spells} color="#4CAF50" />
                      </>
                    ) : (
                      <div className="dim small">No healing events</div>
                    )}
                  </div>
                </div>
              </>
            )}

            {timelineOption && (
              <>
                <h3>DPS / HPS (per second)</h3>
                <Chart option={timelineOption} height={300} />
                <div className="row gap">
                  <select
                    className="input"
                    value={spellFilter}
                    onChange={(e) => setSpellFilter(e.target.value)}
                  >
                    {spellOptions.map((s) => (
                      <option key={s} value={s}>
                        {s === '' ? 'Filter by spell…' : s}
                      </option>
                    ))}
                  </select>
                  {spellFilter && (
                    <button className="btn" onClick={() => setSpellFilter('')}>
                      Clear filter
                    </button>
                  )}
                </div>
              </>
            )}

            {rotationOption && (
              <>
                <h3>Rotation timeline</h3>
                <Chart option={rotationOption} height={Math.max(200, 26 * 20)} />
              </>
            )}
            {uptimeOption && (
              <>
                <h3>Ability uptime</h3>
                <Chart option={uptimeOption} height={Math.max(120, 26 * 20)} />
              </>
            )}

            <h3>Recent events</h3>
            <DataTable
              columns={eventCols}
              rows={recentEvents}
              maxHeight={360}
              initialSort={{ id: 'timestamp' }}
            />
          </>
        )}
      </div>
    </div>
  );
}
