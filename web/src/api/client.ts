// Typed fetch client for the FastAPI backend (same-origin; the Vite dev
// server proxies /api → 127.0.0.1:8000, and the API itself serves the SPA).

import type {
  Abilities,
  BossRow,
  CharacterEntry,
  Characters,
  CombatEvent,
  CombatSummary,
  EncounterMeta,
  EncounterRow,
  Health,
  RunRow,
  TargetTotal,
  TimelinePoint,
} from '../types';

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${url}`);
  return (await res.json()) as T;
}

function sendJson<T>(url: string, method: string, body?: unknown): Promise<T> {
  return fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then((r) => {
    if (!r.ok) throw new Error(`${r.status} ${r.statusText} — ${url}`);
    return r.json() as Promise<T>;
  });
}

function qs(params: Record<string, string | number | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '') p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : '';
}

export interface TimelineParams {
  resampleS?: number;
  smoothS?: number;
  spell?: string;
  source?: string;
}

export const api = {
  health: () => getJson<Health>('/api/health'),

  characters: (includeOthers = false) =>
    getJson<Characters>(
      `/api/characters${includeOthers ? '?include_others=true' : ''}`,
    ),

  encounters: (
    character?: string,
    hidden: 'exclude' | 'include' = 'exclude',
  ) =>
    getJson<{ meta: EncounterMeta; encounters: EncounterRow[] }>(
      `/api/encounters${qs({ character, hidden })}`,
    ),

  combatSummary: (combatId: number, topN = 10) =>
    getJson<CombatSummary>(
      `/api/combat/${combatId}/summary${qs({ top_n: topN })}`,
    ),

  combatTimeline: (combatId: number, p: TimelineParams = {}) =>
    getJson<{ points: TimelinePoint[] }>(
      `/api/combat/${combatId}/timeline${qs({
        resample_s: p.resampleS,
        smooth_s: p.smoothS,
        spell: p.spell,
        source: p.source,
      })}`,
    ),

  combatEvents: (combatId: number, limit = 200) =>
    getJson<{ combat_id: number; events: CombatEvent[] }>(
      `/api/combat/${combatId}/events${qs({ limit })}`,
    ),

  runs: (gapMinutes = 20) =>
    getJson<{ runs: RunRow[]; encounters: RunRow[] }>(
      `/api/runs${qs({ gap_minutes: gapMinutes })}`,
    ),

  totals: (character?: string) =>
    getJson<{ totals: TargetTotal[]; meta: Record<string, unknown> }>(
      `/api/totals${qs({ character })}`,
    ),

  allEncounterAbilities: (character?: string, topN = 15) =>
    getJson<Abilities>(
      `/api/all-encounters/abilities${qs({ character, top_n: topN })}`,
    ),

  comparisonCharacters: () =>
    getJson<{ characters: CharacterEntry[] }>('/api/comparison/characters'),

  comparisonBosses: () =>
    getJson<{ bosses: BossRow[] }>('/api/comparison/bosses'),

  saveNote: (combatId: number, note: string) =>
    sendJson<{ combat_id: number; note: string | null }>(
      `/api/encounters/${combatId}/note`,
      'POST',
      { note },
    ),

  setHidden: (combatId: number, hidden: boolean) =>
    fetch(`/api/encounters/${combatId}/hide`, {
      method: hidden ? 'POST' : 'DELETE',
    }).then((r) => {
      if (!r.ok) throw new Error(`${r.status} — hide ${combatId}`);
      return r.json() as Promise<{ combat_id: number; hidden: boolean }>;
    }),

  exportCsvUrl: (combatId: number) => `/api/combat/${combatId}/export.csv`,
  exportGifUrl: (combatId: number) => `/api/combat/${combatId}/export.gif`,
};
