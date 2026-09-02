import type { Color, Score } from '../../api/types';
import { formatScore, whiteShare } from '../../lib/labels';

type Props = { score: Score | null; orientation: Color; height?: number };

/** Vertical eval bar next to the board; white's share sits at the bottom when white is at the bottom. */
export function EvalBar({ score, orientation, height = 520 }: Props) {
  const share = score ? whiteShare(score) : 0.5;
  const label = score ? formatScore(score) : '';
  const whiteOnTop = orientation === 'black';
  const pctHeight = `${Math.round(share * 1000) / 10}%`;
  return (
    <div className="rv-evalbar" style={{ height }} title={label ? `평가 ${label} (백 기준)` : '평가 없음'}>
      <div className="rv-evalbar-white" style={whiteOnTop ? { top: 0, height: pctHeight } : { bottom: 0, height: pctHeight }} />
      <div className="rv-evalbar-label mono" style={{ color: whiteOnTop ? 'var(--ink)' : 'var(--paper)' }}>{label}</div>
    </div>
  );
}
