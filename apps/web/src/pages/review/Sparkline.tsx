import { useMemo, type MouseEvent } from 'react';
import type { GameAnalysis, GameDetail, MoveReviewOut, Score } from '../../api/types';
import { formatScore, plyLabel } from '../../lib/labels';

type Props = { game: GameDetail; analysis: GameAnalysis | null; review: MoveReviewOut | null; ply: number; onSelect: (ply: number) => void };

const W = 496, H = 44, PAD = 4;
const scoreToPawns = (s: Score): number => (s.mate !== null && s.mate !== undefined ? (s.mate > 0 ? 10 : -10) : (s.cp ?? 0) / 100);

/** Eval curve over the game (white POV, pawns). The user's blunders get a bad dot; the selected ply a marker. */
export function Sparkline({ game, analysis, review, ply, onSelect }: Props) {
  const done = analysis?.status === 'done';
  const values = useMemo(() => {
    if (!done) return [];
    const series = analysis?.summary?.eval_series ?? [];
    if (series.length > 1) return series;
    const moves = analysis?.moves ?? [];
    if (!moves.length) return [];
    return [scoreToPawns(moves[0].eval_before), ...moves.map((m) => scoreToPawns(m.eval_after))];
  }, [done, analysis]);

  const n = values.length;
  const mid = H / 2;
  const x = (i: number) => (n > 1 ? (i / (n - 1)) * (W - PAD * 2) + PAD : W / 2);
  const y = (v: number) => mid - Math.max(-1, Math.min(1, v / 8)) * (mid - 6);
  const pts = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
  const area = n > 1 ? `M${PAD},${mid} L${pts.join(' L')} L${(W - PAD).toFixed(1)},${mid} Z` : '';

  const blunders = useMemo(() => {
    if (!done || !game.user_color) return [];
    return (analysis?.moves ?? []).filter((m) => m.color === game.user_color && m.classification === 'blunder').map((m) => m.ply);
  }, [done, analysis, game.user_color]);

  const caption = useMemo(() => {
    if (!done) return analysis ? '분석이 끝나면 평가 추이가 표시됩니다' : '';
    const m = analysis?.moves.find((mv) => mv.ply === ply);
    const before = review?.eval_before ?? m?.eval_before, after = review?.eval_after ?? m?.eval_after;
    const san = review?.san ?? m?.san;
    if (ply === 0 || !before || !after || !san) return '수를 고르면 평가 변화가 표시됩니다';
    return `${plyLabel(ply)}${san}에서 ${formatScore(before)} → ${formatScore(after)}`;
  }, [done, analysis, review, ply]);

  const pick = (e: MouseEvent<SVGSVGElement>) => {
    if (n < 2) return;
    const r = e.currentTarget.getBoundingClientRect();
    const t = (e.clientX - r.left) / r.width;
    const i = Math.round(((t * W - PAD) / (W - PAD * 2)) * (n - 1));
    onSelect(Math.max(0, Math.min(game.moves.length, i)));
  };

  return (
    <div className="card rv-spark">
      <div className="rv-spark-head"><span className="eyebrow">평가 추이</span><span className="small muted" title={caption}>{caption}</span></div>
      <svg viewBox={`0 0 ${W} ${H}`} height={H} onClick={pick} role="img" aria-label="평가 추이">
        <line x1={PAD} y1={mid} x2={W - PAD} y2={mid} stroke="var(--line-strong)" strokeWidth="1" />
        {n > 1 && (
          <>
            <path d={area} fill="rgba(43, 38, 34, 0.10)" />
            <polyline points={pts.join(' ')} fill="none" stroke="var(--ink)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
            {ply < n && <line x1={x(ply)} y1={2} x2={x(ply)} y2={H - 2} stroke="var(--ink-3)" strokeWidth="1" strokeDasharray="2 2" />}
            {blunders.filter((p) => p < n).map((p) => (
              <circle key={p} cx={x(p)} cy={y(values[p])} r={p === ply ? 5.5 : 4.5} fill="var(--bad)" stroke="var(--surface)" strokeWidth="2">
                <title>{`${plyLabel(p)}${game.moves[p - 1]?.san ?? ''} 블런더`}</title>
              </circle>
            ))}
            {ply < n && !blunders.includes(ply) && <circle cx={x(ply)} cy={y(values[ply])} r="3.5" fill="var(--ink)" stroke="var(--surface)" strokeWidth="1.5" />}
          </>
        )}
        {n <= 1 && <text x={W / 2} y={mid + 4} textAnchor="middle" fontSize="11" fill="var(--ink-3)">{done ? '평가 데이터가 없습니다' : '엔진 분석 대기 중'}</text>}
      </svg>
    </div>
  );
}
