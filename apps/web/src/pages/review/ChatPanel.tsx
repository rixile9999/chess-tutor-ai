import { useCallback, useEffect, useRef, useState } from 'react';
import type { Key } from 'chessground/types';
import { chatStatus, streamChat, type BoardEvent, type ChatEvent, type ChatMove, type ChatStatus } from '../../api/chat';
import type { MoveReviewOut } from '../../api/types';
import type { BoardShape } from '../../components/Board';
import { MiniBoard } from '../../components/MiniBoard';
import { CLASS_LABEL, plyLabel } from '../../lib/labels';
import { IconWarn, arrowShapes, pct, type Preview } from './shared';
import { errorText } from './useReviewData';

/** A move the student made on the review board while the chat tab was open. */
export type BoardMove = { fen: string; san: string };

type TextBlock = { kind: 'text'; text: string; unverified: string[] };
type ToolBlock = { kind: 'tool'; id: string; name: string; input: Record<string, unknown> | null; ok: boolean | null };
type BoardBlock = {
  kind: 'board'; id: string; fen: string; startFen: string; moves: string[];
  lastMove: [string, string] | null; shapes: BoardShape[]; caption: string;
};
type Block = TextBlock | ToolBlock | BoardBlock;
type Turn = {
  id: number; role: 'user' | 'assistant'; text: string; move: ChatMove | null; blocks: Block[];
  error: string | null; warnings: string[]; streaming: boolean; ok: boolean | null;
};
type Limits = { fiveHour: number | null; sevenDay: number | null };
type Conversation = { sessionId: string | null; turns: Turn[]; limits: Limits | null };

const EMPTY: Conversation = { sessionId: null, turns: [], limits: null };
const MAX_QUESTION = 4000;
const TOOL_LABEL: Record<string, string> = {
  analyse: '엔진 분석', show_board: '보드 표시', compare: '두 수 비교', motifs: '전술 확인', maia_probs: 'Maia 확률', features: '국면 특징',
};

function toolSummary(b: ToolBlock): string {
  const i = b.input ?? {};
  const str = (k: string) => (typeof i[k] === 'string' ? (i[k] as string) : '');
  switch (b.name) {
    case 'compare': return `${str('san_a')} vs ${str('san_b')}${i.depth ? ` · depth ${i.depth}` : ''}`;
    case 'analyse': return i.depth ? `depth ${i.depth}` : '';
    case 'show_board': return Array.isArray(i.moves) ? (i.moves as string[]).join(' ') : str('moves');
    case 'motifs': return str('san');
    case 'maia_probs': return i.rating ? `${i.rating}` : '';
    default: return '';
  }
}

function boardBlock(ev: BoardEvent, turnId: number): BoardBlock {
  const shapes = arrowShapes(ev.arrows, ev.highlights);
  if (ev.last_move) shapes.push({ orig: ev.last_move[0] as Key, dest: ev.last_move[1] as Key, brush: 'paleGrey', modifiers: { lineWidth: 6 } });
  return { kind: 'board', id: `chat:${turnId}:${ev.n}`, fen: ev.fen, startFen: ev.start_fen, moves: ev.moves, lastMove: ev.last_move, shapes, caption: ev.caption };
}

export function boardPreview(b: BoardBlock): Preview {
  return { id: b.id, fen: b.fen, label: b.caption || (b.moves.length ? b.moves.join(' ') : '튜터가 보여준 국면'), lastMove: b.lastMove, shapes: b.shapes };
}

