// Pure layout helpers for the opening DAG: filtering, layered ordering, positions, edge geometry, colours.
import type { OpeningEdge, OpeningMap, OpeningNode } from '../../api/types';

export const MINI = 84;
export const NODE_H = 122;
export const NOTE_H = 46;
export const NODE_W_MIN = 124;
export const NODE_W_MAX = 168;
export const COL_GAP = 19;
export const ROW_GAP = 28;
export const NOTE_W = 168;

export interface LaidNode {
  node: OpeningNode; x: number; y: number; w: number; h: number; text: string; col: number; row: number;
}
export interface LaidEdge {
  edge: OpeningEdge; d: string; width: number; stroke: string; master: boolean; key: string;
}
export interface DagLayout { nodes: LaidNode[]; edges: LaidEdge[]; width: number; height: number; byId: Map<string, LaidNode> }

const EMPTY: DagLayout = { nodes: [], edges: [], width: 0, height: 0, byId: new Map() };

// ---------- colours ----------
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

// ---------- text ----------
/** Rough width in px of a label at 11.5px mono (measured: ASCII 6.9px, Korean glyphs about 10px; padded a little). */
export function textW(s: string): number {
  let w = 0;
  for (const ch of s) w += /[ᄀ-ᇿ㄰-㆏가-힯一-鿿]/.test(ch) ? 10.6 : 7;
  return w;
}
/** Width of the games-count span (10.5px mono) next to the label. */
function countW(n: OpeningNode): number {
  return n.master_only ? 30 : String(n.games ?? 0).length * 6.4 + 1;
}
/** Card width: padding 8+8, label, 6px gap, count; clamped to the column width. */
export function nodeW(n: OpeningNode, text: string): number {
  return Math.round(Math.min(NODE_W_MAX, Math.max(NODE_W_MIN, 16 + textW(text) + 6 + countW(n))));
}

export function nodeText(n: OpeningNode): string {
  const base = (n.label || n.san || n.name || '?').trim();
  const name = n.name?.trim();
  if (n.is_tabiya && name) return name.includes('타비야') ? name : `${name} 타비야`;
  if (name && !base.includes(name) && 22 + textW(`${base} ${name}`) + countW(n) <= NODE_W_MAX) return `${base} ${name}`;
  return base;
}

// ---------- layout ----------
const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;

