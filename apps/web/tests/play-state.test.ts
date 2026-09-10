import { describe, expect, it } from 'vitest';
import { buildPgn, movetext } from '../src/pages/play/pgn';
import { moveRows } from '../src/pages/play/MoveList';
import { formatClock } from '../src/pages/play/Clock';
import { matchesQuery } from '../src/pages/play/OpeningPicker';
import * as S from '../src/pages/play/state';

/** Apply a list of actions to a starting state, so a test reads like a game. */
function run(state: S.PlayState, ...actions: S.PlayAction[]): S.PlayState {
  return actions.reduce(S.playReducer, state);
}
const manual = (over: Partial<S.PlayState> = {}) => S.initialState({ control: 'manual', ...over });
const move = (uci: string): S.PlayAction => ({ type: 'move', uci });

describe('moves', () => {
  it('turns a uci into san, fen and a cursor at the end', () => {
    const s = run(manual(), move('e2e4'), move('e7e5'));
    expect(s.plies.map((p) => p.san)).toEqual(['e4', 'e5']);
    expect(s.cursor).toBe(2);
    expect(S.sideToMoveOf(S.liveFen(s))).toBe('white');
    expect(s.plies[0].by).toBe('user');
  });

  it('ignores an illegal move instead of throwing', () => {
    const s = manual();
    expect(S.playReducer(s, move('e2e5'))).toBe(s);
  });

  it('records the AI reply and the book move with their own source', () => {
    const s = run(
      S.initialState({ control: 'ai-black' }),
      move('e2e4'),
      { type: 'aiMove', uci: 'e7e5', source: 'maia', prob: 0.41 },
    );
    expect(s.plies[1]).toMatchObject({ san: 'e5', by: 'ai', source: 'maia', prob: 0.41 });
    const book = S.playReducer(s, { type: 'bookMove', san: 'Nf3' });
    expect(book.plies[2]).toMatchObject({ san: 'Nf3', by: 'book', source: 'book' });
  });

  it('promotes with the piece carried in the uci', () => {
    const fen = '8/P7/8/8/8/8/8/K6k w - - 0 1';
    expect(S.playReducer(manual({ startFen: fen }), move('a7a8q')).plies[0].san).toBe('a8=Q+');
    expect(S.playReducer(manual({ startFen: fen }), move('a7a8n')).plies[0].san).toBe('a8=N');
  });
});

describe('takeback', () => {
  it('pops back to the user turn and counts', () => {
    const s = run(
      S.initialState({ control: 'ai-black' }),
      move('e2e4'),
      { type: 'aiMove', uci: 'e7e5' },
      { type: 'takeback' },
    );
    expect(s.plies).toEqual([]);
    expect(s.cursor).toBe(0);
    expect(s.stats.takebacks).toBe(1);
  });

  it('pops a single ply in 수동 mode, where every turn is the user turn', () => {
    const s = run(manual(), move('e2e4'), move('e7e5'), { type: 'takeback' });
    expect(s.plies.map((p) => p.san)).toEqual(['e4']);
  });

  it('takes back the user move alone when the AI has not answered yet', () => {
    const s = run(S.initialState({ control: 'ai-black' }), move('e2e4'), { type: 'takeback' });
    expect(s.plies).toEqual([]);
  });

  it('reopens a finished game (물리기 from a blunder alert on the mating move)', () => {
    const mate = run(manual(), move('f2f3'), move('e7e5'), move('g2g4'), move('d8h4'));
    expect(mate.status).toBe('over');
    const back = S.playReducer(mate, { type: 'takeback' });
    expect(back.status).toBe('playing');
    expect(back.result).toBe('*');
  });
});

describe('cursor', () => {
  it('stays inside the move list', () => {
    const s = run(manual(), move('e2e4'), move('e7e5'));
    expect(S.playReducer(s, { type: 'setCursor', cursor: 9 }).cursor).toBe(2);
    expect(S.playReducer(s, { type: 'setCursor', cursor: -3 }).cursor).toBe(0);
  });

  it('refuses a plain move while browsing, and truncates when the user confirms', () => {
    const browsing = run(manual(), move('e2e4'), move('e7e5'), move('g1f3'), { type: 'setCursor', cursor: 1 });
    expect(S.playReducer(browsing, move('c7c5'))).toBe(browsing);
    const cut = S.playReducer(browsing, { type: 'truncateAndMove', uci: 'c7c5' });
    expect(cut.plies.map((p) => p.san)).toEqual(['e4', 'c5']);
    expect(cut.cursor).toBe(2);
  });
});

