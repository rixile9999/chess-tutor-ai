import { useEffect, useRef } from 'react';
import { Chessground } from 'chessground';
import type { Api } from 'chessground/api';
import type { Key } from 'chessground/types';
import type { DrawShape } from 'chessground/draw';
import 'chessground/assets/chessground.base.css';
import 'chessground/assets/chessground.brown.css';
import 'chessground/assets/chessground.cburnett.css';
import './board.css';

export type BoardShape = DrawShape;

type Props = {
  fen: string;
  orientation?: 'white' | 'black';
  size?: number;
  shapes?: BoardShape[];
  lastMove?: [string, string] | null;
  /** When set, the side to move may drag pieces; `dests` maps from-square to legal to-squares. */
  movable?: { color: 'white' | 'black'; dests: Map<Key, Key[]> } | null;
  onMove?: (orig: string, dest: string) => void;
  coordinates?: boolean;
};

/** React wrapper around chessground (GPL-3.0). Shared by every page; keep the API additive. */
export function Board({ fen, orientation = 'white', size = 520, shapes, lastMove, movable, onMove, coordinates = true }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const api = useRef<Api | null>(null);
  const onMoveRef = useRef(onMove);
  onMoveRef.current = onMove;

  useEffect(() => {
    if (!ref.current) return;
    api.current = Chessground(ref.current, {
      fen,
      orientation,
      coordinates,
      viewOnly: !movable,
      turnColor: movable?.color,
      movable: movable ? { free: false, color: movable.color, dests: movable.dests, showDests: true } : { free: false, color: undefined },
      draggable: { enabled: !!movable },
      selectable: { enabled: !!movable },
      drawable: { enabled: false, visible: true, shapes: shapes ?? [] },
      lastMove: (lastMove ?? undefined) as Key[] | undefined,
      events: { move: (orig, dest) => onMoveRef.current?.(orig, dest) },
      animation: { enabled: true, duration: 150 },
    });
    return () => { api.current?.destroy(); api.current = null; };
  }, []);

  useEffect(() => {
    api.current?.set({
      fen,
      orientation,
      viewOnly: !movable,
      turnColor: movable?.color,
      movable: movable ? { free: false, color: movable.color, dests: movable.dests, showDests: true } : { free: false, color: undefined, dests: new Map() },
      draggable: { enabled: !!movable },
      selectable: { enabled: !!movable },
      drawable: { shapes: shapes ?? [] },
      lastMove: (lastMove ?? undefined) as Key[] | undefined,
    });
  }, [fen, orientation, shapes, lastMove, movable]);

  return <div ref={ref} className="board" style={{ width: size, height: size }} />;
}
