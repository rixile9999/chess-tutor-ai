import { Chess } from 'chess.js';
import type { CoachPreset, Color, HintLevel, OpponentSpec, PlayCheckResponse, PlaySource, PracticeMode } from '../../api/types';

export const START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

/** Who is holding the pieces. `manual` = the user plays both sides (analysis board). */
export type Control = 'manual' | 'ai-white' | 'ai-black';
export type AlertMode = 'off' | 'blunder' | 'mistake';
export type PlayResult = '1-0' | '0-1' | '1/2-1/2' | '*';
export type DrawOffer = 'none' | 'pending' | 'declined';

export type Ply = {
  san: string;
  uci: string;
  fen: string;
  by: 'user' | 'ai' | 'book';
  source?: PlaySource | null;
  prob?: number | null;
  /** Highest hint level the user had open when this move was played (null = unaided). */
  hintLevel?: HintLevel | null;
};

export type CoachSettings = { preset: CoachPreset; alerts: AlertMode; takebacks: boolean; hints: boolean };
/** Seconds on the clock at the start and added after each move (M7d). */
export type TimeControl = { initial: number; increment: number };
export type OpeningState = { id: string; name: string; line: string[]; tabiyaFen: string; drill: boolean };
export type DrillDeviation = { expected: string; played: string; fenBefore: string };

export type PlayState = {
  startFen: string;
  plies: Ply[];
  /** 0 = start position, plies.length = the live position. Browsing never changes the game. */
  cursor: number;
  control: Control;
  opponent: OpponentSpec;
  coach: CoachSettings;
  opening: OpeningState | null;
  status: 'playing' | 'over' | 'saved';
  result: PlayResult;
  termination: string | null;
  stats: { hints: number; takebacks: number; alerts: number };
  pendingAlert: PlayCheckResponse | null;
  /** Hint levels already opened at `hintFen`; reset whenever the live position changes. */
  hintsShown: HintLevel[];
  hintFen: string | null;
  /** Set when a drill move left the book; cleared by 다시 두기 / 그대로 진행. */
  drillDeviation: DrillDeviation | null;
  /** True once the user chose 그대로 진행 — the book stops driving the AI. */
  drillLeft: boolean;
  drawOffer: DrawOffer;
  savedGameId: number | null;
  /** Null = 시간 없음. Kept across restarts like the other table settings. */
  timeControl: TimeControl | null;
  /** Milliseconds left per side, null without a time control. The page ticks it down. */
  remaining: { white: number; black: number } | null;
  /** Seconds left for the mover after each ply (index = ply); the save turns these into %clk. */
  clocks: number[] | null;
};

export const PRESETS: Record<CoachPreset, Omit<CoachSettings, 'preset'>> = {
  serious: { alerts: 'off', takebacks: false, hints: false },
  learning: { alerts: 'blunder', takebacks: true, hints: true },
  free: { alerts: 'off', takebacks: true, hints: true },
};
export const PRESET_LABEL: Record<CoachPreset, string> = { serious: '진지하게', learning: '배우면서', free: '자유' };
export const CONTROL_LABEL: Record<Control, string> = { manual: '수동', 'ai-white': 'AI가 백', 'ai-black': 'AI가 흑' };
export const ALERT_LABEL: Record<AlertMode, string> = { off: '끔', blunder: '블런더만', mistake: '실수부터' };

