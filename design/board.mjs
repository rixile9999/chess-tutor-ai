import { pieceSVG } from './pieces.mjs';

const FILES = 'abcdefgh';

export function parseFEN(fen) {
  const rows = fen.split(' ')[0].split('/');
  const board = {};
  rows.forEach((row, i) => {
    const rank = 8 - i;
    let file = 0;
    for (const ch of row) {
      if (/\d/.test(ch)) { file += Number(ch); continue; }
      board[FILES[file] + rank] = ch;
      file += 1;
    }
  });
  return board;
}

// Square -> {col,row} in rendered grid (0..7), honouring flip.
function pos(sq, flip) {
  const f = FILES.indexOf(sq[0]);
  const r = Number(sq[1]) - 1;
  return flip ? { col: 7 - f, row: r } : { col: f, row: 7 - r };
}

/**
 * Render a board.
 * opts: { size, flip, light, dark, ink, paper, lastMove:[from,to],
 *         arrows:[{from,to,color,dashed}], marks:[{sq,color}],
 *         heat:{sq: 0..1}, heatColor, coords:boolean }
 */
export function boardHTML(fen, opts = {}) {
  const {
    size = 512, flip = false,
    light = '#ecdfc5', dark = '#a98b68', ink = '#2b2622', paper = '#f8f3ea',
    lastMove = null, arrows = [], marks = [], heat = null, heatColor = '36,120,166',
    coords = true, pieces = true,
  } = opts;
  const sq = size / 8;
  const board = parseFEN(fen);
  let squares = '';
  for (let row = 0; row < 8; row++) {
    for (let col = 0; col < 8; col++) {
      const f = flip ? 7 - col : col;
      const r = flip ? row : 7 - row;
      const name = FILES[f] + (r + 1);
      const isLight = (f + r) % 2 === 1;
      let bg = isLight ? light : dark;
      let overlay = '';
      if (lastMove && (lastMove[0] === name || lastMove[1] === name)) {
        overlay += `<div style="position: absolute; inset: 0; background: rgba(194, 90, 60, 0.28)"></div>`;
      }
      if (heat && heat[name] != null) {
        overlay += `<div style="position: absolute; inset: 0; background: rgba(${heatColor}, ${(0.12 + heat[name] * 0.78).toFixed(2)})"></div>`;
      }
      const mark = marks.find(m => m.sq === name);
      if (mark) {
        overlay += `<div style="position: absolute; inset: 3px; border: 3px solid ${mark.color}; border-radius: 4px"></div>`;
      }
      let coordHTML = '';
      if (coords) {
        const cc = isLight ? dark : light;
        if (col === 0) coordHTML += `<span style="position: absolute; top: 2px; left: 3px; font-size: 10px; font-weight: 600; color: ${cc}">${r + 1}</span>`;
        if (row === 7) coordHTML += `<span style="position: absolute; bottom: 1px; right: 4px; font-size: 10px; font-weight: 600; color: ${cc}">${FILES[f]}</span>`;
      }
      const pc = board[name];
      const pieceHTML = pieces && pc
        ? `<div style="position: absolute; inset: ${Math.round(sq * 0.06)}px">${pieceSVG(pc, pc === pc.toUpperCase() ? 'w' : 'b', Math.round(sq * 0.88), ink, paper)}</div>`
        : '';
      squares += `<div style="position: relative; background: ${bg}">${overlay}${coordHTML}${pieceHTML}</div>`;
    }
  }
  // arrows overlay
  let arrowSVG = '';
  if (arrows.length) {
    const lines = arrows.map(a => {
      const p1 = pos(a.from, flip), p2 = pos(a.to, flip);
      const x1 = (p1.col + 0.5) * sq, y1 = (p1.row + 0.5) * sq;
      const x2 = (p2.col + 0.5) * sq, y2 = (p2.row + 0.5) * sq;
      const dx = x2 - x1, dy = y2 - y1, len = Math.hypot(dx, dy);
      const ux = dx / len, uy = dy / len;
      const head = sq * 0.32, w = sq * 0.11;
      const bx = x2 - ux * head, by = y2 - uy * head;      // base of head
      const sx = x1 + ux * sq * 0.28, sy = y1 + uy * sq * 0.28; // start inset
      const px = -uy, py = ux;
      const dash = a.dashed ? ` stroke-dasharray="${sq * 0.18} ${sq * 0.12}"` : '';
      const line = `<line x1="${sx.toFixed(1)}" y1="${sy.toFixed(1)}" x2="${bx.toFixed(1)}" y2="${by.toFixed(1)}" stroke="${a.color}" stroke-width="${(w * 1.2).toFixed(1)}" stroke-linecap="round" opacity="0.9"${dash}></line>`;
      const poly = `<polygon points="${x2.toFixed(1)},${y2.toFixed(1)} ${(bx + px * w * 1.9).toFixed(1)},${(by + py * w * 1.9).toFixed(1)} ${(bx - px * w * 1.9).toFixed(1)},${(by - py * w * 1.9).toFixed(1)}" fill="${a.color}" opacity="0.9"></polygon>`;
      return line + poly;
    }).join('');
    arrowSVG = `<svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" style="position: absolute; inset: 0; pointer-events: none">${lines}</svg>`;
  }
  return `<div style="position: relative; width: ${size}px; height: ${size}px; display: grid; grid-template-columns: repeat(8, minmax(0, 1fr)); grid-template-rows: repeat(8, minmax(0, 1fr)); border-radius: 4px; overflow: hidden; box-shadow: 0 0 0 1px rgba(43, 38, 34, 0.25)">${squares}</div>` +
    (arrowSVG ? `<div style="position: absolute; inset: 0; pointer-events: none">${arrowSVG}</div>` : '');
}

// Wrap board + arrows in a relative container of the board's size.
export function boardBlock(fen, opts = {}) {
  const size = opts.size || 512;
  return `<div style="position: relative; width: ${size}px; height: ${size}px">${boardHTML(fen, opts)}</div>`;
}
