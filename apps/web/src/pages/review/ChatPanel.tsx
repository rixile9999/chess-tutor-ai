import { useCallback, useEffect, useRef, useState } from 'react';
import { streamChat, type ChatMove } from '../../api/chat';
import type { MoveReviewOut } from '../../api/types';
import { ChatLog } from '../../components/chat/Turns';
import { EMPTY, MAX_QUESTION, boardBlock, boardPreview, usage } from '../../components/chat/model';
import { useChatStatus, useConversations } from '../../components/chat/useChat';
import { CLASS_LABEL, plyLabel } from '../../lib/labels';
import type { Preview } from '../../lib/shapes';
import { errorText } from './useReviewData';

/** A move the student made on the review board while the chat tab was open. */
export type BoardMove = { fen: string; san: string };

type Props = {
  gameId: number; ply: number; review: MoveReviewOut | null; rating: number | undefined; boardFen: string;
  preview: Preview | null; onPreview: (p: Preview | null) => void;
  /** A move the student just made on the board; consumed (sent as a question) once. */
  draft: BoardMove | null; onDraftConsumed: () => void;
  /** Reported while any answer is streaming, so the page can lock the board. */
  onBusy: (busy: boolean) => void;
  hidden?: boolean;
};

/** 튜터에게 질문 tab: one conversation per ply with the Claude Code tutor. Stays mounted for
 * the life of the review page (conversations live in its state), so answers keep streaming
 * across tab switches and ply changes; the tutor's board states drive the main board only
 * while their ply is on screen and the tab is visible. */
export function ChatPanel({ gameId, ply, review, rating, boardFen, preview, onPreview, draft, onDraftConsumed, onBusy, hidden }: Props) {
  const key = `${gameId}:${ply}`;
  const { convs, busyKey, send, stop } = useConversations({ scope: 'chat', errorText });
  const conv = convs[key] ?? EMPTY;
  const [input, setInput] = useState('');
  const status = useChatStatus();
  const busy = busyKey === key;
  const liveRef = useRef({ key, hidden: !!hidden });
  liveRef.current = { key, hidden: !!hidden };

  useEffect(() => { onBusy(busyKey !== null); }, [busyKey, onBusy]);

  /** Sends a question for the ply on screen; false when nothing was sent. */
  const ask = useCallback(async (text: string, move: ChatMove | null): Promise<boolean> => {
    if (busyKey !== null) return false;
    const turnKey = key;
    const [thisPly, thisGame] = [ply, gameId];
    setInput('');
    return send(
      turnKey, text, move,
      (message, sessionId, m, onEvent, signal) =>
        streamChat({ gameId: thisGame, ply: thisPly, message, sessionId, move: m, rating }, onEvent, signal),
      (ev, botId) => {
        // Move the main board only while this ply's chat is what the student is looking at.
        if (ev.type === 'board' && liveRef.current.key === turnKey && !liveRef.current.hidden) onPreview(boardPreview(boardBlock(ev, botId)));
      },
    );
  }, [busyKey, key, ply, gameId, rating, onPreview, send]);
  const askRef = useRef(ask);
  askRef.current = ask;

  // A move made on the board becomes a question; the typed text, if any, is the question.
  useEffect(() => {
    if (!draft) return;
    let question = input.trim();
    if (!question) {
      const target = review && draft.fen === review.fen_before ? `${review.san} 대신 ${draft.san}` : `여기서 ${draft.san}`;
      question = `${target}를 두면 어떤가요?`;
    }
    void askRef.current(question, { fen: draft.fen, san: draft.san });
    onDraftConsumed();
    // The question text is read when the draft arrives; later edits must not resend it.
  }, [draft]);

  const submit = (e: React.FormEvent) => { e.preventDefault(); void ask(input, null); };
  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // WebKit fires the composition-ending Enter with isComposing already false but keyCode 229.
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) { e.preventDefault(); void ask(input, null); }
  };
  const showBefore = () => {
    if (!review) return;
    onPreview({
      id: 'chat:before', fen: review.fen_before, lastMove: null, shapes: [],
      label: `${plyLabel(ply)} ${review.san} 직전 국면 · 보드에서 다른 수를 두어 보세요`,
    });
  };

  const cls = review?.classification;
  const best = review?.alternatives?.find((a) => a.is_best) ?? null;
  const suggestions = review ? [
    cls === 'inaccuracy' || cls === 'mistake' || cls === 'blunder'
      ? `왜 ${review.san}가 ${CLASS_LABEL[cls]}인가요?`
      : `${review.san}의 장단점을 설명해 주세요.`,
    best ? `${best.san}가 왜 더 좋은가요?` : null,
    '이 국면에서 계획은 무엇인가요?',
  ].filter((s): s is string => !!s) : [];
  const atBefore = !!review && boardFen === review.fen_before;
  const unavailable = !!status && !status.available;
  const elsewhere = busyKey !== null && busyKey !== key;

  return (
    <div className="ch-chat" hidden={hidden}>
      {unavailable && (
        <div className="rv-error"><b>튜터를 부를 수 없습니다.</b> {status?.reason} <span className="small muted">Claude Code(`claude`)가 설치되고 로그인돼 있어야 합니다.</span></div>
      )}
      <ChatLog turns={conv.turns} busy={busy} hidden={hidden} preview={preview} onPreview={onPreview}>
        <div className="ch-chat-intro">
          <div>이 수에 대해 튜터와 토론해 보세요. 추천 수가 납득이 안 되면 반박하세요. 튜터는 엔진과 탐지기로 확인한 것만 말하고, 설명하는 동안 보드를 움직입니다.</div>
          <div className="ch-chat-chips">
            {suggestions.map((s) => <button key={s} type="button" className="chip rv-chip-btn" disabled={busyKey !== null || unavailable} onClick={() => void ask(s, null)}>{s}</button>)}
            {review && <button type="button" className={`chip rv-chip-btn${atBefore ? ' rv-chip-on' : ''}`} onClick={showBefore} title="보드를 이 수 직전 국면으로 돌리고 직접 다른 수를 둡니다">이 수 대신 두어보기</button>}
          </div>
          <div className="small muted">보드에서 기물을 움직이면 그 수가 그대로 질문이 됩니다. 입력창에 글을 써 두고 움직이면 그 글이 질문이 됩니다.</div>
        </div>
      </ChatLog>
      <form className="ch-chat-form" onSubmit={submit}>
        <textarea value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={onKey} rows={2} maxLength={MAX_QUESTION}
          placeholder={busy ? '튜터가 답하는 중입니다…' : elsewhere ? '다른 수에 대한 답을 쓰는 중입니다. 끝나면 보낼 수 있습니다.' : '예: 왜 Nf5는 안 되나요? (Enter로 보내기)'}
          disabled={unavailable} aria-label="튜터에게 보낼 질문" />
        {busy
          ? <button type="button" className="btn btn-ghost" onClick={stop}>중단</button>
          : <button type="submit" className="btn btn-primary" disabled={!input.trim() || unavailable || busyKey !== null}>보내기</button>}
      </form>
      <div className="ch-chat-foot small muted">
        <span>Claude Code 구독으로 실행{status?.model ? ` · ${status.model}` : ''}</span>
        {conv.limits && <span>5시간 사용량 {usage(conv.limits.fiveHour)} · 주간 {usage(conv.limits.sevenDay)}</span>}
        {review && conv.turns.length > 0 && !busy && <button type="button" className="chip rv-chip-btn" onClick={showBefore}>이 수 대신 두어보기</button>}
      </div>
    </div>
  );
}
