import { useEffect, useState } from 'react';
import { api } from './api/client';
import { ErrorBoundary } from './components/ErrorBoundary';
import { useQueryState } from './hooks/useQuery';
import { LiveProvider, useLive } from './live/LiveContext';
import { AllEncountersView } from './views/AllEncountersView';
import { BossComparisonView } from './views/BossComparisonView';
import { CharacterComparisonView } from './views/CharacterComparisonView';
import { CombatView } from './views/CombatView';
import { RunsView } from './views/RunsView';
import { TotalsView } from './views/TotalsView';
import { fmtWhen } from './lib/format';

export const VIEWS = [
  'Combat Viewer',
  'Runs',
  'All Encounters',
  'Totals',
  'Character Comparison',
  'Boss Comparison',
] as const;
type View = (typeof VIEWS)[number];

function Shell() {
  const [q, updateQ] = useQueryState({ view: 'Combat Viewer' });
  const view: View = (VIEWS as readonly string[]).includes(q.view)
    ? (q.view as View)
    : 'Combat Viewer';

  const [characters, setCharacters] = useState<string[]>([]);
  const [character, setCharacter] = useState('All');
  const [resampleS, setResampleS] = useState(1);
  const [smoothS, setSmoothS] = useState(0);
  const [followLive, setFollowLive] = useState(true);
  const [health, setHealth] = useState<Record<string, unknown> | null>(null);

  const live = useLive();

  useEffect(() => {
    let on = true;
    api.characters().then((r) => on && setCharacters(r.characters)).catch(() => undefined);
    const tick = () =>
      api.health().then((h) => on && setHealth(h)).catch(() => undefined);
    tick();
    const iv = window.setInterval(tick, 30_000);
    return () => {
      on = false;
      window.clearInterval(iv);
    };
  }, [live.dataVersion]);

  const openCombat = (combatId: number) =>
    updateQ({ view: 'Combat Viewer', combat: String(combatId) });

  const csvAge =
    health && health.csv_mtime
      ? Math.max(0, Math.round((Date.now() / 1000 - Number(health.csv_mtime))))
      : null;
  const csvAgeLabel =
    csvAge === null
      ? 'no CSV'
      : csvAge < 60
        ? `${csvAge}s ago`
        : csvAge < 3600
          ? `${Math.round(csvAge / 60)}m ago`
          : `${Math.round(csvAge / 3600)}h ago`;

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">WoW Combat Viewer</div>
        <nav>
          {VIEWS.map((v) => (
            <button
              key={v}
              className={`nav-item ${v === view ? 'active' : ''}`}
              onClick={() => updateQ({ view: v })}
            >
              {v}
            </button>
          ))}
        </nav>

        <div className="side-section">
          <label className="side-label">Character</label>
          <select
            className="input"
            value={character}
            onChange={(e) => setCharacter(e.target.value)}
          >
            <option value="All">All</option>
            {characters.map((c) => (
              <option key={c} value={c}>
                {c.split('-')[0]}
              </option>
            ))}
            <option value="Others">Others (non-players)</option>
          </select>
          <div className="row gap pad-top">
            <label className="small dim">Resample</label>
            <select
              className="input"
              value={resampleS}
              onChange={(e) => setResampleS(Number(e.target.value))}
            >
              {[1, 3, 5].map((s) => (
                <option key={s} value={s}>
                  {s}s
                </option>
              ))}
            </select>
            <label className="small dim">Smooth</label>
            <select
              className="input"
              value={smoothS}
              onChange={(e) => setSmoothS(Number(e.target.value))}
            >
              <option value={0}>off</option>
              <option value={3}>3s</option>
              <option value={5}>5s</option>
            </select>
          </div>
        </div>

        <div className="side-section">
          <div className="row gap">
            <span
              className={`dot ${live.status === 'live' ? 'dot-live' : live.status === 'error' ? 'dot-error' : ''}`}
            />
            <span className="small dim">
              {live.status === 'live'
                ? 'Live (SSE)'
                : live.status === 'connecting'
                  ? 'Connecting…'
                  : 'Stream dropped (retrying)'}
            </span>
          </div>
          <label className="check">
            <input
              type="checkbox"
              checked={followLive}
              onChange={(e) => setFollowLive(e.target.checked)}
            />
            Follow latest encounter
          </label>
          <div className="small dim">
            CSV: {csvAgeLabel}
            {typeof health?.encounter_count === 'number' && (
              <> · {String(health.encounter_count)} encounters</>
            )}
          </div>
          {live.lastEncounter && (
            <button className="combat-item active" onClick={() => openCombat(live.lastEncounter!.combat_id)}>
              <div className="combat-item-top">
                <span>+ Combat #{live.lastEncounter.combat_id} closed</span>
                <span className="dim">
                  {fmtWhen(
                    new Date(live.lastEncounter.at).toISOString().replace('T', ' ').slice(0, 16),
                  )}
                </span>
              </div>
            </button>
          )}
        </div>
      </aside>

      <main className="main">
        <ErrorBoundary>
        {view === 'Combat Viewer' && (
          <CombatView
            character={character}
            resampleS={resampleS}
            smoothS={smoothS}
            followLive={followLive}
            combat={q.combat}
            setCombat={(id) => updateQ({ combat: id })}
          />
        )}
        {view === 'Runs' && <RunsView openCombat={openCombat} />}
        {view === 'All Encounters' && (
          <AllEncountersView character={character} openCombat={openCombat} />
        )}
        {view === 'Totals' && <TotalsView character={character} />}
        {view === 'Character Comparison' && <CharacterComparisonView />}
        {view === 'Boss Comparison' && <BossComparisonView />}
        </ErrorBoundary>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <LiveProvider>
      <Shell />
    </LiveProvider>
  );
}
