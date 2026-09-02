import { describe, expect, it } from 'vitest';
import { plyOfFen } from '../src/lib/chess';
import { plyLabel, whiteShare } from '../src/lib/labels';
import { labelOnWhite } from '../src/pages/review/EvalBar';

const START = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

describe('plyOfFen', () => {
  it('is null for the starting position (no move produced it)', () => {
    expect(plyOfFen(START)).toBeNull();
  });

  it('reads the ply of the move that produced the position', () => {
    // after 1. e4 it is Black to move on move 1 -> ply 1 was just played.
    expect(plyOfFen('rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1')).toBe(1);
    // after 1... c5 it is White to move on move 2 -> ply 2 was just played.
    expect(plyOfFen('rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2')).toBe(2);
    expect(plyOfFen('8/8/8/8/8/8/8/K6k b - - 0 21')).toBe(41);
  });

  it('gives a move number the comparison header can print', () => {
    expect(plyLabel(plyOfFen('rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1')!)).toBe('1.');
    expect(plyLabel(plyOfFen('rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2')!)).toBe('1…');
  });

  it('is null for junk rather than printing a bogus move number', () => {
    expect(plyOfFen('')).toBeNull();
    expect(plyOfFen('not a fen')).toBeNull();
    expect(plyOfFen('8/8/8/8/8/8/8/K6k x - - 0 3')).toBeNull();
    expect(plyOfFen('8/8/8/8/8/8/8/K6k w - - 0 0')).toBeNull();
  });
});

describe('eval bar label contrast', () => {
  const bigWhite = whiteShare({ cp: 900, mate: null });
  const bigBlack = whiteShare({ cp: -900, mate: null });

  it('reads the white strip when white sits at the top', () => {
    expect(labelOnWhite(0.5, true)).toBe(true);
    expect(labelOnWhite(bigWhite, true)).toBe(true);
  });

  it('reads the dark strip when white sits at the bottom and is not winning', () => {
    expect(labelOnWhite(0.5, false)).toBe(false);
    expect(labelOnWhite(bigBlack, false)).toBe(false);
  });

  it('flips to dark ink when a winning white strip reaches the top from below', () => {
    expect(labelOnWhite(bigWhite, false)).toBe(true);
    expect(labelOnWhite(whiteShare({ cp: null, mate: 3 }), false)).toBe(true);
  });

  it('flips to light ink when a tiny white strip at the top leaves it on dark', () => {
    expect(labelOnWhite(bigBlack, true)).toBe(false);
    expect(labelOnWhite(whiteShare({ cp: null, mate: -3 }), true)).toBe(false);
  });
});
