import { describe, expect, it } from 'vitest';
import type { MoveAnnotation } from '../src/api/types';
import {
  START_FEN, fenAt, initialLine, lastBookPly, lineReducer, onLine, pliesFrom, sansTo,
  type LineAction, type LineState,
} from '../src/pages/openings/line';

const run = (state: LineState, ...actions: LineAction[]) => actions.reduce(lineReducer, state);
const fresh = () => initialLine();
const sansOf = (s: LineState) => s.plies.map((p) => p.san);
const moves = (s: LineState) => s.journal.filter((e) => e.kind === 'move');

/** Only the fields the reducer and the page read. */
function annotation(ply: number, san: string, inBook = true): MoveAnnotation {
  return {
    ply, label: `${ply}.${san}`, san, uci: '', fen_before: '', fen_after: '',
    in_book: inBook, name_before: null, name_after: null, name_after_en: null, transposition: false,
    book_alternatives: [], facts: [], text: `${san} 해설`, engine: null, naturalness: null,
    verified: true, verified_claims: 0, total_claims: 0,
  };
}

describe('line reducer: playing moves', () => {
  it('appends a ply, moves the cursor and queues a pending journal entry with its own SAN path', () => {
    const s = run(fresh(), { type: 'play', uci: 'e2e4' }, { type: 'play', uci: 'e7e5' });
    expect(sansOf(s)).toEqual(['e4', 'e5']);
    expect(s.cursor).toBe(2);
    expect(s.focusPly).toBe(2);
    expect(moves(s).map((e) => e.seq)).toEqual([['e4'], ['e4', 'e5']]);
    expect(moves(s).every((e) => e.pending)).toBe(true);
  });

  it('ignores an illegal move', () => {
    const s = run(fresh(), { type: 'play', uci: 'e2e5' });
    expect(s.plies).toEqual([]);
    expect(s.journal).toEqual([]);
  });

  it('keeps the promotion piece in the uci it stores', () => {
    const start = initialLine('k7/4P3/8/8/8/8/8/4K3 w - - 0 1');
    const s = run(start, { type: 'play', uci: 'e7e8n' });
    expect(s.plies[0].uci).toBe('e7e8n');
    expect(s.plies[0].san).toBe('e8=N');
  });
});

describe('line reducer: going back never rewrites the journal (요구 3)', () => {
  const played = run(
    fresh(),
    { type: 'play', uci: 'e2e4' },
    { type: 'play', uci: 'e7e5' },
    { type: 'play', uci: 'g1f3' },
  );

  it('cuts the line but adds a rewind note and keeps the entry it replaced', () => {
    const s = run(played, { type: 'goto', cursor: 2 }, { type: 'play', uci: 'b1c3' });
    expect(sansOf(s)).toEqual(['e4', 'e5', 'Nc3']);
    expect(s.journal.length).toBe(played.journal.length + 2);
    const rewind = s.journal.find((e) => e.kind === 'rewind');
    expect(rewind).toEqual({ kind: 'rewind', toPly: 2, was: '2.Nf3', instead: '2.Nc3' });
    // The Nf3 entry is still there, in the order it was played.
    expect(moves(s).map((e) => e.seq[e.seq.length - 1])).toEqual(['e4', 'e5', 'Nf3', 'Nc3']);
  });

  it('marks the entries that are no longer on the line', () => {
    const s = run(played, { type: 'goto', cursor: 2 }, { type: 'play', uci: 'b1c3' });
    const flags = moves(s).map((e) => onLine(s, e));
    expect(flags).toEqual([true, true, false, true]);
  });

  it('playing at the end of the line adds no rewind note', () => {
    expect(played.journal.some((e) => e.kind === 'rewind')).toBe(false);
  });
});

