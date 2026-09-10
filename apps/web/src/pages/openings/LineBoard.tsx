import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Chess, type Square } from 'chess.js';
import type { Color } from '../../api/types';
import { Board, type BoardShape } from '../../components/Board';
import { PromotionPicker, type PromotionPiece } from '../../components/PromotionPicker';
import { legalDests } from '../../lib/chess';
import { pct, scoreTone } from './colors';
import type { LinePreview } from './line';

const SIDE_LABEL: Record<Color, string> = { white: '백', black: '흑' };

export interface PositionLine {
  /** "1.e4 1…e5 2.Nf3" — the line up to the cursor, empty at the start position. */
  text: string;
  turn: Color;
  name: string | null;
  eco: string | null;
  inBook: boolean;
  /** My record on this position, from the map DAG; null when I have never been here. */
  record: { games: number; score: number } | null;
  /** How many move orders of mine reach this position (>= 2 means a transposition merge). */
  merges: number;
}

type Props = {
  fen: string;
  orientation: Color;
  size: number;
  /** Off while a trap preview is on the board — previewing must not change the line. */
  canPlay: boolean;
  onPlay: (uci: string) => void;
  lastMove: [string, string] | null;
  shapes: BoardShape[];
  preview: LinePreview | null;
  onClearPreview: () => void;
  cursor: number;
  plyCount: number;
  onGoto: (cursor: number) => void;
  onFlip: () => void;
  onReset: () => void;
  position: PositionLine;
  playHref: string;
};

/**
 * ① 메인 보드: the line is played here (chessground + 승급 피커), the controls walk the cursor and a
 * trap preview borrows the board without touching the line (§9.1).
 */
export function LineBoard({
  fen, orientation, size, canPlay, onPlay, lastMove, shapes, preview, onClearPreview,
  cursor, plyCount, onGoto, onFlip, onReset, position, playHref,
}: Props) {
  const [promo, setPromo] = useState<{ orig: string; dest: string; color: Color } | null>(null);
  const [nonce, setNonce] = useState(0);

  const turn: Color = fen.split(' ')[1] === 'b' ? 'black' : 'white';
  const movable = canPlay ? { color: turn, dests: legalDests(fen) } : null;

  const onMove = (orig: string, dest: string) => {
    let promoting = false;
    try {
      promoting = new Chess(fen).get(orig as Square)?.type === 'p' && (dest[1] === '8' || dest[1] === '1');
    } catch { /* an unreadable FEN cannot promote either */ }
    if (promoting) { setPromo({ orig, dest, color: turn }); return; }
    onPlay(orig + dest);
  };
  // Chessground already moved the piece; re-mounting puts the board back where the state says it is.
  const cancelMove = () => { setPromo(null); setNonce((n) => n + 1); };

  return (
    <div className="op-line-col">
      <div className="op-line-board">
        <Board
          key={nonce}
          fen={fen}
          orientation={orientation}
          size={size}
          movable={movable}
          onMove={onMove}
          shapes={shapes}
          lastMove={lastMove}
        />
        {preview && <span className="op-line-chip">미리보기 · {preview.title}</span>}
        {promo && (
          <PromotionPicker
            color={promo.color}
            orientation={orientation}
            square={promo.dest}
            size={size}
            onPick={(p: PromotionPiece) => { const { orig, dest } = promo; setPromo(null); onPlay(orig + dest + p); }}
            onCancel={cancelMove}
          />
        )}
      </div>

      {preview ? (
        <div className="op-line-meta">
          <span className="badge badge-neutral op-badge">미리보기</span>
          <span className="mv">{preview.title}</span>
          <button type="button" className="btn btn-ghost compact op-line-back" onClick={onClearPreview}>현재 국면으로</button>
        </div>
      ) : (
        <div className="op-line-meta">
          <span className="mv">{position.text || '시작 국면'}</span>
          <span className="faint">▸ {SIDE_LABEL[position.turn]} 차례</span>
          {position.name ? (
            <>
              <span className="badge badge-good op-badge">{position.name}</span>
              {position.eco && <span className="mono faint small">{position.eco}</span>}
            </>
          ) : !position.inBook ? (
            <span className="badge op-badge-warn op-badge">책 밖</span>
          ) : null}
          {position.merges >= 2 && <span className="badge badge-neutral op-badge">합류 {position.merges}경로</span>}
          {position.record && (
            <span className={`badge ${scoreTone(position.record.score)} op-badge`}>
              내 {position.record.games}판 · 승률 {pct(position.record.score)}
            </span>
          )}
        </div>
      )}

      <div className="op-line-ctrl">
        <button type="button" className="op-nav" title="처음" aria-label="처음" disabled={cursor === 0} onClick={() => onGoto(0)}>⏮</button>
        <button type="button" className="op-nav" title="이전" aria-label="이전" disabled={cursor === 0} onClick={() => onGoto(cursor - 1)}>◀</button>
        <button type="button" className="op-nav" title="다음" aria-label="다음" disabled={cursor >= plyCount} onClick={() => onGoto(cursor + 1)}>▶</button>
        <button type="button" className="op-nav" title="끝" aria-label="끝" disabled={cursor >= plyCount} onClick={() => onGoto(plyCount)}>⏭</button>
        <button type="button" className="btn btn-ghost compact" onClick={onFlip}>뒤집기</button>
        <button type="button" className="btn btn-ghost compact" onClick={onReset} disabled={!plyCount}>새 수순</button>
        <div className="op-grow" />
        <Link className="btn btn-primary compact" to={playHref}>여기서부터 두기 →</Link>
      </div>
    </div>
  );
}
