// Pure state behind 국면 탐색: the line being played, the cursor, and the journal that is never
// spliced (요구 3). No React and no fetching here — index.tsx runs the effects (§6.1, §9.3).
import type { MoveAnnotation } from '../../api/types';
import { applyUci, sanToUci } from '../../lib/chess';
import { plyLabel } from '../../lib/labels';

export const START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

export type ExplainDepth = 'brief' | 'normal' | 'deep';
export type CandidateSort = 'mine' | 'master' | 'name';

export interface LinePly {
  san: string; uci: string;
  /** Position after the move. */
  fen: string;
  /** "3.Bb5" / "3…a6". */
  label: string;
  annotation: MoveAnnotation | null;
}

/**
 * The journal only grows. A move that replaces another adds a `rewind` note first, a strip jump adds
 * a `jump` note, and the entries left behind stay readable (they just stop being on the line).
 */
export type JournalEntry =
  | {
      kind: 'move';
      ply: number;
      /** SAN from the start position up to and including this move — a rewound entry still points at its own note. */
      seq: string[];
      annotation: MoveAnnotation | null;
      pending: boolean;
      error: string | null;
    }
  | { kind: 'rewind'; toPly: number; instead: string; was: string }
  /** SAN of the whole line the strip jumped to; empty means 새 수순. */
  | { kind: 'jump'; sans: string[] };

/** A line shown on the board without touching the played line (함정 수순 미리보기). */
export interface LinePreview { key: string; fen: string; title: string; lastMove: [string, string] | null }

export interface LineState {
  startFen: string;
  plies: LinePly[];
  /** 0 = start position, plies.length = the end of the line. */
  cursor: number;
  journal: JournalEntry[];
  /** The ply the explanation panel is showing; 0 = the start position. */
  focusPly: number;
  depth: ExplainDepth;
  preview: LinePreview | null;
  sort: CandidateSort;
}

export type LineAction =
  | { type: 'play'; uci: string }
  | { type: 'goto'; cursor: number }
  | { type: 'jumpTo'; sans: string[] }
  | { type: 'annotating'; sans: string[] }
  | { type: 'annotated'; sans: string[]; annotations: MoveAnnotation[] }
  | { type: 'annotateFailed'; sans: string[]; message: string }
  | { type: 'focus'; ply: number }
  | { type: 'setDepth'; depth: ExplainDepth }
  | { type: 'setSort'; sort: CandidateSort }
  | { type: 'preview'; preview: LinePreview | null }
  | { type: 'reset' };

export const moveLabel = (ply: number, san: string): string => `${plyLabel(ply)}${san}`;
export const seqKey = (sans: string[]): string => sans.join(' ');

export function initialLine(startFen: string = START_FEN, depth: ExplainDepth = 'normal'): LineState {
  return { startFen, plies: [], cursor: 0, journal: [], focusPly: 0, depth, preview: null, sort: 'mine' };
}

export function fenAt(s: Pick<LineState, 'startFen' | 'plies'>, cursor: number): string {
  const i = Math.max(0, Math.min(s.plies.length, cursor));
  return i === 0 ? s.startFen : s.plies[i - 1].fen;
}

/** SAN of the first `ply` moves — the request body for /openings/annotate and the URL's `moves`. */
export function sansTo(s: Pick<LineState, 'plies'>, ply: number): string[] {
  return s.plies.slice(0, Math.max(0, Math.min(s.plies.length, ply))).map((p) => p.san);
}

/** Replay SAN from a position; stops at the first illegal move rather than throwing. */
export function pliesFrom(startFen: string, sans: string[]): LinePly[] {
  const out: LinePly[] = [];
  let fen = startFen;
  for (const san of sans) {
    const uci = sanToUci(fen, san);
    if (!uci) break;
    const r = applyUci(fen, uci);
    if (!r) break;
    out.push({ san: r.san, uci, fen: r.fen, label: moveLabel(out.length + 1, r.san), annotation: null });
    fen = r.fen;
  }
  return out;
}

/** True while this entry's move is still the move played at that ply on the current line. */
export function onLine(s: Pick<LineState, 'plies'>, e: JournalEntry): boolean {
  if (e.kind !== 'move') return false;
  if (e.ply < 1 || e.ply > s.plies.length) return false;
  return seqKey(e.seq) === seqKey(s.plies.slice(0, e.ply).map((p) => p.san));
}

