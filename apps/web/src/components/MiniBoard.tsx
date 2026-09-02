import { useMemo } from 'react';
import './miniboard.css';
import './pieces.css';

const PIECE_GLYPH: Record<string, string> = {
  K: 'wK', Q: 'wQ', R: 'wR', B: 'wB', N: 'wN', P: 'wP',
  k: 'bK', q: 'bQ', r: 'bR', b: 'bB', n: 'bN', p: 'bP',
};

type Props = { fen: string; size?: number; orientation?: 'white' | 'black'; highlight?: string[] };

/** Static board thumbnail (no chessground instance). Uses the cburnett piece sprites via CSS classes. */
export function MiniBoard({ fen, size = 84, orientation = 'white', highlight = [] }: Props) {
  const squares = useMemo(() => {
    const rows = fen.split(' ')[0].split('/');
    const out: { sq: string; piece: string | null; light: boolean }[] = [];
    const ranks = orientation === 'white' ? [7, 6, 5, 4, 3, 2, 1, 0] : [0, 1, 2, 3, 4, 5, 6, 7];
    const files = orientation === 'white' ? [0, 1, 2, 3, 4, 5, 6, 7] : [7, 6, 5, 4, 3, 2, 1, 0];
    const grid: (string | null)[][] = rows.map((r) => {
      const cells: (string | null)[] = [];
      for (const ch of r) {
        if (/\d/.test(ch)) cells.push(...Array(Number(ch)).fill(null));
        else cells.push(ch);
      }
      return cells;
    });
    for (const rank of ranks) for (const file of files) {
      const piece = grid[7 - rank]?.[file] ?? null;
      out.push({ sq: 'abcdefgh'[file] + (rank + 1), piece, light: (file + rank) % 2 === 1 });
    }
    return out;
  }, [fen, orientation]);
  const cell = size / 8;
  return (
    <div className="miniboard" style={{ width: size, height: size, gridTemplateColumns: `repeat(8, ${cell}px)` }}>
      {squares.map(({ sq, piece, light }) => (
        <div key={sq} className={`mb-sq ${light ? 'light' : 'dark'}${highlight.includes(sq) ? ' hl' : ''}`} style={{ width: cell, height: cell }}>
          {piece && <span className={`mb-piece ${PIECE_GLYPH[piece]}`} />}
        </div>
      ))}
    </div>
  );
}
