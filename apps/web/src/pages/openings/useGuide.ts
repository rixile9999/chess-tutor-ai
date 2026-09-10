import type { Color, PositionGuide } from '../../api/types';
import { api } from '../../api/client';
import { useQuery } from './useQuery';

/** FEN without the move counters — the same key the server and the map DAG use for a position. */
export const positionKey = (fen: string): string => fen.trim().split(/\s+/).slice(0, 4).join(' ');

// Walking back and forth over a line asks for the same positions again; the guide is stateless so
// one process-wide cache per (position, colour, masters) is enough.
const cache = new Map<string, PositionGuide>();

export function useGuide(fen: string | null, color: Color, masters: boolean) {
  const key = fen ? `${positionKey(fen)}|${color}|${masters ? 1 : 0}` : '';
  return useQuery<PositionGuide>(() => {
    if (!fen) return null;
    const hit = cache.get(key);
    if (hit) return Promise.resolve(hit);
    return api.openings.position(fen, color, masters).then((data) => {
      cache.set(key, data);
      return data;
    });
  }, [key]);
}
