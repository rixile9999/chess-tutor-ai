// Pure model behind the opening explorer: prune by game count, read the DAG as a tree,
// fold single-child chains into steps, and lay the icicle overview out. No React here.
import type { OpeningEdge, OpeningMap, OpeningNode } from '../../api/types';

export interface ChildEdge { edge: OpeningEdge; node: OpeningNode }

/** One column cell: the move that starts a branch, folded up to the next decision point. */
export interface Step {
  start: OpeningNode;
  end: OpeningNode;
  /** start .. end inclusive; length 1 when nothing was folded. */
  chain: OpeningNode[];
  /** One SAN per ply of `chain`. */
  sans: string[];
  edgeGames: number;
  edgeScore: number;
  master: boolean;
}

export interface Tree {
  root: OpeningNode | null;
  byId: Map<string, OpeningNode>;
  parents: Map<string, string[]>;
  children: Map<string, ChildEdge[]>;
  /** node id -> the parent it is normally reached through. */
  primary: Map<string, string>;
  /** node id -> how many other parents reach it (>= 1 means a transposition merge). */
  others: Map<string, number>;
  important: Set<string>;
  /** anchor id -> its contracted children, memoised. */
  steps: Map<string, Step[]>;
}

const EMPTY: Tree = {
  root: null, byId: new Map(), parents: new Map(), children: new Map(),
  primary: new Map(), others: new Map(), important: new Set(), steps: new Map(),
};

const games = (n: OpeningNode | null | undefined) => n?.games ?? 0;

/**
 * Keep nodes with at least `minGames` of my games, the root, and master-only nodes hanging off a
 * kept parent; then drop whatever the root can no longer reach.
 */
function keptNodes(map: OpeningMap, minGames: number): Map<string, OpeningNode> {
  const all = map.nodes ?? [];
  const edges = map.edges ?? [];
  const byId = new Map(all.map((n) => [n.id, n] as const));
  const kept = new Map<string, OpeningNode>();
  for (const n of all) if (n.id === map.root || games(n) >= minGames) kept.set(n.id, n);
  // A master-only move is worth showing whenever the position it branches from survived.
  for (const e of edges) {
    const t = byId.get(e.target);
    if (t?.master_only && kept.has(e.source)) kept.set(t.id, t);
  }
  if (!kept.has(map.root)) return new Map();

  const adj = new Map<string, string[]>();
  for (const e of edges) {
    if (e.source !== e.target && kept.has(e.source) && kept.has(e.target)) {
      adj.set(e.source, [...(adj.get(e.source) ?? []), e.target]);
    }
  }
  const seen = new Set<string>([map.root]);
  const queue = [map.root];
  while (queue.length) {
    const id = queue.shift()!;
    for (const t of adj.get(id) ?? []) if (!seen.has(t)) { seen.add(t); queue.push(t); }
  }
  for (const id of [...kept.keys()]) if (!seen.has(id)) kept.delete(id);
  return kept;
}

export function buildTree(map: OpeningMap | null | undefined, minGames: number): Tree {
  if (!map) return EMPTY;
  const byId = keptNodes(map, minGames);
  const root = byId.get(map.root) ?? null;
  if (!root) return EMPTY;

  const parents = new Map<string, string[]>();
  const children = new Map<string, ChildEdge[]>();
  const inEdges = new Map<string, OpeningEdge[]>();
  for (const e of map.edges ?? []) {
    if (e.source === e.target || !byId.has(e.source) || !byId.has(e.target)) continue;
    children.set(e.source, [...(children.get(e.source) ?? []), { edge: e, node: byId.get(e.target)! }]);
    parents.set(e.target, [...(parents.get(e.target) ?? []), e.source]);
    inEdges.set(e.target, [...(inEdges.get(e.target) ?? []), e]);
  }
  // Busiest first; the master-only moves I have never played sit at the end.
  for (const list of children.values()) {
    list.sort((a, b) =>
      Number(!!a.edge.master_only) - Number(!!b.edge.master_only)
      || (b.edge.games ?? 0) - (a.edge.games ?? 0)
      || a.node.id.localeCompare(b.node.id));
  }

  const primary = new Map<string, string>();
  const others = new Map<string, number>();
  for (const [id, list] of inEdges) {
    const best = [...list].sort((a, b) =>
      (b.games ?? 0) - (a.games ?? 0)
      || (byId.get(a.source)?.depth ?? 0) - (byId.get(b.source)?.depth ?? 0)
      || a.source.localeCompare(b.source))[0];
    primary.set(id, best.source);
    if (list.length > 1) others.set(id, list.length - 1);
  }

  const important = new Set<string>();
  for (const n of byId.values()) {
    const kids = children.get(n.id) ?? [];
    if (n.id === root.id || (n.name ?? '').trim() !== '' || n.is_tabiya || n.is_deviation
      || n.master_only || kids.length !== 1) important.add(n.id);
  }

  return { root, byId, parents, children, primary, others, important, steps: new Map() };
}

