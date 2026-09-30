import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react';

export interface LiveEvent {
  combat_id: number;
  at: number; // Date.now() when received
}

export interface LiveState {
  status: 'connecting' | 'live' | 'error';
  latestCombatId: number | null;
  /** Bumped on `data_updated` / `hello` — views re-fetch derived data. */
  dataVersion: number;
  lastEncounter: LiveEvent | null;
}

const initial: LiveState = {
  status: 'connecting',
  latestCombatId: null,
  dataVersion: 0,
  lastEncounter: null,
};

const LiveContext = createContext<LiveState>(initial);

/**
 * Shell-level SSE subscription (Phase 4.4). One EventSource for the whole
 * app; views react via useLive(). EventSource auto-reconnects on drop.
 */
export function LiveProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<LiveState>(initial);

  useEffect(() => {
    const es = new EventSource('/api/events');
    const parse = (e: MessageEvent): Record<string, unknown> => {
      try {
        return JSON.parse(e.data);
      } catch {
        return {};
      }
    };
    es.addEventListener('hello', (e) => {
      const d = parse(e as MessageEvent);
      setState((s) => ({
        ...s,
        status: 'live',
        latestCombatId:
          typeof d.max_combat_id === 'number'
            ? d.max_combat_id
            : s.latestCombatId,
        dataVersion: s.dataVersion + 1,
      }));
    });
    es.addEventListener('encounter_closed', (e) => {
      const d = parse(e as MessageEvent);
      const cid = Number(d.combat_id);
      if (!Number.isFinite(cid)) return;
      setState((s) => ({
        ...s,
        status: 'live',
        latestCombatId: cid,
        lastEncounter: { combat_id: cid, at: Date.now() },
        dataVersion: s.dataVersion + 1,
      }));
    });
    es.addEventListener('data_updated', () => {
      setState((s) => ({ ...s, status: 'live', dataVersion: s.dataVersion + 1 }));
    });
    es.onerror = () => {
      setState((s) => ({ ...s, status: 'error' }));
    };
    return () => es.close();
  }, []);

  return <LiveContext.Provider value={state}>{children}</LiveContext.Provider>;
}

export function useLive(): LiveState {
  return useContext(LiveContext);
}
