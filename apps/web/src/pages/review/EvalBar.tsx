import type { Color, Score } from '../../api/types';
import { formatScore, whiteShare } from '../../lib/labels';

type Props = { score: Score | null; orientation: Color; height?: number };

/** Share of the bar the label text covers at the top. */
const LABEL_ZONE = 0.08;

/**
 * Is the strip under the label (the top of the bar) white? White grows from the top when white is
 * on top, otherwise from the bottom, so a big white share reaches the top from below too.
 */
export function labelOnWhite(share: number, whiteOnTop: boolean): boolean {
  return whiteOnTop ? share >= LABEL_ZONE : share >= 1 - LABEL_ZONE;
}

/** Vertical eval bar next to the board; white's share sits at the bottom when white is at the bottom. */
export function EvalBar({ score, orientation, height = 520 }: Props) {
  const share = score ? whiteShare(score) : 0.5;
  const label = score ? formatScore(score) : '';
  const whiteOnTop = orientation === 'black';
  const pctHeight = `${Math.round(share * 1000) / 10}%`;
  // The label hugs the top of the bar, so its colour must follow whichever strip reaches the top.
  const topIsWhite = labelOnWhite(share, whiteOnTop);
  return (
    <div className="rv-evalbar" style={{ height }} title={label ? `평가 ${label} (백 기준)` : '평가 없음'}>
      <div className="rv-evalbar-white" style={whiteOnTop ? { top: 0, height: pctHeight } : { bottom: 0, height: pctHeight }} />
      <div className="rv-evalbar-label mono" style={{ color: topIsWhite ? 'var(--ink)' : 'var(--paper)' }}>{label}</div>
    </div>
  );
}