/** Fold one server event into the conversation; `turnId` is the assistant turn being written. */
function applyEvent(c: Conversation, turnId: number, ev: ChatEvent): Conversation {
  if (ev.type === 'session') {
    const forgotten = !ev.resumed && c.turns.length > 2;
    const next = { ...c, sessionId: ev.session_id };
    return forgotten ? patchTurn(next, turnId, (t) => ({ ...t, warnings: [...t.warnings, '이전 대화가 서버에서 지워져 튜터가 앞선 문답을 기억하지 못합니다.'] })) : next;
  }
  if (ev.type === 'limits') return { ...c, limits: { fiveHour: ev.five_hour, sevenDay: ev.seven_day } };
  return patchTurn(c, turnId, (t) => {
    const blocks = [...t.blocks];
    const last = blocks[blocks.length - 1];
    switch (ev.type) {
      case 'text':
        if (last && last.kind === 'text') blocks[blocks.length - 1] = { ...last, text: last.text + ev.text };
        else blocks.push({ kind: 'text', text: ev.text, unverified: [] });
        return { ...t, blocks };
      case 'text_end': {
        // The block may have been followed by a board or tool event before its end arrived.
        const i = blocks.map((b) => b.kind).lastIndexOf('text');
        if (i < 0) return t;
        blocks[i] = { ...(blocks[i] as TextBlock), unverified: ev.unverified };
        return { ...t, blocks };
      }
      case 'tool':
        blocks.push({ kind: 'tool', id: ev.id, name: ev.name, input: null, ok: null });
        return { ...t, blocks };
      case 'tool_args': {
        const i = blocks.findIndex((b) => b.kind === 'tool' && b.id === ev.id);
        if (i >= 0) blocks[i] = { ...(blocks[i] as ToolBlock), input: ev.input };
        else blocks.push({ kind: 'tool', id: ev.id, name: ev.name, input: ev.input, ok: null });
        return { ...t, blocks };
      }
      case 'tool_result': {
        const i = blocks.findIndex((b) => b.kind === 'tool' && b.id === ev.id);
        if (i < 0) return t;
        blocks[i] = { ...(blocks[i] as ToolBlock), ok: ev.ok };
        return { ...t, blocks };
      }
      case 'board':
        blocks.push(boardBlock(ev, turnId));
        return { ...t, blocks };
      case 'warning':
        return { ...t, warnings: [...t.warnings, ev.message] };
      case 'error':
        return { ...t, error: ev.message };
      case 'done':
        return { ...t, ok: ev.ok };
      default:
        return t;
    }
  });
}

function patchTurn(c: Conversation, turnId: number, fn: (t: Turn) => Turn): Conversation {
  const idx = c.turns.findIndex((t) => t.id === turnId);
  if (idx < 0) return c;
  const next = fn(c.turns[idx]);
  if (next === c.turns[idx]) return c;
  const turns = [...c.turns];
  turns[idx] = next;
  return { ...c, turns };
}