export type PlayAction =
  | { type: 'move'; uci: string; hintLevel?: HintLevel | null }
  | { type: 'truncateAndMove'; uci: string; hintLevel?: HintLevel | null }
  | { type: 'aiMove'; uci?: string | null; san?: string | null; source?: PlaySource | null; prob?: number | null }
  | { type: 'bookMove'; san: string }
  | { type: 'takeback' }
  | { type: 'setCursor'; cursor: number }
  | { type: 'setControl'; control: Control }
  | { type: 'setOpponent'; opponent: OpponentSpec }
  | { type: 'setCoach'; coach: Partial<CoachSettings> }
  | { type: 'setPreset'; preset: CoachPreset }
  | { type: 'resign'; side: Color }
  | { type: 'offerDraw' }
  | { type: 'acceptDraw' }
  | { type: 'declineDraw' }
  | { type: 'restart'; startFen?: string; opening?: OpeningState | null; control?: Control }
  | { type: 'loadOpening'; opening: OpeningState; color: Color }
  | { type: 'drillRetry' }
  | { type: 'drillContinue' }
  | { type: 'alert'; check: PlayCheckResponse }
  | { type: 'dismissAlert' }
  | { type: 'hintShown'; level: HintLevel; fen: string }
  | { type: 'askedTutor' }
  | { type: 'setTimeControl'; timeControl: TimeControl | null }
  | { type: 'tick'; ms: number }
  | { type: 'saved'; gameId: number };

export function validFen(fen: string | null | undefined): string | null {
  if (!fen) return null;
  try { new Chess(fen); return fen; } catch { return null; }
}

export function sideToMoveOf(fen: string): Color {
  return fen.split(' ')[1] === 'b' ? 'black' : 'white';
}

export function moveNumberOf(fen: string): number {
  const n = Number(fen.split(' ')[5]);
  return Number.isFinite(n) && n >= 1 ? n : 1;
}

/** The user's colour, or null in 수동 mode where the user plays both sides. */
export function userColorOf(control: Control): Color | null {
  if (control === 'ai-white') return 'black';
  if (control === 'ai-black') return 'white';
  return null;
}

export function aiColorOf(control: Control): Color | null {
  if (control === 'ai-white') return 'white';
  if (control === 'ai-black') return 'black';
  return null;
}

export function controlForUser(color: Color): Control {
  return color === 'white' ? 'ai-black' : 'ai-white';
}

export function liveFen(s: Pick<PlayState, 'startFen' | 'plies'>): string {
  return s.plies.length ? s.plies[s.plies.length - 1].fen : s.startFen;
}

export function fenAt(s: Pick<PlayState, 'startFen' | 'plies'>, cursor: number): string {
  const i = Math.max(0, Math.min(s.plies.length, cursor));
  return i === 0 ? s.startFen : s.plies[i - 1].fen;
}

/** chess.js instance for the live position, replayed from the start so repetition/50-move see the history. */
export function gameOf(s: Pick<PlayState, 'startFen' | 'plies'>): Chess {
  const c = new Chess(s.startFen);
  for (const p of s.plies) {
    try { c.move(p.san); } catch { break; }
  }
  return c;
}

/** Rules-based end of the game (4.2). null while the game is still running. */
export function detectEnd(c: Chess): { result: PlayResult; termination: string } | null {
  if (!c.isGameOver()) return null;
  if (c.isCheckmate()) return { result: c.turn() === 'w' ? '0-1' : '1-0', termination: '체크메이트' };
  if (c.isStalemate()) return { result: '1/2-1/2', termination: '스테일메이트' };
  if (c.isInsufficientMaterial()) return { result: '1/2-1/2', termination: '기물 부족' };
  if (c.isThreefoldRepetition()) return { result: '1/2-1/2', termination: '3회 동형 반복' };
  if (c.isDrawByFiftyMoves()) return { result: '1/2-1/2', termination: '50수 규칙' };
  return { result: '1/2-1/2', termination: '무승부' };
}

/** "600+0" for the PGN TimeControl header; null when the game had no clocks. */
export function timeControlText(tc: TimeControl | null): string | null {
  return tc ? `${Math.round(tc.initial)}+${Math.round(tc.increment)}` : null;
}

export function startingRemaining(tc: TimeControl | null): { white: number; black: number } | null {
  return tc ? { white: tc.initial * 1000, black: tc.initial * 1000 } : null;
}

