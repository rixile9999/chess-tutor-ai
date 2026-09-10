import type { Key } from 'chessground/types';
import type { Arrow } from '../api/types';
import type { BoardShape } from '../components/Board';

/** A temporary board position played from a line the user clicked, or one the tutor showed. */
export type Preview = { id: string; fen: string; label: string; lastMove: [string, string] | null; shapes: BoardShape[] };

const SQUARE = /^[a-h][1-8]$/;
const BRUSH: Record<Arrow['color'], string> = { good: 'blue', bad: 'red', ink: 'paleGrey' };

/** Review arrows + highlighted squares as chessground shapes (invalid squares are dropped). */
export function arrowShapes(arrows: Arrow[] | undefined, highlights: string[] | undefined): BoardShape[] {
  const out: BoardShape[] = [];
  for (const a of arrows ?? []) {
    if (!SQUARE.test(a.orig) || !SQUARE.test(a.dest)) continue;
    const s: BoardShape = { orig: a.orig as Key, dest: a.dest as Key, brush: BRUSH[a.color] ?? 'paleGrey' };
    if (a.dashed) s.modifiers = { lineWidth: 6 };
    out.push(s);
  }
  for (const sq of highlights ?? []) if (SQUARE.test(sq)) out.push({ orig: sq as Key, brush: 'paleGrey' });
  return out;
}
