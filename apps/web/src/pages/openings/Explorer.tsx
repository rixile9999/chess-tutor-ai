import { useMemo, useState, type KeyboardEvent } from 'react';
import type { Color, OpeningNode } from '../../api/types';
import { MiniBoard } from '../../components/MiniBoard';
import { norm01, pct, plainLabel, scoreColor } from './colors';
import { FocusBoard } from './FocusBoard';
import { currentStep, importantAncestor, pathTo, stepsOf, type Step, type Tree } from './model';

const CELL_BOARD = 128;
const COL_LIMIT = 6;
const CHIP_LIMIT = 4;

type Props = {
  tree: Tree;
  focus: OpeningNode;
  color: Color;
  onFocus: (node: OpeningNode) => void;
};

/** Breadcrumb + big board + Finder-style columns. One column per decision point, one row per branch. */
export function Explorer({ tree, focus, color, onFocus }: Props) {
  const [preview, setPreview] = useState<OpeningNode | null>(null);
  const [nextHover, setNextHover] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const path = useMemo(() => pathTo(tree, focus), [tree, focus]);
  const pathIds = useMemo(() => new Set(path.map((n) => n.id)), [path]);
  const anchor = useMemo(() => importantAncestor(tree, focus), [tree, focus]);
  const cur = useMemo(() => currentStep(tree, focus), [tree, focus]);
  // The previous column is only worth a look where a choice was made: a named position with one
  // continuation is skipped for the nearest ancestor that actually branches.
  const prevAnchor = useMemo(() => {
    let a = importantAncestor(tree, anchor);
    while (a && tree.root && a.id !== tree.root.id && stepsOf(tree, a).length < 2) a = importantAncestor(tree, a);
    return a;
  }, [tree, anchor]);

  const prevSteps = useMemo(() => stepsOf(tree, prevAnchor), [tree, prevAnchor]);
  const curSteps = useMemo(() => stepsOf(tree, anchor), [tree, anchor]);
  const nextAnchor = cur ? cur.end : focus;
  const nextSteps = useMemo(() => stepsOf(tree, nextAnchor), [tree, nextAnchor]);
  const afterStep = nextSteps.find((s) => s.start.id === nextHover) ?? nextSteps[0] ?? null;
  const afterSteps = useMemo(() => stepsOf(tree, afterStep?.end ?? null), [tree, afterStep]);

  const isRoot = !!tree.root && focus.id === tree.root.id;
  const showPrev = !!prevAnchor && !!anchor && !!tree.root && anchor.id !== tree.root.id;
  const toggle = (key: string) => setExpanded((e) => ({ ...e, [key]: !e[key] }));
  // Navigating rebuilds the columns under the pointer, so drop the hover state with it.
  const go = (node: OpeningNode) => { setPreview(null); setNextHover(null); onFocus(node); };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) return;
    e.preventDefault();
    if (e.key === 'ArrowLeft') { if (anchor) go(anchor); return; }
    if (e.key === 'ArrowRight') { if (nextSteps[0]) go(nextSteps[0].end); return; }
    if (!cur || !curSteps.length) return;
    const i = curSteps.findIndex((s) => s.start.id === cur.start.id);
    const j = i + (e.key === 'ArrowUp' ? -1 : 1);
    if (i < 0 || j < 0 || j >= curSteps.length) return;
    go(curSteps[j].end);
  };

  return (
    <div className="op-explorer">
      <Breadcrumb tree={tree} path={path} onFocus={go} />
      <div className="op-focus-row">
        <FocusBoard tree={tree} node={focus} preview={preview} color={color} />
        <div className="op-columns" tabIndex={0} aria-label="오프닝 탐색기" onKeyDown={onKeyDown}>
          {showPrev && (
            <Column
              tree={tree}
              caption="이전 갈림길"
              cacheKey={`prev:${prevAnchor.id}`}
              steps={prevSteps}
              color={color}
              pathIds={pathIds}
              expandedAll={!!expanded[`prev:${prevAnchor.id}`]}
              onToggle={() => toggle(`prev:${prevAnchor.id}`)}
              onFocus={go}
              onPreview={setPreview}
            />
          )}
          <Column
            tree={tree}
            caption="현재"
            cacheKey={`cur:${anchor?.id ?? 'root'}`}
            steps={isRoot ? [] : curSteps}
            color={color}
            currentId={cur?.start.id ?? null}
            rootCell={isRoot ? focus : null}
            expandedAll={!!expanded[`cur:${anchor?.id ?? 'root'}`]}
            onToggle={() => toggle(`cur:${anchor?.id ?? 'root'}`)}
            onFocus={go}
            onPreview={setPreview}
          />
          <Column
            tree={tree}
            caption="다음"
            cacheKey={`next:${nextAnchor.id}`}
            steps={nextSteps}
            color={color}
            empty="이 국면 뒤 기보가 없습니다"
            expandedAll={!!expanded[`next:${nextAnchor.id}`]}
            onToggle={() => toggle(`next:${nextAnchor.id}`)}
            onFocus={go}
            onPreview={setPreview}
            onHoverStep={setNextHover}
            hoverId={afterStep?.start.id ?? null}
          />
          {afterStep && (
            <Column
              tree={tree}
              caption={`그 다음 · ${plainLabel(afterStep.start.label) || afterStep.start.san || ''} 뒤`}
              cacheKey={`after:${afterStep.end.id}`}
              steps={afterSteps}
              color={color}
              empty="이 국면 뒤 기보가 없습니다"
              expandedAll={!!expanded[`after:${afterStep.end.id}`]}
              onToggle={() => toggle(`after:${afterStep.end.id}`)}
              onFocus={go}
              onPreview={setPreview}
            />
          )}
        </div>
      </div>
    </div>
  );
}

