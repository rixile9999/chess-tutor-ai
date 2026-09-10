import { useState } from 'react';
import type { Color, SetupState, SetupStatus } from '../../api/types';
import { badgeClass, type BadgeTone } from './colors';

const SHOWN = 4;
const SIDE_LABEL: Record<Color, string> = { white: '백', black: '흑' };
const STATUS: Record<SetupState, { tone: BadgeTone; label: string }> = {
  completed: { tone: 'good', label: '완성' },
  in_progress: { tone: 'neutral', label: '진행 중' },
  possible: { tone: 'neutral', label: '가능' },
  blocked: { tone: 'bad', label: '막힘' },
};

/** "Nbd2" -> "d2", "e3" -> "e3", "O-O" -> null: the square a placement chip points at. */
export function targetSquare(placement: string): string | null {
  const m = /([a-h][1-8])$/.exec(placement.trim());
  return m ? m[1] : null;
}

type Props = {
  setups: SetupStatus[];
  side: Color;
  loading: boolean;
  /** The square drawn on the board right now, if any. */
  marked: string | null;
  onMark: (square: string | null) => void;
};

/** ⑤ 셋업 플레이: how far each system setup is from being on the board (요구 5). */
export function SetupPanel({ setups, side, loading, marked, onMark }: Props) {
  const [all, setAll] = useState(false);
  const rows = all ? setups : setups.slice(0, SHOWN);

  return (
    <div className="op-setup">
      <div className="op-card-head">
        <span className="h3">셋업</span>
        <span className="small muted">{SIDE_LABEL[side]} 차례 기준 · 배치 완성까지 남은 수</span>
      </div>
      {loading ? (
        <div className="op-state compact"><div className="op-spinner" /></div>
      ) : !setups.length ? (
        <div className="op-setup-empty small muted">이 국면에서 노릴 수 있는 셋업이 없습니다.</div>
      ) : (
        <>
          {rows.map((s) => <Row key={s.id} setup={s} marked={marked} onMark={onMark} />)}
          {setups.length > SHOWN && (
            <button type="button" className="op-link-btn" onClick={() => setAll((v) => !v)}>
              {all ? '접기' : `기타 ${setups.length - SHOWN}가지 더 보기`}
            </button>
          )}
        </>
      )}
    </div>
  );
}

function Row({ setup, marked, onMark }: { setup: SetupStatus; marked: string | null; onMark: (sq: string | null) => void }) {
  const { tone, label } = STATUS[setup.status] ?? STATUS.possible;
  const total = setup.done.length + setup.remaining.length;
  return (
    <div className={`op-setup-row ${setup.status}`}>
      <span className="op-setup-name">
        {setup.name} <span className={`${badgeClass(tone)} op-badge sm`}>{label}</span>
      </span>
      <span className="op-setup-prog">
        <span className="op-setup-pips">
          {Array.from({ length: total }, (_, i) => (
            <i key={i} className={i < setup.done.length ? 'on' : setup.status === 'blocked' && i === setup.done.length ? 'bad' : ''} />
          ))}
        </span>
        <span className="mono">{setup.done.length}/{total}</span>
      </span>
      {setup.blocked_by && <span className="op-setup-why">{setup.blocked_by}</span>}
      <span className="op-setup-rest">
        {setup.status === 'completed' ? '배치 완성' : '남은 배치'}
        {setup.done.map((d) => <span key={`d-${d}`} className="mv op-setup-chip done">{d}</span>)}
        {setup.remaining.map((r) => {
          const sq = targetSquare(r);
          return (
            <button
              key={`r-${r}`}
              type="button"
              className={`mv op-setup-chip${sq && marked === sq ? ' on' : ''}`}
              title={sq ? `${sq} 칸을 보드에 표시` : '캐슬링'}
              disabled={!sq}
              onClick={() => onMark(sq && marked === sq ? null : sq)}
            >
              {r}
            </button>
          );
        })}
      </span>
      {(setup.plans.length > 0 || setup.typical_against) && (
        <span className="op-setup-plan small">
          {setup.plans.length > 0 && <>계획: {setup.plans.join(' · ')}</>}
          {setup.typical_against && <span className="faint">{setup.plans.length ? ' · ' : ''}{setup.typical_against}</span>}
        </span>
      )}
    </div>
  );
}
