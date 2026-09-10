import { useEffect, useRef, type ReactNode } from 'react';
import { MiniBoard } from '../MiniBoard';
import type { Preview } from '../../lib/shapes';
import { TOOL_LABEL, boardPreview, toolSummary, type Turn } from './model';
import './chat.css';

type LogProps = {
  turns: Turn[];
  busy: boolean;
  hidden?: boolean;
  preview: Preview | null;
  onPreview: (p: Preview | null) => void;
  /** Tooltip on a board the tutor showed. */
  boardHint?: string;
  /** Shown while the conversation is empty. */
  children?: ReactNode;
};

/** The scrolling transcript. It follows the answer unless the user scrolled up to read. */
export function ChatLog({ turns, busy, hidden, preview, onPreview, boardHint, children }: LogProps) {
  const logRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const countRef = useRef(turns.length);
  if (turns.length > countRef.current) stickRef.current = true; // a new question always scrolls down
  countRef.current = turns.length;
  useEffect(() => {
    const el = logRef.current;
    if (el && !hidden && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [turns, hidden]);
  const onScroll = () => {
    const el = logRef.current;
    if (el) stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };
  return (
    <div className="ch-chat-log" ref={logRef} onScroll={onScroll} role="log" aria-live="polite" aria-busy={busy}>
      {turns.length === 0 && children}
      {turns.map((t) => (t.role === 'user'
        ? <UserTurn key={t.id} turn={t} />
        : <BotTurn key={t.id} turn={t} preview={preview} onPreview={onPreview} boardHint={boardHint} />))}
    </div>
  );
}

export function UserTurn({ turn }: { turn: Turn }) {
  return (
    <div className="ch-chat-turn">
      <div className="ch-chat-user">{turn.move && <span className="ch-chat-move">{turn.move.san}</span>}{turn.text}</div>
    </div>
  );
}

type BotProps = { turn: Turn; preview: Preview | null; onPreview: (p: Preview | null) => void; boardHint?: string };

/** One answer: text with its unverified squares, the tools it ran, and the boards it showed. */
export function BotTurn({ turn, preview, onPreview, boardHint = '보드에서 보기' }: BotProps) {
  const last = turn.blocks[turn.blocks.length - 1];
  const pending = turn.streaming && (!last || (last.kind === 'tool' && last.ok === null));
  return (
    <div className="ch-chat-turn">
      {turn.blocks.map((b, i) => {
        if (b.kind === 'text') {
          return (
            <div key={i}>
              <p className="ch-chat-text">{b.text}</p>
              {b.unverified.length > 0 && <div className="ch-chat-unverified"><IconWarn /> 근거 미확인 칸: {b.unverified.join(', ')}</div>}
            </div>
          );
        }
        if (b.kind === 'tool') {
          const summary = toolSummary(b);
          return (
            <div key={i} className={`ch-chat-tool${b.ok === false ? ' fail' : ''}`}>
              {b.ok === null && turn.streaming ? '⋯' : b.ok === false ? '✕' : '✓'} {TOOL_LABEL[b.name] ?? b.name}{summary ? ` · ${summary}` : ''}
            </div>
          );
        }
        const p = boardPreview(b);
        const on = preview?.id === p.id;
        return (
          <button key={i} type="button" className={`ch-chat-board${on ? ' active' : ''}`} onClick={() => onPreview(on ? null : p)} title={boardHint}>
            <MiniBoard fen={b.fen} size={72} highlight={b.lastMove ?? []} />
            <div>
              {b.moves.length > 0 && <div className="ch-chat-board-moves">{b.moves.join(' ')}</div>}
              <div className="ch-chat-board-cap">{b.caption || '이 국면'}</div>
              <div className="ch-chat-board-hint">{on ? '보드에서 보는 중' : boardHint}</div>
            </div>
          </button>
        );
      })}
      {pending && <div className="ch-chat-thinking">{last && last.kind === 'tool' ? `${TOOL_LABEL[last.name] ?? last.name} 중…` : '생각하는 중…'}</div>}
      {turn.warnings.map((w, i) => <div key={`w${i}`} className="small muted">{w}</div>)}
      {turn.error && <div className="ch-chat-error"><b>답을 받지 못했습니다.</b> {turn.error}</div>}
    </div>
  );
}

const S = { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const };
function IconWarn() { return <svg {...S} width="16" height="16" strokeWidth="2.2" style={{ color: 'var(--bad)' }}><path d="M12 3l9.5 17h-19z" /><path d="M12 9v5M12 17.5v.5" /></svg>; }