// ---------- breadcrumb ----------
type Crumb = { kind: 'node'; node: OpeningNode } | { kind: 'fold'; count: number };

function Breadcrumb({ tree, path, onFocus }: { tree: Tree; path: OpeningNode[]; onFocus: (n: OpeningNode) => void }) {
  const focus = path[path.length - 1];
  const crumbs: Crumb[] = [];
  let fold = 0;
  path.forEach((n, i) => {
    if (!tree.important.has(n.id) && i !== path.length - 1) { fold += 1; return; }
    if (fold) { crumbs.push({ kind: 'fold', count: fold }); fold = 0; }
    crumbs.push({ kind: 'node', node: n });
  });

  return (
    <nav className="op-crumbs" aria-label="현재 수순">
      {crumbs.map((c, i) => (
        <span className="op-crumb-item" key={c.kind === 'node' ? c.node.id : `fold-${i}`}>
          {i > 0 && <span className="op-crumb-sep faint">›</span>}
          {c.kind === 'fold' ? (
            <span className="faint small">+{c.count}수</span>
          ) : (
            <button
              type="button"
              className={`op-crumb${focus && c.node.id === focus.id ? ' on' : ''}`}
              onClick={() => onFocus(c.node)}
            >
              {tree.root && c.node.id === tree.root.id ? '시작' : plainLabel(c.node.label) || c.node.san || '?'}
            </button>
          )}
        </span>
      ))}
      {focus?.name && <span className="muted op-crumb-name">{focus.name}</span>}
      {focus?.eco && <span className="mono faint">{focus.eco}</span>}
    </nav>
  );
}

// ---------- columns ----------
type ColumnProps = {
  tree: Tree;
  caption: string;
  cacheKey: string;
  steps: Step[];
  color: Color;
  /** The step that the focus itself sits on. */
  currentId?: string | null;
  /** Ids on the way to the focus; the step whose chain meets them is the one the focus came through. */
  pathIds?: Set<string>;
  /** The step whose continuation the next column is showing. */
  hoverId?: string | null;
  /** Shown instead of steps when the focus is the start position. */
  rootCell?: OpeningNode | null;
  empty?: string;
  expandedAll: boolean;
  onToggle: () => void;
  onFocus: (node: OpeningNode) => void;
  onPreview: (node: OpeningNode | null) => void;
  onHoverStep?: (id: string | null) => void;
};

