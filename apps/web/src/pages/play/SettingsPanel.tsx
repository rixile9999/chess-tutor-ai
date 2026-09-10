import type { CoachPreset, OpponentKind, OpponentSpec } from '../../api/types';
import { ALERT_LABEL, CONTROL_LABEL, PRESETS, PRESET_LABEL, type AlertMode, type CoachSettings, type Control } from './state';

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

type Props = {
  control: Control;
  onControl: (c: Control) => void;
  opponent: OpponentSpec;
  onOpponent: (o: OpponentSpec) => void;
  coach: CoachSettings;
  onPreset: (p: CoachPreset) => void;
  onCoach: (c: Partial<CoachSettings>) => void;
  username: string | null;
  onUsername: (name: string) => void;
  onNewGame: () => void;
};

/** 설정 tab: who holds the pieces, who the opponent is, and how much the coach says. */
export function SettingsPanel({ control, onControl, opponent, onOpponent, coach, onPreset, onCoach, username, onUsername, onNewGame }: Props) {
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
