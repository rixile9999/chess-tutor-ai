import { useEffect, useMemo, useRef, useState } from 'react';
import type { Color, NamedCandidate } from '../../api/types';
import { MiniBoard } from '../../components/MiniBoard';
import { norm01, pct, scoreColor } from './colors';
import type { CandidateSort } from './line';
import { positionKey } from './useGuide';

const MINI = 72;
const SORTS: { id: CandidateSort; label: string }[] = [
  { id: 'mine', label: '내 판수' },
  { id: 'master', label: '마스터' },
  { id: 'name', label: '이름' },
];

/** My games on the arriving position, keyed by its position key (= the map DAG node id). */
export type MyMoves = Map<string, { games: number; score: number }>;

type Props = {
  candidates: NamedCandidate[];
  mine: MyMoves;
  sort: CandidateSort;
  onSort: (sort: CandidateSort) => void;
  orientation: Color;
  onPlay: (uci: string) => void;
  /** Hover/focus draws the move as an arrow on the main board. */
  onHover: (uci: string | null) => void;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onBackToBook: () => void;
  backToBookLabel: string;
};

type Row = NamedCandidate & { mine: { games: number; score: number } | null };

function sorted(rows: Row[], sort: CandidateSort): Row[] {
  const by: Record<CandidateSort, (a: Row, b: Row) => number> = {
    mine: (a, b) => (b.mine?.games ?? 0) - (a.mine?.games ?? 0) || (b.master_games ?? 0) - (a.master_games ?? 0),
    master: (a, b) => (b.master_games ?? 0) - (a.master_games ?? 0)
      || (b.master_score ?? 0) - (a.master_score ?? 0)
      || (b.mine?.games ?? 0) - (a.mine?.games ?? 0),
    name: (a, b) => Number(!!b.name) - Number(!!a.name) || (a.name || '').localeCompare(b.name || '', 'ko'),
  };
  return [...rows].sort((a, b) => by[sort](a, b) || a.san.localeCompare(b.san));
}

/** ② 다음 갈 수 있는 네임드 국면: every book (and master-only) move, all shown, the area scrolls (§9.1). */
export function Candidates({
  candidates, mine, sort, onSort, orientation, onPlay, onHover, loading, error, onRetry, onBackToBook, backToBookLabel,
}: Props) {
  const listRef = useRef<HTMLDivElement>(null);
  const [atEnd, setAtEnd] = useState(true);

  const rows = useMemo(
    () => sorted(candidates.map((c) => ({ ...c, mine: mine.get(positionKey(c.fen_after)) ?? null })), sort),
    [candidates, mine, sort],
  );

  // The bottom fade says "there is more below"; it must go away once the list is scrolled through.
  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    const update = () => setAtEnd(el.scrollHeight - el.scrollTop - el.clientHeight < 8);
    update();
    el.addEventListener('scroll', update, { passive: true });
    return () => el.removeEventListener('scroll', update);
  }, [rows]);

  const mineCount = rows.filter((r) => (r.mine?.games ?? 0) > 0).length;

  return (
    <div className="op-cand">
      <div className="op-card-head">
        <span className="h3">다음 갈 수 있는 네임드 국면</span>
        <span className="small muted">
          {loading || error ? '' : `${rows.length}가지 · 내 기보 ${mineCount}가지 · 책·마스터 ${rows.length - mineCount}가지 · 전부 표시, 스크롤`}
        </span>
        <div className="op-grow" />
        <div className="op-seg op-cand-sort" role="group" aria-label="정렬">
          {SORTS.map((s) => (
            <button key={s.id} type="button" className={sort === s.id ? 'on' : ''} onClick={() => onSort(s.id)}>{s.label}</button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="op-state compact"><div className="op-spinner" /><div>이 국면의 후보를 찾는 중</div></div>
      ) : error ? (
        <div className="op-state compact">
          <div className="op-error">이 국면 정보를 불러오지 못했습니다: {error}</div>
          <button type="button" className="btn btn-ghost compact" onClick={onRetry}>다시 시도</button>
        </div>
      ) : !rows.length ? (
        <div className="op-cand-empty">
          <span>이 국면 다음 수는 책에 없습니다. 보드에서 계속 둘 수 있고, 수마다 해설은 계속 붙습니다.</span>
          <button type="button" className="btn btn-ghost compact" onClick={onBackToBook}>↶ 마지막 책 국면으로 ({backToBookLabel})</button>
        </div>
      ) : (
        <div className={`op-cand-wrap${atEnd ? ' end' : ''}`}>
          <div className="op-cand-list" ref={listRef}>
            {rows.map((c) => (
              <Card key={c.uci + c.san} row={c} orientation={orientation} onPlay={onPlay} onHover={onHover} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Card({ row, orientation, onPlay, onHover }: {
  row: Row; orientation: Color; onPlay: (uci: string) => void; onHover: (uci: string | null) => void;
}) {
  const masterOnly = row.master_only;
  const via = !row.named_here && row.to_name.length ? `${row.to_name.join(' ')} 뒤 ${row.name}` : null;
  const score = norm01(row.mine?.score);
  return (
    <button
      type="button"
      className={`op-cand-card${masterOnly ? ' master' : ''}`}
      onClick={() => onPlay(row.uci)}
      onMouseEnter={() => onHover(row.uci)}
      onMouseLeave={() => onHover(null)}
      onFocus={() => onHover(row.uci)}
      onBlur={() => onHover(null)}
    >
      <MiniBoard
        fen={row.fen_after}
        size={MINI}
        orientation={orientation}
        highlight={[row.uci.slice(0, 2), row.uci.slice(2, 4)]}
      />
      <span className="op-cand-meta">
        <span className="op-cand-row">
          <span className="mv op-cand-label">{row.label}</span>
          {row.eco && <span className="mono faint small">{row.eco}</span>}
        </span>
        <span className={`op-cand-name${row.named_here ? '' : ' via'}`} title={row.name_en ?? row.name}>
          {row.named_here ? row.name : via ?? (row.name || (masterOnly ? '마스터 DB에만 있는 수' : '이름 없는 중간 국면'))}
        </span>
        {row.mine ? (
          <span className="op-cand-rec">
            <span className="op-bar"><i style={{ width: `${Math.round((score ?? 0) * 100)}%`, background: scoreColor(row.mine.score) }} /></span>
            <span className="mono">내 {row.mine.games}판 {pct(row.mine.score)}</span>
          </span>
        ) : (
          <span className="op-cand-rec faint">내 기보 없음</span>
        )}
        {row.master_games !== null && row.master_games !== undefined && (
          <span className="op-cand-rec faint mono">
            마스터 {row.master_games.toLocaleString()}판{row.master_score !== null && row.master_score !== undefined ? ` · ${pct(row.master_score)}` : ''}
          </span>
        )}
      </span>
    </button>
  );
}
