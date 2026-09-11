import { useCallback, useRef, useState } from 'react';
import type { NoteRequest, OpeningNote } from '../../api/types';
import { ApiError, api } from '../../api/client';
import { streamNote, type NoteSectionName, type NoteStage } from '../../api/noteStream';
import { TOOL_LABEL, toolSummary } from '../../components/chat/model';
import { errorText } from '../training/util';
import { useQuery } from './useQuery';
import { positionKey } from './useGuide';

// null = the server answered "missing"; an absent entry = not asked yet. Notes are written once per
// (position, move) and never change on their own, so caching them for the session is safe (§9.2).
const cache = new Map<string, OpeningNote | null>();

const noteKey = (fenBefore: string, san: string) => `${positionKey(fenBefore)}|${san}`;

/** The note already in hand for a move, without asking for it. Used by the journal's one-line index. */
export function cachedNote(fenBefore: string | null, san: string | null): OpeningNote | null {
  if (!fenBefore || !san) return null;
  return cache.get(noteKey(fenBefore, san)) ?? null;
}

/** Put a freshly generated note where the next lookup will find it. */
export function rememberNote(fenBefore: string, san: string, note: OpeningNote): void {
  cache.set(noteKey(fenBefore, san), note);
}

/** One part of the note as it arrived, already verified by the server (§10.2). */
export type StreamedSection = { name: NoteSectionName; payload: unknown; verified: number; total: number };

/** Everything the panel draws while a note is being written, and how the run ended. */
export interface NoteRun {
  running: boolean;
  /** Steps of the facts block that are done, in order; the next one is the running one. */
  stages: { name: NoteStage; detail: string }[];
  /** Tool calls the model made, as chips on the 작성 중 row. */
  tools: string[];
  sections: StreamedSection[];
  warnings: string[];
  error: string | null;
  /** Set once the server stored the note; the panel then shows the stored note itself. */
  note: OpeningNote | null;
  /** True when the student pressed 중단: what arrived stays on screen, nothing was stored. */
  aborted: boolean;
  seconds: number;
}

const EMPTY_RUN: NoteRun = {
  running: true, stages: [], tools: [], sections: [], warnings: [], error: null,
  note: null, aborted: false, seconds: 0,
};

/**
 * Runs POST /openings/note/stream and folds its events into one `NoteRun`.
 *
 * Only one run at a time; starting another (or leaving the move) aborts the one in flight,
 * which closes the connection — the server kills Claude Code and stores nothing.
 */
export function useNoteStream(onNote: (note: OpeningNote) => void) {
  const [run, setRun] = useState<NoteRun | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const stop = useCallback(() => { abortRef.current?.abort(); }, []);
  const reset = useCallback(() => { abortRef.current?.abort(); abortRef.current = null; setRun(null); }, []);
  // A finished run keeps showing its own copy of the note, so a note that changed elsewhere - an
  // answer kept with 해설에 반영 - has to replace it, or the new section would only appear after
  // walking away from the move and back.
  const adopt = useCallback((note: OpeningNote) => {
    setRun((r) => (r && r.note ? { ...r, note } : r));
  }, []);

  const start = useCallback((body: NoteRequest) => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    const startedAt = Date.now();
    const since = () => (Date.now() - startedAt) / 1000;
    setRun({ ...EMPTY_RUN });
    const patch = (fn: (r: NoteRun) => NoteRun) => setRun((r) => (r ? fn(r) : r));
    streamNote(body, (ev) => {
      switch (ev.type) {
        case 'stage':
          return patch((r) => ({ ...r, stages: [...r.stages, { name: ev.name, detail: ev.detail }] }));
        case 'tool':
          return patch((r) => ({ ...r, tools: [...r.tools, toolChip(ev.name, ev.input)] }));
        case 'section':
          return patch((r) => ({
            ...r,
            sections: [...r.sections.filter((s) => s.name !== ev.name),
              { name: ev.name, payload: ev.payload, verified: ev.verified_claims, total: ev.total_claims }],
          }));
        case 'note':
          if (body.fen && body.san) rememberNote(body.fen, body.san, ev.note);
          onNote(ev.note);
          return patch((r) => ({ ...r, note: ev.note, seconds: since() }));
        case 'warning':
          return patch((r) => ({ ...r, warnings: [...r.warnings, ev.message] }));
        case 'error':
          return patch((r) => ({ ...r, error: ev.message }));
      }
    }, ac.signal)
      .catch((e: unknown) => {
        patch((r) => (ac.signal.aborted ? { ...r, aborted: true } : { ...r, error: errorText(e) }));
      })
      .finally(() => {
        patch((r) => ({ ...r, running: false, seconds: r.seconds || since() }));
        if (abortRef.current === ac) abortRef.current = null;
      });
  }, [onNote]);

  return { run, start, stop, reset, adopt };
}

/** "엔진 분석 · depth 12" — the same label and summary the chat puts on a tool call. */
function toolChip(name: string, input: Record<string, unknown> | null): string {
  const summary = toolSummary({ kind: 'tool', id: '', name, input, ok: null });
  return `${TOOL_LABEL[name] ?? name}${summary ? ` · ${summary}` : ''}`;
}

/**
 * The deep note for the move `san` played from `fenBefore`. `data === null` after loading means the
 * note has not been written yet — the panel then offers 깊은 해설 만들기.
 */
export function useNote(fenBefore: string | null, san: string | null) {
  const key = fenBefore && san ? noteKey(fenBefore, san) : '';
  return useQuery<OpeningNote | null>(() => {
    if (!fenBefore || !san) return null;
    if (cache.has(key)) return Promise.resolve(cache.get(key) ?? null);
    return api.openings.note(fenBefore, san)
      .then((res) => {
        const note = res && 'status' in res ? null : (res as OpeningNote);
        cache.set(key, note);
        return note;
      })
      .catch((e: unknown) => {
        // A server without the note table (or the endpoint) answers 404: that is "no note", not an error.
        if (e instanceof ApiError && e.status === 404) { cache.set(key, null); return null; }
        throw e;
      });
  }, [key]);
}