function Column(p: ColumnProps) {
  const shown = p.expandedAll ? p.steps : p.steps.slice(0, COL_LIMIT);
  const hidden = p.steps.slice(shown.length);
  const hiddenGames = hidden.reduce((a, s) => a + (s.master ? 0 : s.edgeGames), 0);
  return (
    <div className="op-col">
      <div className="op-col-head small muted">{p.caption}</div>
      {p.rootCell && (
        <div className="op-cell current">
          <button type="button" className="op-cell-main" onClick={() => p.onFocus(p.rootCell!)}>
            <MiniBoard fen={p.rootCell.fen} size={CELL_BOARD} orientation={p.color} />
            <span className="op-cell-row">
              <span className="mv op-cell-move">시작 국면</span>
              <span className="mono op-cell-count">{p.rootCell.games ?? 0}</span>
            </span>
          </button>
        </div>
      )}
      {!p.rootCell && !p.steps.length && <div className="op-col-empty small faint">{p.empty ?? '이 국면 뒤 기보가 없습니다'}</div>}
      {shown.map((s) => (
        <StepCell
          key={s.start.id}
          tree={p.tree}
          step={s}
          color={p.color}
          state={
            s.start.id === p.currentId ? 'current'
              : (p.pathIds && s.chain.some((n) => p.pathIds!.has(n.id))) || s.start.id === p.hoverId ? 'on-path' : ''
          }
          onFocus={p.onFocus}
          onPreview={p.onPreview}
          onHoverStep={p.onHoverStep}
        />
      ))}
      {hidden.length > 0 && (
        <button type="button" className="op-cell-more small" onClick={p.onToggle}>
          기타 {hiddenGames}판 · {hidden.length}가지
        </button>
      )}
      {p.expandedAll && p.steps.length > COL_LIMIT && (
        <button type="button" className="op-cell-more small" onClick={p.onToggle}>접기</button>
      )}
    </div>
  );
}

type CellProps = {
  tree: Tree;
  step: Step;
  color: Color;
  state: string;
  onFocus: (node: OpeningNode) => void;
  onPreview: (node: OpeningNode | null) => void;
  onHoverStep?: (id: string | null) => void;
};

function StepCell({ tree, step, color, state, onFocus, onPreview, onHoverStep }: CellProps) {
  const end = step.end;
  const s = norm01(end.score);
  const chips = step.chain.slice(1);
  const extra = Math.max(0, chips.length - CHIP_LIMIT);
  const merges = tree.others.get(end.id) ?? 0;

  return (
    <div
      className={['op-cell', state, step.master ? 'master' : ''].filter(Boolean).join(' ')}
      onMouseEnter={() => { onPreview(end); onHoverStep?.(step.start.id); }}
      onMouseLeave={() => { onPreview(null); }}
    >
      <button type="button" className="op-cell-main" onClick={() => onFocus(end)} onFocus={() => onHoverStep?.(step.start.id)}>
        <MiniBoard fen={end.fen} size={CELL_BOARD} orientation={color} />
        <span className="op-cell-row">
          <span className="mv op-cell-move">{plainLabel(step.start.label) || step.start.san || '?'}</span>
          <span className="mono op-cell-count">{step.master ? '마스터' : end.games ?? 0}</span>
        </span>
        {step.master ? (
          <span className="op-cell-row small faint mono">마스터 승률 {pct(step.edgeScore)}</span>
        ) : (
          <span className="op-cell-row">
            <span className="op-bar"><i style={{ width: `${(s ?? 0) * 100}%`, background: scoreColor(end.score) }} /></span>
            <span className="mono op-cell-pct">{pct(end.score)}</span>
          </span>
        )}
      </button>
      {chips.length > 0 && (
        <div className="op-cell-chips">
          {chips.slice(0, CHIP_LIMIT).map((n, i) => (
            <button
              key={n.id}
              type="button"
              className="op-move-chip mono"
              title={plainLabel(n.label) || undefined}
              onClick={(e) => { e.stopPropagation(); onFocus(n); }}
            >
              {step.sans[i + 1] || n.san || '?'}
            </button>
          ))}
          {extra > 0 && <span className="op-move-chip faint mono">+{extra}</span>}
        </div>
      )}
      {(end.is_deviation || end.is_tabiya || merges > 0) && (
        <div className="op-cell-badges">
          {end.is_deviation && <span className="badge badge-bad op-badge sm">이탈</span>}
          {end.is_tabiya && <span className="badge badge-neutral op-badge sm">타비야</span>}
          {merges > 0 && <span className="badge badge-neutral op-badge sm">합류</span>}
        </div>
      )}
    </div>
  );
}