/** Both clocks after `n` plies, read back from the recorded per-ply seconds (물리기·잘라내기). */
export function remainingAfter(s: PlayState, n: number): { white: number; black: number } | null {
  const out = startingRemaining(s.timeControl);
  if (!out) return null;
  const clocks = s.clocks ?? [];
  for (let i = 0; i < n && i < clocks.length && i < s.plies.length; i += 1) {
    out[sideToMoveOf(i === 0 ? s.startFen : s.plies[i - 1].fen)] = Math.max(0, clocks[i]) * 1000;
  }
  return out;
}

/** Clock state after a move list was cut back to `n` plies. */
function rewindClocks(s: PlayState, n: number): Pick<PlayState, 'remaining' | 'clocks'> {
  return { remaining: remainingAfter(s, n), clocks: s.clocks ? s.clocks.slice(0, n) : null };
}

export function practiceModeOf(s: Pick<PlayState, 'opening'>): PracticeMode {
  if (!s.opening) return 'free';
  return s.opening.drill ? 'drill' : 'tabiya';
}

/** The book is still driving: a drill that has not been abandoned and has line moves left. */
export function drillActive(s: PlayState): boolean {
  return (
    !!s.opening?.drill && !s.drillLeft && s.status === 'playing'
    && !s.drillDeviation && s.plies.length < s.opening.line.length
  );
}

/** The drill walked the whole line — from here it is an ordinary tabiya game. */
export function tabiyaReached(s: PlayState): boolean {
  return !!s.opening?.drill && !s.drillLeft && s.plies.length >= s.opening.line.length;
}

export function initialState(over: Partial<PlayState> = {}): PlayState {
  const startFen = validFen(over.startFen) ?? START_FEN;
  return {
    plies: [],
    cursor: 0,
    control: 'ai-black',
    opponent: { kind: 'maia', rating: 1500 },
    coach: { preset: 'learning', ...PRESETS.learning },
    opening: null,
    status: 'playing',
    result: '*',
    termination: null,
    stats: { hints: 0, takebacks: 0, alerts: 0 },
    pendingAlert: null,
    hintsShown: [],
    hintFen: null,
    drillDeviation: null,
    drillLeft: false,
    drawOffer: 'none',
    savedGameId: null,
    timeControl: null,
    ...over,
    // A restored payload may carry an unreadable FEN or a cursor past the end of the move list.
    startFen,
    // A restored payload may carry a time control without the clocks that go with it.
    ...(over.timeControl
      ? { remaining: over.remaining ?? startingRemaining(over.timeControl), clocks: over.clocks ?? [] }
      : { remaining: null, clocks: null }),
    ...(over.plies ? { cursor: Math.max(0, Math.min(over.plies.length, over.cursor ?? over.plies.length)) } : null),
  };
}

/** Reset the move list but keep the table settings (상대·코치·오프닝). */
function reset(s: PlayState, over: Partial<PlayState>): PlayState {
  return {
    ...s,
    plies: [],
    cursor: 0,
    status: 'playing',
    result: '*',
    termination: null,
    stats: { hints: 0, takebacks: 0, alerts: 0 },
    pendingAlert: null,
    hintsShown: [],
    hintFen: null,
    drillDeviation: null,
    drillLeft: false,
    drawOffer: 'none',
    savedGameId: null,
    remaining: startingRemaining(s.timeControl),
    clocks: s.timeControl ? [] : null,
    ...over,
  };
}

function playUci(fen: string, uci: string): { san: string; uci: string; fen: string } | null {
  try {
    const c = new Chess(fen);
    const m = c.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci[4] || undefined });
    return { san: m.san, uci: m.from + m.to + (m.promotion ?? ''), fen: c.fen() };
  } catch { return null; }
}

function playSan(fen: string, san: string): { san: string; uci: string; fen: string } | null {
  try {
    const c = new Chess(fen);
    const m = c.move(san);
    return { san: m.san, uci: m.from + m.to + (m.promotion ?? ''), fen: c.fen() };
  } catch { return null; }
}

