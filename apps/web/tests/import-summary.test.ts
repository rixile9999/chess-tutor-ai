import { describe, expect, it } from 'vitest';
import { importSummary } from '../src/pages/games/ImportPanel';
import type { ImportResult } from '../src/api/types';

const result = (over: Partial<ImportResult> = {}): ImportResult =>
  ({ imported: 0, skipped: 0, game_ids: [], user_id: null, errors: [], ...over });

/** routers/games.py folds len(errors) into `skipped`, so unreadable games must not read as duplicates. */
describe('importSummary', () => {
  it('never calls an unreadable PGN an already saved game', () => {
    const text = importSummary(result({ skipped: 1, errors: ["1번째 게임의 수를 읽을 수 없어 건너뜁니다."] }), 'pgn');
    expect(text).not.toContain('이미 저장된');
    expect(text).toContain('1판을 읽지 못했습니다');
  });

  it('separates already-saved games from unreadable ones', () => {
    const text = importSummary(result({ skipped: 4, errors: ['a', 'b'] }), 'chesscom');
    expect(text).toContain('2판은 이미 있고');
    expect(text).toContain('2판은 읽지 못했습니다');
  });

  it('keeps the duplicate wording when nothing failed to parse', () => {
    expect(importSummary(result({ skipped: 3 }), 'lichess')).toBe('새 기보가 없습니다. 모두 이미 저장된 기보입니다.');
  });

  it('reports the source and the count on a successful import', () => {
    expect(importSummary(result({ imported: 2 }), 'lichess')).toBe('lichess에서 2판을 새로 저장했습니다.');
    expect(importSummary(result({ imported: 2, skipped: 1 }), 'pgn')).toBe('PGN에서 2판을 새로 저장했습니다. 1판은 이미 있어 건너뛰었습니다.');
    expect(importSummary(result({ imported: 2, skipped: 1, errors: ['x'] }), 'pgn'))
      .toBe('PGN에서 2판을 새로 저장했습니다. 1판은 읽지 못했습니다.');
  });

  it('says nothing was found when the source was empty', () => {
    expect(importSummary(result(), 'pgn')).toBe('가져올 기보를 찾지 못했습니다.');
  });

  it('never emits an em-dash', () => {
    const texts = [
      importSummary(result({ skipped: 1, errors: ['x'] }), 'pgn'),
      importSummary(result({ imported: 1, skipped: 2, errors: ['x'] }), 'chesscom'),
      importSummary(result({ skipped: 2 }), 'lichess'),
      importSummary(result(), 'pgn'),
    ];
    for (const t of texts) expect(t).not.toContain('—');
  });
});
