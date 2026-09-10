import { useCallback, useEffect, useRef, useState } from 'react';
import { chatStatus, type ChatEvent, type ChatMove, type ChatStatus } from '../../api/chat';
import { EMPTY, MAX_QUESTION, applyEvent, askedTurns, patchTurn, type Conversation } from './model';

/** Whether the Claude Code tutor can be called at all; unavailable is reported, never thrown. */
export function useChatStatus(): ChatStatus | null {
  const [status, setStatus] = useState<ChatStatus | null>(null);
  useEffect(() => {
    let cancelled = false;
    chatStatus()
      .then((s) => { if (!cancelled) setStatus(s); })
      .catch(() => { if (!cancelled) setStatus({ available: false, command: '', model: '', reason: 'API 서버에 연결할 수 없습니다.' }); });
    return () => { cancelled = true; };
  }, []);
  return status;
}

/** Opens the SSE stream for one question. The caller binds the endpoint and its context. */
export type RunStream = (
  message: string, sessionId: string | null, move: ChatMove | null,
  onEvent: (e: ChatEvent) => void, signal: AbortSignal,
) => Promise<void>;

type Options = {
  /** Prefix for board-block ids, so two panels never collide in one preview slot. */
  scope: string;
  errorText: (e: unknown) => string;
};

/**
 * Conversations keyed by position (a review ply, a live FEN): asking writes the two turns,
 * folds every event into the keyed conversation, and keeps streaming while the user moves
 * away — the answer lands in the conversation it belongs to, not the one on screen.
 */
export function useConversations({ scope, errorText }: Options) {
  const [convs, setConvs] = useState<Record<string, Conversation>>({});
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const nextId = useRef(1);
  const convsRef = useRef(convs);
  convsRef.current = convs;
  const busyRef = useRef(busyKey);
  busyRef.current = busyKey;

  useEffect(() => () => { abortRef.current?.abort(); abortRef.current = null; }, []);

  const updateKey = useCallback((key: string, fn: (c: Conversation) => Conversation) => {
    setConvs((all) => ({ ...all, [key]: fn(all[key] ?? EMPTY) }));
  }, []);

  /** Sends one question into `key`'s conversation; false when nothing was sent. */
  const send = useCallback(async (
    key: string, text: string, move: ChatMove | null, run: RunStream,
    onEvent?: (e: ChatEvent, botId: number) => void,
  ): Promise<boolean> => {
    const question = text.trim().slice(0, MAX_QUESTION);
    if (!question || busyRef.current !== null) return false;
    const userId = nextId.current++;
    const botId = nextId.current++;
    updateKey(key, (c) => ({ ...c, turns: [...c.turns, ...askedTurns(userId, botId, question, move)] }));
    setBusyKey(key);
    busyRef.current = key;
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      await run(question, (convsRef.current[key] ?? EMPTY).sessionId, move, (ev) => {
        onEvent?.(ev, botId);
        updateKey(key, (c) => applyEvent(c, botId, ev, scope));
      }, ac.signal);
    } catch (e) {
      const aborted = ac.signal.aborted;
      updateKey(key, (c) => patchTurn(c, botId, (t) => (aborted ? { ...t, warnings: [...t.warnings, '중단했습니다.'] } : { ...t, error: errorText(e) })));
    } finally {
      updateKey(key, (c) => patchTurn(c, botId, (t) => ({ ...t, streaming: false })));
      setBusyKey(null);
      busyRef.current = null;
      if (abortRef.current === ac) abortRef.current = null;
    }
    return true;
  }, [scope, errorText, updateKey]);

  const stop = useCallback(() => abortRef.current?.abort(), []);

  return { convs, busyKey, send, stop };
}
