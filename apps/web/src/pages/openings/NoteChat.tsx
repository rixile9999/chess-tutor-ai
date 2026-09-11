import { useCallback, useMemo, useRef, useState } from 'react';
import { streamLiveChat, type BoardEvent, type ChatEvent } from '../../api/chat';
import { api } from '../../api/client';
import type { ChatBoardOut, OpeningNote } from '../../api/types';
import { ChatLog } from '../../components/chat/Turns';
import { EMPTY, MAX_QUESTION, usage, type Turn } from '../../components/chat/model';
import { useChatStatus, useConversations } from '../../components/chat/useChat';
import { MiniBoard } from '../../components/MiniBoard';
import type { Preview } from '../../lib/shapes';
import { errorText } from '../training/util';

type Props = {
  /** The position the move was played in, and the move: the note's key and the session's. */
  fenBefore: string;
  san: string;
  /** "4.Ba4" — what the chips call the move. */
  label: string;
  /** The position after the move: what the tutor is looking at. */
  fenAfter: string;
  /** The line up to and including the move. */
  movesSan: string[];
  startFen: string;
  openingName: string | null;
  note: OpeningNote;
  /** The board preview the page is showing, by id, so the active card can say so. */
  previewKey: string | null;
  onPreview: (p: Preview | null) => void;
  /** A note that gained an addendum. */
  onNote: (note: OpeningNote) => void;
};

/**
 * 튜터에게 질문: the note's own chat (plan §10.3).
 *
 * The same conversation machinery as 리뷰 and 대국 (`useConversations`, `ChatLog`), pointed at
 * POST /play/chat with the note attached — the server puts the note in the system prompt and
 * keys the conversation by (position, move), so every move keeps its own thread. A finished
 * answer can be kept on the note ([해설에 반영]), and what was kept is drawn above the chat as
 * 내 질문, with the answer's unverified squares marked the way the chat marks them.
 */
