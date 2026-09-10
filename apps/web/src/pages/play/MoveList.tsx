import { useMemo } from 'react';
import { moveNumberOf, sideToMoveOf, type Ply } from './state';

type Cell = Ply & { i: number };
type Row = { n: number; w: Cell | null; b: Cell | null };

type Props = { startFen: string; plies: Ply[]; cursor: number; onSelect: (cursor: number) => void };

/** Rows for a move list that may start mid-game (a black-to-move start opens with an empty white cell). */
export function moveRows(startFen: string, plies: Ply[]): Row[] {
  const first = moveNumberOf(startFen);
  const offset = sideToMoveOf(startFen) === 'black' ? 1 : 0;
  const out: Row[] = [];
  plies.forEach((m, i) => {
    const p = i + offset;
    const n = first + Math.floor(p / 2);
    let row = out[out.length - 1];
    if (!row || row.n !== n) { row = { n, w: null, b: null }; out.push(row); }
    if (p % 2 === 0) row.w = { ...m, i }; else row.b = { ...m, i };
  });
  return out;
}

const BY_LABEL: Record<Ply['by'], string | null> = { user: null, ai: 'AI', book: '책' };

/** 수 목록 tab. Clicking a move only moves the cursor — the game itself never changes (4.2). */
export function MoveList({ startFen, plies, cursor, onSelect }: Props) {
  const rows = useMemo(() => moveRows(startFen, plies), [startFen, plies]);
  return (
    <div className="card tr-movelist pl-movelist">
      <div className="tr-movelist-head">
        <span className="eyebrow">기보</span>
        <span className="small muted">{plies.length === 0 ? '아직 둔 수가 없습니다' : `${plies.length}수`}</span>
        <div className="spacer" />
        <button type="button" className="pl-mini" onClick={() => onSelect(0)} disabled={cursor === 0}>처음으로</button>
      </div>
      {rows.length === 0 ? (
        <p className="small faint" style={{ margin: '8px 2px' }}>보드에서 수를 두면 여기에 쌓입니다.</p>
      ) : rows.map((row) => (
        <div key={row.n} className="tr-row">
          <div className="no">{row.n}.</div>
          {[row.w, row.b].map((m, j) => m ? (
            <button
              key={j}
              type="button"
              className={`tr-cell pl-cell${m.i + 1 === cursor ? ' sel' : ''}`}
              onClick={() => onSelect(m.i + 1)}
            >
              <span className="mv" style={{ fontSize: 13 }}>{m.san}</span>
              {BY_LABEL[m.by] && <span className="pl-tag">{BY_LABEL[m.by]}</span>}
              {m.hintLevel ? <span className="pl-tag hint">힌트{m.hintLevel}</span> : null}
            </button>
          ) : <div key={j} />)}
        </div>
      ))}
    </div>
  );
}
