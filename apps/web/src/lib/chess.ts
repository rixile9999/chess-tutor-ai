import { Chess, type Square } from 'chess.js';
import type { Key } from 'chessground/types';

/** Play SAN moves from a FEN; returns the resulting FEN or null when a move is illegal. */
export function playSans(fen: string, sans: string[]): string | null {
  const c = new Chess(fen);
  for (const san of sans) {
    try { c.move(san); } catch { return null; }
  }
  return c.fen();
}

/** FEN after each SAN move (index 0 = the starting fen). */
export function fenTrail(fen: string, sans: string[]): string[] {
  const c = new Chess(fen);
  const out = [c.fen()];
  for (const san of sans) {
    try { c.move(san); out.push(c.fen()); } catch { break; }
  }
  return out;
}

export function sanToUci(fen: string, san: string): string | null {
  try { const m = new Chess(fen).move(san); return m.from + m.to + (m.promotion ?? ''); } catch { return null; }
}

export function uciToSan(fen: string, uci: string): string | null {
  try {
    const m = new Chess(fen).move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci[4] });
    return m.san;
  } catch { return null; }
}

/** Legal destinations map for chessground's `movable.dests`. */
export function legalDests(fen: string): Map<Key, Key[]> {
  const c = new Chess(fen);
  const dests = new Map<Key, Key[]>();
  for (const m of c.moves({ verbose: true })) {
    const from = m.from as Key;
    dests.set(from, [...(dests.get(from) ?? []), m.to as Key]);
  }
  return dests;
}

export function sideToMove(fen: string): 'white' | 'black' {
  return fen.split(' ')[1] === 'b' ? 'black' : 'white';
}

export function applyUci(fen: string, uci: string): { fen: string; san: string } | null {
  try {
    const c = new Chess(fen);
    const m = c.move({ from: uci.slice(0, 2) as Square, to: uci.slice(2, 4) as Square, promotion: uci[4] });
    return { fen: c.fen(), san: m.san };
  } catch { return null; }
}
