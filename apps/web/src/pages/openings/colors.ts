// Colour and number formatting shared by the opening page. No layout, no React.
import type { Classification } from '../../api/types';
import { CLASS_LABEL } from '../../lib/labels';
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

// ---------- 수 해설 배지 (M8) ----------
export type BadgeTone = 'good' | 'bad' | 'warn' | 'neutral';
export interface MoveBadge { tone: BadgeTone; text: string }

/** `badge` classes for a tone; 경고색은 이 페이지에서만 쓰므로 openings.css의 클래스를 씁니다. */
export const badgeClass = (tone: BadgeTone): string =>
  `badge ${tone === 'warn' ? 'op-badge-warn' : `badge-${tone}`}`;

/** MoveFact.kind -> the one-word badge; unknown kinds fall through to the kind itself. */
export const FACT_LABEL: Record<string, string> = {
  book: '책', name: '이름', transposition: '전위', center: '중앙', development: '전개',
  castling: '캐슬링', fianchetto: '피안케토', tension: '긴장', break: '브레이크', gambit: '갬빗',
  motif: '전술', plan: '계획', setup: '셋업', prophylaxis: '예방', naturalness: '자연스러움', engine: '엔진',
};
/** Facts that already have a badge of their own (or none): they are not repeated as a plain chip. */
const FACT_SKIP = new Set(['book', 'name', 'transposition', 'naturalness', 'engine']);

/** Badges for a move, most telling first (⚑이름 / ⚠책 밖 / 전위 / 사실 종류 / 마이아). Engine gets its own. */
export function moveBadges(a: {
  in_book: boolean; name_before: string | null; name_after: string | null; transposition: boolean;
  facts: { kind: string }[]; naturalness: number | null;
}): MoveBadge[] {
  const out: MoveBadge[] = [];
  const name = (a.name_after ?? '').trim();
  if (name && name !== (a.name_before ?? '').trim()) out.push({ tone: 'good', text: `⚑ ${name}` });
  if (!a.in_book) out.push({ tone: 'warn', text: '⚠ 책 밖' });
  if (a.transposition) out.push({ tone: 'neutral', text: '전위' });
  const seen = new Set<string>();
  for (const f of a.facts ?? []) {
    if (FACT_SKIP.has(f.kind) || seen.has(f.kind)) continue;
    seen.add(f.kind);
    out.push({ tone: 'neutral', text: FACT_LABEL[f.kind] ?? f.kind });
  }
  if (a.naturalness !== null && a.naturalness !== undefined) {
    out.push({ tone: 'neutral', text: `이 레이팅대 ${pct(a.naturalness)}가 두는 수` });
  }
  return out;
}

/** 엔진 판정 배지. `win_loss`는 0..1 승률 손실이라 퍼센트로 적습니다. */
export function engineBadge(e: { classification: string; win_loss: number } | null | undefined): MoveBadge | null {
  if (!e) return null;
  const tone: BadgeTone = e.classification === 'blunder' || e.classification === 'mistake' ? 'bad'
    : e.classification === 'inaccuracy' ? 'warn' : 'neutral';
  const label = CLASS_LABEL[e.classification as Classification] ?? e.classification;
  return { tone, text: `엔진 ${label} −${Math.round((e.win_loss ?? 0) * 100)}%` };
}