describe('line reducer: annotations land by SAN path', () => {
  it('fills the plies and the matching journal entries', () => {
    const played = run(fresh(), { type: 'play', uci: 'e2e4' }, { type: 'play', uci: 'e7e5' });
    const s = lineReducer(played, {
      type: 'annotated',
      sans: ['e4', 'e5'],
      annotations: [annotation(1, 'e4'), annotation(2, 'e5')],
    });
    expect(s.plies.map((p) => p.annotation?.san)).toEqual(['e4', 'e5']);
    expect(moves(s).every((e) => !e.pending && e.annotation)).toBe(true);
  });

  it('never gives an entry left behind by a rewind another move’s note', () => {
    const s = run(
      fresh(),
      { type: 'play', uci: 'e2e4' },
      { type: 'play', uci: 'e7e5' },
      { type: 'goto', cursor: 1 },
      { type: 'play', uci: 'g8f6' },
      { type: 'annotated', sans: ['e4', 'Nf6'], annotations: [annotation(1, 'e4'), annotation(2, 'Nf6')] },
    );
    const bySan = new Map(moves(s).map((e) => [e.seq.join(' '), e.annotation?.san ?? null]));
    expect(bySan.get('e4 e5')).toBeNull();
    expect(bySan.get('e4 Nf6')).toBe('Nf6');
  });

  it('reports a failure on the entries that were waiting, and clears it on the retry', () => {
    const played = run(fresh(), { type: 'play', uci: 'e2e4' });
    const failed = lineReducer(played, { type: 'annotateFailed', sans: ['e4'], message: '서버 오류' });
    expect(moves(failed)[0]).toMatchObject({ pending: false, error: '서버 오류' });
    const retry = lineReducer(failed, { type: 'annotating', sans: ['e4'] });
    expect(retry.journal.filter((e) => e.kind === 'move')[0]).toMatchObject({ pending: true, error: null });
  });
});

describe('line reducer: jumps and reset', () => {
  it('replaces the line with the strip path and logs one jump plus one entry per ply', () => {
    const s = lineReducer(fresh(), { type: 'jumpTo', sans: ['e4', 'e5', 'Nf3'] });
    expect(sansOf(s)).toEqual(['e4', 'e5', 'Nf3']);
    expect(s.cursor).toBe(3);
    expect(s.journal[0]).toEqual({ kind: 'jump', sans: ['e4', 'e5', 'Nf3'] });
    expect(moves(s)).toHaveLength(3);
  });

  it('stops at the first illegal SAN instead of throwing', () => {
    const s = lineReducer(fresh(), { type: 'jumpTo', sans: ['e4', 'zz9', 'Nf3'] });
    expect(sansOf(s)).toEqual(['e4']);
  });

  it('starts a new line without dropping what is already in the journal', () => {
    const played = run(fresh(), { type: 'play', uci: 'e2e4' }, { type: 'play', uci: 'e7e5' });
    const s = lineReducer(played, { type: 'reset' });
    expect(s.plies).toEqual([]);
    expect(s.cursor).toBe(0);
    expect(moves(s)).toHaveLength(2);
    expect(s.journal[s.journal.length - 1]).toEqual({ kind: 'jump', sans: [] });
  });

  it('a preview never touches the line', () => {
    const played = run(fresh(), { type: 'play', uci: 'e2e4' });
    const s = lineReducer(played, { type: 'preview', preview: { key: '0:1', fen: START_FEN, title: '함정', lastMove: null } });
    expect(sansOf(s)).toEqual(['e4']);
    expect(s.preview?.title).toBe('함정');
    expect(lineReducer(s, { type: 'goto', cursor: 1 }).preview).toBeNull();
  });
});

describe('line helpers', () => {
  it('reads the position at a cursor and the SAN list up to a ply', () => {
    const s = run(fresh(), { type: 'play', uci: 'e2e4' }, { type: 'play', uci: 'e7e5' });
    expect(fenAt(s, 0)).toBe(START_FEN);
    expect(fenAt(s, 1)).toBe(s.plies[0].fen);
    expect(fenAt(s, 99)).toBe(s.plies[1].fen);
    expect(sansTo(s, 1)).toEqual(['e4']);
    expect(sansTo(s, 99)).toEqual(['e4', 'e5']);
  });

  it('labels plies the way the book does: 1.e4, 1…e5', () => {
    const plies = pliesFrom(START_FEN, ['e4', 'e5', 'Nf3']);
    expect(plies.map((p) => p.label)).toEqual(['1.e4', '1…e5', '2.Nf3']);
  });

  it('finds the last ply the book still knew', () => {
    const played = run(fresh(), { type: 'play', uci: 'e2e4' }, { type: 'play', uci: 'e7e5' });
    const s = lineReducer(played, {
      type: 'annotated',
      sans: ['e4', 'e5'],
      annotations: [annotation(1, 'e4'), annotation(2, 'e5', false)],
    });
    expect(lastBookPly(s)).toBe(1);
    expect(lastBookPly(fresh())).toBe(0);
  });
});
