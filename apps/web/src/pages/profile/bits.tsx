import type { ReactNode } from 'react';

export const MINUS = '−';

/** Rates arrive as 0..1 fractions (see TimeStats.baseline = 0.09); tolerate a backend that already sends percent. */
export function toPercent(v: number | null | undefined): number {
  if (v === null || v === undefined || Number.isNaN(v)) return 0;
  return v > 1 ? v : v * 100;
}

export function fmtPercent(v: number | null | undefined): string {
  const p = toPercent(v);
  const digits = p > 0 && p < 10 && Math.round(p) !== p ? 1 : 0;
  return `${p.toFixed(digits)}%`;
}

export function fmtSigned(v: number, digits = 0): string {
  const r = Number(v.toFixed(digits));
  if (r > 0) return `+${r.toFixed(digits)}`;
  if (r < 0) return `${MINUS}${Math.abs(r).toFixed(digits)}`;
  return '0';
}

/** Centipawn loss shown as a negative integer, e.g. 142 -> "−142". */
export function fmtCp(v: number | null | undefined): string {
  return `${MINUS}${Math.abs(Math.round(v ?? 0))}`;
}

/** Centipawn loss shown in pawns, e.g. 80 -> "−0.8". */
export function fmtPawns(v: number | null | undefined): string {
  return `${MINUS}${(Math.abs(v ?? 0) / 100).toFixed(1)}`;
}

export function fmtDate(s: string | null | undefined): string | null {
  if (!s) return null;
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

export function platformLabel(p: string | null | undefined): string {
  const key = (p ?? '').toLowerCase().replace(/[^a-z]/g, '');
  if (key === 'chesscom') return 'chess.com';
  if (key === 'lichess') return 'Lichess';
  return p && p.trim() ? p : '';
}

export function Card({ title, sub, children, className }: { title: string; sub?: string; children: ReactNode; className?: string }) {
  return (
    <section className={`card pf-card${className ? ` ${className}` : ''}`}>
      <header className="pf-card-head">
        <h3 className="h3">{title}</h3>
        {sub && <span className="small muted">{sub}</span>}
      </header>
      {children}
    </section>
  );
}

export type BarTone = 'ink' | 'bad' | 'faint';

/** Label + horizontal bar + right-aligned figure. Text stays in ink tokens; only the fill takes a tone. */
export function Bar({ label, value, max, right, tone = 'ink', marker }: {
  label: string; value: number; max: number; right: string; tone?: BarTone; marker?: number;
}) {
  const clamp = (n: number) => Math.max(0, Math.min(100, n));
  const pct = max > 0 ? clamp((value / max) * 100) : 0;
  const markerPct = marker !== undefined && max > 0 ? clamp((marker / max) * 100) : null;
  return (
    <div className="pf-bar">
      <div className="pf-bar-label" title={label}>{label}</div>
      <div className="pf-bar-track" role="img" aria-label={`${label} ${right}`}>
        <div className={`pf-bar-fill pf-bar-fill-${tone}`} style={{ width: `${pct}%` }} />
        {markerPct !== null && <div className="pf-bar-marker" style={{ left: `${markerPct}%` }} aria-hidden="true" />}
      </div>
      <div className="mono muted pf-bar-right">{right}</div>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="pf-empty small muted">{children}</div>;
}

const S = {
  width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2.2,
  strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, 'aria-hidden': true as const,
};
export function IconArrow() { return <svg {...S}><path d="M5 12h14M13 6l6 6-6 6" /></svg>; }
export function IconPlay() { return <svg width={14} height={14} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M6 4l14 8-14 8z" /></svg>; }
export function IconRefresh() { return <svg {...S}><path d="M4 9a8 8 0 0 1 14-3l2 2M20 4v4h-4M20 15a8 8 0 0 1-14 3l-2-2M4 20v-4h4" /></svg>; }
export function IconUser() { return <svg {...S} width={22} height={22} strokeWidth={1.8}><circle cx="12" cy="8" r="4" /><path d="M4 21c0-4 3.6-7 8-7s8 3 8 7" /></svg>; }
export function IconImport() { return <svg {...S} width={22} height={22} strokeWidth={1.8}><path d="M12 4v11M7 10l5 5 5-5M4 19h16" /></svg>; }
export function IconAlert() { return <svg {...S} width={22} height={22} strokeWidth={1.8}><path d="M12 3l10 18H2z" /><path d="M12 10v5M12 18h.01" /></svg>; }
