import { useState } from 'react';
import type { CoachPreset, OpponentKind, OpponentSpec } from '../../api/types';
import { ALERT_LABEL, CONTROL_LABEL, PRESETS, PRESET_LABEL, type AlertMode, type CoachSettings, type Control, type TimeControl } from './state';

/** Rating ranges the backends actually support (docs 5.2): Maia-2 buckets, Stockfish UCI_Elo. */
export const OPPONENT_RANGE: Record<OpponentKind, { min: number; max: number; step: number }> = {
  maia: { min: 1100, max: 2000, step: 100 },
  stockfish: { min: 1320, max: 3190, step: 50 },
};
export const OPPONENT_LABEL: Record<OpponentKind, string> = { maia: 'Maia', stockfish: 'Stockfish' };

export function clampRating(kind: OpponentKind, rating: number): number {
  const r = OPPONENT_RANGE[kind];
  if (!Number.isFinite(rating)) return kind === 'maia' ? 1500 : 1600;
  return Math.min(r.max, Math.max(r.min, Math.round(rating / r.step) * r.step));
}

export function opponentName(o: OpponentSpec): string {
  return `${OPPONENT_LABEL[o.kind] ?? o.kind} ${o.rating}`;
}

const PRESET_NOTE: Record<CoachPreset, string> = {
  serious: '실전과 같은 조건. 알림·물리기·힌트를 모두 끕니다.',
  learning: '블런더만 알리고, 물리기와 힌트를 허용합니다.',
  free: '알림 없이 자유롭게. 분석판 용도입니다.',
};

/** 시간 제한 presets (4.2). 직접 입력 is the escape hatch for anything else. */
export const TIME_PRESETS: { label: string; tc: TimeControl | null }[] = [
  { label: '없음', tc: null },
  { label: '5+0', tc: { initial: 300, increment: 0 } },
  { label: '10+0', tc: { initial: 600, increment: 0 } },
  { label: '15+10', tc: { initial: 900, increment: 10 } },
];

function samePreset(a: TimeControl | null, b: TimeControl | null): boolean {
  if (!a || !b) return a === b;
  return a.initial === b.initial && a.increment === b.increment;
}

type Props = {
  control: Control;
  onControl: (c: Control) => void;
  opponent: OpponentSpec;
  onOpponent: (o: OpponentSpec) => void;
  coach: CoachSettings;
  onPreset: (p: CoachPreset) => void;
  onCoach: (c: Partial<CoachSettings>) => void;
  timeControl: TimeControl | null;
  onTimeControl: (tc: TimeControl | null) => void;
  username: string | null;
  onUsername: (name: string) => void;
  onNewGame: () => void;
};

