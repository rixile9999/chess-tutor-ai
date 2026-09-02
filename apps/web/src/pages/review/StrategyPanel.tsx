import { Link } from 'react-router-dom';
import type { Color, MoveReviewOut, Plan, StrategyView } from '../../api/types';
import { playSans } from '../../lib/chess';
import { formatScore, plyLabel } from '../../lib/labels';
import { maiaLabel, trainingHref } from './ExplanationPanel';
import { ClassBadge, IconArrow, IconCheck, IconPlay, IconSave, LineChips, VerifyRow, pct, sideLabel, type Preview } from './shared';

type Props = {
  review: MoveReviewOut; strategy: StrategyView; ply: number; userColor: Color | null; rating: number | undefined; boardFen: string;
  preview: Preview | null; onPreview: (p: Preview | null) => void; onSavePuzzle: () => void;
};

const STATUS: Record<Plan['status'], { label: string; tone: 'good' | 'neutral' | 'bad'; check?: boolean }> = {
  pv_match: { label: '엔진 PV 일치', tone: 'good', check: true },
  executed: { label: '실행함', tone: 'good' },
  later: { label: '아직 이름', tone: 'neutral' },
  unavailable: { label: '지금은 불가', tone: 'bad' },
};

function PlanRow({ plan }: { plan: Plan }) {
  const s = STATUS[plan.status] ?? STATUS.later;
  return (
    <div className="rv-plan">
      <div className="rv-plan-title">
        <span>{plan.title}</span>
        {(plan.moves_hint ?? []).length > 0 && <span className="mv small muted">{plan.moves_hint.join(' ')}</span>}
        <div className="spacer" />
        <span className={`badge badge-${s.tone} rv-badge-sm`} style={{ fontWeight: 600 }}>{s.check && <IconCheck />}{s.label}</span>
      </div>
      {plan.condition && <div className="rv-plan-cond">{plan.condition}</div>}
    </div>
  );
}

