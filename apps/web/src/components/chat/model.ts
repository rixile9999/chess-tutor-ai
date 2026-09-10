import type { Key } from 'chessground/types';
import type { BoardEvent, ChatEvent, ChatMove } from '../../api/chat';
import { arrowShapes, type Preview } from '../../lib/shapes';
import type { BoardShape } from '../Board';

/** The tutor conversation model, shared by 리뷰 (a stored ply) and 대국 (a live position). */

export type TextBlock = { kind: 'text'; text: string; unverified: string[] };
export type ToolBlock = { kind: 'tool'; id: string; name: string; input: Record<string, unknown> | null; ok: boolean | null };
export type BoardBlock = {
  kind: 'board'; id: string; fen: string; startFen: string; moves: string[];
  lastMove: [string, string] | null; shapes: BoardShape[]; caption: string;
};
export type Block = TextBlock | ToolBlock | BoardBlock;
export type Turn = {
  id: number; role: 'user' | 'assistant'; text: string; move: ChatMove | null; blocks: Block[];
  error: string | null; warnings: string[]; streaming: boolean; ok: boolean | null;
};
export type Limits = { fiveHour: number | null; sevenDay: number | null };
export type Conversation = { sessionId: string | null; turns: Turn[]; limits: Limits | null };

export const EMPTY: Conversation = { sessionId: null, turns: [], limits: null };
export const MAX_QUESTION = 4000;
export const TOOL_LABEL: Record<string, string> = {
  analyse: '엔진 분석', show_board: '보드 표시', compare: '두 수 비교', motifs: '전술 확인', maia_probs: 'Maia 확률', features: '국면 특징',
};

export function toolSummary(b: ToolBlock): string {
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

/** A show_board event as a block; `scope` keeps ids unique across panels. */
export function boardBlock(ev: BoardEvent, turnId: number, scope = 'chat'): BoardBlock {
  const shapes = arrowShapes(ev.arrows, ev.highlights);
  if (ev.last_move) shapes.push({ orig: ev.last_move[0] as Key, dest: ev.last_move[1] as Key, brush: 'paleGrey', modifiers: { lineWidth: 6 } });
  return { kind: 'board', id: `${scope}:${turnId}:${ev.n}`, fen: ev.fen, startFen: ev.start_fen, moves: ev.moves, lastMove: ev.last_move, shapes, caption: ev.caption };
}

export function boardPreview(b: BoardBlock): Preview {
  return { id: b.id, fen: b.fen, label: b.caption || (b.moves.length ? b.moves.join(' ') : '튜터가 보여준 국면'), lastMove: b.lastMove, shapes: b.shapes };
}

export function patchTurn(c: Conversation, turnId: number, fn: (t: Turn) => Turn): Conversation {
  const idx = c.turns.findIndex((t) => t.id === turnId);
  if (idx < 0) return c;
  const next = fn(c.turns[idx]);
  if (next === c.turns[idx]) return c;
  const turns = [...c.turns];
  turns[idx] = next;
  return { ...c, turns };
}

/** The two turns a question opens: what the student asked, and the answer being written. */
export function askedTurns(userId: number, botId: number, question: string, move: ChatMove | null): Turn[] {
  const blank = { blocks: [], error: null, warnings: [], ok: null };
  return [
    { id: userId, role: 'user', text: question, move, streaming: false, ...blank },
    { id: botId, role: 'assistant', text: '', move: null, streaming: true, ...blank },
  ];
}

/** Fold one server event into the conversation; `turnId` is the assistant turn being written. */
export function applyEvent(c: Conversation, turnId: number, ev: ChatEvent, scope = 'chat'): Conversation {
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
        blocks.push(boardBlock(ev, turnId, scope));
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

/** Usage share in the limits footer; "?" until the first limits event arrives. */
export function usage(v: number | null): string {
  return v === null ? '?' : `${Math.round(v <= 1 ? v * 100 : v)}%`;
}
