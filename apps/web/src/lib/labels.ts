import type { Classification, Score } from '../api/types';

export const CLASS_LABEL: Record<Classification, string> = {
  book: '책', best: '최선', good: '좋음', inaccuracy: '부정확', mistake: '실수', blunder: '블런더', forced: '강제',
};

export const CLASS_TONE: Record<Classification, 'good' | 'bad' | 'neutral'> = {
  book: 'neutral', best: 'good', good: 'neutral', inaccuracy: 'neutral', mistake: 'bad', blunder: 'bad', forced: 'neutral',
};

export const MOTIF_LABEL: Record<string, string> = {
  discovered_attack: '디스커버드 어택', fork: '포크', pin: '핀', skewer: '스큐어', hanging_piece: '떠 있는 기물',
  remove_defender: '수비수 제거', overload: '과부하', back_rank: '백랭크', trapped_piece: '갇힌 기물', mate_threat: '메이트 위협',
};

export function motifLabel(kind: string): string { return MOTIF_LABEL[kind] ?? kind; }

export function formatScore(s: Score): string {
  if (s.mate !== null && s.mate !== undefined) return s.mate > 0 ? `M${s.mate}` : `-M${-s.mate}`;
  const p = (s.cp ?? 0) / 100;
  return (p > 0 ? '+' : '') + p.toFixed(1);
}

/** 0..1 share of the eval bar that should be white, from a white-POV score. */
export function whiteShare(s: Score): number {
  if (s.mate !== null && s.mate !== undefined) return s.mate > 0 ? 0.98 : 0.02;
  const cp = s.cp ?? 0;
  return 1 / (1 + Math.exp(-0.00368208 * cp));
}

/** Move number display for a ply (1-based ply): 1 -> "1.", 2 -> "1…" */
export function plyLabel(ply: number): string {
  const n = Math.ceil(ply / 2);
  return ply % 2 === 1 ? `${n}.` : `${n}…`;
}
