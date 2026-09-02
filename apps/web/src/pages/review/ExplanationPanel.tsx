import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import type { MotifOut, MoveReviewOut } from '../../api/types';
import { CLASS_TONE, formatScore, plyLabel } from '../../lib/labels';
import { AlternativesSection, ComparisonSection, RefutationSection } from './ExplanationSections';
import { ClassBadge, IconPlay, IconSave, VerifyRow, ratingBand, type Preview } from './shared';

type Props = {
  review: MoveReviewOut; ply: number; rating: number | undefined; boardFen: string;
  preview: Preview | null; onPreview: (p: Preview | null) => void; onSavePuzzle: () => void;
};

export function trainingHref(fen: string, rating: number | undefined): string {
  return `/training?fen=${encodeURIComponent(fen)}${rating ? `&rating=${rating}` : ''}`;
}
export function maiaLabel(rating: number | undefined): string {
  return rating ? ` · Maia ${Math.round(rating / 100) * 100}` : '';
}

/** 이 수의 설명 tab: header, lead, refutation, alternatives, comparison, verify row. */
export function ExplanationPanel({ review, ply, rating, boardFen, preview, onPreview, onSavePuzzle }: Props) {
  const { refutation, human, explanation, comparison } = review;
  const alternatives = review.alternatives ?? [];
  const tone = CLASS_TONE[review.classification] ?? 'neutral';
  const motifs = useMemo(() => {
    const seen = new Set<string>();
    const out: MotifOut[] = [];
    for (const m of [...(refutation?.motifs ?? []), ...(review.motifs ?? [])]) {
      const k = `${m.kind}|${m.attacker}|${(m.targets ?? []).join(',')}`;
      if (!seen.has(k)) { seen.add(k); out.push(m); }
    }
    return out;
  }, [refutation, review.motifs]);
  const lead = explanation?.lead || explanation?.headline || '';
  const extra = (explanation?.sentences ?? []).filter((s) => s && s !== lead && !lead.includes(s));
  const prob = human?.played_prob;

  return (
    <>
      <div className="rv-head" style={{ marginTop: -6 }}>
        <div className="mv rv-head-move">{plyLabel(ply)} {review.san}</div>
        <ClassBadge cls={review.classification} size="lg" />
        <div className="mono rv-evals">{formatScore(review.eval_before)} → <b className={`rv-tone-${tone}`}>{formatScore(review.eval_after)}</b></div>
        <div className="spacer" />
        {human && prob !== null && prob !== undefined && (
          <span className="chip rv-chip-dashed" title={human.natural_reason ?? undefined}>{ratingBand(human.rating)} 구간의 {Math.round(prob * 100)}%가 두는 수</span>
        )}
      </div>
      {lead && <p className="rv-lead">{lead}</p>}
      {extra.length > 0 && <p className="rv-lead-sub">{extra.join(' ')}</p>}
      {human?.natural_reason && <p className="rv-lead-sub"><b>왜 자연스러운가.</b> {human.natural_reason}</p>}

      {refutation && (refutation.main_line?.length > 0 || (refutation.branches ?? []).length > 0 || refutation.note) && (
        <RefutationSection refutation={refutation} motifs={motifs} review={review} ply={ply} preview={preview} onPreview={onPreview} />
      )}
      {alternatives.length > 0 && (
        <AlternativesSection alternatives={alternatives} human={human} review={review} ply={ply} preview={preview} onPreview={onPreview} />
      )}
      {comparison && (comparison.rows ?? []).length > 0 && (
        <ComparisonSection comparison={comparison} preview={preview} onPreview={onPreview} />
      )}
      {!refutation && alternatives.length === 0 && !comparison && (
        <p className="rv-lead-sub">이 수에는 반박 수순이나 대안이 따로 없습니다. 흐름을 따라 다음 수로 넘어가 보세요.</p>
      )}

      <VerifyRow explanation={explanation}>
        <Link className="btn btn-primary" to={trainingHref(boardFen, rating)}><IconPlay /> 이 국면에서 이어 두기{maiaLabel(rating)}</Link>
        <button type="button" className="btn btn-ghost" onClick={onSavePuzzle}><IconSave /> 이 게임에서 퍼즐 만들기</button>
      </VerifyRow>
    </>
  );
}