/** The last ply whose move the book still knew, or 0 for the start position. */
export function lastBookPly(s: Pick<LineState, 'plies'>): number {
  for (let i = s.plies.length - 1; i >= 0; i -= 1) if (s.plies[i].annotation?.in_book) return i + 1;
  return 0;
}

/** Annotations keyed by the SAN prefix they belong to, so a rewound entry never takes another's note. */
function byPrefix(sans: string[], annotations: MoveAnnotation[]): Map<string, MoveAnnotation> {
  const out = new Map<string, MoveAnnotation>();
  annotations.forEach((a, i) => { if (i < sans.length) out.set(seqKey(sans.slice(0, i + 1)), a); });
  return out;
}

function withAnnotations(s: LineState, hits: Map<string, MoveAnnotation>): LineState {
  const plies = s.plies.map((p, i) => {
    const a = hits.get(seqKey(s.plies.slice(0, i + 1).map((x) => x.san)));
    return a ? { ...p, annotation: a } : p;
  });
  const journal = s.journal.map((e) => {
    if (e.kind !== 'move') return e;
    const a = hits.get(seqKey(e.seq));
    return a ? { ...e, annotation: a, pending: false, error: null } : e;
  });
  return { ...s, plies, journal };
}

export function lineReducer(s: LineState, a: LineAction): LineState {
  switch (a.type) {
    case 'play': {
      const base = s.plies.slice(0, s.cursor);
      const r = applyUci(fenAt(s, s.cursor), a.uci);
      if (!r) return s;
      const ply = s.cursor + 1;
      const journal = [...s.journal];
      // Going back and playing something else cuts the line but only adds to the journal (요구 3).
      if (s.cursor < s.plies.length) {
        journal.push({ kind: 'rewind', toPly: s.cursor, was: s.plies[s.cursor].label, instead: moveLabel(ply, r.san) });
      }
      const plies = [...base, { san: r.san, uci: a.uci, fen: r.fen, label: moveLabel(ply, r.san), annotation: null }];
      journal.push({ kind: 'move', ply, seq: plies.map((p) => p.san), annotation: null, pending: true, error: null });
      return { ...s, plies, cursor: plies.length, journal, focusPly: ply, preview: null };
    }
    case 'goto': {
      const cursor = Math.max(0, Math.min(s.plies.length, a.cursor));
      return { ...s, cursor, focusPly: cursor, preview: null };
    }
    case 'jumpTo': {
      const plies = pliesFrom(s.startFen, a.sans);
      const journal: JournalEntry[] = [...s.journal, { kind: 'jump', sans: plies.map((p) => p.san) }];
      plies.forEach((_, i) => journal.push({
        kind: 'move', ply: i + 1, seq: plies.slice(0, i + 1).map((p) => p.san), annotation: null, pending: true, error: null,
      }));
      return { ...s, plies, cursor: plies.length, journal, focusPly: plies.length, preview: null };
    }
    case 'annotating': {
      const wanted = new Set(a.sans.map((_, i) => seqKey(a.sans.slice(0, i + 1))));
      return {
        ...s,
        journal: s.journal.map((e) =>
          e.kind === 'move' && !e.annotation && wanted.has(seqKey(e.seq)) ? { ...e, pending: true, error: null } : e),
      };
    }
    case 'annotated':
      return withAnnotations(s, byPrefix(a.sans, a.annotations));
    case 'annotateFailed': {
      const wanted = new Set(a.sans.map((_, i) => seqKey(a.sans.slice(0, i + 1))));
      return {
        ...s,
        journal: s.journal.map((e) =>
          e.kind === 'move' && e.pending && wanted.has(seqKey(e.seq)) ? { ...e, pending: false, error: a.message } : e),
      };
    }
    case 'focus':
      return { ...s, focusPly: Math.max(0, Math.min(s.plies.length, a.ply)) };
    case 'setDepth':
      return { ...s, depth: a.depth };
    case 'setSort':
      return { ...s, sort: a.sort };
    case 'preview':
      return { ...s, preview: a.preview };
    case 'reset':
      // 새 수순: the line starts over, the journal keeps everything that came before.
      return {
        ...s,
        plies: [], cursor: 0, focusPly: 0, preview: null,
        journal: s.plies.length ? [...s.journal, { kind: 'jump', sans: [] }] : s.journal,
      };
    default:
      return s;
  }
}