describe('control', () => {
  it('switches sides mid-game and changes who the takeback pops back to', () => {
    const s = run(S.initialState({ control: 'ai-black' }), move('e2e4'), { type: 'aiMove', uci: 'e7e5' });
    expect(S.userColorOf(s.control)).toBe('white');
    const flipped = S.playReducer(s, { type: 'setControl', control: 'ai-white' });
    expect(S.userColorOf(flipped.control)).toBe('black');
    expect(S.aiColorOf(flipped.control)).toBe('white');
    // The user is black now, so a takeback stops after popping White's move only.
    expect(S.playReducer(flipped, { type: 'takeback' }).plies.map((p) => p.san)).toEqual(['e4']);
  });

  it('maps a preset onto the three coach switches', () => {
    const s = S.playReducer(S.initialState(), { type: 'setPreset', preset: 'serious' });
    expect(s.coach).toEqual({ preset: 'serious', alerts: 'off', takebacks: false, hints: false });
    expect(S.playReducer(s, { type: 'setCoach', coach: { hints: true } }).coach.hints).toBe(true);
  });
});

describe('drill', () => {
  const opening: S.OpeningState = {
    id: 'carlsbad-exchange', name: '칼스바드', line: ['d4', 'd5', 'c4', 'e6'], tabiyaFen: S.START_FEN, drill: true,
  };
  const start = () => S.playReducer(S.initialState(), { type: 'loadOpening', opening, color: 'white' });

  it('starts a drill from the initial position with the user on the chosen side', () => {
    const s = start();
    expect(s.startFen).toBe(S.START_FEN);
    expect(s.control).toBe('ai-black');
    expect(S.drillActive(s)).toBe(true);
    expect(S.practiceModeOf(s)).toBe('drill');
  });

  it('starts a 타비야 game from the tabiya FEN when drill is off', () => {
    const tabiya = 'rnbqkbnr/ppp2ppp/4p3/3p4/3P4/8/PPP1PPPP/RNBQKBNR w KQkq - 0 3';
    const s = S.playReducer(S.initialState(), { type: 'loadOpening', opening: { ...opening, tabiyaFen: tabiya, drill: false }, color: 'black' });
    expect(s.startFen).toBe(tabiya);
    expect(s.control).toBe('ai-white');
    expect(S.practiceModeOf(s)).toBe('tabiya');
  });

  it('flags a move that leaves the book and offers the line move', () => {
    const s = S.playReducer(start(), move('e2e4'));
    expect(s.drillDeviation).toEqual({ expected: 'd4', played: 'e4', fenBefore: S.START_FEN });
    expect(S.drillActive(s)).toBe(false);
  });

  it('다시 두기 pops the deviating move, 그대로 진행 leaves the book for good', () => {
    const off = S.playReducer(start(), move('e2e4'));
    const retry = S.playReducer(off, { type: 'drillRetry' });
    expect(retry.plies).toEqual([]);
    expect(retry.drillDeviation).toBeNull();
    expect(S.drillActive(retry)).toBe(true);
    expect(retry.stats.takebacks).toBe(0);

    const keep = S.playReducer(off, { type: 'drillContinue' });
    expect(keep.plies.map((p) => p.san)).toEqual(['e4']);
    expect(keep.drillLeft).toBe(true);
    expect(S.drillActive(keep)).toBe(false);
  });

  it('says nothing when the user follows the line, and ends at the tabiya', () => {
    const s = run(start(), move('d2d4'), { type: 'bookMove', san: 'd5' }, move('c2c4'), { type: 'bookMove', san: 'e6' });
    expect(s.drillDeviation).toBeNull();
    expect(S.drillActive(s)).toBe(false);
    expect(S.tabiyaReached(s)).toBe(true);
  });
});

describe('game over', () => {
  it('detects the fool\'s mate as a black win', () => {
    const s = run(manual(), move('f2f3'), move('e7e5'), move('g2g4'), move('d8h4'));
    expect(s.status).toBe('over');
    expect(s.result).toBe('0-1');
    expect(s.termination).toBe('체크메이트');
    // A finished game accepts no more moves.
    expect(S.playReducer(s, move('e1f2'))).toBe(s);
  });

  it('detects stalemate', () => {
    const s = S.playReducer(manual({ startFen: '7k/8/8/6Q1/8/8/8/K7 w - - 0 1' }), move('g5g6'));
    expect(s.result).toBe('1/2-1/2');
    expect(s.termination).toBe('스테일메이트');
  });

  it('records 기권 and 합의 무승부 with the side that gave up', () => {
    expect(S.playReducer(manual(), { type: 'resign', side: 'white' })).toMatchObject({ result: '0-1', termination: '기권', status: 'over' });
    expect(S.playReducer(manual(), { type: 'acceptDraw' })).toMatchObject({ result: '1/2-1/2', termination: '합의 무승부' });
  });
});