function usage(v: number | null): string { return v === null ? '?' : pct(v); }

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
  const [convs, setConvs] = useState<Record<string, Conversation>>({});
  const conv = convs[key] ?? EMPTY;
  const [input, setInput] = useState('');
  const [status, setStatus] = useState<ChatStatus | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const busy = busyKey === key;
  const abortRef = useRef<AbortController | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const nextId = useRef(1);
  const convsRef = useRef(convs);
  convsRef.current = convs;
  const liveRef = useRef({ key, hidden: !!hidden });
  liveRef.current = { key, hidden: !!hidden };

  useEffect(() => {
    chatStatus().then(setStatus).catch(() => setStatus({ available: false, command: '', model: '', reason: 'API 서버에 연결할 수 없습니다.' }));
  }, []);
  useEffect(() => () => { abortRef.current?.abort(); abortRef.current = null; }, []);
  useEffect(() => { onBusy(busyKey !== null); }, [busyKey, onBusy]);
  useEffect(() => {
    const el = logRef.current;
    if (el && !hidden && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [conv, hidden]);
  const onLogScroll = () => {
    const el = logRef.current;
    if (el) stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };

  const updateKey = useCallback((turnKey: string, fn: (c: Conversation) => Conversation) => {
    setConvs((all) => ({ ...all, [turnKey]: fn(all[turnKey] ?? EMPTY) }));
  }, []);

  /** Sends a question for the ply on screen; false when nothing was sent. */
  const send = useCallback(async (text: string, move: ChatMove | null): Promise<boolean> => {
    const question = text.trim().slice(0, MAX_QUESTION);
    if (!question || busyKey !== null) return false;
    const turnKey = key;
    const [thisPly, thisGame] = [ply, gameId];
    const userId = nextId.current++;
    const botId = nextId.current++;
    const blank = { blocks: [], error: null, warnings: [], ok: null };
    updateKey(turnKey, (c) => ({
      ...c,
      turns: [
        ...c.turns,
        { id: userId, role: 'user', text: question, move, streaming: false, ...blank },
        { id: botId, role: 'assistant', text: '', move: null, streaming: true, ...blank },
      ],
    }));
    setInput('');
    setBusyKey(turnKey);
    stickRef.current = true;
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      await streamChat(
        { gameId: thisGame, ply: thisPly, message: question, sessionId: (convsRef.current[turnKey] ?? EMPTY).sessionId, move, rating },
        (ev) => {
          // Move the main board only while this ply's chat is what the student is looking at.
          if (ev.type === 'board' && liveRef.current.key === turnKey && !liveRef.current.hidden) onPreview(boardPreview(boardBlock(ev, botId)));
          updateKey(turnKey, (c) => applyEvent(c, botId, ev));
        },
        ac.signal,
      );
    } catch (e) {
      const aborted = ac.signal.aborted;
      updateKey(turnKey, (c) => patchTurn(c, botId, (t) => (aborted ? { ...t, warnings: [...t.warnings, '중단했습니다.'] } : { ...t, error: errorText(e) })));
    } finally {
      updateKey(turnKey, (c) => patchTurn(c, botId, (t) => ({ ...t, streaming: false })));
      setBusyKey(null);
      if (abortRef.current === ac) abortRef.current = null;
    }
    return true;
  }, [busyKey, key, ply, gameId, rating, onPreview, updateKey]);
  const sendRef = useRef(send);
  sendRef.current = send;

  // A move made on the board becomes a question; the typed text, if any, is the question.
  useEffect(() => {
    if (!draft) return;
    let question = input.trim();
    if (!question) {
      const target = review && draft.fen === review.fen_before ? `${review.san} 대신 ${draft.san}` : `여기서 ${draft.san}`;
      question = `${target}를 두면 어떤가요?`;
    }
    void sendRef.current(question, { fen: draft.fen, san: draft.san });
    onDraftConsumed();
    // The question text is read when the draft arrives; later edits must not resend it.
  }, [draft]);

  const stop = () => abortRef.current?.abort();
  const submit = (e: React.FormEvent) => { e.preventDefault(); void send(input, null); };
  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // WebKit fires the composition-ending Enter with isComposing already false but keyCode 229.
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) { e.preventDefault(); void send(input, null); }
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
    <div className="rv-chat" hidden={hidden}>
      {unavailable && (
        <div className="rv-error"><b>튜터를 부를 수 없습니다.</b> {status?.reason} <span className="small muted">Claude Code(`claude`)가 설치되고 로그인돼 있어야 합니다.</span></div>
      )}
      <div className="rv-chat-log" ref={logRef} onScroll={onLogScroll} role="log" aria-live="polite" aria-busy={busy}>
        {conv.turns.length === 0 && (
          <div className="rv-chat-intro">
            <div>이 수에 대해 튜터와 토론해 보세요. 추천 수가 납득이 안 되면 반박하세요. 튜터는 엔진과 탐지기로 확인한 것만 말하고, 설명하는 동안 보드를 움직입니다.</div>
            <div className="rv-chat-chips">
              {suggestions.map((s) => <button key={s} type="button" className="chip rv-chip-btn" disabled={busyKey !== null || unavailable} onClick={() => void send(s, null)}>{s}</button>)}
              {review && <button type="button" className={`chip rv-chip-btn${atBefore ? ' rv-chip-on' : ''}`} onClick={showBefore} title="보드를 이 수 직전 국면으로 돌리고 직접 다른 수를 둡니다">이 수 대신 두어보기</button>}
            </div>
            <div className="small muted">보드에서 기물을 움직이면 그 수가 그대로 질문이 됩니다. 입력창에 글을 써 두고 움직이면 그 글이 질문이 됩니다.</div>
          </div>
        )}
        {conv.turns.map((t) => (t.role === 'user' ? <UserTurn key={t.id} turn={t} /> : <BotTurn key={t.id} turn={t} preview={preview} onPreview={onPreview} />))}
      </div>
      <form className="rv-chat-form" onSubmit={submit}>
        <textarea value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={onKey} rows={2} maxLength={MAX_QUESTION}
          placeholder={busy ? '튜터가 답하는 중입니다…' : elsewhere ? '다른 수에 대한 답을 쓰는 중입니다. 끝나면 보낼 수 있습니다.' : '예: 왜 Nf5는 안 되나요? (Enter로 보내기)'}
          disabled={unavailable} aria-label="튜터에게 보낼 질문" />
        {busy
          ? <button type="button" className="btn btn-ghost" onClick={stop}>중단</button>
          : <button type="submit" className="btn btn-primary" disabled={!input.trim() || unavailable || busyKey !== null}>보내기</button>}
      </form>
      <div className="rv-chat-foot small muted">
        <span>Claude Code 구독으로 실행{status?.model ? ` · ${status.model}` : ''}</span>
        {conv.limits && <span>5시간 사용량 {usage(conv.limits.fiveHour)} · 주간 {usage(conv.limits.sevenDay)}</span>}
        {review && conv.turns.length > 0 && !busy && <button type="button" className="chip rv-chip-btn" onClick={showBefore}>이 수 대신 두어보기</button>}
      </div>
    </div>
  );
}

