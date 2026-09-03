import { useCallback, useEffect, useRef, useState } from 'react';
import type { Key } from 'chessground/types';
import { chatStatus, streamChat, type BoardEvent, type ChatEvent, type ChatMove, type ChatStatus } from '../../api/chat';
import type { MoveReviewOut } from '../../api/types';
import type { BoardShape } from '../../components/Board';
import { MiniBoard } from '../../components/MiniBoard';
import { CLASS_LABEL, plyLabel } from '../../lib/labels';
import { IconWarn, arrowShapes, type Preview } from './shared';
import { errorText } from './useReviewData';

/** A move the student made on the review board while the chat tab was open. */
export type BoardMove = { fen: string; san: string; fenAfter: string };

type TextBlock = { kind: 'text'; text: string; unverified: string[] };
type ToolBlock = { kind: 'tool'; id: string; name: string; input: Record<string, unknown> | null; ok: boolean | null };
type BoardBlock = {
  kind: 'board'; n: number; fen: string; startFen: string; moves: string[];
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
const TOOL_LABEL: Record<string, string> = {
  analyse: '엔진 분석', show_board: '보드 표시', compare: '두 수 비교', motifs: '전술 확인', maia_probs: 'Maia 확률', features: '국면 특징',
};

function toolSummary(b: ToolBlock): string {
  const i = b.input ?? {};
  const str = (k: string) => (typeof i[k] === 'string' ? (i[k] as string) : '');
  switch (b.name) {
    case 'compare': return `${str('san_a')} vs ${str('san_b')}${i.depth ? ` · depth ${i.depth}` : ''}`;
    case 'analyse': return i.depth ? `depth ${i.depth}` : '';
    case 'show_board': return Array.isArray(i.moves) ? (i.moves as string[]).join(' ') : '';
    case 'motifs': return str('san');
    case 'maia_probs': return i.rating ? `${i.rating}` : '';
    default: return '';
  }
}

function boardBlock(ev: BoardEvent): BoardBlock {
  const shapes = arrowShapes(ev.arrows, ev.highlights);
  if (ev.last_move) shapes.push({ orig: ev.last_move[0] as Key, dest: ev.last_move[1] as Key, brush: 'paleGrey', modifiers: { lineWidth: 6 } });
  return { kind: 'board', n: ev.n, fen: ev.fen, startFen: ev.start_fen, moves: ev.moves, lastMove: ev.last_move, shapes, caption: ev.caption };
}

export function boardPreview(b: BoardBlock): Preview {
  return { id: `chat:${b.n}`, fen: b.fen, label: b.caption || (b.moves.length ? b.moves.join(' ') : '튜터가 보여준 국면'), lastMove: b.lastMove, shapes: b.shapes };
}

/** Fold one server event into the conversation; `turnId` is the assistant turn being written. */
function applyEvent(c: Conversation, turnId: number, ev: ChatEvent): Conversation {
  if (ev.type === 'session') return { ...c, sessionId: ev.session_id };
  if (ev.type === 'limits') return { ...c, limits: { fiveHour: ev.five_hour, sevenDay: ev.seven_day } };
  const idx = c.turns.findIndex((t) => t.id === turnId);
  if (idx < 0) return c;
  const t = c.turns[idx];
  const blocks = [...t.blocks];
  const last = blocks[blocks.length - 1];
  let next: Turn = t;
  switch (ev.type) {
    case 'text':
      if (last && last.kind === 'text') blocks[blocks.length - 1] = { ...last, text: last.text + ev.text };
      else blocks.push({ kind: 'text', text: ev.text, unverified: [] });
      next = { ...t, blocks };
      break;
    case 'text_end':
      if (last && last.kind === 'text') { blocks[blocks.length - 1] = { ...last, unverified: ev.unverified }; next = { ...t, blocks }; }
      break;
    case 'tool':
      blocks.push({ kind: 'tool', id: ev.id, name: ev.name, input: null, ok: null });
      next = { ...t, blocks };
      break;
    case 'tool_args': {
      const i = blocks.findIndex((b) => b.kind === 'tool' && b.id === ev.id);
      if (i >= 0) blocks[i] = { ...(blocks[i] as ToolBlock), input: ev.input };
      else blocks.push({ kind: 'tool', id: ev.id, name: ev.name, input: ev.input, ok: null });
      next = { ...t, blocks };
      break;
    }
    case 'tool_result': {
      const i = blocks.findIndex((b) => b.kind === 'tool' && b.id === ev.id);
      if (i >= 0) { blocks[i] = { ...(blocks[i] as ToolBlock), ok: ev.ok }; next = { ...t, blocks }; }
      break;
    }
    case 'board':
      blocks.push(boardBlock(ev));
      next = { ...t, blocks };
      break;
    case 'warning':
      next = { ...t, warnings: [...t.warnings, ev.message] };
      break;
    case 'error':
      next = { ...t, error: ev.message };
      break;
    case 'done':
      next = { ...t, ok: ev.ok };
      break;
  }
  if (next === t) return c;
  const turns = [...c.turns];
  turns[idx] = next;
  return { ...c, turns };
}

function patchTurn(c: Conversation, turnId: number, patch: Partial<Turn>): Conversation {
  const idx = c.turns.findIndex((t) => t.id === turnId);
  if (idx < 0) return c;
  const turns = [...c.turns];
  turns[idx] = { ...turns[idx], ...patch };
  return { ...c, turns };
}

function pct(v: number | null): string { return v === null ? '?' : `${Math.round(v * 100)}%`; }

type Props = {
  gameId: number; ply: number; review: MoveReviewOut; rating: number | undefined; boardFen: string;
  preview: Preview | null; onPreview: (p: Preview | null) => void;
  /** A move the student just made on the board; consumed (sent as a question) once. */
  draft: BoardMove | null; onDraftConsumed: () => void;
  /** Reported while an answer is streaming, so the page can lock the board. */
  onBusy: (busy: boolean) => void;
  hidden?: boolean;
};

let nextTurnId = 1;

/** 튜터에게 질문 tab: a conversation per ply with the Claude Code tutor. The tutor's board
 * states are shown on the main board as they stream in and stay clickable in the log. */
export function ChatPanel({ gameId, ply, review, rating, boardFen, preview, onPreview, draft, onDraftConsumed, onBusy, hidden }: Props) {
  const key = `${gameId}:${ply}`;
  const [convs, setConvs] = useState<Record<string, Conversation>>({});
  const conv = convs[key] ?? EMPTY;
  const [input, setInput] = useState('');
  const [status, setStatus] = useState<ChatStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const convRef = useRef(conv);
  convRef.current = conv;

  useEffect(() => {
    chatStatus().then(setStatus).catch(() => setStatus({ available: false, command: '', model: '', reason: 'API 서버에 연결할 수 없습니다.' }));
  }, []);
  useEffect(() => { onBusy(busy); }, [busy, onBusy]);
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [conv]);

  const update = useCallback((fn: (c: Conversation) => Conversation) => {
    setConvs((all) => ({ ...all, [key]: fn(all[key] ?? EMPTY) }));
  }, [key]);

  const send = useCallback(async (text: string, move: ChatMove | null) => {
    const question = text.trim();
    if (!question || busy) return;
    const userId = nextTurnId++;
    const botId = nextTurnId++;
    const blank = { blocks: [], error: null, warnings: [], ok: null };
    update((c) => ({
      ...c,
      turns: [
        ...c.turns,
        { id: userId, role: 'user', text: question, move, streaming: false, ...blank },
        { id: botId, role: 'assistant', text: '', move: null, streaming: true, ...blank },
      ],
    }));
    setInput('');
    setBusy(true);
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      await streamChat(
        { gameId, ply, message: question, sessionId: convRef.current.sessionId, move, rating },
        (ev) => {
          if (ev.type === 'board') onPreview(boardPreview(boardBlock(ev)));
          update((c) => applyEvent(c, botId, ev));
        },
        ac.signal,
      );
    } catch (e) {
      const aborted = e instanceof DOMException && e.name === 'AbortError';
      update((c) => patchTurn(c, botId, aborted ? { warnings: ['중단했습니다.'] } : { error: errorText(e) }));
    } finally {
      update((c) => patchTurn(c, botId, { streaming: false }));
      setBusy(false);
      abortRef.current = null;
    }
  }, [busy, gameId, ply, rating, onPreview, update]);
  const sendRef = useRef(send);
  sendRef.current = send;

  // A move made on the board becomes a question; the typed text, if any, is the question.
  useEffect(() => {
    if (!draft) return;
    onDraftConsumed();
    let question = input.trim();
    if (!question) {
      const target = draft.fen === review.fen_before ? `${review.san} 대신 ${draft.san}` : `여기서 ${draft.san}`;
      question = `${target}를 두면 어떤가요?`;
    }
    void sendRef.current(question, { fen: draft.fen, san: draft.san });
  }, [draft]);

  const stop = () => abortRef.current?.abort();
  const submit = (e: React.FormEvent) => { e.preventDefault(); void send(input, null); };
  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(input, null); }
  };
  const showBefore = () => onPreview({
    id: 'chat:before', fen: review.fen_before, lastMove: null, shapes: [],
    label: `${plyLabel(ply)} ${review.san} 직전 국면 · 보드에서 다른 수를 두어 보세요`,
  });

  const cls = review.classification;
  const best = review.alternatives?.find((a) => a.is_best) ?? null;
  const suggestions = [
    cls === 'inaccuracy' || cls === 'mistake' || cls === 'blunder'
      ? `왜 ${review.san}가 ${CLASS_LABEL[cls]}인가요?`
      : `${review.san}의 장단점을 설명해 주세요.`,
    best ? `${best.san}가 왜 더 좋은가요?` : null,
    '이 국면에서 계획은 무엇인가요?',
  ].filter((s): s is string => !!s);
  const atBefore = boardFen === review.fen_before;

  return (
    <div className="rv-chat" hidden={hidden}>
      {status && !status.available && (
        <div className="rv-error"><b>튜터를 부를 수 없습니다.</b> {status.reason} <span className="small muted">Claude Code(`claude`)가 설치되고 로그인돼 있어야 합니다.</span></div>
      )}
      <div className="rv-chat-log" ref={logRef}>
        {conv.turns.length === 0 && (
          <div className="rv-chat-intro">
            <div>이 수에 대해 튜터와 토론해 보세요. 추천 수가 납득이 안 되면 반박하세요. 튜터는 엔진과 탐지기로 확인한 것만 말하고, 설명하는 동안 보드를 움직입니다.</div>
            <div className="rv-chat-chips">
              {suggestions.map((s) => <button key={s} type="button" className="chip rv-chip-btn" disabled={busy} onClick={() => void send(s, null)}>{s}</button>)}
              <button type="button" className={`chip rv-chip-btn${atBefore ? ' rv-chip-on' : ''}`} onClick={showBefore} title="보드를 이 수 직전 국면으로 돌리고 직접 다른 수를 둡니다">이 수 대신 두어보기</button>
            </div>
            <div className="small muted">보드에서 기물을 움직이면 그 수가 그대로 질문이 됩니다. 입력창에 글을 써 두고 움직이면 그 글이 질문이 됩니다.</div>
          </div>
        )}
        {conv.turns.map((t) => (t.role === 'user' ? <UserTurn key={t.id} turn={t} /> : <BotTurn key={t.id} turn={t} preview={preview} onPreview={onPreview} />))}
      </div>
      <form className="rv-chat-form" onSubmit={submit}>
        <textarea value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={onKey} rows={2}
          placeholder={busy ? '튜터가 답하는 중입니다…' : '예: 왜 Nf5는 안 되나요? (Enter로 보내기)'} disabled={!!status && !status.available} />
        {busy
          ? <button type="button" className="btn btn-ghost" onClick={stop}>중단</button>
          : <button type="submit" className="btn btn-primary" disabled={!input.trim() || (!!status && !status.available)}>보내기</button>}
      </form>
      <div className="rv-chat-foot small muted">
        <span>Claude Code 구독으로 실행{status?.model ? ` · ${status.model}` : ''}</span>
        {conv.limits && <span>5시간 사용량 {pct(conv.limits.fiveHour)} · 주간 {pct(conv.limits.sevenDay)}</span>}
        {conv.turns.length > 0 && !busy && <button type="button" className="chip rv-chip-btn" onClick={showBefore}>이 수 대신 두어보기</button>}
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
          return (
            <div key={i} className={`rv-chat-tool${b.ok === false ? ' fail' : ''}`}>
              {b.ok === null && turn.streaming ? '⋯' : b.ok === false ? '✕' : '✓'} {TOOL_LABEL[b.name] ?? b.name}{toolSummary(b) ? ` · ${toolSummary(b)}` : ''}
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
