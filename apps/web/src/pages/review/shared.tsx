import type { ReactNode } from 'react';
import type { Key } from 'chessground/types';
import type { Arrow, Classification, Color, Explanation } from '../../api/types';
import type { BoardShape } from '../../components/Board';
import { fenTrail, sanToUci } from '../../lib/chess';
import { CLASS_LABEL, CLASS_TONE, plyLabel } from '../../lib/labels';

/** A temporary board position played from a line the user clicked. */
export type Preview = { id: string; fen: string; label: string; lastMove: [string, string] | null; shapes: BoardShape[] };

const SQUARE = /^[a-h][1-8]$/;
const BRUSH: Record<Arrow['color'], string> = { good: 'blue', bad: 'red', ink: 'paleGrey' };

/** Review arrows + highlighted squares as chessground shapes (invalid squares are dropped). */
export function arrowShapes(arrows: Arrow[] | undefined, highlights: string[] | undefined): BoardShape[] {
  const out: BoardShape[] = [];
  for (const a of arrows ?? []) {
    if (!SQUARE.test(a.orig) || !SQUARE.test(a.dest)) continue;
    const s: BoardShape = { orig: a.orig as Key, dest: a.dest as Key, brush: BRUSH[a.color] ?? 'paleGrey' };
    if (a.dashed) s.modifiers = { lineWidth: 6 };
    out.push(s);
  }
  for (const sq of highlights ?? []) if (SQUARE.test(sq)) out.push({ orig: sq as Key, brush: 'paleGrey' });
  return out;
}

/** Preview after playing `sans` from `fen`; null when any move is illegal there. */
export function makePreview(id: string, fen: string, sans: string[], label: string): Preview | null {
  if (!sans.length) return null;
  const trail = fenTrail(fen, sans);
  if (trail.length !== sans.length + 1) return null;
  const uci = sanToUci(trail[trail.length - 2], sans[sans.length - 1]);
  const lastMove: [string, string] | null = uci ? [uci.slice(0, 2), uci.slice(2, 4)] : null;
  const shapes: BoardShape[] = lastMove ? [{ orig: lastMove[0] as Key, dest: lastMove[1] as Key, brush: 'paleGrey', modifiers: { lineWidth: 6 } }] : [];
  return { id, fen: trail[trail.length - 1], label, lastMove, shapes };
}

/** "21. Nxf6+" for white plies, "20… Qd7" for black; later white moves in a line keep their number. */
export function moveText(ply: number, san: string, first: boolean): string {
  return first || ply % 2 === 1 ? `${plyLabel(ply)} ${san}` : san;
}
export function lineText(sans: string[], startPly: number): string {
  return sans.map((s, i) => moveText(startPly + i, s, i === 0)).join(' ');
}
export function sideLabel(c: Color | null | undefined): string { return c === 'black' ? '흑' : c === 'white' ? '백' : '?'; }
export function ratingBand(rating: number): string { const lo = Math.floor(rating / 200) * 200; return `${lo}-${lo + 200}`; }
export function pct(v: number): string { return `${Math.round((v <= 1 ? v * 100 : v))}%`; }

export function ClassBadge({ cls, size = 'sm' }: { cls: Classification; size?: 'sm' | 'lg' }) {
  const tone = CLASS_TONE[cls] ?? 'neutral';
  return <span className={`badge badge-${tone} rv-badge-${size}${cls === 'inaccuracy' ? ' rv-badge-dashed' : ''}`}>{CLASS_LABEL[cls] ?? cls}</span>;
}

type LineProps = {
  sans: string[]; startPly: number; baseFen: string; idPrefix: string;
  preview: Preview | null; onPreview: (p: Preview | null) => void; size?: 'sm' | 'md';
};
/** A line of SAN chips; clicking chip i previews the position after sans[0..i] played from baseFen. */
export function LineChips({ sans, startPly, baseFen, idPrefix, preview, onPreview, size = 'md' }: LineProps) {
  const cls = `rv-mv${size === 'sm' ? ' rv-mv-sm' : ''}`;
  return (
    <span className="rv-line">
      {sans.map((san, i) => {
        const id = `${idPrefix}:${i}`;
        const on = preview?.id === id;
        return (
          <button key={id} type="button" className={`${cls}${on ? ' active' : ''}`} title="보드에서 보기"
            onClick={() => onPreview(on ? null : makePreview(id, baseFen, sans.slice(0, i + 1), lineText(sans.slice(0, i + 1), startPly)))}>
            {moveText(startPly + i, san, i === 0)}
          </button>
        );
      })}
    </span>
  );
}

export function VerifyRow({ explanation, children }: { explanation: Explanation | null | undefined; children?: ReactNode }) {
  const e = explanation;
  const ok = !!e?.verified;
  const total = e?.total_claims ?? 0, good = e?.verified_claims ?? 0;
  return (
    <div className="rv-verify">
      <div className="rv-verify-text">
        {ok ? <IconCheck /> : <IconWarn />}
        {e ? (
          ok
            ? <span><b style={{ color: 'var(--ink)' }}>검증됨</b> · 문장 속 {total}개 주장(칸·기물·공격 관계)이 보드와 일치{e.source === 'template' ? ' · 템플릿 설명' : ''}</span>
            : <span><b style={{ color: 'var(--bad)' }}>검증 실패</b> · {total}개 주장 중 {good}개만 보드와 일치합니다. 설명을 그대로 믿지 마세요.</span>
        ) : <span>검증 정보 없음</span>}
      </div>
      {children && <div className="rv-verify-btns">{children}</div>}
    </div>
  );
}

const S = { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const };
export function IconCheck() { return <svg {...S} width="16" height="16" strokeWidth="2.2" style={{ color: 'var(--good)' }}><circle cx="12" cy="12" r="9" /><path d="M8 12.5l2.8 2.8L16.5 9.5" /></svg>; }
export function IconWarn() { return <svg {...S} width="16" height="16" strokeWidth="2.2" style={{ color: 'var(--bad)' }}><path d="M12 3l9.5 17h-19z" /><path d="M12 9v5M12 17.5v.5" /></svg>; }
export function IconPlay() { return <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M6 4l14 8-14 8z" /></svg>; }
export function IconSave() { return <svg {...S} width="15" height="15" strokeWidth="2"><path d="M5 3h11l3 3v15H5z" /><path d="M8 3v6h8V3M8 21v-7h8v7" /></svg>; }
export function IconFlip() { return <svg {...S} width="15" height="15" strokeWidth="2"><path d="M4 9a8 8 0 0 1 14-3l2 2M20 4v4h-4M20 15a8 8 0 0 1-14 3l-2-2M4 20v-4h4" /></svg>; }
export function IconArrow() { return <svg {...S} width="14" height="14" strokeWidth="2.2" style={{ color: 'var(--ink-3)' }}><path d="M5 12h14M13 6l6 6-6 6" /></svg>; }
export function IconChev({ dir }: { dir: 'l' | 'r' | 'll' | 'rr' }) {
  const d = dir === 'l' ? 'M15 5l-7 7 7 7' : dir === 'r' ? 'M9 5l7 7-7 7' : dir === 'll' ? 'M18 5l-7 7 7 7M11 5l-7 7 7 7' : 'M6 5l7 7-7 7M13 5l7 7-7 7';
  return <svg {...S} width="14" height="14" strokeWidth="2.4"><path d={d} /></svg>;
}
