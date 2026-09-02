import { useCallback, useEffect, useMemo, useRef } from 'react';
import { select, zoom, zoomIdentity, type D3ZoomEvent, type ZoomBehavior } from 'd3';
import type { Color, OpeningMap, OpeningNode } from '../../api/types';
import { MiniBoard } from '../../components/MiniBoard';
import { layoutMap, MINI, NODE_H, NOTE_W, norm01, pct } from './layout';

type Props = {
  map: OpeningMap;
  color: Color;
  minGames: number;
  selectedId: string | null;
  onSelect: (node: OpeningNode | null) => void;
};

/** Layered DAG of the repertoire. Every node carries a board snapshot; edges are drawn in an SVG layer beneath the cards. */
export function OpeningDag({ map, color, minGames, selectedId, onSelect }: Props) {
  const layout = useMemo(() => layoutMap(map, minGames), [map, minGames]);
  const viewportRef = useRef<HTMLDivElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);
  const zoomRef = useRef<ZoomBehavior<HTMLDivElement, unknown> | null>(null);
  const { width, height } = layout;

  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const z = zoom<HTMLDivElement, unknown>()
      .scaleExtent([0.35, 2.5])
      .on('zoom', (ev: D3ZoomEvent<HTMLDivElement, unknown>) => {
        const inner = innerRef.current;
        if (inner) inner.style.transform = `translate(${ev.transform.x}px, ${ev.transform.y}px) scale(${ev.transform.k})`;
      });
    zoomRef.current = z;
    select(el).call(z);
    return () => { select(el).on('.zoom', null); zoomRef.current = null; };
  }, []);

  const fit = useCallback(() => {
    const el = viewportRef.current, z = zoomRef.current;
    if (!el || !z) return;
    const vw = el.clientWidth, vh = el.clientHeight;
    if (!vw || !vh || !width || !height) return;
    const pad = 20;
    const k = Math.min(1, (vw - pad * 2) / width, (vh - pad * 2) / height);
    const tx = (vw - width * k) / 2, ty = Math.max(pad, (vh - height * k) / 2);
    select(el).call(z.transform, zoomIdentity.translate(tx, ty).scale(k));
  }, [width, height]);
  useEffect(() => { fit(); }, [fit]);

  const scaleBy = (f: number) => {
    const el = viewportRef.current, z = zoomRef.current;
    if (el && z) select(el).call(z.scaleBy, f);
  };

  if (!layout.nodes.length) {
    return (
      <div className="op-state" style={{ minHeight: 320 }}>
        <div>표시할 가지가 없습니다.</div>
        <div className="small faint">최소 판수를 낮춰 보세요.</div>
      </div>
    );
  }

  return (
    <div className="op-dag-viewport" ref={viewportRef}>
      <div className="op-dag-inner" ref={innerRef} style={{ width, height }}>
        <svg className="op-dag-svg" width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
          {layout.edges.map((e) => (
            <path
              key={e.key}
              d={e.d}
              fill="none"
              stroke={e.stroke}
              strokeWidth={e.width}
              strokeLinecap="round"
              opacity={e.master ? 0.7 : 0.85}
              strokeDasharray={e.master ? '4 5' : undefined}
            >
              <title>{`${e.edge.san ?? ''} · ${e.master ? '마스터 DB' : `${e.edge.games ?? 0}판`} · 승률 ${pct(e.edge.score)}`}</title>
            </path>
          ))}
        </svg>
        {layout.nodes.map((l) => {
          const n = l.node;
          const cls = ['op-node', n.is_tabiya ? 'tabiya' : '', n.is_deviation ? 'dev' : '', n.master_only ? 'master' : '', selectedId === n.id ? 'sel' : '']
            .filter(Boolean).join(' ');
          const s = norm01(n.score);
          return (
            <div key={n.id}>
              <div
                className={cls}
                style={{ left: l.x, top: l.y, width: l.w, height: NODE_H }}
                title={[l.text, n.name && !l.text.includes(n.name) ? n.name : null, n.eco].filter(Boolean).join(' · ')}
                onClick={() => onSelect(selectedId === n.id ? null : n)}
              >
                <MiniBoard fen={n.fen} size={MINI} orientation={color} />
                <div className="op-node-label">
                  <span className="mv op-node-text">{l.text}</span>
                  <span className="mono op-node-count">{n.master_only ? '마스터' : (n.games ?? 0)}</span>
                </div>
              </div>
              {n.is_deviation && (
                <div className="op-node-note" style={{ left: l.x, top: l.y + NODE_H + 8, width: NOTE_W }}>
                  <b>
                    {n.games ?? 0}판{typeof n.losses === 'number' ? ` 중 ${n.losses}판 패배.` : '.'}
                  </b>{' '}
                  {s === null ? '책에서 이탈한 수' : `내 승률 ${pct(s)}`}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="op-dag-tools">
        <button type="button" className="op-tool" title="확대" onClick={() => scaleBy(1.25)}><IconPlus /></button>
        <button type="button" className="op-tool" title="축소" onClick={() => scaleBy(0.8)}><IconMinus /></button>
        <button type="button" className="op-tool" title="화면에 맞춤" onClick={fit}><IconFit /></button>
      </div>
    </div>
  );
}

const I = { width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2.2, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const };
function IconPlus() { return <svg {...I}><path d="M12 5v14M5 12h14" /></svg>; }
function IconMinus() { return <svg {...I}><path d="M5 12h14" /></svg>; }
function IconFit() { return <svg {...I}><path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5" /></svg>; }
