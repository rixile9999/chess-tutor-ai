import { sideToMove } from '../../lib/chess';
import { plyLabel } from '../../lib/labels';
import { IconChev, IconFlip, type Preview } from './shared';

type Props = {
  ply: number; plyCount: number; san: string | null; fen: string;
  preview: Preview | null; onGo: (ply: number) => void; onFlip: () => void; onRestore: () => void;
};

/** 첫 수 / 이전 / 다음 / 마지막, the current move label, and 보드 뒤집기. */
export function Controls({ ply, plyCount, san, fen, preview, onGo, onFlip, onRestore }: Props) {
  const turn = sideToMove(fen) === 'white' ? '백 차례' : '흑 차례';
  return (
    <div className="rv-controls">
      <button type="button" className="rv-ctl" title="첫 수 (Home)" aria-label="첫 수" disabled={ply <= 0} onClick={() => onGo(0)}><IconChev dir="ll" /></button>
      <button type="button" className="rv-ctl" title="이전 (ArrowLeft)" aria-label="이전" disabled={ply <= 0} onClick={() => onGo(ply - 1)}><IconChev dir="l" /></button>
      <button type="button" className="rv-ctl" title="다음 (ArrowRight)" aria-label="다음" disabled={ply >= plyCount} onClick={() => onGo(ply + 1)}><IconChev dir="r" /></button>
      <button type="button" className="rv-ctl" title="마지막 (End)" aria-label="마지막" disabled={ply >= plyCount} onClick={() => onGo(plyCount)}><IconChev dir="rr" /></button>
      <div className="rv-current mv">
        {preview && <span className="rv-preview-tag">미리보기</span>}
        <span className="rv-label" title={preview?.label}>{preview ? preview.label : ply === 0 || !san ? '시작 국면' : `${plyLabel(ply)} ${san}`}</span>
        <span className="rv-turn">· {turn}</span>
      </div>
      {preview && <button type="button" className="chip rv-chip-btn" onClick={onRestore}>원래 국면</button>}
      <button type="button" className="btn btn-ghost btn-sm" onClick={onFlip}><IconFlip /> 보드 뒤집기</button>
    </div>
  );
}
