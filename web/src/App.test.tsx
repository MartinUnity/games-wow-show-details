import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';

const routes: Record<string, unknown> = {
  '/api/health': {
    sqlite_active: false,
    csv_exists: true,
    csv_mtime: Date.now() / 1000,
  },
  '/api/characters': {
    characters: ['Lumiara-TheMaelstrom-EU', 'Zethrok-TheMaelstrom-EU'],
    counts: { 'Lumiara-TheMaelstrom-EU': 2, 'Zethrok-TheMaelstrom-EU': 4 },
  },
  '/api/encounters': { meta: { n_encounters: 0 }, encounters: [] },
};

describe('App shell', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: RequestInfo | URL) => {
        const u = String(url);
        const key = Object.keys(routes).find((k) => u.startsWith(k));
        return new Response(JSON.stringify(key ? routes[key] : {}), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders the brand and all six views', async () => {
    render(<App />);
    expect(screen.getByText('WoW Combat Viewer')).toBeTruthy();
    for (const v of [
      'Combat Viewer',
      'Runs',
      'All Encounters',
      'Totals',
      'Character Comparison',
      'Boss Comparison',
    ]) {
      expect(screen.getAllByText(v).length).toBeGreaterThan(0);
    }
    expect(screen.getByText('Follow latest encounter')).toBeTruthy();
  });
});
