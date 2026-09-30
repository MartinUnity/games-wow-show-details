// Formatting helpers shared by the views.

/** "1.23M" / "45.0K" / "789" — compact amounts (Streamlit _fmt_compact_amount parity). */
export function fmtCompact(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  const abs = Math.abs(n);
  if (abs >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(Math.round(n));
}

export function fmtInt(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return Math.round(n).toLocaleString('en-US');
}

export function fmt1(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return n.toFixed(1);
}

/** "1h 02m" / "12m 03s" / "45s" */
export function fmtDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return '—';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`;
  if (m > 0) return `${m}m ${String(s).padStart(2, '0')}s`;
  return `${s}s`;
}

/** Parse the CSV timestamp "MM/DD/YYYY HH:MM:SS.mmm" (local time) to epoch ms. */
export function parseCsvTs(s: string): number {
  const m = /^(\d{2})\/(\d{2})\/(\d{4}) (\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?$/.exec(
    s,
  );
  if (!m) return NaN;
  const frac = m[7] ? Number(m[7].padEnd(3, '0').slice(0, 3)) : 0;
  return new Date(
    +m[3],
    +m[1] - 1,
    +m[2],
    +m[4],
    +m[5],
    +m[6],
    frac,
  ).getTime();
}

/** Parse the API ISO-ish timestamp "YYYY-MM-DD HH:MM:SS.mmm" (local time). */
export function parseIsoLocal(s: string): number {
  return Date.parse(s.replace(' ', 'T'));
}

/** "2026-09-28 13:45" for display. */
export function fmtWhen(s: string): string {
  const t = parseIsoLocal(s);
  if (Number.isNaN(t)) return s;
  const d = new Date(t);
  const p = (x: number) => String(x).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function hourOfDay(s: string): number {
  const t = parseIsoLocal(s);
  return Number.isNaN(t) ? -1 : new Date(t).getHours();
}