/** 설정 tab: who holds the pieces, who the opponent is, and how much the coach says. */
export function SettingsPanel({ control, onControl, opponent, onOpponent, coach, onPreset, onCoach, timeControl, onTimeControl, username, onUsername, onNewGame }: Props) {
  const range = OPPONENT_RANGE[opponent.kind];
  return (
    <div className="pl-tabbody">
      <div className="tr-section" style={{ borderTop: 0, paddingTop: 0 }}>
        <span className="eyebrow">진행 방식</span>
        <div className="pl-seg">
          {(['manual', 'ai-white', 'ai-black'] as Control[]).map((c) => (
            <button key={c} type="button" className={`pl-seg-btn${control === c ? ' on' : ''}`} onClick={() => onControl(c)}>{CONTROL_LABEL[c]}</button>
          ))}
        </div>
        <span className="small faint">게임 도중 언제든 바꿀 수 있습니다. AI 차례가 되면 바로 둡니다.</span>
      </div>

      <div className="tr-section">
        <span className="eyebrow">상대</span>
        <div className="pl-seg">
          {(['maia', 'stockfish'] as OpponentKind[]).map((k) => (
            <button
              key={k}
              type="button"
              className={`pl-seg-btn${opponent.kind === k ? ' on' : ''}`}
              onClick={() => onOpponent({ kind: k, rating: clampRating(k, opponent.rating) })}
            >
              {OPPONENT_LABEL[k]}
            </button>
          ))}
        </div>
        <div className="tr-field">
          <label htmlFor="pl-rating">실력</label>
          <input
            id="pl-rating"
            className="tr-range"
            type="range"
            min={range.min}
            max={range.max}
            step={range.step}
            value={clampRating(opponent.kind, opponent.rating)}
            onChange={(e) => onOpponent({ ...opponent, rating: clampRating(opponent.kind, Number(e.target.value)) })}
          />
          <span className="mono" style={{ width: 46, textAlign: 'right' }}>{opponent.rating}</span>
        </div>
        <span className="small faint">
          {opponent.kind === 'maia' ? 'Maia는 이 레이팅대의 사람이 둘 법한 수를 고릅니다 — 실수도 사람처럼 합니다.' : 'Stockfish는 강도를 낮춰도 실수 패턴이 사람과 다릅니다.'}
        </span>
      </div>

      <div className="tr-section">
        <span className="eyebrow">코치</span>
        <div className="pl-seg">
          {(Object.keys(PRESETS) as CoachPreset[]).map((p) => (
            <button key={p} type="button" className={`pl-seg-btn${coach.preset === p ? ' on' : ''}`} onClick={() => onPreset(p)}>{PRESET_LABEL[p]}</button>
          ))}
        </div>
        <span className="small faint">{PRESET_NOTE[coach.preset]}</span>
        <div className="tr-field">
          <label htmlFor="pl-alerts">실수 알림</label>
          <select id="pl-alerts" className="tr-input" style={{ minWidth: 140 }} value={coach.alerts} onChange={(e) => onCoach({ alerts: e.target.value as AlertMode })}>
            {(['off', 'blunder', 'mistake'] as AlertMode[]).map((m) => <option key={m} value={m}>{ALERT_LABEL[m]}</option>)}
          </select>
        </div>
        <label className="pl-check">
          <input type="checkbox" checked={coach.takebacks} onChange={(e) => onCoach({ takebacks: e.target.checked })} />
          <span>물리기 허용</span>
        </label>
        <label className="pl-check">
          <input type="checkbox" checked={coach.hints} onChange={(e) => onCoach({ hints: e.target.checked })} />
          <span>힌트 허용</span>
        </label>
      </div>

      <div className="tr-section">
        <span className="eyebrow">시간 제한</span>
        <TimePicker value={timeControl} onChange={onTimeControl} />
        <span className="small faint">
          시간을 바꾸면 양쪽 시계가 처음부터 다시 갑니다. 내 시계가 0이 되면 시간패로 게임이 끝납니다 — 상대 시계는 참고용이라 떨어져도 계속 둡니다.
        </span>
      </div>

      <div className="tr-section">
        <span className="eyebrow">저장</span>
        <form
          className="tr-actions"
          onSubmit={(e) => { e.preventDefault(); const v = new FormData(e.currentTarget).get('name'); if (typeof v === 'string' && v.trim()) onUsername(v.trim()); }}
        >
          <input name="name" className="tr-input" placeholder="사용자명" defaultValue={username ?? ''} key={username ?? ''} />
          <button type="submit" className="btn btn-ghost compact">저장</button>
        </form>
        <span className="small faint">저장한 연습 게임은 이 이름으로 들어가고, 프로필 통계에서는 기본적으로 제외됩니다.</span>
      </div>

      <div className="tr-section">
        <button type="button" className="btn btn-ghost compact" onClick={onNewGame}>새 게임</button>
      </div>
    </div>
  );
}

/** 없음 / 5+0 / 10+0 / 15+10 / 직접 입력(분 + 초 가산). */
function TimePicker({ value, onChange }: { value: TimeControl | null; onChange: (tc: TimeControl | null) => void }) {
  const preset = TIME_PRESETS.find((p) => samePreset(p.tc, value)) ?? null;
  const [custom, setCustom] = useState(() => ({
    minutes: value ? Math.max(1, Math.round(value.initial / 60)) : 10,
    increment: value?.increment ?? 5,
  }));
  const customOn = value !== null && preset === null;
  const apply = (next: { minutes: number; increment: number }) => {
    setCustom(next);
    onChange({ initial: Math.max(1, Math.round(next.minutes)) * 60, increment: Math.max(0, Math.round(next.increment)) });
  };
  return (
    <>
      <div className="pl-seg">
        {TIME_PRESETS.map((p) => (
          <button key={p.label} type="button" className={`pl-seg-btn${!customOn && samePreset(p.tc, value) ? ' on' : ''}`} onClick={() => onChange(p.tc)}>{p.label}</button>
        ))}
        <button type="button" className={`pl-seg-btn${customOn ? ' on' : ''}`} onClick={() => apply(custom)}>직접</button>
      </div>
      {customOn && (
        <div className="tr-field">
          <label htmlFor="pl-tc-min">분</label>
          <input id="pl-tc-min" className="tr-input" style={{ width: 64 }} type="number" min={1} max={180} value={custom.minutes}
            onChange={(e) => apply({ ...custom, minutes: Number(e.target.value) || 1 })} />
          <label htmlFor="pl-tc-inc">가산(초)</label>
          <input id="pl-tc-inc" className="tr-input" style={{ width: 64 }} type="number" min={0} max={60} value={custom.increment}
            onChange={(e) => apply({ ...custom, increment: Number(e.target.value) || 0 })} />
        </div>
      )}
    </>
  );
}
