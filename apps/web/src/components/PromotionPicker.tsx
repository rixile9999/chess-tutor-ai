import { useEffect } from 'react';
import './pieces.css';
import './promotion.css';

export type PromotionPiece = 'q' | 'n' | 'r' | 'b';

const ORDER: PromotionPiece[] = ['q', 'n', 'r', 'b'];
const GLYPH: Record<PromotionPiece, string> = { q: 'Q', n: 'N', r: 'R', b: 'B' };
const NAME: Record<PromotionPiece, string> = { q: '퀸', n: '나이트', r: '룩', b: '비숍' };

type Props = {
  color: 'white' | 'black';
  orientation: 'white' | 'black';
  /** Destination square of the promoting move, e.g. "e8". */
  square: string;
  /** Board edge length in px — the picker is placed on the same 8x8 grid. */
  size?: number;
  onPick: (piece: PromotionPiece) => void;
  onCancel: () => void;
};

/**
 * Popover over the board with the four promotion pieces in the mover's colour.
 * Keys 1-4 pick, Esc cancels. Generic on purpose: 대국·퍼즐 both mount it.
 */
export function PromotionPicker({ color, orientation, square, size = 520, onPick, onCancel }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); onCancel(); return; }
      const n = Number(e.key);
      if (Number.isInteger(n) && n >= 1 && n <= ORDER.length) { e.preventDefault(); onPick(ORDER[n - 1]); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onPick, onCancel]);

  const file = square.charCodeAt(0) - 97;
  const rank = Number(square[1]) - 1;
  const col = orientation === 'white' ? file : 7 - file;
  const row = orientation === 'white' ? 7 - rank : rank;
  const cell = size / 8;
  // Four cells fit downwards from the top half of the board, upwards from the bottom half.
  const down = row <= 3;
  const top = down ? row * cell : (row - 3) * cell;

  return (
    <div className="pp-veil" onClick={onCancel} role="presentation">
      <div
        className="pp-col"
        style={{ left: col * cell, top, width: cell, height: cell * 4 }}
        onClick={(e) => e.stopPropagation()}
        role="menu"
        aria-label="승급할 기물"
      >
        {(down ? ORDER : [...ORDER].reverse()).map((p) => (
          <button
            key={p}
            type="button"
            role="menuitem"
            className="pp-cell"
            style={{ height: cell }}
            title={`${NAME[p]} (${ORDER.indexOf(p) + 1})`}
            onClick={() => onPick(p)}
          >
            <span className={`pp-piece ${color === 'white' ? 'w' : 'b'}${GLYPH[p]}`} />
            <span className="pp-key">{ORDER.indexOf(p) + 1}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
