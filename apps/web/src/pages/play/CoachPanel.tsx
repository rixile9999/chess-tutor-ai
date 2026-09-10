import { useState } from 'react';
import type { HintLevel, PlayCheckResponse, PlayHintResponse } from '../../api/types';
import { CLASS_LABEL, CLASS_TONE, motifLabel } from '../../lib/labels';
import { IconBulb } from '../training/icons';
import type { CoachSettings, PlayState } from './state';

export type HintState = { level: HintLevel | null; loading: boolean; error: string | null; data: PlayHintResponse | null };

export const HINT_LABEL: Record<HintLevel, string> = { 1: '구조·계획', 2: '자연스러운 수', 3: '최선수' };
const HINT_NOTE: Record<HintLevel, string> = {
  1: '수는 말하지 않고 폰 구조와 그 구조의 전형적인 계획만 보여 줍니다.',
  2: '이 레이팅대의 사람들이 실제로 두는 후보 수와 그 비율입니다.',
  3: '엔진 최선수와 주변화·모티프입니다. 대국 중 평가 막대는 보여 주지 않습니다.',
};

function pct(p: number | null | undefined): string {
  if (p === null || p === undefined || !Number.isFinite(p)) return '';
  const v = p <= 1 ? p * 100 : p;
  return `${v < 10 ? v.toFixed(1) : Math.round(v)}%`;
}

type Props = {
  coach: CoachSettings;
  stats: PlayState['stats'];
  hint: HintState;
  hintsShown: HintLevel[];
  canHint: boolean;
  hintReason: string | null;
  onHint: (level: HintLevel) => void;
};