/** Append one ply, re-run the end detection, and note a drill deviation. */
function push(s: PlayState, plies: Ply[], ply: Ply): PlayState {
  const next: PlayState = {
    ...s,
    plies: [...plies, ply],
    cursor: plies.length + 1,
    drawOffer: 'none',
    hintsShown: [],
    hintFen: null,
  };
  // The mover keeps what is left plus the increment, and that is what the PGN records for this ply.
  if (s.timeControl && s.remaining) {
    const mover = sideToMoveOf(plies.length ? plies[plies.length - 1].fen : s.startFen);
    const left = Math.max(0, s.remaining[mover]) + s.timeControl.increment * 1000;
    const past = (s.clocks ?? []).slice(0, plies.length);
    while (past.length < plies.length) past.push(Math.round(s.timeControl.initial));
    next.remaining = { ...s.remaining, [mover]: left };
    next.clocks = [...past, Math.round(left / 1000)];
  }
  const expected = s.opening && !s.drillLeft && s.opening.drill ? s.opening.line[plies.length] : undefined;
  if (ply.by === 'user' && expected && expected !== ply.san) {
    next.drillDeviation = { expected, played: ply.san, fenBefore: plies.length ? plies[plies.length - 1].fen : s.startFen };
  }
  const end = detectEnd(gameOf(next));
  if (end) return { ...next, status: 'over', result: end.result, termination: end.termination, pendingAlert: null };
  return next;
}

