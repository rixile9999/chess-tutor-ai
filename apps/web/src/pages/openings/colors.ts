// Colour and number formatting shared by the opening explorer. No layout, no React.
export const TABIYA_SUFFIX = ' 타비야';
export const DEVIATION_SUFFIX = ' 책 이탈';

type Rgb = [number, number, number];
const FALLBACK = { bad: '#c25a3c', good: '#2478a6', neutral: '#9b9187' };

function hexToRgb(h: string): Rgb | null {
  const m = /^#([0-9a-f]{6})$/i.exec(h.trim());
  if (!m) return null;
  return [1, 3, 5].map((i) => parseInt(m[1].slice(i - 1, i + 1), 16)) as Rgb;
}
let palette: { bad: Rgb; good: Rgb; neutral: Rgb } | null = null;
function tokens() {
  if (palette) return palette;
  let bad = FALLBACK.bad, good = FALLBACK.good;
  try {
    const cs = getComputedStyle(document.documentElement);
    bad = cs.getPropertyValue('--bad').trim() || bad;
    good = cs.getPropertyValue('--good').trim() || good;
  } catch { /* no DOM */ }
  palette = {
    bad: hexToRgb(bad) ?? hexToRgb(FALLBACK.bad)!,
    good: hexToRgb(good) ?? hexToRgb(FALLBACK.good)!,
    neutral: hexToRgb(FALLBACK.neutral)!,
  };
  return palette;
}
const lerp = (a: number, b: number, t: number) => Math.round(a + (b - a) * t);

/** 0..1 score (or 0..100) -> bad at <= 0.3, neutral grey at 0.5, good at >= 0.7. */
export function scoreColor(score: number | null | undefined): string {
  const s = norm01(score);
  if (s === null) return `rgb(${tokens().neutral.join(', ')})`;
  const { bad, good, neutral } = tokens();
  const t = Math.max(-1, Math.min(1, (s - 0.5) / 0.2));
  const to = t < 0 ? bad : good, k = Math.abs(t);
  return `rgb(${lerp(neutral[0], to[0], k)}, ${lerp(neutral[1], to[1], k)}, ${lerp(neutral[2], to[2], k)})`;
}

/** Score as a 0..1 fraction; tolerates percentages. */
export function norm01(score: number | null | undefined): number | null {
  if (score === null || score === undefined || Number.isNaN(score)) return null;
  return score > 1 ? Math.min(1, score / 100) : Math.max(0, score);
}

export const pct = (score: number | null | undefined): string => {
  const s = norm01(score);
  return s === null ? '-' : `${Math.round(s * 100)}%`;
};

/** `badge-good` / `badge-bad` / `badge-neutral` for a 0..1 score. */
export function scoreTone(score: number | null | undefined): string {
  const s = norm01(score);
  return s === null ? 'badge-neutral' : s >= 0.55 ? 'badge-good' : s <= 0.45 ? 'badge-bad' : 'badge-neutral';
}

/** A cell tint: the score colour mixed into the surface so text stays readable. */
export const scoreTint = (score: number | null | undefined, mix: number): string =>
  `color-mix(in srgb, ${scoreColor(score)} ${mix}%, var(--surface))`;

/** "Queen's Gambit Accepted: Old Variation" -> "Old Variation". */
export function shortName(name: string | null | undefined): string {
  const v = (name ?? '').trim();
  const i = v.lastIndexOf(':');
  return i >= 0 ? v.slice(i + 1).trim() || v : v;
}

/** The API appends " 타비야" / " 책 이탈" to the move label; badges say that already. */
export function plainLabel(label: string | null | undefined): string {
  let v = (label ?? '').trim();
  for (const suffix of [TABIYA_SUFFIX, DEVIATION_SUFFIX]) {
    if (v.endsWith(suffix)) v = v.slice(0, -suffix.length).trim();
  }
  return v;
}