describe('coach bookkeeping', () => {
  it('counts a hint once per level and position', () => {
    const s = run(
      S.initialState(),
      { type: 'hintShown', level: 1, fen: S.START_FEN },
      { type: 'hintShown', level: 1, fen: S.START_FEN },
      { type: 'hintShown', level: 3, fen: S.START_FEN },
    );
    expect(s.stats.hints).toBe(2);
    expect(s.hintsShown).toEqual([1, 3]);
    // Playing a move clears the hints so the next position starts unaided.
    expect(S.playReducer(s, move('e2e4')).hintsShown).toEqual([]);
  });

  it('keeps the hint level the user had open on the ply', () => {
    const s = run(S.initialState(), { type: 'hintShown', level: 2, fen: S.START_FEN }, { type: 'move', uci: 'e2e4', hintLevel: 2 });
    expect(s.plies[0].hintLevel).toBe(2);
  });
});

describe('pgn', () => {
  const plies = run(manual(), move('e2e4'), move('e7e5'), move('g1f3')).plies;

  it('writes the roster, the extra headers and the movetext', () => {
    const text = buildPgn({
      startFen: S.START_FEN,
      plies,
      result: '1-0',
      headers: { Date: '2026.09.11', White: '나', Black: 'Maia 1500', Hints: 1, Takebacks: 0, PracticeMode: 'free' },
    });
    expect(text).toContain('[Event "chess-tutor practice"]');
    expect(text).toContain('[White "나"]');
    expect(text).toContain('[Black "Maia 1500"]');
    expect(text).toContain('[Result "1-0"]');
    expect(text).toContain('[Hints "1"]');
    expect(text).toContain('[PracticeMode "free"]');
    expect(text).not.toContain('[SetUp');
    expect(text.trim().endsWith('1. e4 e5 2. Nf3 1-0')).toBe(true);
  });

  it('adds SetUp/FEN and keeps the move numbers of a mid-game start', () => {
    const fen = 'r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 5 4';
    const text = buildPgn({ startFen: fen, plies: run(manual({ startFen: fen }), move('f8c5')).plies, result: '*' });
    expect(text).toContain('[SetUp "1"]');
    expect(text).toContain(`[FEN "${fen}"]`);
    expect(text.trim().endsWith('4... Bc5 *')).toBe(true);
  });

  it('numbers a black-to-move start from the fullmove counter', () => {
    expect(movetext('rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1', ['e5', 'Nf3'])).toBe('1... e5 2. Nf3');
    expect(movetext(S.START_FEN, ['e4', 'e5', 'Nf3'])).toBe('1. e4 e5 2. Nf3');
  });
});

describe('move list rows', () => {
  it('leaves the white cell empty when the game starts on a black move', () => {
    const fen = 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1';
    const rows = moveRows(fen, run(manual({ startFen: fen }), move('e7e5'), move('g1f3')).plies);
    expect(rows.map((r) => [r.n, r.w?.san ?? null, r.b?.san ?? null])).toEqual([[1, null, 'e5'], [2, 'Nf3', null]]);
  });
});

describe('restore', () => {
  it('drops an unreadable start FEN and clamps a cursor past the end', () => {
    const s = S.initialState({ startFen: 'not a fen', plies: [], cursor: 7 });
    expect(s.startFen).toBe(S.START_FEN);
    expect(s.cursor).toBe(0);
  });
});

