import type { Color, OpeningNode } from '../../api/types';
import { MiniBoard } from '../../components/MiniBoard';
import { pct, plainLabel, scoreTone } from './colors';
import { currentStep, type Tree } from './model';

const BOARD = 320;

type Props = {
  tree: Tree;
  node: OpeningNode;
  /** Set while the pointer is over a column cell; the panel shows that position instead. */
  preview: OpeningNode | null;
  color: Color;
};

/** The big board on the left of the explorer, plus the record of the position it shows. */
export function FocusBoard({ tree, node, preview, color }: Props) {
  const shown = preview ?? node;
  const step = currentStep(tree, shown);
  const rest = step && !tree.important.has(shown.id)
    ? step.chain.length - 1 - step.chain.findIndex((c) => c.id === shown.id)
    : 0;
  const merges = tree.others.get(shown.id) ?? 0;

  return (
    <div className="op-focus">
      <div className="op-focus-board">
        <MiniBoard fen={shown.fen} size={BOARD} orientation={color} />
        {preview && <span className="op-preview-chip">미리보기</span>}
      </div>
      <div className="op-focus-line">
        <span className="mv">{plainLabel(shown.label) || shown.san || '시작 국면'}</span>
        {shown.is_tabiya && <span className="badge badge-neutral op-badge">타비야</span>}
        {shown.is_deviation && <span className="badge badge-bad op-badge">책 이탈</span>}
        {merges >= 1 && <span className="badge badge-neutral op-badge">합류 {merges + 1}경로</span>}
        {shown.master_only && <span className="badge badge-neutral op-badge dashed">마스터 DB 전용</span>}
      </div>
      {(shown.name || shown.eco) && (
        <div className="op-focus-line">
          {shown.name && <span className="muted">{shown.name}</span>}
          {shown.eco && <span className="mono faint">{shown.eco}</span>}
        </div>
      )}
      <div className="op-focus-line">
        {shown.master_only ? (
          <span className="muted">마스터 DB 전용 · 내 기보 없음</span>
        ) : (
          <>
            <span className="mono">
              {shown.games ?? 0}판 · 승 {shown.wins ?? 0} 무 {shown.draws ?? 0} 패 {shown.losses ?? 0}
            </span>
            <span className={`badge ${scoreTone(shown.score)} op-badge`}>승률 {pct(shown.score)}</span>
          </>
        )}
      </div>
      {rest > 0 && (
        <div className="small muted">이 국면은 외길 위에 있습니다. 다음 갈림길까지 {rest}수</div>
      )}
    </div>
  );
}
