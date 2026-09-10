import { START_FEN, moveNumberOf, sideToMoveOf, type PlayResult, type Ply } from './state';

export type PgnHeaders = Record<string, string | number | null | undefined>;

/** Seven Tag Roster order first, then SetUp/FEN, then whatever the caller added. */
const ROSTER = ['Event', 'Site', 'Date', 'Round', 'White', 'Black', 'Result'];

function escape(v: string): string {
  return v.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

/** "1. e4 e5 2. Nf3" from a start FEN — a black-to-move start opens with "24... Nf6". */
export function movetext(startFen: string, sans: string[]): string {
  const first = moveNumberOf(startFen);
  const blackFirst = sideToMoveOf(startFen) === 'black';
  const tokens: string[] = [];
  sans.forEach((san, i) => {
    const p = i + (blackFirst ? 1 : 0);
    if (p % 2 === 0) tokens.push(`${first + Math.floor(p / 2)}.`);
    else if (i === 0) tokens.push(`${first}...`);
    tokens.push(san);
  });
  return tokens.join(' ');
}

/** Wrap at 80 columns on token boundaries, as PGN export format asks for. */
function wrap(text: string, width = 80): string {
  const out: string[] = [];
  let line = '';
  for (const token of text.split(' ')) {
    if (!line) line = token;
    else if (line.length + 1 + token.length <= width) line += ` ${token}`;
    else { out.push(line); line = token; }
  }
  if (line) out.push(line);
  return out.join('\n');
}

/**
 * PGN for a practice game. The server rebuilds its own headers on save (docs 4.5); this is the
 * text behind "PGN 복사" and the fallback when the user wants the game outside the app.
 */
export function buildPgn(input: {
  startFen: string;
  plies: Ply[];
  result: PlayResult;
  headers?: PgnHeaders;
}): string {
  const { startFen, plies, result } = input;
  const headers: PgnHeaders = { Event: 'chess-tutor practice', Site: 'chess-tutor', ...input.headers, Result: result };
  const lines: string[] = [];
  const seen = new Set<string>();
  const put = (k: string) => {
    const v = headers[k];
    if (v === null || v === undefined || v === '' || seen.has(k)) return;
    seen.add(k);
    lines.push(`[${k} "${escape(String(v))}"]`);
  };
  for (const k of ROSTER) put(k);
  if (startFen !== START_FEN) { lines.push('[SetUp "1"]'); lines.push(`[FEN "${escape(startFen)}"]`); }
  for (const k of Object.keys(headers)) put(k);
  const body = movetext(startFen, plies.map((p) => p.san));
  return `${lines.join('\n')}\n\n${wrap(body ? `${body} ${result}` : result)}\n`;
}