/** 코치 tab: the three hint levels and this game's help counters (docs 4.3). */
export function CoachPanel({ coach, stats, hint, hintsShown, canHint, hintReason, onHint }: Props) {
  const data = hint.data;
  return (
    <div className="pl-tabbody">
      <div className="tr-panel-head">
        <span className="eyebrow">코치</span>
        <span className="small muted">프리셋 {coach.hints ? '힌트 켬' : '힌트 끔'} · 알림 {coach.alerts === 'off' ? '끔' : coach.alerts === 'blunder' ? '블런더만' : '실수부터'}</span>
      </div>

      <div className="pl-hints">
        {([1, 2, 3] as HintLevel[]).map((level) => (
          <button
            key={level}
            type="button"
            className={`btn btn-ghost compact${hint.level === level ? ' on' : ''}`}
            disabled={!canHint || hint.loading}
            onClick={() => onHint(level)}
          >
            <IconBulb /> {HINT_LABEL[level]}
            {hintsShown.includes(level) && <span className="badge badge-neutral">봄</span>}
          </button>
        ))}
      </div>
      {!canHint && <p className="small faint" style={{ margin: 0 }}>{hintReason}</p>}

      {hint.loading && <div className="tr-skeleton" style={{ width: '60%' }} />}
      {hint.error && <div className="tr-msg bad"><span><b>힌트를 받지 못했습니다.</b> {hint.error}</span></div>}

      {data && !hint.loading && (
        <div className="pl-hint">
          <div className="tr-line">
            <span className="chip">{HINT_LABEL[data.level] ?? `힌트 ${data.level}`}</span>
            {data.structure?.name && <span className="chip">구조 {data.structure.name}</span>}
            {data.verified === false && <span className="badge badge-bad">검증 실패</span>}
            {data.total_claims > 0 && <span className="small faint">근거 {data.verified_claims}/{data.total_claims}</span>}
          </div>
          {data.text && <p className="pl-text">{data.text}</p>}
          <p className="small faint" style={{ margin: 0 }}>{HINT_NOTE[data.level] ?? ''}</p>

          {(data.plans ?? []).length > 0 && (
            <ul className="pl-plans">
              {data.plans.map((p, i) => (
                <li key={`${p.title}-${i}`}>
                  <b>{p.title}</b>
                  {p.condition && <span className="small muted"> — {p.condition}</span>}
                  {(p.moves_hint ?? []).length > 0 && (
                    <span className="tr-line" style={{ marginTop: 4 }}>
                      {p.moves_hint.map((m) => <span key={m} className="tr-mv">{m}</span>)}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}

          {(data.candidates ?? []).length > 0 && (
            <div className="tr-probs">
              {data.candidates.map((c) => (
                <div key={c.uci || c.san} className="pl-cand">
                  <div className="tr-prob">
                    <span className="mv">{c.san}</span>
                    <div className="tr-bar"><i style={{ width: `${Math.round(Math.min(1, Math.max(0, c.prob ?? 0)) * 100)}%` }} /></div>
                    <span className="mono small muted" style={{ textAlign: 'right' }}>{pct(c.prob)}</span>
                  </div>
                  {c.reason && <p className="small muted pl-cand-why">{c.reason}</p>}
                </div>
              ))}
            </div>
          )}

          {data.best && (
            <div className="pl-best">
              <div className="tr-line">
                <span className="chip">최선수 <span className="mv">{data.best.san}</span></span>
                {data.best.computer_move && <span className="badge badge-neutral">사람은 잘 두지 않는 수</span>}
              </div>
              {data.best.reason && <p className="pl-text">{data.best.reason}</p>}
              {(data.best.pv ?? []).length > 0 && (
                <div className="tr-line">{data.best.pv.slice(0, 8).map((san, i) => <span key={`${san}-${i}`} className="tr-mv">{san}</span>)}</div>
              )}
              {(data.best.motifs ?? []).length > 0 && (
                <div className="tr-line">{data.best.motifs.map((m, i) => <span key={`${m.kind}-${i}`} className="chip">{motifLabel(m.kind)}</span>)}</div>
              )}
            </div>
          )}
        </div>
      )}

      <div className="tr-section">
        <span className="small muted">이번 게임: 힌트 {stats.hints}회, 물리기 {stats.takebacks}회, 알림 {stats.alerts}회</span>
        <span className="small faint">도움을 받은 게임은 저장할 때 헤더에 남고, 프로필 통계에서 구분됩니다.</span>
      </div>
    </div>
  );
}

type AlertProps = {
  alert: PlayCheckResponse;
  canTakeback: boolean;
  onTakeback: () => void;
  onKeep: () => void;
};

/** 실수 알림 (4.3). Shown above the tab body so it is visible from every tab. */
export function AlertBanner({ alert, canTakeback, onTakeback, onKeep }: AlertProps) {
  const [why, setWhy] = useState(false);
  const tone = CLASS_TONE[alert.classification] ?? 'neutral';
  return (
    <div className={`pl-alert ${tone}`}>
      <div className="tr-line">
        <span className={`badge badge-${tone === 'good' ? 'good' : tone === 'bad' ? 'bad' : 'neutral'}`}>
          {CLASS_LABEL[alert.classification] ?? alert.classification}
        </span>
        <b><span className="mv">{alert.san}</span> 는 큰 실수일 수 있어요</b>
      </div>
      <div className="tr-actions">
        <button type="button" className="btn btn-ghost compact" onClick={onTakeback} disabled={!canTakeback}>물리기</button>
        <button type="button" className="btn btn-ghost compact" onClick={onKeep}>그대로 두기</button>
        <button type="button" className="btn btn-ghost compact" onClick={() => setWhy((v) => !v)}>{why ? '이유 접기' : '이유 보기'}</button>
      </div>
      {why && (
        <div className="pl-alert-why">
          {alert.reason && <p className="pl-text">{alert.reason}</p>}
          {alert.best_san && (
            <div className="tr-line">
              <span className="chip">{alert.computer_move ? '엔진 최선수' : '최선수'} <span className="mv">{alert.best_san}</span></span>
              {(alert.pv ?? []).slice(0, 6).map((san, i) => <span key={`${san}-${i}`} className="tr-mv">{san}</span>)}
            </div>
          )}
          {alert.computer_move && alert.alternative_san && (
            <div className="tr-line">
              <span className="chip">사람이 둘 만한 대안 <span className="mv">{alert.alternative_san}</span></span>
              {alert.alternative_reason && <span className="small muted">{alert.alternative_reason}</span>}
            </div>
          )}
          {alert.verified === false && <span className="badge badge-bad">검증 실패</span>}
        </div>
      )}
    </div>
  );
}
