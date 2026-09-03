import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { OpeningNode } from '../../api/types';
import { pct, scoreTint, shortName } from './colors';
import { stripLayout, type Tree } from './model';

const ROW_H = 20;
const ROW_GAP = 2;
const MAX_PLY = 10;

type Props = {
  tree: Tree;
  focusId: string | null;
  pathIds: Set<string>;
  onFocus: (node: OpeningNode) => void;
};

/**
 * Icicle overview of the whole repertoire: one row per ply, width is the share of my games,
 * colour is my score there. Row 0 is the start position, a full-width bar, so it is left out.
 */
export function Strip({ tree, focusId, pathIds, onFocus }: Props) {
  const boxRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);

  useLayoutEffect(() => { setWidth(boxRef.current?.clientWidth ?? 0); }, []);
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const measure = () => setWidth(el.clientWidth);
    measure();
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measure);
      return () => window.removeEventListener('resize', measure);
    }
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const cells = useMemo(() => stripLayout(tree, width, MAX_PLY).filter((c) => c.ply >= 1), [tree, width]);
  const rows = cells.length ? Math.max(...cells.map((c) => c.ply)) : 0;

  return (
    <div className="op-strip" ref={boxRef} style={{ height: rows ? rows * (ROW_H + ROW_GAP) - ROW_GAP : ROW_H }}>
      {cells.map((c) => {
        const n = c.node;
        const onPath = !c.merged && pathIds.has(n.id);
        const isFocus = !c.merged && n.id === focusId;
        // Outlines on slivers read as empty boxes, so the flags only show once a cell has some width.
        const cls = ['op-strip-cell', c.merged ? 'merged' : '', onPath ? 'on-path' : '',
          isFocus ? 'focus' : '', !c.merged && n.is_tabiya && c.width >= 20 ? 'tabiya' : '',
          !c.merged && n.is_deviation && c.width >= 10 ? 'dev' : '']
          .filter(Boolean).join(' ');
        const title = c.merged
          ? `${c.merged.length}가지 · 총 ${c.games}판`
          : [n.label || n.san || '?', n.name ?? '', `${n.games ?? 0}판`, `승률 ${pct(n.score)}`].filter(Boolean).join(' · ');
        const name = c.merged ? '' : shortName(n.name);
        return (
          <button
            key={c.key}
            type="button"
            className={cls}
            title={title}
            aria-label={title}
            style={{
              left: c.x,
              top: (c.ply - 1) * (ROW_H + ROW_GAP),
              width: Math.max(1, c.width - 1),
              height: ROW_H,
              background: c.merged ? 'var(--line)' : scoreTint(n.score, onPath ? 55 : 28),
            }}
            onClick={() => { if (!c.merged) onFocus(n); }}
          >
            {c.merged ? (c.width >= 12 ? <span className="op-strip-text mono">…</span> : null)
              : c.width >= 40 ? (
                <span className="op-strip-text mono">
                  {n.san || n.label}
                  {c.width >= 140 && name ? <span className="op-strip-name"> {name}</span> : null}
                </span>
              ) : null}
          </button>
        );
      })}
    </div>
  );
}
