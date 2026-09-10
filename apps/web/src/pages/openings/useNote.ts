import type { OpeningNote } from '../../api/types';
import { ApiError, api } from '../../api/client';
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