/** The direct children of `anchor`, each followed through its single-child run to the next decision point. */
export function stepsOf(tree: Tree, anchor: OpeningNode | null): Step[] {
  if (!anchor) return [];
  const hit = tree.steps.get(anchor.id);
  if (hit) return hit;
  const out: Step[] = [];
  for (const first of tree.children.get(anchor.id) ?? []) {
    const chain = [first.node];
    const sans = [first.edge.san || first.node.san || ''];
    const seen = new Set([anchor.id, first.node.id]);
    let cur = first.node;
    while (!tree.important.has(cur.id)) {
      const kids = tree.children.get(cur.id) ?? [];
      if (kids.length !== 1 || seen.has(kids[0].node.id)) break;
      cur = kids[0].node;
      seen.add(cur.id);
      chain.push(cur);
      sans.push(kids[0].edge.san || cur.san || '');
    }
    out.push({
      start: first.node, end: cur, chain, sans,
      edgeGames: first.edge.games ?? 0, edgeScore: first.edge.score, master: !!first.edge.master_only,
    });
  }
  tree.steps.set(anchor.id, out);
  return out;
}

/** root -> ... -> node, following the busiest parent at every ply. */
export function pathTo(tree: Tree, node: OpeningNode | null): OpeningNode[] {
  if (!node || !tree.root) return [];
  const out = [node];
  const seen = new Set([node.id]);
  let cur = node;
  while (cur.id !== tree.root.id) {
    const up = tree.primary.get(cur.id);
    const parent = up ? tree.byId.get(up) : undefined;
    if (!parent || seen.has(parent.id)) break;
    seen.add(parent.id);
    out.push(parent);
    cur = parent;
  }
  return out.reverse();
}

/** The nearest strict ancestor that is a decision point; null only for the root itself. */
export function importantAncestor(tree: Tree, node: OpeningNode | null): OpeningNode | null {
  const path = pathTo(tree, node);
  for (let i = path.length - 2; i >= 0; i--) if (tree.important.has(path[i].id)) return path[i];
  return null;
}

/** The step under `importantAncestor(node)` whose chain passes through `node`. */
export function currentStep(tree: Tree, node: OpeningNode | null): Step | null {
  if (!node) return null;
  const anchor = importantAncestor(tree, node);
  if (!anchor) return null;
  return stepsOf(tree, anchor).find((s) => s.chain.some((c) => c.id === node.id)) ?? null;
}

// ---------- icicle overview ----------
export interface StripCell {
  key: string;
  node: OpeningNode;
  x: number;
  width: number;
  ply: number;
  /** Set on the trailing "…" cell that stands for the branches too thin to draw. */
  merged?: OpeningNode[];
  /** My games behind this cell (the sum, for a merged one). */
  games: number;
}

/**
 * Rows are plies, a cell's width is its share of the parent's games, so a transposed position shows
 * up under each parent at the right size. Master-only moves are left out; the run of branches too
 * narrow to read is merged into one trailing cell.
 */
export function stripLayout(tree: Tree, totalWidth: number, maxPly = 10, minWidth = 4): StripCell[] {
  if (!tree.root || totalWidth <= 0) return [];
  const out: StripCell[] = [{ key: `${tree.root.id}@0`, node: tree.root, x: 0, width: totalWidth, ply: 0, games: games(tree.root) }];
  let row: StripCell[] = [out[0]];
  for (let ply = 1; ply <= maxPly; ply++) {
    const next: StripCell[] = [];
    for (const parent of row) {
      if (parent.merged) continue;
      const total = games(parent.node);
      if (total <= 0) continue;
      const kids = (tree.children.get(parent.node.id) ?? []).filter((c) => !c.edge.master_only);
      let x = parent.x;
      const thin: OpeningNode[] = [];
      let thinWidth = 0, thinGames = 0;
      for (const c of kids) {
        const w = parent.width * Math.min(1, (c.edge.games ?? 0) / total);
        if (w < minWidth) { thin.push(c.node); thinWidth += w; thinGames += c.edge.games ?? 0; continue; }
        next.push({ key: `${parent.key}>${c.node.id}`, node: c.node, x, width: w, ply, games: c.edge.games ?? 0 });
        x += w;
      }
      if (thin.length) {
        next.push({ key: `${parent.key}>+`, node: thin[0], x, width: Math.max(minWidth, thinWidth), ply, merged: thin, games: thinGames });
      }
    }
    out.push(...next);
    row = next;
    if (!next.length) break;
  }
  return out;
}