export function NoteChat({
  fenBefore, san, label, fenAfter, movesSan, startFen, openingName, note, previewKey, onPreview, onNote,
}: Props) {
  const key = `${fenBefore}|${san}`;
  const { convs, busyKey, send, stop } = useConversations({ scope: 'opening', errorText });
  const conv = convs[key] ?? EMPTY;
  const [input, setInput] = useState('');
  const [quote, setQuote] = useState<string | null>(null);
  const [kept, setKept] = useState<Record<number, 'saving' | 'done' | string>>({});
  // The board events as the server sent them, per answer: the blocks in the conversation have
  // been turned into chessground shapes, and an addendum should keep the arrows themselves.
  const boards = useRef<Map<number, BoardEvent[]>>(new Map());
  const status = useChatStatus();
  const busy = busyKey === key;
  const unavailable = !!status && !status.available;

  const ask = useCallback(async (text: string, quoted: string | null) => {
    if (busyKey !== null || !text.trim()) return;
    setInput('');
    setQuote(null);
    const opening = {
      fen_before: fenBefore, san, note_summary: plain(note.summary),
      section: null, quote: quoted ? plain(quoted) : null,
    };
    await send(key, text, null, (message, sessionId, move, onEvent, signal) =>
      streamLiveChat(
        { fen: fenAfter, startFen, movesSan, userColor: null, opponent: null, openingName, opening, message, sessionId, move },
        onEvent, signal,
      ),
      (ev: ChatEvent, botId: number) => {
        if (ev.type === 'board') boards.current.set(botId, [...(boards.current.get(botId) ?? []), ev]);
      });
  }, [busyKey, fenBefore, san, note.summary, fenAfter, startFen, movesSan, openingName, key, send]);

  const submit = (e: React.FormEvent) => { e.preventDefault(); void ask(input, quote); };
  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // WebKit fires the composition-ending Enter with isComposing already false but keyCode 229.
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) {
      e.preventDefault();
      void ask(input, quote);
    }
  };

  /** The sentence the summary offers to quote: the first verified one, else the first sentence. */
  const quotable = useMemo(() => {
    const marked = /\[\[(.+?)\]\]/.exec(note.summary);
    if (marked) return marked[1];
    const first = plain(note.summary).split(/(?<=\.)\s/)[0];
    return first || plain(note.summary);
  }, [note.summary]);

  const askAboutSentence = () => {
    setQuote(quotable);
    setInput(`"${quotable}" — 이 문장을 더 설명해 주세요.`);
  };

  // The answer that can still be kept: the last finished assistant turn, and the question above it.
  const last = conv.turns.length >= 2 ? conv.turns[conv.turns.length - 1] : null;
  const keepable = last && last.role === 'assistant' && !last.streaming && !last.error && answerText(last)
    ? { bot: last, question: conv.turns[conv.turns.length - 2].text }
    : null;
  const keepState = keepable ? kept[keepable.bot.id] : undefined;

  const keep = async () => {
    if (!keepable) return;
    setKept((k) => ({ ...k, [keepable.bot.id]: 'saving' }));
    try {
      const updated = await api.openings.addendum({
        fen: fenBefore,
        san,
        question: keepable.question,
        answer: answerText(keepable.bot),
        boards: (boards.current.get(keepable.bot.id) ?? []).map(asBoardOut),
        unverified: unverifiedOf(keepable.bot),
      });
      setKept((k) => ({ ...k, [keepable.bot.id]: 'done' }));
      onNote(updated);
    } catch (e: unknown) {
      setKept((k) => ({ ...k, [keepable.bot.id]: errorText(e) }));
    }
  };

  return (
    <>
      {note.addenda.length > 0 && (
        <details className="op-explain-sec" open>
          <summary>
            내 질문
            <span className="badge op-badge-note op-badge sm">해설에 반영됨</span>
            <span className="op-explain-n">{note.addenda.length}개</span>
          </summary>
          <div className="op-explain-body">
            {note.addenda.map((a, i) => (
              <div className="op-note-addendum" key={`${a.created_at}-${i}`}>
                <span className="q">{a.question}</span>
                <span>{a.answer}</span>
                {a.unverified.length > 0 && (
                  <span className="op-note-unverified">근거 미확인 칸: {a.unverified.join(', ')}</span>
                )}
                {a.boards.map((b, j) => {
                  const id = `add:${i}:${j}`;
                  const on = previewKey === id;
                  return (
                    <button
                      key={id}
                      type="button"
                      className={`ch-chat-board${on ? ' active' : ''}`}
                      title="보드에 보기"
                      onClick={() => onPreview(on ? null : {
                        id, fen: b.fen, label: b.caption || b.moves.join(' ') || '튜터가 보여준 국면',
                        lastMove: b.last_move, shapes: [],
                      })}
                    >
                      <MiniBoard fen={b.fen} size={64} highlight={b.last_move ?? []} />
                      <div>
                        {b.moves.length > 0 && <div className="ch-chat-board-moves">{b.moves.join(' ')}</div>}
                        <div className="ch-chat-board-cap">{b.caption || '이 국면'}</div>
                        <div className="ch-chat-board-hint">{on ? '보드에서 보는 중' : '보드에 보기'}</div>
                      </div>
                    </button>
                  );
                })}
                <span className="small faint">{a.created_at.slice(0, 10)}</span>
              </div>
            ))}
          </div>
        </details>
      )}

      <details className="op-explain-sec" open>
        <summary>튜터에게 질문<span className="op-explain-n">해설 문맥 포함</span></summary>
        <div className="op-explain-body op-note-chat">
          {unavailable && (
            <div className="op-error">
              <b>튜터를 부를 수 없습니다.</b> {status?.reason} Claude Code(`claude`)가 설치되고 로그인돼 있어야 합니다.
            </div>
          )}
          <div className="op-note-chips">
            <span className="chip">수 {label}</span>
            {openingName && <span className="chip">{openingName}</span>}
            <span className="chip">해설 요약 포함</span>
            {note.mine && <span className="chip">내 기보 포함</span>}
          </div>
          {note.summary && (
            <div className="op-note-quotebar">
              <button type="button" className="btn btn-ghost compact" onClick={askAboutSentence} disabled={unavailable}>
                ❝ 이 문장에 대해 질문
              </button>
              <span className="small faint">{quotable}</span>
            </div>
          )}
          {note.questions.length > 0 && (
            <>
              <div className="small faint">제안 질문 — 해설의 함정·대안·응수에서 만든 것</div>
              <div className="op-note-chips">
                {note.questions.map((q) => (
                  <button key={q} type="button" className="chip op-chip-btn op-note-ask"
                    disabled={busyKey !== null || unavailable} onClick={() => void ask(q, null)}>
                    {q}
                  </button>
                ))}
              </div>
            </>
          )}
          <ChatLog turns={conv.turns} busy={busy} preview={markerPreview(previewKey)} onPreview={onPreview} boardHint="보드에 보기">
            <div className="small muted">
              이 수에 대한 대화입니다. 튜터는 해설과 도구로 확인한 것만 말하고, 대화는 수마다 따로 이어집니다.
            </div>
          </ChatLog>
          {keepable && (
            <div className="op-note-keep">
              <span className="small faint">좋은 답이면 해설에 남길 수 있습니다.</span>
              <div className="op-grow" />
              {typeof keepState === 'string' && keepState !== 'done' && keepState !== 'saving' && (
                <span className="op-error small">{keepState}</span>
              )}
              <button type="button" className="btn btn-ghost compact" disabled={keepState === 'done' || keepState === 'saving'}
                onClick={() => void keep()}>
                {keepState === 'done' ? '반영됨' : keepState === 'saving' ? '반영하는 중…' : '해설에 반영'}
              </button>
            </div>
          )}
          {quote && (
            <div className="op-note-quoted">
              <span className="small">인용: {quote}</span>
              <button type="button" className="btn btn-ghost compact" onClick={() => setQuote(null)}>인용 지우기</button>
            </div>
          )}
          <form className="ch-chat-form" onSubmit={submit}>
            <textarea value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={onKey} rows={2} maxLength={MAX_QUESTION}
              placeholder={busy ? '튜터가 답하는 중입니다…' : '질문을 적거나 위 제안을 누르세요 (Enter로 보내기)'}
              disabled={unavailable} aria-label="튜터에게 보낼 질문" />
            {busy
              ? <button type="button" className="btn btn-ghost" onClick={stop}>중단</button>
              : <button type="submit" className="btn btn-primary" disabled={!input.trim() || unavailable || busyKey !== null}>보내기</button>}
          </form>
          <div className="ch-chat-foot small muted">
            <span>Claude Code 구독으로 실행{status?.model ? ` · ${status.model}` : ''}</span>
            {conv.limits && <span>5시간 사용량 {usage(conv.limits.fiveHour)} · 주간 {usage(conv.limits.sevenDay)}</span>}
          </div>
        </div>
      </details>
    </>
  );
}

/** ChatLog only compares ids, so the active board needs nothing else to be marked as active. */
function markerPreview(id: string | null): Preview | null {
  return id ? { id, fen: '', label: '', lastMove: null, shapes: [] } : null;
}

const plain = (text: string) => text.replace(/\[\[|\]\]/g, '');

function answerText(turn: Turn): string {
  return turn.blocks.filter((b) => b.kind === 'text').map((b) => b.text).join('\n\n').trim();
}

function unverifiedOf(turn: Turn): string[] {
  const out = new Set<string>();
  for (const b of turn.blocks) if (b.kind === 'text') for (const sq of b.unverified) out.add(sq);
  return [...out];
}

function asBoardOut(ev: BoardEvent): ChatBoardOut {
  return {
    type: 'board', n: ev.n, start_fen: ev.start_fen, fen: ev.fen, moves: ev.moves,
    last_move: ev.last_move, arrows: ev.arrows, highlights: ev.highlights, caption: ev.caption,
  };
}