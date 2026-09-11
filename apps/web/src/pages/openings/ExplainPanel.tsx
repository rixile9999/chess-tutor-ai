import type { ReactNode } from 'react';
import type { MoveAnnotation, OpeningNote, TrapLine } from '../../api/types';
import { badgeClass, engineBadge, moveBadges } from './colors';
import { moveLabel, type ExplainDepth } from './line';
import { DeeperPanel } from './DeeperPanel';

const DEPTHS: { id: ExplainDepth; label: string }[] = [
  { id: 'brief', label: '요약' },
  { id: 'normal', label: '보통' },
  { id: 'deep', label: '깊이' },
];
const ORDER: ExplainDepth[] = ['brief', 'normal', 'deep'];
const SOURCE_LABEL: Record<string, string> = {
  book: '책 · lichess TSV', plans: '구조·계획 KB', engine: '엔진', maia: '마이아', llm: 'LLM 해설', map: '내 기보',
};

type Props = {
  /** 0 = the start position: there is no move to explain, only the position. */
  ply: number;
  label: string | null;
  annotation: MoveAnnotation | null;
  /** Name of the position the focused move arrives at. */
  name: string | null;
  eco: string | null;
  note: OpeningNote | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  depth: ExplainDepth;
  onDepth: (depth: ExplainDepth) => void;
  generating: boolean;
  genError: string | null;
  onGenerate: (regenerate: boolean) => void;
  /** Trap step buttons put the line on the board as a preview; `previewKey` is "{trap}:{step}". */
  onTrap: (trap: TrapLine, index: number, step: number) => void;
  previewKey: string | null;
};