export function playReducer(s: PlayState, a: PlayAction): PlayState {
  switch (a.type) {
    case 'move':
    case 'truncateAndMove': {
      if (s.status !== 'playing' || s.drillDeviation) return s;
      const plies = a.type === 'truncateAndMove' ? s.plies.slice(0, s.cursor) : s.plies;
      if (a.type === 'move' && s.cursor !== s.plies.length) return s;
      const r = playUci(liveFen({ startFen: s.startFen, plies }), a.uci);
      if (!r) return s;
      const base = a.type === 'truncateAndMove' ? { ...s, ...rewindClocks(s, s.cursor) } : s;
      return push(base, plies, { ...r, by: 'user', source: null, prob: null, hintLevel: a.hintLevel ?? null });
    }
    case 'aiMove': {
      if (s.status !== 'playing') return s;
      const fen = liveFen(s);
      const r = (a.uci ? playUci(fen, a.uci) : null) ?? (a.san ? playSan(fen, a.san) : null);
      if (!r) return s;
      return push(s, s.plies, { ...r, by: 'ai', source: a.source ?? null, prob: a.prob ?? null, hintLevel: null });
    }
    case 'bookMove': {
      if (s.status !== 'playing') return s;
      const r = playSan(liveFen(s), a.san);
      if (!r) return s;
      return push(s, s.plies, { ...r, by: 'book', source: 'book', prob: null, hintLevel: null });
    }
    case 'takeback': {
      if (!s.plies.length) return s;
      const user = userColorOf(s.control);
      let plies = s.plies.slice(0, -1);
      // Pop whole exchanges so the user always lands on their own turn (4.2).
      if (user) {
        while (plies.length && sideToMoveOf(liveFen({ startFen: s.startFen, plies })) !== user) plies = plies.slice(0, -1);
      }
      return {
        ...s,
        plies,
        cursor: plies.length,
        status: 'playing',
        result: '*',
        termination: null,
        pendingAlert: null,
        drillDeviation: null,
        drawOffer: 'none',
        hintsShown: [],
        hintFen: null,
        ...rewindClocks(s, plies.length),
        stats: { ...s.stats, takebacks: s.stats.takebacks + 1 },
      };
    }
    case 'setCursor':
      return { ...s, cursor: Math.max(0, Math.min(s.plies.length, a.cursor)) };
    case 'setControl':
      return { ...s, control: a.control };
    case 'setOpponent':
      return { ...s, opponent: a.opponent };
    case 'setCoach':
      return { ...s, coach: { ...s.coach, ...a.coach } };
    case 'setPreset':
      return { ...s, coach: { preset: a.preset, ...PRESETS[a.preset] } };
    case 'resign':
      return { ...s, status: 'over', result: a.side === 'white' ? '0-1' : '1-0', termination: '기권', pendingAlert: null, drawOffer: 'none' };
    case 'offerDraw':
      return s.status === 'playing' ? { ...s, drawOffer: 'pending' } : s;
    case 'acceptDraw':
      return { ...s, status: 'over', result: '1/2-1/2', termination: '합의 무승부', pendingAlert: null, drawOffer: 'none' };
    case 'declineDraw':
      return { ...s, drawOffer: 'declined' };
    case 'restart': {
      const startFen = a.startFen !== undefined ? validFen(a.startFen) ?? s.startFen : s.startFen;
      const opening = a.opening !== undefined ? a.opening : s.opening;
      return reset(s, { startFen, opening, control: a.control ?? s.control });
    }
    case 'loadOpening': {
      const startFen = a.opening.drill ? START_FEN : validFen(a.opening.tabiyaFen) ?? START_FEN;
      return reset(s, { startFen, opening: a.opening, control: controlForUser(a.color) });
    }
    case 'drillRetry': {
      if (!s.drillDeviation) return s;
      const plies = s.plies.slice(0, -1);
      return {
        ...s, plies, cursor: plies.length, drillDeviation: null, ...rewindClocks(s, plies.length),
        status: 'playing', result: '*', termination: null, pendingAlert: null, hintsShown: [], hintFen: null,
      };
    }
    case 'drillContinue':
      return { ...s, drillDeviation: null, drillLeft: true };
    case 'alert':
      return { ...s, pendingAlert: a.check, stats: { ...s.stats, alerts: s.stats.alerts + 1 } };
    case 'dismissAlert':
      return { ...s, pendingAlert: null };
    case 'hintShown': {
      const same = s.hintFen === a.fen;
      if (same && s.hintsShown.includes(a.level)) return s;
      return {
        ...s,
        hintFen: a.fen,
        hintsShown: same ? [...s.hintsShown, a.level] : [a.level],
        stats: { ...s.stats, hints: s.stats.hints + 1 },
      };
    }
    case 'askedTutor':
      // A live question is help, so it counts like a hint in the header and the profile (4.5).
      return { ...s, stats: { ...s.stats, hints: s.stats.hints + 1 } };
    case 'setTimeControl':
      // Moves played before the clock existed keep the full time, so index = ply stays true.
      return {
        ...s,
        timeControl: a.timeControl,
        remaining: startingRemaining(a.timeControl),
        clocks: a.timeControl ? s.plies.map(() => Math.round(a.timeControl!.initial)) : null,
      };
    case 'tick': {
      if (s.status !== 'playing' || !s.timeControl || !s.remaining) return s;
      const side = sideToMoveOf(liveFen(s));
      const left = s.remaining[side] - Math.max(0, a.ms);
      if (left > 0) return { ...s, remaining: { ...s.remaining, [side]: left } };
      // The AI's clock is decorative (docs §10): it stops at zero and the game goes on.
      const user = userColorOf(s.control);
      if (user !== null && side !== user) return s.remaining[side] === 0 ? s : { ...s, remaining: { ...s.remaining, [side]: 0 } };
      return {
        ...s,
        remaining: { ...s.remaining, [side]: 0 },
        status: 'over',
        result: side === 'white' ? '0-1' : '1-0',
        termination: '시간 초과',
        pendingAlert: null,
        drawOffer: 'none',
      };
    }
    case 'saved':
      return { ...s, status: 'saved', savedGameId: a.gameId };
    default:
      return s;
  }
}
