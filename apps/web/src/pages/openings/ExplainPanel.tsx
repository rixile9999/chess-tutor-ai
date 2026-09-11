import { useCallback, useEffect, type ReactNode } from 'react';
import type { MoveAnnotation, OpeningNote, TrapLine } from '../../api/types';
import type { Preview } from '../../lib/shapes';
import { NoteChat } from './NoteChat';
import { NOTE_STAGES, SECTION_TITLE, STAGE_LABEL, sectionPayload, type NoteStage } from '../../api/noteStream';
import { badgeClass, engineBadge, moveBadges } from './colors';
import { moveLabel, type ExplainDepth } from './line';
import { useNoteStream, type NoteRun, type StreamedSection } from './useNote';
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
  /** Its English name: the badge's tooltip, so a transliteration can be checked (M8d-5). */
  nameEn: string | null;
  eco: string | null;
  /** The position the focused move was played in, and the move: the note's own key. */
  fenBefore: string | null;
  san: string | null;
  /** Whose games fill the note's 내 기보 line. */
  username: string | null;
  note: OpeningNote | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  depth: ExplainDepth;
  onDepth: (depth: ExplainDepth) => void;
  /** A note that was just written or changed: the page caches it and reloads its query. */
  onNote: (note: OpeningNote) => void;
  /** Trap step buttons put the line on the board as a preview; `previewKey` is "{trap}:{step}". */
  onTrap: (trap: TrapLine, index: number, step: number) => void;
  previewKey: string | null;
  /** The position after the focused move and the line that reaches it: the chat's context. */
  fenAfter: string | null;
  movesSan: string[];
  startFen: string;
  /** A board the tutor drew, put on the main board as a preview (§10.3). */
  onPreview: (p: Preview | null) => void;
};

/**
 * ③ 의도 해설: the summary from the detectors, the deep note when one exists, and — while one is
 * being written — the stages, the tool calls and every section the moment it is verified (§10.2).
 */
