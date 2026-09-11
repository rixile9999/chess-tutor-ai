import { describe, expect, it } from 'vitest';
import { buildStudyPgn, pgnComment, studyMovetext } from '../src/pages/openings/pgn';

const START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
const NOW = new Date(2026, 8, 11); // 2026.09.11, local time like the builder reads it

const LINE = [
  { san: 'e4', text: '킹즈 폰 오프닝. [[e4 폰이 d5와 f5를 통제합니다]].' },
  { san: 'e5', text: '오픈 게임.' },
  { san: 'Nf3', text: 'e5 폰을 공격하며 첫 기물을 전개합니다.' },
  { san: 'Nc6', text: 'e5를 지킵니다.' },
];

describe('pgnComment', () => {
  it('drops the verification marks and flattens the sentence', () => {
    expect(pgnComment('킹즈 폰 오프닝. [[중앙을 잡습니다]].')).toBe('킹즈 폰 오프닝. 중앙을 잡습니다.');
    expect(pgnComment(' 두\n줄짜리   해설 ')).toBe('두 줄짜리 해설');
  });

  it('replaces braces, which would end the comment', () => {
    expect(pgnComment('a {b} c')).toBe('a (b) c');
  });
});

describe('studyMovetext', () => {
  it('puts every comment after its move and repeats the number after one', () => {
    expect(studyMovetext(START_FEN, LINE)).toBe(
      '1. e4 {킹즈 폰 오프닝. e4 폰이 d5와 f5를 통제합니다.} 1... e5 {오픈 게임.}'
      + ' 2. Nf3 {e5 폰을 공격하며 첫 기물을 전개합니다.} 2... Nc6 {e5를 지킵니다.}',
    );
  });

  it('leaves a move without an explanation bare, and then no "..." is needed', () => {
    expect(studyMovetext(START_FEN, [{ san: 'e4' }, { san: 'e5', text: '' }, { san: 'Nf3' }]))
      .toBe('1. e4 e5 2. Nf3');
  });

  it('starts a black-to-move position with the move number and three dots', () => {
    const fen = 'rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2';
    expect(studyMovetext(fen, [{ san: 'Nf3' }, { san: 'Nc6' }])).toBe('2. Nf3 Nc6');
    const black = 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1';
    expect(studyMovetext(black, [{ san: 'e5', text: '오픈 게임.' }, { san: 'Nf3' }]))
      .toBe('1... e5 {오픈 게임.} 2. Nf3');
  });
});

describe('buildStudyPgn', () => {
  it('writes the study headers, the opening of the position and an unfinished result', () => {
    const pgn = buildStudyPgn({ moves: LINE, name: '루이 로페즈: 모피 방어', eco: 'C70', now: NOW });
    const headers = pgn.split('\n\n')[0].split('\n');
    expect(headers).toEqual([
      '[Event "chess-tutor opening study"]',
      '[Site "chess-tutor"]',
      '[Date "2026.09.11"]',
      '[Round "-"]',
      '[White "?"]',
      '[Black "?"]',
      '[Result "*"]',
      '[Opening "루이 로페즈: 모피 방어"]',
      '[ECO "C70"]',
    ]);
    expect(pgn.trimEnd().endsWith('*')).toBe(true);
    expect(pgn.endsWith('\n')).toBe(true);
  });

  it('leaves out an opening the position does not have', () => {
    const pgn = buildStudyPgn({ moves: [{ san: 'e4' }], name: null, eco: null, now: NOW });
    expect(pgn).not.toContain('[Opening');
    expect(pgn).not.toContain('[ECO');
  });

  it('adds SetUp and FEN when the line does not start from the initial position', () => {
    const fen = 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1';
    const pgn = buildStudyPgn({ moves: [{ san: 'e5' }], startFen: fen, now: NOW });
    expect(pgn).toContain('[SetUp "1"]');
    expect(pgn).toContain(`[FEN "${fen}"]`);
  });

  it('keeps an empty line readable and wraps a long comment at 80 columns', () => {
    expect(buildStudyPgn({ moves: [], now: NOW }).trimEnd().endsWith('\n*')).toBe(true);
    const long = { san: 'e4', text: '중앙을 잡는 수입니다. '.repeat(12) };
    const body = buildStudyPgn({ moves: [long], now: NOW }).split('\n\n')[1];
    const rows = body.trimEnd().split('\n');
    expect(rows.length).toBeGreaterThan(1);
    expect(rows.every((line) => line.length <= 80)).toBe(true);
  });
});
