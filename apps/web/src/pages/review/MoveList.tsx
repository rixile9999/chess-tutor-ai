import { useEffect, useMemo, useRef } from 'react';
import type { Classification, GameAnalysis, GameDetail } from '../../api/types';
import { plyLabel } from '../../lib/labels';
import { ClassBadge } from './shared';

type Props = { game: GameDetail; analysis: GameAnalysis | null; ply: number; onSelect: (ply: number) => void };

function resultText(game: GameDetail): string {
  const moves = Math.ceil(game.moves.length / 2);
  return `${game.result || '?'} (${moves}수)`;
}

/** Two columns per move number with classification badges; the selected move is outlined. */
export function MoveList({ game, analysis, ply, onSelect }: Props) {
  const classOf = useMemo(() => {
    const m = new Map<number, Classification>();
    if (analysis?.status === 'done') for (const x of analysis.moves ?? []) m.set(x.ply, x.classification);
    return m;
  }, [analysis]);

  const note = useMemo(() => {
    const parts: string[] = [];
    if (game.opening_name) parts.push(game.opening_name);
    else if (game.eco) parts.push(game.eco);
    if (classOf.size) {
      const first = game.moves.find((mv) => classOf.get(mv.ply) !== undefined && classOf.get(mv.ply) !== 'book');
      if (first && first.ply > 1) parts.push(`책 이탈 ${plyLabel(first.ply)}${first.san}`);
    }
    parts.push(resultText(game));
    return parts.join(' · ');
  }, [game, classOf]);

  const rows = useMemo(() => {
    const out: { n: number; w: typeof game.moves[number] | null; b: typeof game.moves[number] | null }[] = [];
    for (let i = 0; i < game.moves.length; i += 2) out.push({ n: i / 2 + 1, w: game.moves[i] ?? null, b: game.moves[i + 1] ?? null });
    return out;
  }, [game.moves]);

  const selectedRef = useRef<HTMLButtonElement>(null);
  useEffect(() => { selectedRef.current?.scrollIntoView({ block: 'nearest' }); }, [ply]);

  const cell = (mv: typeof game.moves[number] | null) => {
    if (!mv) return <div />;
    const sel = mv.ply === ply;
    const cls = classOf.get(mv.ply);
    return (
      <button type="button" ref={sel ? selectedRef : undefined} className={`rv-cell${sel ? ' selected' : ''}`} onClick={() => onSelect(mv.ply)}>
        <span className="mv" style={{ fontSize: 13 }}>{mv.san}</span>
        {cls && <ClassBadge cls={cls} />}
      </button>
    );
  };

  return (
    <div className="card rv-moves">
      <div className="rv-moves-head"><span className="eyebrow">기보</span><span className="small muted" title={note}>{note}</span></div>
      <div className="rv-row">
        <div className="mono faint rv-row-n">0.</div>
        <button type="button" ref={ply === 0 ? selectedRef : undefined} className={`rv-cell start${ply === 0 ? ' selected' : ''}`} onClick={() => onSelect(0)}>
          <span className="small muted">시작 국면</span>
        </button>
      </div>
      {rows.map((r) => (
        <div className="rv-row" key={r.n}>
          <div className="mono faint rv-row-n">{r.n}.</div>
          {cell(r.w)}
          {cell(r.b)}
        </div>
      ))}
      {game.moves.length === 0 && <div className="small muted" style={{ padding: '8px 0' }}>수가 없는 기보입니다.</div>}
    </div>
  );
}
