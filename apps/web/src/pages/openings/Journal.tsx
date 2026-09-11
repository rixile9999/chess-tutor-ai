import { badgeClass, engineBadge, moveBadges } from './colors';
import { moveLabel, onLine, type JournalEntry, type LinePly } from './line';

const MAX_BADGES = 4;

type Props = {
  journal: JournalEntry[];
  plies: LinePly[];
  focusPly: number;
  onGoto: (ply: number) => void;
  /** Ask for this entry's annotation again after a failed /annotate. */
  onRetry: (seq: string[]) => void;
  /** The deep note's summary when one is already in hand, else null (§9.3). */
  noteSummary: (seq: string[]) => string | null;
};

/** The first sentence of a summary, without the [[…]] verification marks. */
export function firstSentence(text: string): string {
  const t = text.replace(/\[\[|\]\]/g, '').trim();
  const m = /^(.*?[.!?])\s/.exec(t);
  return m ? m[1] : t;
}

/** ③ 해설 일지: one line per move, in the order they were played. Nothing is ever removed (요구 3). */
export function Journal({ journal, plies, focusPly, onGoto, onRetry, noteSummary }: Props) {
  if (!journal.length) {
    return <div className="op-journal-empty">아직 둔 수가 없습니다. 첫 수를 두면 여기에 해설이 쌓입니다.</div>;
  }
  return (
    <div className="op-journal">
      {journal.map((e, i) => {
        if (e.kind === 'rewind') {
          return (
            <div className="op-journal-note" key={i}>
              <span className="mono op-journal-lab">↶</span>
              <span>{e.toPly}수로 돌아가 <span className="mv">{e.was}</span> 대신 <span className="mv">{e.instead}</span></span>
            </div>
          );
        }
        if (e.kind === 'jump') {
          return (
            <div className="op-journal-note" key={i}>
              <span className="mono op-journal-lab">↷</span>
              <span>
                {e.sans.length
                  ? <>개요에서 점프: <span className="mv">{e.sans.map((s, j) => moveLabel(j + 1, s)).join(' ')}</span> — 수순 전체를 한 번에 해설</>
                  : '새 수순으로 시작'}
              </span>
            </div>
          );
        }

        const live = onLine({ plies }, e);
        const focused = live && e.ply === focusPly;
        const a = e.annotation;
        const badges = a ? moveBadges(a).slice(0, MAX_BADGES) : [];
        const engine = engineBadge(a?.engine ?? null);
        const note = noteSummary(e.seq);
        const summary = note ? firstSentence(note) : a ? firstSentence(a.text) : '';
        const label = a?.label ?? moveLabel(e.ply, e.seq[e.seq.length - 1] ?? '');
        return (
          <div
            key={i}
            className={`op-journal-entry${focused ? ' cur' : ''}${live ? '' : ' past'}`}
            role="button"
            tabIndex={0}
            aria-current={focused || undefined}
            onClick={() => { if (live) onGoto(e.ply); }}
            onKeyDown={(ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); if (live) onGoto(e.ply); } }}
          >
            <span className="mv op-journal-lab">{label}</span>
            <span className="op-journal-body">
              {(badges.length > 0 || engine) && (
                <span className="op-journal-tags">
                  {badges.map((b, j) => <span key={j} className={`${badgeClass(b.tone)} op-badge sm`} title={b.title}>{b.text}</span>)}
                  {engine && <span className={`${badgeClass(engine.tone)} op-badge sm`}>{engine.text}</span>}
                </span>
              )}
              {e.pending ? (
                <span className="op-journal-text pending">해설을 만드는 중…</span>
              ) : e.error ? (
                <span className="op-journal-text">
                  <span className="op-error">해설 실패: {e.error}</span>
                  <button
                    type="button"
                    className="op-link-btn"
                    onClick={(ev) => { ev.stopPropagation(); onRetry(e.seq); }}
                  >
                    다시
                  </button>
                </span>
              ) : (
                <span className="op-journal-text">{summary || '해설이 없습니다.'}</span>
              )}
            </span>
            <span className="op-journal-detail small">{focused ? '◀ 옆 패널에 표시 중' : live ? '자세히 →' : ''}</span>
          </div>
        );
      })}
    </div>
  );
}
