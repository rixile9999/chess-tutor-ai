import { describe, expect, it } from 'vitest';
import {
  NOTE_STAGES, SECTION_TITLE, STAGE_LABEL, sectionPayload, type NoteSectionName,
} from '../src/api/noteStream';

/** The panel draws a `section` event straight from its payload, so a payload that is not what
 * its name promises (an old server, a truncated frame) must come back empty, never crash. */
describe('section payloads', () => {
  it('reads each section as the shape the panel draws', () => {
    expect(sectionPayload('summary', '압박을 유지합니다.')).toBe('압박을 유지합니다.');
    expect(sectionPayload('mine', '내 기보 26판')).toBe('내 기보 26판');
    expect(sectionPayload('why', ['첫 문단', '둘째 문단'])).toEqual(['첫 문단', '둘째 문단']);
    expect(sectionPayload('replies', [['Nf6', '메인'], ['d6', '스타이니츠']])).toEqual([
      ['Nf6', '메인'], ['d6', '스타이니츠'],
    ]);
    const traps = [{ title: '노아의 방주', line_san: ['d6', 'd4'], text: '퇴로가 막힙니다.' }];
    expect(sectionPayload('traps', traps)).toEqual(traps);
  });

  it('falls back to an empty value for a payload of the wrong shape', () => {
    expect(sectionPayload('summary', { text: '객체' })).toBe('');
    expect(sectionPayload('why', '문단이 아니라 문자열')).toEqual([]);
    expect(sectionPayload('why', ['좋은 문단', 42, null])).toEqual(['좋은 문단']);
    expect(sectionPayload('replies', [['Nf6'], 'x', ['d6', '설명']])).toEqual([['d6', '설명']]);
    expect(sectionPayload('traps', null)).toEqual([]);
  });

  it('names every stage and every section it can be sent', () => {
    for (const stage of NOTE_STAGES) expect(STAGE_LABEL[stage]).toBeTruthy();
    const names: NoteSectionName[] = ['mine', 'engine', 'summary', 'why', 'replies', 'alternatives', 'traps'];
    for (const name of names) expect(SECTION_TITLE[name]).toBeTruthy();
  });
});
