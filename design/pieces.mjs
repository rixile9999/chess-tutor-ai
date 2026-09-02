// Original flat chess piece silhouettes, viewBox 0 0 45 45.
// Each piece: array of parts. { d, kind: 'body' | 'detail' }
// body parts fill with the piece colour; detail parts stroke in the contrast colour.

export const PIECES = {
  p: [
    { kind: 'body', d: 'M22.5 10.5a4.2 4.2 0 0 0-2.4 7.7c-2.6 1-4.4 3.4-4.4 6.2 0 2 .9 3.8 2.3 5C14.6 30.9 12.5 34.5 12.5 38.5h20c0-4-2.1-7.6-5.5-9.1 1.4-1.2 2.3-3 2.3-5 0-2.8-1.8-5.2-4.4-6.2a4.2 4.2 0 0 0-2.4-7.7z' },
  ],
  r: [
    { kind: 'body', d: 'M12 38.5V35h2v-4h1.5l1-3V15.5h-3v-6h4v3h3v-3h3v3h3v-3h4v6h-3V28l1 3H31v4h2v3.5z' },
    { kind: 'detail', d: 'M16.5 28h12' },
  ],
  n: [
    { kind: 'body', d: 'M14 38.5V35h1.5c0-5 2.5-7.5 5-9.5-1.6.9-3.5 1.9-5.5 1.9-1.6 0-3-1.2-3-2.7 0-1.1.6-2 1.4-2.8l3.1-3.5c.6-1.6 1.7-3.3 2.8-4.6l-.2-3.2 2.1 1.9 2.2-2.4.8 3c1.1.2 2.2.6 3.3 1.1 4.6 2.2 9.5 8.5 9.5 20.8h1.5v3.5z' },
    { kind: 'detail', d: 'M20.2 17.4a.9.9 0 1 0 .1 0M13.6 24.6a.6.6 0 1 0 .1 0' },
  ],
  b: [
    { kind: 'body', d: 'M13 38.5V35h2l1-3c-1.5-2-2.5-4.5-2.5-7 0-6 4-11 9-13.5 5 2.5 9 7.5 9 13.5 0 2.5-1 5-2.5 7l1 3h2v3.5z' },
    { kind: 'body', d: 'M22.5 6.2a2.4 2.4 0 1 0 .1 0' },
    { kind: 'detail', d: 'M22.5 15.5v9M20 20h5' },
  ],
  q: [
    { kind: 'body', d: 'M12 38.5V35h2v-3l-1.5-2L9 15l6.5 7 2-11 5 9 5-9 2 11 6.5-7-3.5 15-1.5 2v3h2v3.5z' },
    { kind: 'body', d: 'M22.5 5.9a1.9 1.9 0 1 0 .1 0M17.5 8.4a1.7 1.7 0 1 0 .1 0M27.5 8.4a1.7 1.7 0 1 0 .1 0M9 12.6a1.7 1.7 0 1 0 .1 0M36 12.6a1.7 1.7 0 1 0 .1 0' },
    { kind: 'detail', d: 'M13 32h19' },
  ],
  k: [
    { kind: 'body', d: 'M21.4 4.5h2.2v3h3v2.2h-3v4h-2.2v-4h-3V7.5h3z' },
    { kind: 'body', d: 'M12 38.5V35h2v-4c-1.2-2.6-1.4-5.5.7-8.2-3.5-1.4-5.4-4.3-4.2-7.2 1.1-2.7 4.5-3.9 7.5-2.5 2 1 3.4 2.7 4.5 5 1.1-2.3 2.5-4 4.5-5 3-1.4 6.4-.2 7.5 2.5 1.2 2.9-.7 5.8-4.2 7.2 2.1 2.7 1.9 5.6.7 8.2v4h2v3.5z' },
    { kind: 'body', d: 'M21 12.5h3v8h-3z' },
    { kind: 'detail', d: 'M14 31h17' },
  ],
};

// Render one piece as inline SVG. colour: 'w' | 'b'.
export function pieceSVG(letter, colour, size, ink = '#2b2622', paper = '#f8f3ea') {
  const parts = PIECES[letter.toLowerCase()];
  const fill = colour === 'w' ? paper : ink;
  const contrast = colour === 'w' ? ink : paper;
  const body = parts.filter(p => p.kind === 'body').map(p =>
    `<path d="${p.d}" fill="${fill}" stroke="${ink}" stroke-width="1.3" stroke-linejoin="round"></path>`).join('');
  const detail = parts.filter(p => p.kind === 'detail').map(p =>
    `<path d="${p.d}" fill="none" stroke="${contrast}" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"></path>`).join('');
  return `<svg viewBox="0 0 45 45" width="${size}" height="${size}" style="display: block">${body}${detail}</svg>`;
}