export function ExplainPanel({
  ply, label, annotation, name, nameEn, eco, fenBefore, san, username, note, loading, error, onRetry,
  depth, onDepth, onNote, onTrap, previewKey, fenAfter, movesSan, startFen, onPreview,
}: Props) {
  const { run, start, stop, reset, adopt } = useNoteStream(onNote);
  // The panel shows the run's own note while a run is on screen, so a note the chat changed has to
  // reach that copy too (M8d-5 smoke: 해설에 반영 stored the addendum but the section stayed hidden).
  const noteChanged = useCallback((n: OpeningNote) => { adopt(n); onNote(n); }, [adopt, onNote]);
  const key = fenBefore && san ? `${fenBefore}|${san}` : '';
  // Walking to another move abandons the run: the connection closes, the server stores nothing.
  useEffect(() => { reset(); }, [key, reset]);

  const openAt = (level: ExplainDepth) => ORDER.indexOf(depth) >= ORDER.indexOf(level);
  // The annotation is about this move on this line, so it wins over the stored note's own flag.
  const offBook = annotation ? !annotation.in_book : note ? !note.in_book : false;
  const badges = annotation ? moveBadges(annotation) : [];
  const engine = engineBadge(annotation?.engine ?? null);
  const generate = (regenerate: boolean) => {
    if (fenBefore && san) start({ fen: fenBefore, san, username, regenerate });
  };
  const shown = run?.note ?? note;
  const generating = !!run?.running;

  const head = (
    <div className="op-explain-head">
      <span className="op-explain-label">{label ? <span className="mv">{label}</span> : '시작 국면'}</span>
      {name ? (
        <>
          <span className="badge badge-good op-badge" title={nameEn ?? name}>{name}</span>
          {eco && <span className="mono faint small">{eco}</span>}
        </>
      ) : offBook ? (
        <span className="badge op-badge-warn op-badge">⚠ 책 밖</span>
      ) : null}
      <div className="op-grow" />
      {ply > 0 && generating && (
        <button type="button" className="btn btn-ghost compact" onClick={stop}>중단</button>
      )}
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

  // A run without a stored note yet: the stages and whatever sections have arrived (§10.2).
  if (run && !shown) {
    return (
      <div className="op-explain">
        {head}
        <Stages run={run} />
        <Arrived run={run} ply={ply} onTrap={onTrap} previewKey={previewKey} />
        {run.warnings.map((w, i) => <div key={`w${i}`} className="small muted">{w}</div>)}
        {run.error && <div className="op-error">{run.error}</div>}
        {!run.running && (
          <div className="op-note-done warn">
            <span>{run.aborted ? '중단됨 · 받은 섹션은 저장하지 않았습니다' : '미완성 · 저장하지 않았습니다'}</span>
            <div className="op-grow" />
            <button type="button" className="btn btn-ghost compact" onClick={() => generate(true)}>다시 만들기</button>
          </div>
        )}
      </div>
    );
  }

  if (!shown) {
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
              준비 단계와 섹션이 도착하는 순서대로 보이고, 다 끝나면 저장됩니다.
            </span>
            <button type="button" className="btn btn-primary compact op-explain-genbtn" onClick={() => generate(false)}>
              깊은 해설 만들기
            </button>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="op-explain">
      {head}
      {run && <Stages run={run} />}
      <div className="op-explain-summary"><Marked text={shown.summary} /></div>
      <Badges badges={badges} engine={engine} />
      {shown.engine && <div className="op-explain-engine">{shown.engine}</div>}

      {shown.why.length > 0 && (
        <Section title={offBook ? '왜 책이 아닌가' : '왜 이 수인가'} depth={depth} level="normal" open={openAt('normal')}>
          {shown.why.map((p, i) => <p key={i}><Marked text={p} /></p>)}
        </Section>
      )}
      {shown.replies.length > 0 && (
        <Section title="상대의 응수와 계획" count={`${shown.replies.length}가지`} depth={depth} level="normal" open={openAt('normal')}>
          {shown.replies.map(([s, text], i) => (
            <div className="op-explain-alt" key={`${s}-${i}`}><span className="mv">{s}</span><span><Marked text={text} /></span></div>
          ))}
        </Section>
      )}
      {shown.alternatives.length > 0 && (
        <Section
          title={offBook ? '책 수와 비교' : '대안과 비교'}
          count={`${shown.alternatives.length}가지`}
          depth={depth}
          level="deep"
          open={openAt('deep')}
        >
          {shown.alternatives.map(([s, text], i) => (
            <div className={`op-explain-alt${offBook ? ' good' : ''}`} key={`${s}-${i}`}>
              <span className="mv">{s}</span><span><Marked text={text} /></span>
            </div>
          ))}
        </Section>
      )}
      {shown.traps.length > 0 && (
        <Section title="전형적인 실수와 함정" count={`${shown.traps.length}개`} depth={depth} level="deep" open={openAt('deep')}>
          <Traps traps={shown.traps} ply={ply} onTrap={onTrap} previewKey={previewKey} />
        </Section>
      )}
      {shown.mine && (
        <Section title="내 기보에서" depth={depth} level="deep" open={openAt('deep')}>
          <p>{shown.mine}</p>
        </Section>
      )}
      <Section title="더 깊이" depth={depth} level="deep" open={openAt('deep')}>
        {annotation?.engine && (
          <p className="small muted">
            엔진: 최선 {annotation.engine.best_san} · 승률 손실 {Math.round((annotation.engine.win_loss ?? 0) * 100)}%
            {annotation.engine.pv.length > 0 ? ` · ${annotation.engine.pv.slice(0, 6).join(' ')}` : ''}
          </p>
        )}
        <div className="op-line-ctrl">
          <button type="button" className="btn btn-ghost compact" disabled={generating} onClick={() => generate(true)}>
            {generating ? '다시 쓰는 중…' : '해설 다시 만들기'}
          </button>
        </div>
        <DeeperPanel ply={ply} fen={annotation?.fen_after ?? null} note={note} onTrap={onTrap} previewKey={previewKey} />
      </Section>

      {fenBefore && san && fenAfter && (
        <NoteChat
          fenBefore={fenBefore}
          san={san}
          label={label ?? san}
          fenAfter={fenAfter}
          movesSan={movesSan}
          startFen={startFen}
          openingName={name}
          note={shown}
          previewKey={previewKey}
          onPreview={onPreview}
          onNote={noteChanged}
        />
      )}

      {run?.note && (
        <div className="op-note-done">
          <span>
            저장됨 · {run.seconds.toFixed(1)}초 · 검증 {run.note.verified_claims}/{run.note.total_claims}
            {run.tools.length ? ` · 도구 호출 ${run.tools.length}회` : ''} · 모델 {run.note.model}
          </span>
          <div className="op-grow" />
          <button type="button" className="btn btn-ghost compact" onClick={() => generate(true)}>다시 만들기</button>
        </div>
      )}
      <div className="op-explain-sources">
        <span>근거:</span>
        {(shown.sources.length ? shown.sources : ['book']).map((s) => (
          <span key={s} className="badge badge-neutral op-badge sm">{SOURCE_LABEL[s] ?? s}</span>
        ))}
        <div className="op-grow" />
        <span className="small faint">
          <span className="op-explain-v">문장</span> = 보드에서 확인된 사실 · 나머지는 책·LLM 견해
          {shown.total_claims > 0 ? ` (${shown.verified_claims}/${shown.total_claims})` : ''}
        </span>
      </div>
    </div>
  );
}

/** The checklist: what the server has finished, what it is doing, and the tools the model calls. */
function Stages({ run }: { run: NoteRun }) {
  const done = new Map(run.stages.map((s) => [s.name, s.detail]));
  const running = NOTE_STAGES.find((s) => !done.has(s)) ?? null;
  const writing = running === null;
  const row = (name: NoteStage | 'write', label: string, detail: ReactNode, state: string) => (
    <div key={name} className={`op-note-stage ${state}`}>
      <span className="ic">{state === 'done' ? '✓' : state === 'run' ? '⟳' : '·'}</span>
      <span className="what">{label}</span>
      <span className="detail">{detail}</span>
    </div>
  );
  return (
    <div className="op-note-stages">
      {NOTE_STAGES.map((s) => row(
        s, STAGE_LABEL[s], done.get(s) ?? '',
        done.has(s) ? 'done' : s === running ? 'run' : '',
      ))}
      {row(
        'write',
        run.note ? '작성 완료' : '작성 중',
        run.tools.length > 0 && <span className="op-note-tools">{run.tools.map((t, i) => <span key={i}>{t}</span>)}</span>,
        run.note ? 'done' : writing && run.running ? 'run' : writing ? 'done' : '',
      )}
    </div>
  );
}

/** The sections of a run in the order they arrived, each with its own 검증 count (§10.2). */
function Arrived({ run, ply, onTrap, previewKey }: {
  run: NoteRun; ply: number; onTrap: Props['onTrap']; previewKey: string | null;
}) {
  return (
    <>
      {run.sections.map((s) => <ArrivedSection key={s.name} s={s} ply={ply} onTrap={onTrap} previewKey={previewKey} />)}
    </>
  );
}

function ArrivedSection({ s, ply, onTrap, previewKey }: {
  s: StreamedSection; ply: number; onTrap: Props['onTrap']; previewKey: string | null;
}) {
  const chip = <span className={`badge ${s.verified === s.total && s.total > 0 ? 'badge-good' : 'badge-neutral'} op-badge sm`}>검증 {s.verified}/{s.total}</span>;
  if (s.name === 'summary') {
    return (
      <div className="op-note-arrive">
        <div className="op-explain-summary"><Marked text={sectionPayload('summary', s.payload) ?? ''} /></div>
        <div className="small faint">요약 · 검증 {s.verified}/{s.total}</div>
      </div>
    );
  }
  const body = (): ReactNode => {
    switch (s.name) {
      case 'mine': case 'engine':
        return <p>{sectionPayload(s.name, s.payload)}</p>;
      case 'why':
        return (sectionPayload('why', s.payload) ?? []).map((p, i) => <p key={i}><Marked text={p} /></p>);
      case 'replies': case 'alternatives':
        return (sectionPayload(s.name, s.payload) ?? []).map(([san, text], i) => (
          <div className="op-explain-alt" key={`${san}-${i}`}><span className="mv">{san}</span><span><Marked text={text} /></span></div>
        ));
      case 'traps':
        return <Traps traps={sectionPayload('traps', s.payload) ?? []} ply={ply} onTrap={onTrap} previewKey={previewKey} />;
      default:
        return null;
    }
  };
  return (
    <details className="op-explain-sec op-note-arrive" open>
      <summary>{SECTION_TITLE[s.name]}{s.total > 0 && chip}</summary>
      <div className="op-explain-body">{body()}</div>
    </details>
  );
}

function Traps({ traps, ply, onTrap, previewKey }: {
  traps: TrapLine[]; ply: number; onTrap: Props['onTrap']; previewKey: string | null;
}) {
  return (
    <>
      {traps.map((t, i) => (
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
    </>
  );
}

function Badges({ badges, engine }: { badges: ReturnType<typeof moveBadges>; engine: ReturnType<typeof engineBadge> }) {
  if (!badges.length && !engine) return null;
  return (
    <div className="op-explain-badges">
      {badges.map((b, i) => <span key={i} className={`${badgeClass(b.tone)} op-badge sm`} title={b.title}>{b.text}</span>)}
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