function UserTurn({ turn }: { turn: Turn }) {
  return (
    <div className="rv-chat-turn">
      <div className="rv-chat-user">{turn.move && <span className="rv-chat-move">{turn.move.san}</span>}{turn.text}</div>
    </div>
  );
}

function BotTurn({ turn, preview, onPreview }: { turn: Turn; preview: Preview | null; onPreview: (p: Preview | null) => void }) {
  const last = turn.blocks[turn.blocks.length - 1];
  const pending = turn.streaming && (!last || (last.kind === 'tool' && last.ok === null));
  return (
    <div className="rv-chat-turn">
      {turn.blocks.map((b, i) => {
        if (b.kind === 'text') {
          return (
            <div key={i}>
              <p className="rv-chat-text">{b.text}</p>
              {b.unverified.length > 0 && <div className="rv-chat-unverified"><IconWarn /> 근거 미확인 칸: {b.unverified.join(', ')}</div>}
            </div>
          );
        }
        if (b.kind === 'tool') {
          const summary = toolSummary(b);
          return (
            <div key={i} className={`rv-chat-tool${b.ok === false ? ' fail' : ''}`}>
              {b.ok === null && turn.streaming ? '⋯' : b.ok === false ? '✕' : '✓'} {TOOL_LABEL[b.name] ?? b.name}{summary ? ` · ${summary}` : ''}
            </div>
          );
        }
        const p = boardPreview(b);
        const on = preview?.id === p.id;
        return (
          <button key={i} type="button" className={`rv-chat-board${on ? ' active' : ''}`} onClick={() => onPreview(on ? null : p)} title="보드에서 보기">
            <MiniBoard fen={b.fen} size={72} highlight={b.lastMove ?? []} />
            <div>
              {b.moves.length > 0 && <div className="rv-chat-board-moves">{b.moves.join(' ')}</div>}
              <div className="rv-chat-board-cap">{b.caption || '이 국면'}</div>
            </div>
          </button>
        );
      })}
      {pending && <div className="rv-chat-thinking">{last && last.kind === 'tool' ? `${TOOL_LABEL[last.name] ?? last.name} 중…` : '생각하는 중…'}</div>}
      {turn.warnings.map((w, i) => <div key={`w${i}`} className="small muted">{w}</div>)}
      {turn.error && <div className="rv-error"><b>답을 받지 못했습니다.</b> {turn.error}</div>}
    </div>
  );
}
