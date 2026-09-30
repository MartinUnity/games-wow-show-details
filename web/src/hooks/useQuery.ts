import { useCallback, useEffect, useState } from 'react';

type QueryState = Record<string, string>;

/**
 * URL query-param backed state (deep-link parity with the Streamlit
 * `?view=…&combat=…` scheme). Uses history.replaceState so navigation
 * doesn't spam the back-button.
 */
export function useQueryState(initial: QueryState) {
  const read = useCallback((): QueryState => {
    const p = new URLSearchParams(window.location.search);
    const out: QueryState = { ...initial };
    for (const [k, v] of p.entries()) out[k] = v;
    return out;
  }, []);

  const [state, setState] = useState<QueryState>(read);

  useEffect(() => {
    const onPop = () => setState(read());
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, [read]);

  const update = useCallback(
    (patch: Record<string, string | null>) => {
      setState((prev) => {
        const next: QueryState = { ...prev };
        for (const [k, v] of Object.entries(patch)) {
          if (v === null || v === '') delete next[k];
          else next[k] = v;
        }
        const p = new URLSearchParams();
        for (const [k, v] of Object.entries(next)) {
          if (v !== initial[k]) p.set(k, v);
        }
        const qs = p.toString();
        const url = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
        window.history.replaceState(null, '', url);
        return next;
      });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  return [state, update] as const;
}
