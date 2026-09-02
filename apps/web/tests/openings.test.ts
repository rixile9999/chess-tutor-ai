import { describe, expect, it } from 'vitest';
import { defaultPiece, mirrorPiece, pieceOptions } from '../src/pages/openings/Heatmap';
import { api } from '../src/api/client';

/** GET /openings/heatmap takes `<colour w|b><start square>` (services/openings_map.py parse_piece). */
const CODE = /^[wb][a-h][18]$/;

describe('piece codes match the heatmap API contract', () => {
  it('white options are w + a first-rank square', () => {
    const codes = pieceOptions('white').map((o) => o.code);
    expect(codes).toEqual(['wf1', 'wc1', 'wg1', 'wb1', 'wd1', 'wa1', 'wh1', 'we1']);
    for (const c of codes) expect(c).toMatch(CODE);
  });

  it('black options are b + an eighth-rank square', () => {
    const codes = pieceOptions('black').map((o) => o.code);
    expect(codes).toEqual(['bf8', 'bc8', 'bg8', 'bb8', 'bd8', 'ba8', 'bh8', 'be8']);
    for (const c of codes) expect(c).toMatch(CODE);
  });

  it('never emits a piece letter as the first character (the old ng8 / qd8 / bf1 bug)', () => {
    const all = [...pieceOptions('white'), ...pieceOptions('black')].map((o) => o.code);
    for (const bad of ['ng8', 'nb8', 'qd8', 'ra8', 'rh8', 'ke8', 'bf1']) expect(all).not.toContain(bad);
  });

  it('labels name the side, the square and the piece', () => {
    const byCode = new Map(pieceOptions('black').map((o) => [o.code, o.label]));
    expect(byCode.get('bg8')).toBe('흑 g8 나이트');
    expect(byCode.get('bd8')).toBe('흑 d8 퀸');
    expect(pieceOptions('white').find((o) => o.code === 'we1')?.label).toBe('백 e1 킹');
  });

  it('defaults to the light-squared bishop of the right colour', () => {
    expect(defaultPiece('white')).toBe('wf1');
    expect(defaultPiece('black')).toBe('bf8');
  });

  it('mirroring swaps both the colour letter and the rank, keeping the file', () => {
    expect(mirrorPiece('wg1', 'black')).toBe('bg8');
    expect(mirrorPiece('bd8', 'white')).toBe('wd1');
    expect(mirrorPiece('ba8', 'white')).toBe('wa1');
  });

  it('falls back to the default for an unknown code', () => {
    expect(mirrorPiece('zz9', 'white')).toBe('wf1');
    expect(mirrorPiece('', 'black')).toBe('bf8');
  });
});

function captureUrl(): { urls: string[]; restore: () => void } {
  const urls: string[] = [];
  const real = globalThis.fetch;
  globalThis.fetch = ((input: RequestInfo | URL) => {
    urls.push(String(input));
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) } as Response);
  }) as typeof fetch;
  return { urls, restore: () => { globalThis.fetch = real; } };
}

describe('openings map request', () => {
  it('sends min_games so the server does not prune at its default of 2', async () => {
    const { urls, restore } = captureUrl();
    try {
      await api.openings.map('duke', 'black', 12, 1);
      await api.openings.map('duke', 'black', 16, 3);
    } finally { restore(); }
    expect(urls[0]).toContain('min_games=1');
    expect(urls[0]).toContain('color=black');
    expect(urls[0]).toContain('depth=12');
    expect(urls[1]).toContain('min_games=3');
  });

  it('clamps to the API minimum of 1 so "show everything" stays a valid request', async () => {
    const { urls, restore } = captureUrl();
    try {
      await api.openings.map('duke', 'white', 12, 0);
      await api.openings.map('duke', 'white', 12, -5);
      await api.openings.map('duke', 'white');
    } finally { restore(); }
    for (const url of urls) expect(url).toContain('min_games=1');
  });
});
