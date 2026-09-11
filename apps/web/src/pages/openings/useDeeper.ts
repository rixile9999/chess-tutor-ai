// 더 깊이 (M8d-4, plan §10.4): the two fetches behind DeeperPanel, plus the page's play action.
import { useEffect } from 'react';
import { api } from '../../api/client';
import type { DeeperLines, MasterStats } from '../../api/types';
import { useQuery } from './useQuery';

export const DEEPER_DEPTH = 12;
export const DEEPER_MULTIPV = 3;

/**
 * Engine lines for a position — only once `open` is true. The section is closed by default, so
 * opening the panel costs nothing; the engine runs because the user asked for it (§10.4).
 */
export function useDeeperLines(fen: string | null, open: boolean) {
  return useQuery<DeeperLines>(
    () => (open && fen ? api.openings.lines(fen, DEEPER_DEPTH, DEEPER_MULTIPV) : null),
    [fen, open],
  );
}

/** Master statistics for the same position, under the same "only when opened" rule. */
export function useDeeperMasters(fen: string | null, open: boolean) {
  return useQuery<MasterStats>(() => (open && fen ? api.openings.masters(fen) : null), [fen, open]);
}

// ---------- the page's play action ----------

let handler: ((uci: string) => void) | null = null;

/**
 * The panel is mounted deep inside ExplainPanel, which knows nothing about playing moves, so
 * the page registers its play action here instead of threading a prop through every component
 * in between. One page owns the board, so one handler is enough.
 */
export function useDeeperPlay(onPlay: (uci: string) => void): void {
  useEffect(() => {
    handler = onPlay;
    return () => { if (handler === onPlay) handler = null; };
  }, [onPlay]);
}

/** Play a move from the panel ([이 수 두기] / [두기]); a no-op when no page has registered. */
export function playFromPanel(uci: string): void {
  handler?.(uci);
}
