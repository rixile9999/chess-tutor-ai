import { ApiError } from './client';
import type { Arrow } from './types';

/** One board state the tutor showed (the show_board tool). */
export type BoardEvent = {
  type: 'board'; n: number; start_fen: string; fen: string; moves: string[];
  last_move: [string, string] | null; arrows: Arrow[]; highlights: string[]; caption: string;
};
export type LimitsEvent = {
  type: 'limits'; status: string | null; five_hour: number | null; five_hour_resets_at: number | null;
  seven_day: number | null; seven_day_resets_at: number | null;
};
export type ChatEvent =
  | { type: 'session'; session_id: string; resumed: boolean }
  | { type: 'text'; text: string }
  | { type: 'text_end'; unverified: string[] }
  | { type: 'tool'; id: string; name: string }
  | { type: 'tool_args'; id: string; name: string; input: Record<string, unknown> | null }
  | { type: 'tool_result'; id: string; name: string; ok: boolean; preview: string }
  | BoardEvent
  | LimitsEvent
  | { type: 'warning'; message: string }
  | { type: 'error'; message: string }
  | { type: 'done'; ok: boolean; subtype: string; duration_ms?: number | null; turns?: number | null; cost_usd?: number | null };

export type ChatStatus = { available: boolean; command: string; model: string; reason: string | null };
/** A move the student made on the board, with the position it was made in. */
export type ChatMove = { fen: string; san: string };

const BASE = '/api';

export async function chatStatus(): Promise<ChatStatus> {
  const res = await fetch(`${BASE}/chat/status`);
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  return (await res.json()) as ChatStatus;
}

type StreamParams = {
  gameId: number; ply: number; message: string; sessionId: string | null; move: ChatMove | null; rating?: number;
};

/** POST a question and hand every server-sent event to `onEvent` as it arrives. Resolves when
 * the stream closes; rejects on a non-2xx response or when `signal` aborts. */
export async function streamChat(p: StreamParams, onEvent: (e: ChatEvent) => void, signal?: AbortSignal): Promise<void> {
  const q = p.rating ? `?rating=${p.rating}` : '';
  const res = await fetch(`${BASE}/review/${p.gameId}/${p.ply}/chat${q}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', accept: 'text/event-stream' },
    body: JSON.stringify({ message: p.message, session_id: p.sessionId, move: p.move }),
    signal,
  });
  if (!res.ok || !res.body) {
    let detail: unknown = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* not json */ }
    throw new ApiError(res.status, typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  const flush = (chunk: string) => {
    for (const line of chunk.split('\n')) {
      if (!line.startsWith('data: ')) continue;
      try { onEvent(JSON.parse(line.slice(6)) as ChatEvent); } catch { /* skip a malformed event */ }
    }
  };
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx = buffer.indexOf('\n\n');
    while (idx >= 0) {
      flush(buffer.slice(0, idx));
      buffer = buffer.slice(idx + 2);
      idx = buffer.indexOf('\n\n');
    }
  }
  if (buffer.trim()) flush(buffer);
}