/** 전략과 계획 tab: structure, timeline, plans for both sides, your move, counterfactual, record. */
export function StrategyPanel({ review, strategy, ply, userColor, rating, boardFen, preview, onPreview, onSavePuzzle }: Props) {
  const st = strategy.structure;
  const plans = strategy.plans ?? [];
  const first: Color = userColor ?? review.color;
  const second: Color = first === 'white' ? 'black' : 'white';
  const bySide = (c: Color) => plans.filter((p) => p.side === c);
  const timeline = strategy.timeline ?? [];
  const cf = strategy.counterfactual;
  const cfBase = cf && cf.line?.length ? (playSans(review.fen_after, cf.line) ? review.fen_after : playSans(review.fen_before, cf.line) ? review.fen_before : null) : null;
  const rec = strategy.record ?? {};
  const games = rec.games ?? null, winRate = rec.win_rate ?? null, avgBreak = rec.avg_break_move ?? null;
  const myPlans = bySide(first).map((p) => p.title);
  const lead = st
    ? `${st.name} 구조입니다.${myPlans.length ? ` ${sideLabel(first)}의 핵심 계획은 ${myPlans.join(', ')}이고, 승부는 그 타이밍에서 갈립니다.` : ''}`
    : '';

  return (
    <>
      <div className="rv-head" style={{ marginTop: -6 }}>
        <div className="rv-head-title">{st ? `${st.name} 구조` : '구조 미분류'}</div>
        {st && (st.defining_pawns ?? []).length > 0 && (
          <span className="chip">{st.side === 'both' || !st.side ? '' : `${sideLabel(st.side)}: `}{st.defining_pawns.join(' ')}</span>
        )}
        <div className="spacer" />
        {st && <span className="small faint">분류 확신 {st.confidence.toFixed(2)}</span>}
      </div>
      {timeline.length > 0 && (
        <div className="rv-timeline">
          <span className="eyebrow" style={{ marginRight: 4 }}>구조 흐름</span>
          {timeline.map((t, i) => {
            const on = ply >= t.from_ply && ply <= t.to_ply;
            return (
              <span key={`${t.key}-${i}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                {i > 0 && <IconArrow />}
                <span className={`chip${on ? ' rv-chip-on' : ''}`} style={{ height: 22 }}>{t.name} {Math.ceil(t.from_ply / 2)}~{Math.ceil(t.to_ply / 2)}수</span>
              </span>
            );
          })}
        </div>
      )}
      {lead && <p className="rv-lead">{lead}</p>}
      {!st && plans.length === 0 && !strategy.your_move && !cf && <p className="rv-lead-sub">이 국면에서는 아직 전략 정보를 만들지 못했습니다.</p>}
      {(review.arrows ?? []).length > 0 || (review.highlights ?? []).length > 0 ? (
        <div className="rv-legend">
          <span><i />좋은 계획, 브레이크</span>
          <span><i className="thick" />상대의 계획, 위협</span>
          {(review.highlights ?? []).length > 0 && <span><i className="mark" />방금 둔 수</span>}
        </div>
      ) : null}

      {plans.length > 0 && (
        <div className="rv-two">
          {[first, second].map((c) => (
            <div key={c}>
              <div className="h3" style={{ marginBottom: 4 }}>{sideLabel(c)}의 계획</div>
              {bySide(c).length ? bySide(c).map((p, i) => <PlanRow key={i} plan={p} />) : <div className="small muted" style={{ padding: '8px 0' }}>정리된 계획이 없습니다.</div>}
            </div>
          ))}
        </div>
      )}

      {(strategy.your_move || cf) && (
        <div className="rv-two">
          {strategy.your_move && (
            <div className="rv-box rv-box-good">
              <div className="rv-box-head">
                <span className="eyebrow" style={{ color: 'var(--good)' }}>당신이 한 것</span>
                <span className="mv" style={{ fontSize: 14 }}>{plyLabel(ply)} {strategy.your_move.san}</span>
                <ClassBadge cls={strategy.your_move.classification} />
                {strategy.your_move.plan_match && <span className="chip" style={{ height: 20 }}>계획과 일치</span>}
              </div>
              <div>{strategy.your_move.note}</div>
            </div>
          )}
          {cf && (
            <div className="rv-box rv-box-paper">
              <div className="rv-box-head"><span className="eyebrow">반사실 · {cf.question}</span></div>
              {cf.line?.length > 0 && (cfBase
                ? <LineChips sans={cf.line} startPly={cfBase === review.fen_after ? ply + 1 : ply} baseFen={cfBase} idPrefix="cf" preview={preview} onPreview={onPreview} size="sm" />
                : <div className="mv small muted">{cf.line.join(' ')}</div>)}
              <div>{cf.verdict}{cf.eval && (cf.eval.cp !== null || cf.eval.mate !== null) ? <span className="mono"> ({formatScore(cf.eval)})</span> : null}</div>
            </div>
          )}
        </div>
      )}

      {games !== null && (
        <div className="rv-record">
          <span className="eyebrow">이 구조에서 당신의 기록</span>
          <span><b>{games}판</b>{winRate !== null && <> · 승률 <b style={{ color: winRate <= (winRate <= 1 ? 0.45 : 45) ? 'var(--bad)' : 'var(--ink)' }}>{pct(winRate)}</b></>}</span>
          {avgBreak !== null && <span className="muted">평균 브레이크 시점 <b style={{ color: 'var(--ink)' }}>{Math.round(avgBreak)}수</b></span>}
          <div className="spacer" />
          <Link to="/profile">구조별 리포트 보기</Link>
        </div>
      )}

      <VerifyRow explanation={review.explanation}>
        <Link className="btn btn-primary" to={trainingHref(boardFen, rating)}><IconPlay /> 이 국면에서 이어 두기{maiaLabel(rating)}</Link>
        <button type="button" className="btn btn-ghost" onClick={onSavePuzzle}><IconSave /> 이 게임에서 퍼즐 만들기</button>
      </VerifyRow>
    </>
  );
}
