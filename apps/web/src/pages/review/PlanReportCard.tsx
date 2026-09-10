import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import type { PlanReport } from '../../api/types';
import { PlanRow } from './StrategyPanel';
import { IconArrow, sideLabel } from './shared';
import { errorText } from './useReviewData';

type State = { status: 'loading' | 'ready' | 'error'; report: PlanReport | null; error: string | null };

const MODE_LABEL: Record<NonNullable<PlanReport['practice_mode']>, string> = { free: '자유 대국', drill: '수순 드릴', tabiya: '타비야 대국' };

/** Placeholder shown in place of the report while the game is still being analysed.
 * GET /play/report/{id} needs the finished analysis and blocks until it has it (minutes on a
 * long game), so the card itself is only mounted once /analysis reports `done`. */
export function PlanReportPending() {
  return (
    <div className="rv-section rv-report">
      <span className="eyebrow">연습 게임 계획 리포트</span>
      <div className="small muted">분석이 끝나면 계획 리포트가 나옵니다.</div>
    </div>
  );
}

/** Game-level plan report for a practice game (GET /play/report/{id}): which typical plans of
 * the structure the student executed, which the engine still saw, which never came. Shown
 * above the per-move strategy panel so the 전략과 계획 tab reads game first, move second. */
export function PlanReportCard({ gameId }: { gameId: number }) {
  const [state, setState] = useState<State>({ status: 'loading', report: null, error: null });
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setState((s) => ({ ...s, status: 'loading', error: null }));
    api.play.report(gameId)
      .then((report) => { if (!cancelled) setState({ status: 'ready', report, error: null }); })
      .catch((e) => { if (!cancelled) setState({ status: 'error', report: null, error: errorText(e) }); });
    return () => { cancelled = true; };
  }, [gameId, retry]);

  if (state.status === 'loading') {
    return <div className="rv-section rv-report" aria-busy="true"><span className="eyebrow">연습 게임 계획 리포트</span><div className="rv-skel" style={{ height: 16, width: '70%' }} /></div>;
  }
  if (state.status === 'error' || !state.report) {
    return (
      <div className="rv-section rv-report">
        <span className="eyebrow">연습 게임 계획 리포트</span>
        <div className="small muted">리포트를 만들지 못했습니다. {state.error}</div>
        <div><button type="button" className="btn btn-ghost compact" onClick={() => setRetry((n) => n + 1)}>다시 시도</button></div>
      </div>
    );
  }
  const r = state.report;
  const done = [...r.executed, ...r.pv_match];
  const total = done.length + r.later.length + r.unavailable.length;
  return (
    <div className="rv-section rv-report">
      <div className="rv-section-head">
        <span className="eyebrow">연습 게임 계획 리포트</span>
        {r.structure && <span className="chip">{r.structure.name}</span>}
        <span className="chip">{sideLabel(r.side)}</span>
        {r.practice_mode && <span className="badge badge-neutral rv-badge-sm">{MODE_LABEL[r.practice_mode]}</span>}
        {r.opening_name && <span className="small muted">{r.opening_name}</span>}
        <div className="spacer" />
        {r.opening_id && (
          <Link className="btn btn-ghost compact" to={`/play?opening=${encodeURIComponent(r.opening_id)}&color=${r.side}`}>같은 타비야 다시 두기 <IconArrow /></Link>
        )}
      </div>
      {r.summary && <p className="rv-lead-sub">{r.summary}</p>}
      {total > 0 && (
        <div className="small muted">계획 {total}개 중 실행 {r.executed.length} · 엔진 PV 일치 {r.pv_match.length} · 아직 {r.later.length} · 불가 {r.unavailable.length}</div>
      )}
      {r.breaks.length > 0 && (
        <div className="small">둔 브레이크: {r.breaks.map((b) => <span key={b} className="mv" style={{ marginRight: 8 }}>{b}</span>)}</div>
      )}
      <div>
        {[...r.executed, ...r.pv_match, ...r.later, ...r.unavailable].map((plan, i) => <PlanRow key={`${plan.title}:${i}`} plan={plan} />)}
      </div>
    </div>
  );
}
