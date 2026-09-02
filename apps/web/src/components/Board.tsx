import { useEffect, useRef } from 'react';
import { Chessground } from 'chessground';
import type { Api } from 'chessground/api';
import type { Config } from 'chessground/config';
import 'chessground/assets/chessground.base.css';
import 'chessground/assets/chessground.brown.css';
import 'chessground/assets/chessground.cburnett.css';

type Props = {
  fen: string;
  orientation?: 'white' | 'black';
  size?: number;
  shapes?: Config['drawable'] extends infer D ? (D extends { shapes?: infer S } ? S : never) : never;
};

/** Thin React wrapper around chessground (GPL-3.0). */
export function Board({ fen, orientation = 'white', size = 520, shapes }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const api = useRef<Api | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    api.current = Chessground(ref.current, {
      fen,
      orientation,
      viewOnly: true,
      coordinates: true,
      drawable: { enabled: false, visible: true, shapes: shapes ?? [] },
    });
    return () => api.current?.destroy();
  }, []);

  useEffect(() => {
    api.current?.set({ fen, orientation, drawable: { shapes: shapes ?? [] } });
  }, [fen, orientation, shapes]);

  return <div ref={ref} style={{ width: size, height: size }} />;
}
