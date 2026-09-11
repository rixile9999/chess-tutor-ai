import { streamFrom } from './chat';
import type { NoteRequest, OpeningNote, TrapLine } from './types';

/**
 * POST /openings/note/stream — the deep note reported while it is being written (plan §10.2).
 *
 * Four `stage` events while the server builds the facts block, then one `section` per part of
 * the note: the server's own `mine` and `engine` first, the model's five as each NDJSON line
 * lands, each already verified. `tool` is a tool the model called, `note` the stored result.
 * Aborting the signal closes the connection, which kills the process and stores nothing.
 */

/** The steps of the facts block, in the order the checklist shows them. */
export const NOTE_STAGES = ['book', 'engine', 'plans', 'maia'] as const;
export type NoteStage = (typeof NOTE_STAGES)[number];
/** The parts of a note, in the order they arrive; `mine`/`engine` come from the server. */
export type NoteSectionName = 'mine' | 'engine' | 'summary' | 'why' | 'replies' | 'alternatives' | 'traps';

export type NoteEvent =
  | { type: 'stage'; name: NoteStage; detail: string }
  | { type: 'tool'; name: string; input: Record<string, unknown> | null }
  | { type: 'section'; name: NoteSectionName; payload: unknown; verified_claims: number; total_claims: number }
  | { type: 'note'; note: OpeningNote }
  | { type: 'warning'; message: string }
  | { type: 'error'; message: string };

export const STAGE_LABEL: Record<NoteStage, string> = {
  book: '책 후보', engine: '엔진 라인', plans: '구조·계획', maia: '마이아',
};
export const SECTION_TITLE: Record<NoteSectionName, string> = {
  mine: '내 기보에서', engine: '엔진 판정', summary: '요약', why: '왜 이 수인가',
  replies: '상대의 응수와 계획', alternatives: '대안과 비교', traps: '전형적인 실수와 함정',
};

/** What a `section` event carries, once narrowed by its name. */
export type SectionPayload = {
  mine: string; engine: string; summary: string; why: string[];
  replies: [string, string][]; alternatives: [string, string][]; traps: TrapLine[];
};

export function sectionPayload<K extends NoteSectionName>(name: K, payload: unknown): SectionPayload[K] | null {
  switch (name) {
    case 'mine': case 'engine': case 'summary':
      return (typeof payload === 'string' ? payload : '') as SectionPayload[K];
    case 'why':
      return (Array.isArray(payload) ? payload.filter((p) => typeof p === 'string') : []) as SectionPayload[K];
    case 'replies': case 'alternatives':
      return (Array.isArray(payload) ? payload.filter((p) => Array.isArray(p) && p.length === 2) : []) as SectionPayload[K];
    case 'traps':
      return (Array.isArray(payload) ? (payload as TrapLine[]) : []) as SectionPayload[K];
    default:
      return null;
  }
}

export function streamNote(body: NoteRequest, onEvent: (e: NoteEvent) => void, signal?: AbortSignal): Promise<void> {
  return streamFrom<NoteEvent>('/api/openings/note/stream', body, onEvent, signal);
}
