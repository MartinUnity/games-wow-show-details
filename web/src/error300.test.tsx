// Regression test for Minified React error #300
// ("Rendered fewer hooks than expected").
//
// Root cause: `DataTable` called `useReactTable` inside a `useMemo`. When a
// second combat was clicked, React re-rendered the previous `DataTable`
// before the new data arrived; its `useMemo` (unchanged `rows`/`columns`/
// `sorting`) skipped its callback, so `useReactTable` was called fewer times
// than on the previous render and the whole tree unmounted (black screen).
//
// This test mocks the API and replays the exact click sequence: open combat
// #894 (DataTable mounts), then click combat #893 (the old bug crashed here).
// It is hermetic — no live server or canvas required (Chart is mocked).

import { expect, it, vi } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import App from './App';
import type {
  CombatEvent,
  CombatSummary,
  EncounterRow,
  TimelinePoint,
} from './types';

vi.mock('./components/Chart', () => ({
  Chart: () => null,
  baseAxis: {},
  tooltipBase: {},
}));

const enc = (id: number): EncounterRow => ({
  combat_id: id,
  start_dt: '2026-01-01T00:00:00',
  end_dt: '2026-01-01T00:10:00',
  duration_s: 600,
  total_damage: 1_000_000,
  total_heal: 100_000,
  dps: 1666.7,
  hps: 166.7,
  zone_name: 'Test Dungeon',
  note: null,
  hidden: false,
});

const summary = (id: number): CombatSummary => ({
  combat_id: id,
  target_name: `Boss ${id}`,
  note: null,
  bounds: {
    start_dt: '2026-01-01T00:00:00',
    end_dt: '2026-01-01T00:10:00',
    duration_s: 600,
    total_damage: 1_000_000,
    total_heal: 100_000,
    dps: 1666.7,
    hps: 166.7,
  },
  ttk_s: 30,
  overkill_pct: 5.2,
  total_damage_raw: 1_050_000,
  total_absorb: 12_000,
  targets: [{ target: `Boss ${id}`, total: 1_000_000, count: 500, avg: 2000 }],
  top_damage_spells: [
    { spell: 'Fireball', total: 500_000, count: 200, avg: 2500, pct: 50 },
  ],
  top_heal_spells: [
    { spell: 'Heal', total: 100_000, count: 100, avg: 1000, pct: 100 },
  ],
});

const timeline = (id: number): { points: TimelinePoint[] } => ({
  points: [
    { t: 0, dps: 100 + id, hps: 10 },
    { t: 1, dps: 110 + id, hps: 12 },
    { t: 2, dps: 120 + id, hps: 14 },
  ],
});

const events = (id: number): { combat_id: number; events: CombatEvent[] } => ({
  combat_id: id,
  events: [
    {
      timestamp: '01/01/2026 00:00:00.100',
      event: 'DAMAGE',
      source: 'PlayerA',
      target: `Boss ${id}`,
      spell_name: 'Fireball',
      amount: 1000,
      effective_amount: 1000,
      type: 'damage',
    },
    {
      timestamp: '01/01/2026 00:00:01.100',
      event: 'DAMAGE',
      source: 'PlayerA',
      target: `Boss ${id}`,
      spell_name: 'Fireball',
      amount: 1200,
      effective_amount: 1200,
      type: 'damage',
    },
  ],
});

it('clicks two distinct combats without a render crash (React error #300)', async () => {
  const route = (url: string): string | null => {
    const p = new URL(url, 'http://x').pathname;
    if (p === '/api/health')
      return JSON.stringify({
        sqlite_active: true,
        csv_exists: true,
        csv_mtime: Date.now() / 1000,
        encounter_count: 2,
      });
    if (p === '/api/characters')
      return JSON.stringify({ characters: ['PlayerA'], counts: { PlayerA: 2 } });
    if (p === '/api/encounters')
      return JSON.stringify({
        meta: {
          n_encounters: 2,
          total_duration_s: 1200,
          total_damage: 2_000_000,
          total_heal: 200_000,
          avg_dps: 1666,
          avg_hps: 166,
        },
        encounters: [enc(894), enc(893)],
      });
    const m = p.match(/^\/api\/combat\/(\d+)\/summary$/);
    if (m) return JSON.stringify(summary(Number(m[1])));
    const t = p.match(/^\/api\/combat\/(\d+)\/timeline$/);
    if (t) return JSON.stringify(timeline(Number(t[1])));
    const e = p.match(/^\/api\/combat\/(\d+)\/events$/);
    if (e) return JSON.stringify(events(Number(e[1])));
    return null;
  };

  vi.stubGlobal(
    'fetch',
    ((input: RequestInfo | URL, _init?: RequestInit) => {
      const body = route(String(input));
      return Promise.resolve(
        new Response(body ?? 'not found', {
          status: body ? 200 : 404,
          headers: { 'Content-Type': 'application/json' },
        }),
      );
    }) as typeof fetch,
  );

  const { container, unmount } = render(<App />);

  const findButton = (id: number): HTMLButtonElement => {
    const btn = Array.from(
      container.querySelectorAll<HTMLButtonElement>('.combat-item'),
    ).find((b) => b.textContent?.includes(`#${id}`));
    if (!btn) throw new Error(`no combat button for #${id}`);
    return btn;
  };

  const waitSummary = async (id: number) => {
    await waitFor(() => {
      const h = container.querySelector('h2');
      if (!h?.textContent?.includes(String(id)))
        throw new Error(`summary #${id} not shown`);
    }, { timeout: 5000 });
  };

  // Encounter list must be loaded first.
  await waitFor(() => {
    if (container.querySelectorAll('.combat-item').length < 2)
      throw new Error('encounter list not loaded');
  }, { timeout: 5000 });

  // First combat: mounts the DataTable.
  findButton(894).click();
  await waitSummary(894);

  // Second combat: before its data arrives, the previous DataTable
  // re-renders with unchanged rows/columns — with the old
  // useMemo-wrapped `useReactTable` this unmounted the entire tree.
  findButton(893).click();
  await waitSummary(893);

  expect(container.querySelector('h2')?.textContent).toContain('893');
  unmount();
}, 30_000);