export function layoutMap(map: OpeningMap | null | undefined, minGames: number): DagLayout {
  if (!map) return EMPTY;
  const allNodes = map.nodes ?? [];
  const allEdges = map.edges ?? [];
  const root = map.root;

  // 1. filter by my game count (master-only branches and the root always stay), then keep what the root reaches.
  const kept = new Map<string, OpeningNode>();
  for (const n of allNodes) {
    if (n.id === root || n.master_only || (n.games ?? 0) >= minGames) kept.set(n.id, n);
  }
  if (kept.has(root)) {
    const adj = new Map<string, string[]>();
    for (const e of allEdges) {
      if (kept.has(e.source) && kept.has(e.target)) adj.set(e.source, [...(adj.get(e.source) ?? []), e.target]);
    }
    const seen = new Set<string>([root]);
    const queue = [root];
    while (queue.length) {
      const id = queue.shift()!;
      for (const t of adj.get(id) ?? []) if (!seen.has(t)) { seen.add(t); queue.push(t); }
    }
    for (const id of [...kept.keys()]) if (!seen.has(id)) kept.delete(id);
  }
  const list = [...kept.values()];
  if (!list.length) return EMPTY;

  // 2. columns by depth.
  const depths = [...new Set(list.map((n) => n.depth ?? 0))].sort((a, b) => a - b);
  const colOf = new Map(depths.map((d, i) => [d, i] as const));
  const cols: OpeningNode[][] = depths.map(() => []);
  for (const n of list) cols[colOf.get(n.depth ?? 0)!].push(n);
  const byGames = (a: OpeningNode, b: OpeningNode) => ((b.games ?? 0) - (a.games ?? 0)) || a.id.localeCompare(b.id);
  for (const c of cols) c.sort(byGames);

  const edges = allEdges.filter((e) => e.source !== e.target && kept.has(e.source) && kept.has(e.target));
  const parents = new Map<string, string[]>();
  const children = new Map<string, string[]>();
  for (const e of edges) {
    parents.set(e.target, [...(parents.get(e.target) ?? []), e.source]);
    children.set(e.source, [...(children.get(e.source) ?? []), e.target]);
  }

  // 3. barycentre ordering: two sweeps down and up the layers.
  const pos = new Map<string, number>();
  const refresh = () => {
    for (const c of cols) c.forEach((n, i) => pos.set(n.id, c.length > 1 ? i / (c.length - 1) : 0.5));
  };
  const bary = (n: OpeningNode, rel: string[]): number => {
    const ps = rel.map((id) => pos.get(id)).filter((p): p is number => p !== undefined);
    return ps.length ? mean(ps) : pos.get(n.id) ?? 0.5;
  };
  const sortBy = (col: OpeningNode[], rel: Map<string, string[]>) => {
    const key = new Map(col.map((n) => [n.id, bary(n, rel.get(n.id) ?? [])] as const));
    col.sort((a, b) => (key.get(a.id)! - key.get(b.id)!) || byGames(a, b));
  };
  refresh();
  for (let sweep = 0; sweep < 2; sweep++) {
    for (let c = 1; c < cols.length; c++) sortBy(cols[c], parents);
    refresh();
    for (let c = cols.length - 2; c >= 0; c--) sortBy(cols[c], children);
    refresh();
  }

  // 4. positions: x by column, y near the mean of the parents then pushed apart to avoid overlap.
  const byId = new Map<string, LaidNode>();
  const colX = (c: number) => c * (NODE_W_MAX + COL_GAP);
  for (let c = 0; c < cols.length; c++) {
    const col = cols[c];
    const items = col.map((n) => {
      const text = nodeText(n);
      const w = nodeW(n, text);
      const h = NODE_H + (n.is_deviation ? NOTE_H : 0);
      return { n, text, w, h };
    });
    const desired = items.map(({ n }) => {
      const ps = (parents.get(n.id) ?? []).map((id) => byId.get(id)).filter((p): p is LaidNode => !!p);
      return ps.length ? mean(ps.map((p) => p.y + NODE_H / 2)) - NODE_H / 2 : 0;
    });
    const ys = [...desired];
    for (let i = 1; i < ys.length; i++) ys[i] = Math.max(ys[i], ys[i - 1] + items[i - 1].h + ROW_GAP);
    const shift = ys.length ? mean(ys.map((y, i) => y - desired[i])) : 0;
    items.forEach(({ n, text, w, h }, i) => {
      byId.set(n.id, { node: n, x: colX(c), y: ys[i] - shift, w, h, text, col: c, row: i });
    });
  }
  const laid = [...byId.values()];
  const minY = Math.min(...laid.map((l) => l.y));
  for (const l of laid) l.y -= minY;
  const width = Math.max(...laid.map((l) => Math.max(l.x + l.w, l.node.is_deviation ? l.x + NOTE_W : 0)));
  const height = Math.max(...laid.map((l) => l.y + l.h));

  // 5. edges: cubic paths, width from my game count, colour from my score, dashed when only masters play it.
  const maxGames = Math.max(1, ...edges.filter((e) => !e.master_only).map((e) => e.games ?? 0));
  const laidEdges: LaidEdge[] = edges.map((e, i) => {
    const A = byId.get(e.source)!, B = byId.get(e.target)!;
    let d: string;
    if (B.x > A.x + A.w - 1) {
      const x1 = A.x + A.w, y1 = A.y + MINI / 2 + 8, x2 = B.x, y2 = B.y + MINI / 2 + 8;
      const mx = (x1 + x2) / 2;
      d = `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
    } else {
      const down = B.y >= A.y;
      const x1 = A.x + A.w / 2, y1 = down ? A.y + NODE_H : A.y, x2 = B.x + B.w / 2, y2 = down ? B.y : B.y + NODE_H;
      const my = (y1 + y2) / 2;
      d = `M${x1},${y1} C${x1},${my} ${x2},${my} ${x2},${y2}`;
    }
    const master = !!e.master_only;
    const width = master ? 1.5 : 2 + 9 * Math.max(0, Math.min(1, (e.games ?? 0) / maxGames));
    return { edge: e, d, width, master, stroke: master ? `rgb(${tokens().neutral.join(', ')})` : scoreColor(e.score), key: `${e.source}>${e.target}#${i}` };
  });
  laidEdges.sort((a, b) => b.width - a.width);

  return { nodes: laid, edges: laidEdges, width, height, byId };
}