describe('clocks', () => {
  const TC: S.TimeControl = { initial: 300, increment: 5 };
  /** A game whose clocks are already running, playing white against an AI black. */
  const timed = (over: Partial<S.PlayState> = {}) => S.initialState({ control: 'ai-black', timeControl: TC, ...over });

  it('starts both clocks at the initial time and records nothing yet', () => {
    const s = timed();
    expect(s.remaining).toEqual({ white: 300_000, black: 300_000 });
    expect(s.clocks).toEqual([]);
    expect(S.timeControlText(TC)).toBe('300+5');
    expect(S.timeControlText(null)).toBeNull();
  });

  it('deducts from the side to move only', () => {
    const s = run(timed(), { type: 'tick', ms: 4_000 });
    expect(s.remaining).toEqual({ white: 296_000, black: 300_000 });
    const after = run(s, move('e2e4'), { type: 'tick', ms: 2_000 });
    expect(after.remaining?.black).toBe(298_000);
  });

  it('adds the increment to the mover and writes one clock per ply', () => {
    const s = run(
      timed(),
      { type: 'tick', ms: 10_000 },
      move('e2e4'),
      { type: 'tick', ms: 4_000 },
      { type: 'aiMove', uci: 'e7e5' },
    );
    // white: 300 - 10 + 5, black: 300 - 4 + 5
    expect(s.clocks).toEqual([295, 301]);
    expect(s.remaining).toEqual({ white: 295_000, black: 301_000 });
  });

  it('is inert without a time control, so the save sends no clocks', () => {
    const s = run(manual(), move('e2e4'), { type: 'tick', ms: 5_000 });
    expect(s.remaining).toBeNull();
    expect(s.clocks).toBeNull();
    expect(s.status).toBe('playing');
  });

  it('flags the user: the game is over and lost on time', () => {
    const s = run(timed({ remaining: { white: 900, black: 300_000 } }), { type: 'tick', ms: 1_000 });
    expect(s.remaining?.white).toBe(0);
    expect(s.status).toBe('over');
    expect(s.result).toBe('0-1');
    expect(s.termination).toBe('시간 초과');
  });

  it('never flags the AI — its clock stops at zero and the game goes on (docs 10)', () => {
    const s = run(timed({ remaining: { white: 300_000, black: 500 } }), move('e2e4'), { type: 'tick', ms: 2_000 });
    expect(s.remaining?.black).toBe(0);
    expect(s.status).toBe('playing');
    // A further tick changes nothing, so the page does not re-render forever.
    expect(S.playReducer(s, { type: 'tick', ms: 250 })).toBe(s);
  });

  it('flags whoever is to move in 수동 mode', () => {
    const s = run(
      S.initialState({ control: 'manual', timeControl: TC, remaining: { white: 300_000, black: 100 } }),
      move('e2e4'),
      { type: 'tick', ms: 1_000 },
    );
    expect(s.status).toBe('over');
    expect(s.result).toBe('1-0');
  });

  it('물리기 puts both clocks back to what they were', () => {
    const s = run(
      timed(),
      { type: 'tick', ms: 10_000 },
      move('e2e4'),
      { type: 'tick', ms: 20_000 },
      { type: 'aiMove', uci: 'e7e5' },
      { type: 'tick', ms: 30_000 },
      move('g1f3'),
    );
    expect(s.clocks).toEqual([295, 285, 270]);
    // 물리기 pops back to the user's own turn, so only Nf3 goes.
    const back = S.playReducer(s, { type: 'takeback' });
    expect(back.plies).toHaveLength(2);
    expect(back.clocks).toEqual([295, 285]);
    expect(back.remaining).toEqual({ white: 295_000, black: 285_000 });
  });

  it('keeps index = ply when the clock is turned on mid-game', () => {
    const s = run(manual(), move('e2e4'), move('e7e5'));
    const timedNow = S.playReducer(s, { type: 'setTimeControl', timeControl: TC });
    expect(timedNow.clocks).toEqual([300, 300]);
    const next = run(timedNow, { type: 'tick', ms: 7_000 }, move('g1f3'));
    expect(next.clocks).toHaveLength(3);
    expect(next.clocks?.[2]).toBe(298);
    expect(S.playReducer(timedNow, { type: 'setTimeControl', timeControl: null }).clocks).toBeNull();
  });

  it('shows tenths under 20 seconds', () => {
    expect(formatClock(305_000)).toBe('5:05');
    expect(formatClock(60_000)).toBe('1:00');
    expect(formatClock(9_400)).toBe('0:09.4');
    expect(formatClock(-5)).toBe('0:00.0');
  });
});

describe('튜터에게 질문', () => {
  it('counts a live question as a hint', () => {
    const s = run(manual(), { type: 'askedTutor' }, { type: 'askedTutor' });
    expect(s.stats.hints).toBe(2);
  });
});

describe('오프닝 카탈로그 검색', () => {
  const card = {
    id: 'french-advance', family: 'e4-other', family_label: '1.e4 기타', name: '프렌치 어드밴스', name_en: 'French Defense: Advance Variation',
    eco: 'C02', line_san: [], tabiya_fen: '', structure: { key: 'french-chain', name: '프렌치 사슬' }, sides: ['white' as const], record: null,
  };

  it('matches the Korean name, the English name and the ECO code, ignoring case', () => {
    expect(matchesQuery(card, '프렌치')).toBe(true);
    expect(matchesQuery(card, 'advance')).toBe(true);
    expect(matchesQuery(card, 'c02')).toBe(true);
    expect(matchesQuery(card, '  ')).toBe(true);
    expect(matchesQuery(card, '시실리안')).toBe(false);
  });
});
