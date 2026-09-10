import { useCallback, useEffect, useState } from 'react';
import { streamLiveChat } from '../../api/chat';
import type { Color } from '../../api/types';
import { ChatLog } from '../../components/chat/Turns';
import { EMPTY, MAX_QUESTION, usage } from '../../components/chat/model';
import { useChatStatus, useConversations } from '../../components/chat/useChat';
import type { Preview } from '../../lib/shapes';
import { errorText } from '../training/util';

type Props = {
  /** The position on the board; one conversation per FEN, as the server keys live sessions by FEN. */
  fen: string;
  startFen: string;
  movesSan: string[];
  userColor: Color | null;
  /** "Maia 1500" / "Stockfish 1800", null in 수동 mode. */
  opponent: string | null;
  openingName: string | null;
  rating: number;
  preview: Preview | null;
  onPreview: (p: Preview | null) => void;
  /** Counted as help before the question goes out (4.5 헤더 Hints). */
  onAsked: () => void;
  /** Reported while any answer is streaming. */
  onBusy?: (busy: boolean) => void;
  hidden?: boolean;
};

/**
 * 튜터에게 질문 tab of the 대국 page. One conversation per position, kept in this component's
 * state so an answer keeps streaming while the student switches tabs or plays on. Unlike the
 * review chat, a move made on the board is a move in the game, never a question, and a board
 * the tutor shows is only a preview — it never touches the game.
 */
export function LiveChatPanel({
  fen, startFen, movesSan, userColor, opponent, openingName, rating, preview, onPreview, onAsked, onBusy, hidden,
}: Props) {
  const { convs, busyKey, send, stop } = useConversations({ scope: 'live', errorText });
  const conv = convs[fen] ?? EMPTY;
  const [input, setInput] = useState('');
  const status = useChatStatus();
  const busy = busyKey === fen;

  useEffect(() => { onBusy?.(busyKey !== null); }, [busyKey, onBusy]);

  const ask = useCallback(async (text: string): Promise<boolean> => {
    if (busyKey !== null || !text.trim()) return false;
    const key = fen;
    const ctx = { fen, startFen, movesSan, userColor, opponent, openingName, rating };
    setInput('');
    onAsked();
    return send(key, text, null, (message, sessionId, move, onEvent, signal) =>
      streamLiveChat({ ...ctx, message, sessionId, move }, onEvent, signal));
  }, [busyKey, fen, startFen, movesSan, userColor, opponent, openingName, rating, onAsked, send]);

  const submit = (e: React.FormEvent) => { e.preventDefault(); void ask(input); };
  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // WebKit fires the composition-ending Enter with isComposing already false but keyCode 229.
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) { e.preventDefault(); void ask(input); }
  };

  const last = movesSan.length ? movesSan[movesSan.length - 1] : null;
  const suggestions = [
    '이 국면에서 내 계획은 무엇이어야 하나요?',
    last ? `상대의 ${last}는 무엇을 노리나요?` : '이 오프닝의 요점은 무엇인가요?',
    '지금 조심해야 할 전술이 있나요?',
  ];
  const unavailable = !!status && !status.available;
  const elsewhere = busyKey !== null && busyKey !== fen;

  return (
    <div className="ch-chat pl-chat" hidden={hidden}>
      {unavailable && (
        <div className="tr-msg bad">
          <span><b>튜터를 부를 수 없습니다.</b> {status?.reason} Claude Code(`claude`)가 설치되고 로그인돼 있어야 합니다.</span>
        </div>
      )}
      <ChatLog turns={conv.turns} busy={busy} hidden={hidden} preview={preview} onPreview={onPreview} boardHint="보드에 표시">
        <div className="ch-chat-intro">
          <div>지금 보고 있는 국면을 두고 튜터와 이야기할 수 있습니다. 튜터는 엔진과 탐지기로 확인한 것만 말합니다. 질문은 힌트로 세어 저장되는 게임 헤더에 남습니다.</div>
          <div className="ch-chat-chips">
            {suggestions.map((s) => (
              <button key={s} type="button" className="chip pl-chip-btn" disabled={busyKey !== null || unavailable} onClick={() => void ask(s)}>{s}</button>
            ))}
          </div>
          <div className="small muted">국면마다 대화가 따로 이어집니다. 앞 국면으로 돌아가면 그때 나눈 대화가 다시 보입니다. 보드에서 기물을 움직이면 질문이 아니라 그대로 수가 됩니다.</div>
        </div>
      </ChatLog>
      <form className="ch-chat-form" onSubmit={submit}>
        <textarea value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={onKey} rows={2} maxLength={MAX_QUESTION}
          placeholder={busy ? '튜터가 답하는 중입니다…' : elsewhere ? '앞 국면에 대한 답을 쓰는 중입니다. 끝나면 보낼 수 있습니다.' : '예: 여기서 c5를 밀어도 되나요? (Enter로 보내기)'}
          disabled={unavailable} aria-label="튜터에게 보낼 질문" />
        {busy
          ? <button type="button" className="btn btn-ghost" onClick={stop}>중단</button>
          : <button type="submit" className="btn btn-primary" disabled={!input.trim() || unavailable || busyKey !== null}>보내기</button>}
      </form>
      <div className="ch-chat-foot small muted">
        <span>Claude Code 구독으로 실행{status?.model ? ` · ${status.model}` : ''}</span>
        {conv.limits && <span>5시간 사용량 {usage(conv.limits.fiveHour)} · 주간 {usage(conv.limits.sevenDay)}</span>}
        {preview && <button type="button" className="chip pl-chip-btn" onClick={() => onPreview(null)}>원래 국면으로</button>}
      </div>
    </div>
  );
}
