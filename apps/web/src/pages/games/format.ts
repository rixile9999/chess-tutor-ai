import type { AnalysisStatus, Color, GameSummary } from '../../api/types';
import { ApiError } from '../../api/client';

export type Outcome = 'win' | 'draw' | 'loss';
export type Tone = 'good' | 'neutral' | 'bad';

export function describeError(e: unknown): string {
  if (e instanceof ApiError) return `${e.status} · ${e.message}`;
  if (e instanceof Error) return e.message || '요청에 실패했습니다';
  return '알 수 없는 오류';
}

/** YYYY-MM-DD in local time; falls back to the raw date part when the string does not parse. */
export function formatDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10) || null;
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export interface TimeControl { category: string; text: string | null }

/** PGN / chess.com style time control ("600+5", "180", "1/86400") to a category and a short mono text. */
export function formatTimeControl(tc: string | null | undefined): TimeControl | null {
  if (!tc) return null;
  const s = tc.trim();
  if (!s || s === '-' || s === '?') return null;
  const daily = s.match(/^1\/(\d+)$/);
  if (daily) {
    const days = Math.round(Number(daily[1]) / 86400);
    return { category: '일일', text: days > 0 ? `${days}일/수` : null };
  }
  const m = s.match(/^(\d+)(?:\+(\d+))?$/);
  if (!m) return { category: s, text: null };
  const base = Number(m[1]);
  const inc = Number(m[2] ?? 0);
  const estimate = base + 40 * inc;
  const category = estimate < 180 ? '불릿' : estimate < 480 ? '블리츠' : estimate < 1500 ? '래피드' : '클래시컬';
  const minutes = base / 60;
  const baseText = Number.isInteger(minutes) ? String(minutes) : minutes.toFixed(1).replace(/\.0$/, '');
  return { category, text: `${baseText}+${inc}` };
}

/** The colour the current user played: the server's user_color first, then a name match. */
export function userColor(g: GameSummary, username: string | null): Color | null {
  if (g.user_color === 'white' || g.user_color === 'black') return g.user_color;
  const u = (username ?? '').trim().toLowerCase();
  if (!u) return null;
  if ((g.white ?? '').toLowerCase() === u) return 'white';
  if ((g.black ?? '').toLowerCase() === u) return 'black';
  return null;
}

export function normalizeResult(r: string | null | undefined): '1-0' | '0-1' | '1/2-1/2' | null {
  const s = (r ?? '').replace(/\s+/g, '');
  if (s === '1-0') return '1-0';
  if (s === '0-1') return '0-1';
  if (s === '1/2-1/2' || s === '½-½' || s === '½–½' || s === '1/2') return '1/2-1/2';
  return null;
}

export function outcomeOf(g: GameSummary, color: Color | null): Outcome | null {
  const r = normalizeResult(g.result);
  if (!r || !color) return null;
  if (r === '1/2-1/2') return 'draw';
  const whiteWon = r === '1-0';
  return (color === 'white') === whiteWon ? 'win' : 'loss';
}

export const OUTCOME_LABEL: Record<Outcome, string> = { win: '승', draw: '무', loss: '패' };
export const OUTCOME_TONE: Record<Outcome, Tone> = { win: 'good', draw: 'neutral', loss: 'bad' };

export function opponentOf(g: GameSummary, color: Color | null): { name: string; elo: number | null } | null {
  if (!color) return null;
  return color === 'white'
    ? { name: g.black || '?', elo: g.black_elo ?? null }
    : { name: g.white || '?', elo: g.white_elo ?? null };
}

export const STATUS_LABEL: Record<AnalysisStatus, string> = {
  none: '미분석', pending: '대기 중', running: '분석 중', done: '분석 완료', failed: '실패',
};

export function statusOf(g: GameSummary): AnalysisStatus {
  const s = g.analysis_status;
  return s === 'pending' || s === 'running' || s === 'done' || s === 'failed' ? s : 'none';
}

export function isInProgress(s: AnalysisStatus): boolean {
  return s === 'pending' || s === 'running';
}

/** Rough count of games in a pasted PGN: one per [Event] header, or one when there are moves but no headers. */
export function countPgnGames(pgn: string): number {
  const t = pgn.trim();
  if (!t) return 0;
  const events = (t.match(/^\s*\[Event\s/gm) ?? []).length;
  return events > 0 ? events : 1;
}

export function moveCount(plyCount: number | null | undefined): number | null {
  if (!plyCount || plyCount <= 0) return null;
  return Math.ceil(plyCount / 2);
}

/** Newest first; games without a date go last; ties broken by id (newer import first). */
export function sortGames(games: GameSummary[]): GameSummary[] {
  return [...games].sort((a, b) => {
    const ta = a.played_at ? new Date(a.played_at).getTime() : NaN;
    const tb = b.played_at ? new Date(b.played_at).getTime() : NaN;
    const va = Number.isNaN(ta) ? -Infinity : ta;
    const vb = Number.isNaN(tb) ? -Infinity : tb;
    if (va !== vb) return vb - va;
    return b.id - a.id;
  });
}

export function dedupeGames(games: GameSummary[]): GameSummary[] {
  const seen = new Set<number>();
  const out: GameSummary[] = [];
  for (const g of games) {
    if (seen.has(g.id)) continue;
    seen.add(g.id);
    out.push(g);
  }
  return out;
}
