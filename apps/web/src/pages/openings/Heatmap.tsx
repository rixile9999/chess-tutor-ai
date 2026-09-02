import { useMemo } from 'react';
import type { Color, PieceHeatmap } from '../../api/types';

const FILES = 'abcdefgh';

export interface PieceOption { code: string; label: string }

const colorLetter = (color: Color) => (color === 'white' ? 'w' : 'b');
const homeRank = (color: Color) => (color === 'white' ? '1' : '8');

/**
 * Piece codes follow the API contract: colour letter (w/b) + starting square, e.g. 'bf8' is the
 * black bishop that starts on f8. See services/openings_map.py `parse_piece`; the square must
 * hold a piece of that colour in the starting position, so the rank follows the colour.
 */
export function pieceOptions(color: Color): PieceOption[] {
  const c = colorLetter(color);
  const r = homeRank(color);
  const side = color === 'white' ? '백' : '흑';
  const defs: [string, string][] = [
    ['f', '비숍'], ['c', '비숍'], ['g', '나이트'], ['b', '나이트'],
    ['d', '퀸'], ['a', '룩'], ['h', '룩'], ['e', '킹'],
  ];
  return defs.map(([f, name]) => ({ code: `${c}${f}${r}`, label: `${side} ${f}${r} ${name}` }));
}
export function defaultPiece(color: Color): string { return `${colorLetter(color)}f${homeRank(color)}`; }
/** Keep the same piece when the colour flips (f8 bishop <-> f1 bishop): swap both colour and rank. */
export function mirrorPiece(code: string, color: Color): string {
  const file = code.slice(1, 2);
  const want = `${colorLetter(color)}${file}${homeRank(color)}`;
  return pieceOptions(color).some((o) => o.code === want) ? want : defaultPiece(color);
}

type Props = { data: PieceHeatmap | null; color: Color; size?: number };

/** 8x8 destination heatmap with the top squares listed as percentages. */
export function Heatmap({ data, color, size = 232 }: Props) {
  const { cells, top, total } = useMemo(() => {
    const squares = data?.squares ?? {};
    const entries = Object.entries(squares).filter(([sq, v]) => /^[a-h][1-8]$/.test(sq) && typeof v === 'number' && v > 0);
    const max = Math.max(0, ...entries.map(([, v]) => v));
    const sum = entries.reduce((a, [, v]) => a + v, 0);
    const heat = new Map(entries.map(([sq, v]) => [sq, max > 0 ? v / max : 0] as const));
    const ranks = color === 'white' ? [7, 6, 5, 4, 3, 2, 1, 0] : [0, 1, 2, 3, 4, 5, 6, 7];
    const files = color === 'white' ? [0, 1, 2, 3, 4, 5, 6, 7] : [7, 6, 5, 4, 3, 2, 1, 0];
    const cells: { sq: string; light: boolean; heat: number | null; rank: string | null; file: string | null }[] = [];
    ranks.forEach((r, row) => files.forEach((f, col) => {
      const sq = FILES[f] + (r + 1);
      cells.push({
        sq, light: (f + r) % 2 === 1, heat: heat.get(sq) ?? null,
        rank: col === 0 ? String(r + 1) : null, file: row === 7 ? FILES[f] : null,
      });
    }));
    const top = entries.sort((a, b) => b[1] - a[1]).slice(0, 4).map(([sq, v]) => ({ sq, share: sum > 0 ? v / sum : 0 }));
    return { cells, top, total: sum };
  }, [data, color]);

  const fmt = (share: number) => (share < 0.01 && share > 0 ? '<1%' : `${Math.round(share * 100)}%`);

  return (
    <div className="op-heat-row">
      <div className="op-board" style={{ width: size, height: size }}>
        {cells.map((c) => (
          <div key={c.sq} className={`op-sq ${c.light ? 'light' : 'dark'}`}>
            {c.heat !== null && <div className="op-sq-heat" style={{ background: `rgba(36, 120, 166, ${(0.12 + c.heat * 0.78).toFixed(2)})` }} />}
            {c.rank && <span className="op-coord rank">{c.rank}</span>}
            {c.file && <span className="op-coord file">{c.file}</span>}
          </div>
        ))}
      </div>
      <div className="op-heat-list">
        {data ? (
          <>
            <div className="small muted">{data.games ?? 0}판 기준{total > 0 ? ` · ${top.length}개 칸` : ''}</div>
            {top.length === 0 && <div className="small faint">이 기물이 이동한 기록이 없습니다.</div>}
            {top.map((t) => (
              <div key={t.sq} className="op-heat-item">
                <span className="mv">{t.sq}</span>
                <span className="mono muted">{fmt(t.share)}</span>
              </div>
            ))}
            {top.length > 0 && (
              <div className="op-heat-note">
                {top[0].sq}에 두는 것이 주류({fmt(top[0].share)}).
                {top[1] ? ` ${top[1].sq}로 가는 ${fmt(top[1].share)}는 다른 계획.` : ''}
              </div>
            )}
          </>
        ) : (
          <div className="small faint">데이터 없음</div>
        )}
      </div>
    </div>
  );
}
