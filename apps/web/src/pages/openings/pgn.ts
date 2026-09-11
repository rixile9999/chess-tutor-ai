// 일지 내보내기 (M8d-4, plan §10.1 row 6): the line on the board as a PGN whose every move
// carries the journal's one-line explanation as a {comment}.
//
// pages/play/pgn.ts writes a *game* (a result, no comments); a study is the other shape, so the
// movetext is built here and only the FEN helpers are shared. Pure: index.tsx passes the moves.
import { START_FEN, moveNumberOf, sideToMoveOf } from '../play/state';

export type PgnHeaders = Record<string, string | number | null | undefined>;

export interface JournalMove {
  san: string;
  /** The journal's one-line text; empty or missing leaves the move without a comment. */
  text?: string | null;
}

/** Seven Tag Roster first (PGN needs all seven), then the opening tags, then the caller's. */
const ROSTER = ['Event', 'Site', 'Date', 'Round', 'White', 'Black', 'Result'];
const WIDTH = 80;

function escape(value: string): string {
  return value.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

/** "2026.09.11" — the PGN date format; `??` is what the spec asks for when a field is unknown. */
export function pgnDate(now: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}.${pad(now.getMonth() + 1)}.${pad(now.getDate())}`;
}

/**
 * One explanation as PGN comment text: the verifier's `[[…]]` marks come off, newlines become
 * spaces, and braces are swapped for parentheses because `}` would end the comment.
 */
export function pgnComment(text: string): string {
  return text.replace(/\[\[|\]\]/g, '').replace(/[{}]/g, (c) => (c === '{' ? '(' : ')')).replace(/\s+/g, ' ').trim();
}

/** Wrap at 80 columns on space boundaries, as PGN export format asks for. */
function wrap(text: string): string {
  const out: string[] = [];
  let line = '';
  for (const token of text.split(' ')) {
    if (!line) line = token;
    else if (line.length + 1 + token.length <= WIDTH) line += ` ${token}`;
    else { out.push(line); line = token; }
  }
  if (line) out.push(line);
  return out.join('\n');
}

/**
 * "1. e4 {…} 1... e5 {…} 2. Nf3" — a Black move that follows a comment repeats the move number
 * with three dots, which is what strict readers expect after a comment.
 */
export function studyMovetext(startFen: string, moves: JournalMove[]): string {
  const first = moveNumberOf(startFen);
  const blackFirst = sideToMoveOf(startFen) === 'black';
  const tokens: string[] = [];
  let commented = false;
  moves.forEach((move, i) => {
    const p = i + (blackFirst ? 1 : 0);
    const number = first + Math.floor(p / 2);
    if (p % 2 === 0) tokens.push(`${number}.`);
    else if (i === 0 || commented) tokens.push(`${number}...`);
    tokens.push(move.san);
    const comment = pgnComment(move.text ?? '');
    if (comment) tokens.push(`{${comment}}`);
    commented = !!comment;
  });
  return tokens.join(' ');
}

/**
 * The whole study: headers, then the commented movetext, ending in `*` (a study has no result).
 * `name`/`eco` are the opening of the position the board is on.
 */
export function buildStudyPgn(input: {
  moves: JournalMove[];
  startFen?: string;
  name?: string | null;
  eco?: string | null;
  headers?: PgnHeaders;
  now?: Date;
}): string {
  const startFen = input.startFen ?? START_FEN;
  const headers: PgnHeaders = {
    Event: 'chess-tutor opening study',
    Site: 'chess-tutor',
    Date: pgnDate(input.now),
    Round: '-',
    White: '?',
    Black: '?',
    Result: '*',
    Opening: input.name ?? undefined,
    ECO: input.eco ?? undefined,
    ...input.headers,
  };
  const lines: string[] = [];
  const seen = new Set<string>();
  const put = (key: string) => {
    const value = headers[key];
    if (value === null || value === undefined || value === '' || seen.has(key)) return;
    seen.add(key);
    lines.push(`[${key} "${escape(String(value))}"]`);
  };
  for (const key of ROSTER) put(key);
  if (startFen !== START_FEN) { lines.push('[SetUp "1"]'); lines.push(`[FEN "${escape(startFen)}"]`); }
  for (const key of Object.keys(headers)) put(key);
  const body = studyMovetext(startFen, input.moves);
  return `${lines.join('\n')}\n\n${wrap(body ? `${body} *` : '*')}\n`;
}
