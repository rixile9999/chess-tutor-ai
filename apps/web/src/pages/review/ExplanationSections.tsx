import type { Alternative, Comparison, HumanView, MotifOut, MoveReviewOut, Refutation } from '../../api/types';
import { playSans } from '../../lib/chess';
import { formatScore, motifLabel, plyLabel } from '../../lib/labels';
import { ClassBadge, LineChips, makePreview, type Preview } from './shared';

type Common = { review: MoveReviewOut; ply: number; preview: Preview | null; onPreview: (p: Preview | null) => void };

/** 무슨 일이 일어나는가: the punishing line, its branches, motifs and the "why not X" note. */
export function RefutationSection({ refutation, motifs, review, ply, preview, onPreview }: Common & { refutation: Refutation; motifs: MotifOut[] }) {
  const base = review.fen_after;
  const main = refutation.main_line ?? [];
  const afterMain = main.length ? playSans(base, [main[0]]) : null;
  return (
    <div className="rv-section">
      <div className="rv-section-head">
        <span className="h3">무슨 일이 일어나는가</span>
        <span className="small muted">반박 수순 · 클릭하면 보드에 재생</span>
        {preview && <button type="button" className="chip rv-chip-btn" onClick={() => onPreview(null)}>원래 국면</button>}
      </div>
      {main.length > 0 && (
        <div className="rv-refute">
          <LineChips sans={main} startPly={ply + 1} baseFen={base} idPrefix="ref" preview={preview} onPreview={onPreview} />
        </div>
      )}
      {(refutation.branches ?? []).length > 0 && (
        <table className="rv-branches">
          <tbody>
            {refutation.branches.map((b, i) => {
              const moves = b.moves ?? [];
              const fromMain = afterMain !== null && playSans(afterMain, moves) !== null;
              const branchBase = fromMain ? afterMain : base;
              return (
                <tr key={i}>
                  <td><LineChips sans={moves} startPly={ply + (fromMain ? 2 : 1)} baseFen={branchBase} idPrefix={`br${i}`} preview={preview} onPreview={onPreview} /></td>
                  <td>{b.result}{b.eval && (b.eval.cp !== null || b.eval.mate !== null) ? <span className="mono"> · {formatScore(b.eval)}</span> : null}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {motifs.length > 0 && (
        <div className="rv-chips">
          <span className="small faint" style={{ marginRight: 4 }}>모티프</span>
          {motifs.map((m, i) => <span className="chip" key={`${m.kind}-${i}`} title={m.targets?.length ? `${m.attacker} → ${m.targets.join(', ')}` : undefined}>{motifLabel(m.kind)}{m.with_check ? ' · 체크' : ''}</span>)}
        </div>
      )}
      {refutation.note && <div className="rv-note"><div>{refutation.note}</div></div>}
    </div>
  );
}

/** 대신 두었어야 할 수: each alternative with its eval, continuation and reason. */
export function AlternativesSection({ alternatives, human, review, ply, preview, onPreview }: Common & { alternatives: Alternative[]; human: HumanView | null }) {
  const base = review.fen_before;
  return (
    <div className="rv-section" style={{ gap: 8 }}>
      <div className="rv-section-head">
        <span className="h3">대신 두었어야 할 수</span>
        {human?.computer_move && <span className="chip rv-chip-dashed">엔진 최선수는 이 구간에서 거의 나오지 않는 수</span>}
      </div>
      <div className="rv-alts">
        {alternatives.map((a, i) => {
          const id = `alt${i}`;
          const on = preview?.id === id;
          const afterSan = playSans(base, [a.san]);
          const line = (a.line ?? []).filter((_, j) => !(j === 0 && a.line[0] === a.san));
          const p = human?.move_probs?.[a.san];
          return (
            <div key={id} style={{ display: 'contents' }}>
              <div className="rv-alt-san">
                <button type="button" className={`rv-mv${on ? ' active' : ''}`} style={{ fontSize: 14 }} title="보드에서 보기"
                  onClick={() => onPreview(on ? null : makePreview(id, base, [a.san], `${plyLabel(ply)} ${a.san}`))}>
                  {plyLabel(ply)} {a.san}
                </button>
                {a.is_best && <ClassBadge cls="best" />}
              </div>
              <div className="mono" style={{ color: a.is_best ? 'var(--good)' : 'var(--ink-2)', fontWeight: 600 }}>{formatScore(a.eval)}</div>
              <div className="rv-alt-why">
                {afterSan && line.length > 0 && <LineChips sans={line} startPly={ply + 1} baseFen={afterSan} idPrefix={`${id}l`} preview={preview} onPreview={onPreview} size="sm" />}
                {a.why && <span>{a.why}</span>}
                {p !== undefined && <span className="small faint">사람 {Math.round(p * 100)}%</span>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** 왜 A가 B보다 나은가: the two-column feature comparison at the divergence point. */
export function ComparisonSection({ comparison, preview, onPreview }: { comparison: Comparison; preview: Preview | null; onPreview: (p: Preview | null) => void }) {
  const c = comparison;
  const on = preview?.id === 'cmp';
  return (
    <div className="rv-section" style={{ gap: 8 }}>
      <div className="rv-section-head">
        <span className="h3">왜 {c.a_san}가 {c.b_san}보다 나은가</span>
        {c.divergence_ply != null && <span className="small muted">분기점 · {plyLabel(c.divergence_ply)} 이후 국면 비교</span>}
        {c.divergence_fen && (
          <button type="button" className={`chip rv-chip-btn${on ? ' rv-chip-on' : ''}`}
            onClick={() => onPreview(on ? null : { id: 'cmp', fen: c.divergence_fen as string, label: '분기 국면', lastMove: null, shapes: [] })}>
            {on ? '원래 국면' : '분기 국면 보기'}
          </button>
        )}
      </div>
      <table className="rv-table">
        <thead>
          <tr><th>항목</th><th className="col-a">{c.a_san} 후</th><th className="col-b">{c.b_san} 후</th><th className="col-d">차이</th></tr>
        </thead>
        <tbody>
          {c.rows.map((r, i) => (
            <tr key={i}>
              <td>{r.feature}</td>
              <td className="col-a">{r.a}</td>
              <td className="col-b">{r.b}</td>
              <td className="col-d mono" style={{ color: r.delta == null ? 'var(--ink-3)' : r.delta > 0 ? 'var(--good)' : r.delta < 0 ? 'var(--bad)' : 'var(--ink-2)' }}>
                {r.delta == null ? '' : `${r.delta > 0 ? '+' : ''}${Number.isInteger(r.delta) ? r.delta : r.delta.toFixed(1)}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {c.summary && <p className="rv-lead-sub">{c.summary}</p>}
    </div>
  );
}