/** ③ 의도 해설: the summary from the detectors, the deep note when one exists, depth toggle (§9.1). */
export function ExplainPanel({
  ply, label, annotation, name, eco, note, loading, error, onRetry, depth, onDepth,
  generating, genError, onGenerate, onTrap, previewKey,
}: Props) {
  const openAt = (level: ExplainDepth) => ORDER.indexOf(depth) >= ORDER.indexOf(level);
  // The annotation is about this move on this line, so it wins over the stored note's own flag.
  const offBook = annotation ? !annotation.in_book : note ? !note.in_book : false;
  const badges = annotation ? moveBadges(annotation) : [];
  const engine = engineBadge(annotation?.engine ?? null);

  const head = (
    <div className="op-explain-head">
      <span className="op-explain-label">{label ? <span className="mv">{label}</span> : '시작 국면'}</span>
      {name ? (
        <>
          <span className="badge badge-good op-badge">{name}</span>
          {eco && <span className="mono faint small">{eco}</span>}
        </>
      ) : offBook ? (
        <span className="badge op-badge-warn op-badge">⚠ 책 밖</span>
      ) : null}
      <div className="op-grow" />
      <div className="op-seg op-explain-depth" role="group" aria-label="해설 깊이">
        {DEPTHS.map((d) => (
          <button key={d.id} type="button" className={depth === d.id ? 'on' : ''} onClick={() => onDepth(d.id)}>{d.label}</button>
        ))}
      </div>
    </div>
  );

  if (loading) {
    return <div className="op-explain">{head}<div className="op-state compact"><div className="op-spinner" /><div>해설을 불러오는 중</div></div></div>;
  }
  if (error) {
    return (
      <div className="op-explain">
        {head}
        <div className="op-state compact">
          <div className="op-error">해설을 불러오지 못했습니다: {error}</div>
          <button type="button" className="btn btn-ghost compact" onClick={onRetry}>다시 시도</button>
        </div>
      </div>
    );
  }

  if (!note) {
    // No stored note: the deterministic facts now, the deep note on request (§9.2).
    return (
      <div className="op-explain">
        {head}
        <div className="op-explain-summary">
          {annotation ? annotation.text : ply ? '이 수의 해설을 아직 만들지 않았습니다.' : '아직 둔 수가 없습니다. 보드에서 한 수 두거나 아래 후보를 누르면 해설이 붙습니다.'}
        </div>
        <Badges badges={badges} engine={engine} />
        {annotation && !annotation.in_book && annotation.book_alternatives.length > 0 && (
          <div className="op-explain-alts small">
            책 수: {annotation.book_alternatives.map((c) => `${c.label}${c.name ? ` (${c.name})` : ''}`).join(' · ')}
          </div>
        )}
        {annotation?.engine?.reason && <div className="op-explain-engine">{annotation.engine.reason}</div>}
        {ply > 0 && (
          <div className="op-explain-gen">
            <span>
              위 문장은 보드에서 바로 뽑은 사실입니다. 깊은 해설(왜 이 수인가, 대안 비교, 상대의 계획, 함정, 내 기보)은
              처음 볼 때 한 번 만들고 저장해 둡니다.
            </span>
            {genError && <span className="op-error">{genError}</span>}
            <button type="button" className="btn btn-primary compact op-explain-genbtn" disabled={generating} onClick={() => onGenerate(false)}>
              {generating ? '책 수·엔진 라인·구조 계획을 확인하며 쓰는 중…' : '깊은 해설 만들기 · 10초 안팎'}
            </button>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="op-explain">
      {head}
      <div className="op-explain-summary"><Marked text={note.summary} /></div>
      <Badges badges={badges} engine={engine} />
      {note.engine && <div className="op-explain-engine">{note.engine}</div>}

      {note.why.length > 0 && (
        <Section title={offBook ? '왜 책이 아닌가' : '왜 이 수인가'} depth={depth} level="normal" open={openAt('normal')}>
          {note.why.map((p, i) => <p key={i}><Marked text={p} /></p>)}
        </Section>
      )}
      {note.replies.length > 0 && (
        <Section title="상대의 응수와 계획" count={`${note.replies.length}가지`} depth={depth} level="normal" open={openAt('normal')}>
          {note.replies.map(([san, text], i) => (
            <div className="op-explain-alt" key={`${san}-${i}`}><span className="mv">{san}</span><span><Marked text={text} /></span></div>
          ))}
        </Section>
      )}
      {note.alternatives.length > 0 && (
        <Section
          title={offBook ? '책 수와 비교' : '대안과 비교'}
          count={`${note.alternatives.length}가지`}
          depth={depth}
          level="deep"
          open={openAt('deep')}
        >
          {note.alternatives.map(([san, text], i) => (
            <div className={`op-explain-alt${offBook ? ' good' : ''}`} key={`${san}-${i}`}>
              <span className="mv">{san}</span><span><Marked text={text} /></span>
            </div>
          ))}
        </Section>
      )}
      {note.traps.length > 0 && (
        <Section title="전형적인 실수와 함정" count={`${note.traps.length}개`} depth={depth} level="deep" open={openAt('deep')}>
          {note.traps.map((t, i) => (
            <div className="op-explain-trap" key={`${t.title}-${i}`}>
              <span className="op-explain-trap-title">{t.title}</span>
              <span className="op-explain-steps">
                {t.line_san.map((san, j) => (
                  <button
                    key={`${san}-${j}`}
                    type="button"
                    className={previewKey === `${i}:${j + 1}` ? 'on' : ''}
                    onClick={() => onTrap(t, i, j + 1)}
                  >
                    {moveLabel(ply + j + 1, san)}
                  </button>
                ))}
              </span>
              <span className="small"><Marked text={t.text} /> <span className="faint">수를 누르면 보드에 미리보기</span></span>
            </div>
          ))}
        </Section>
      )}
      {note.mine && (
        <Section title="내 기보에서" depth={depth} level="deep" open={openAt('deep')}>
          <p>{note.mine}</p>
        </Section>
      )}
      <Section title="더 깊이" depth={depth} level="deep" open={openAt('deep')}>
        {annotation?.engine && (
          <p className="small muted">
            엔진: 최선 {annotation.engine.best_san} · 승률 손실 {Math.round((annotation.engine.win_loss ?? 0) * 100)}%
            {annotation.engine.pv.length > 0 ? ` · ${annotation.engine.pv.slice(0, 6).join(' ')}` : ''}
          </p>
        )}
        {genError && <p className="op-error">{genError}</p>}
        <div className="op-line-ctrl">
          <button type="button" className="btn btn-ghost compact" disabled={generating} onClick={() => onGenerate(true)}>
            {generating ? '다시 쓰는 중…' : '해설 다시 만들기'}
          </button>
        </div>
        <DeeperPanel ply={ply} fen={annotation?.fen_after ?? null} note={note} onTrap={onTrap} previewKey={previewKey} />
      </Section>

      <div className="op-explain-sources">
        <span>근거:</span>
        {(note.sources.length ? note.sources : ['book']).map((s) => (
          <span key={s} className="badge badge-neutral op-badge sm">{SOURCE_LABEL[s] ?? s}</span>
        ))}
        <div className="op-grow" />
        <span className="small faint">
          <span className="op-explain-v">문장</span> = 보드에서 확인된 사실 · 나머지는 책·LLM 견해
          {note.total_claims > 0 ? ` (${note.verified_claims}/${note.total_claims})` : ''}
        </span>
      </div>
    </div>
  );
}

function Badges({ badges, engine }: { badges: ReturnType<typeof moveBadges>; engine: ReturnType<typeof engineBadge> }) {
  if (!badges.length && !engine) return null;
  return (
    <div className="op-explain-badges">
      {badges.map((b, i) => <span key={i} className={`${badgeClass(b.tone)} op-badge sm`}>{b.text}</span>)}
      {engine && <span className={`${badgeClass(engine.tone)} op-badge sm`}>{engine.text}</span>}
    </div>
  );
}

/** `key` on the depth makes a depth change reset the sections; a manual toggle stays until then. */
function Section({ title, count, depth, level, open, children }: {
  title: string; count?: string; depth: ExplainDepth; level: ExplainDepth; open: boolean; children: ReactNode;
}) {
  return (
    <details className="op-explain-sec" key={`${depth}-${level}-${title}`} open={open}>
      <summary>{title}{count && <span className="op-explain-n">{count}</span>}</summary>
      <div className="op-explain-body">{children}</div>
    </details>
  );
}

/** `[[…]]` marks a sentence the verifier confirmed on the board; it is drawn with a dotted underline. */
function Marked({ text }: { text: string }): ReactNode {
  const parts = text.split(/(\[\[.+?\]\])/g);
  return (
    <>
      {parts.map((p, i) => (p.startsWith('[[') && p.endsWith(']]')
        ? <span key={i} className="op-explain-v">{p.slice(2, -2)}</span>
        : <span key={i}>{p}</span>))}
    </>
  );
}
