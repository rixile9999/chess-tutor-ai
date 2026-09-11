import { useEffect, useState, type ReactNode } from 'react';
import type { DeeperLine, MasterMove, OpeningNote, TrapLine } from '../../api/types';
import { applyUci, sanToUci } from '../../lib/chess';
import { formatScore, whiteShare } from '../../lib/labels';
import type { QueryState } from './useQuery';
import { firstSentence } from './Journal';
import { moveLabel, type LinePly } from './line';
import { buildStudyPgn } from './pgn';
import { DEEPER_DEPTH, playFromPanel, useDeeperLines, useDeeperMasters } from './useDeeper';

/**
 * Preview keys are shared with the note's own trap lines, which number from 0. A note never has
 * a hundred traps, so starting the engine lines at 100 and the master moves at 200 keeps the
 * three sets apart without touching the page's preview state.
 */
const PREVIEW_LINES = 100;
const PREVIEW_MASTERS = 200;

/** How long a clipboard write may take before the copy box is offered instead (ms). */
const CLIPBOARD_WAIT = 1200;

type Props = {
  /** Ply of the move the panel explains; everything here starts at the position after it. */
  ply: number;
  /** That position, from the annotation; null while it has not arrived (then it is replayed). */
  fen: string | null;
  note: OpeningNote | null;
  /** The page's preview callback — the same one the trap steps use (§9.2). */
  onTrap: (trap: TrapLine, index: number, step: number) => void;
  previewKey: string | null;
};

/** The position after the explained move, replayed from the note when no annotation is in hand. */
function fenAfterNote(note: OpeningNote | null): string | null {
  if (!note) return null;
  const before = `${note.position_key} 0 1`;
  const uci = sanToUci(before, note.san);
  const played = uci ? applyUci(before, uci) : null;
  return played ? played.fen : null;
}

/**
 * ③ 더 깊이 (M8d-4, plan §10.4): the engine's lines and the master statistics for the position
 * the explained move reaches. Both blocks are closed to start with and only fetch while they
 * are open, so the engine never runs unless the user asked to see it.
 */
export function DeeperPanel({ ply, fen, note, onTrap, previewKey }: Props) {
  const [linesOpen, setLinesOpen] = useState(false);
  const [mastersOpen, setMastersOpen] = useState(false);
  const base = fen ?? fenAfterNote(note);
  const linesQ = useDeeperLines(base, linesOpen);
  const mastersQ = useDeeperMasters(base, mastersOpen);
  const lines = linesQ.data?.lines ?? [];
  const masters = mastersQ.data;

  const preview = (title: string, index: number, sans: string[], step: number) =>
    onTrap({ title, line_san: sans, text: '' }, index, step);

  return (
    <div className="op-deep">
      <Block
        title="엔진 라인"
        badge={`깊이 ${DEEPER_DEPTH} · 열 때만 계산`}
        count={lines.length ? `${lines.length}개` : ''}
        open={linesOpen}
        onOpen={setLinesOpen}
      >
        <Loaded query={linesQ} empty="이 국면에서 엔진이 낼 수 있는 수가 없습니다.">
          <div className="op-deep-lines">
            {lines.map((line, i) => (
              <LineRow
                key={line.uci}
                line={line}
                ply={ply}
                index={i}
                previewKey={previewKey}
                onPreview={(step) => preview(`엔진 라인 ${i + 1}`, PREVIEW_LINES + i, line.pv_san, step)}
              />
            ))}
          </div>
          <p className="small faint">
            평가는 백 기준입니다. 라인의 수를 누르면 보드에 미리보기가 뜨고, [이 수 두기]는 첫 수를 실제로 둡니다.
          </p>
        </Loaded>
      </Block>

      <Block
        title="마스터 통계"
        badge="Lichess masters · 캐시"
        count={masters?.moves.length ? `${masters.moves.length}가지` : ''}
        open={mastersOpen}
        onOpen={setMastersOpen}
      >
        <Loaded query={mastersQ} empty="이 국면의 마스터 기보가 없습니다.">
          {masters && !masters.available ? (
            <p className="small muted">{masters.reason ?? '마스터 통계를 가져오지 못했습니다.'}</p>
          ) : masters && masters.moves.length === 0 ? (
            <p className="small muted">이 국면의 마스터 기보가 없습니다.</p>
          ) : (
            <table className="op-deep-masters">
              <thead>
                <tr>
                  <th>수</th><th>판수</th><th>백승 · 무 · 흑승</th><th>평균 레이팅</th><th aria-label="두기" />
                </tr>
              </thead>
              <tbody>
                {(masters?.moves ?? []).map((move, i) => (
                  <MasterRow
                    key={move.uci}
                    move={move}
                    ply={ply}
                    previewKey={previewKey}
                    onPreview={() => preview(`마스터 ${move.san}`, PREVIEW_MASTERS + i, [move.san], 1)}
                    previewOwnKey={`${PREVIEW_MASTERS + i}:1`}
                  />
                ))}
              </tbody>
            </table>
          )}
        </Loaded>
      </Block>
    </div>
  );
}

function LineRow({ line, ply, index, previewKey, onPreview }: {
  line: DeeperLine; ply: number; index: number; previewKey: string | null; onPreview: (step: number) => void;
}) {
  return (
    <div className="op-deep-line">
      <span className="op-deep-ev">
        <span className="mono">{formatScore(line.score)}</span>
        <i><b style={{ width: `${Math.round(whiteShare(line.score) * 100)}%` }} /></i>
      </span>
      <span className="op-explain-steps">
        {line.pv_san.map((san, j) => (
          <button
            key={`${san}-${j}`}
            type="button"
            className={previewKey === `${PREVIEW_LINES + index}:${j + 1}` ? 'on' : ''}
            onClick={() => onPreview(j + 1)}
          >
            {moveLabel(ply + j + 1, san)}
          </button>
        ))}
      </span>
      <button type="button" className="btn btn-ghost compact" onClick={() => playFromPanel(line.uci)}>이 수 두기</button>
    </div>
  );
}

function MasterRow({ move, ply, previewKey, onPreview, previewOwnKey }: {
  move: MasterMove; ply: number; previewKey: string | null; onPreview: () => void; previewOwnKey: string;
}) {
  return (
    <tr>
      <td>
        <span className="op-explain-steps">
          <button type="button" className={previewKey === previewOwnKey ? 'on' : ''} onClick={onPreview}>
            {moveLabel(ply + 1, move.san)}
          </button>
        </span>
      </td>
      <td className="mono">{move.games.toLocaleString()}</td>
      <td>
        <span className="op-deep-wdl-cell">
          <span className="op-deep-wdl">
            <i className="w" style={{ width: `${move.white}%` }} />
            <i className="dr" style={{ width: `${move.draws}%` }} />
            <i className="b" style={{ width: `${move.black}%` }} />
          </span>
          <span className="mono small faint">{move.white}·{move.draws}·{move.black}</span>
        </span>
      </td>
      <td className="mono">{move.avg_rating ?? '—'}</td>
      <td><button type="button" className="btn btn-ghost compact" onClick={() => playFromPanel(move.uci)}>두기</button></td>
    </tr>
  );
}

/** A collapsible block that tells the panel when it is open; nothing is fetched while it is not. */
function Block({ title, badge, count, open, onOpen, children }: {
  title: string; badge: string; count: string; open: boolean; onOpen: (open: boolean) => void; children: ReactNode;
}) {
  return (
    <details className="op-explain-sec op-deep-sec" open={open} onToggle={(e) => onOpen(e.currentTarget.open)}>
      <summary>
        {title}
        <span className="badge badge-neutral op-badge sm">{badge}</span>
        {count && <span className="op-explain-n">{count}</span>}
      </summary>
      <div className="op-explain-body">{children}</div>
    </details>
  );
}

/** Loading / error / empty, the way the rest of the page shows them. */
function Loaded<T>({ query, empty, children }: { query: QueryState<T>; empty: string; children: ReactNode }) {
  if (query.idle) return null;
  if (query.loading) return <div className="op-state compact"><div className="op-spinner" /><div>불러오는 중</div></div>;
  if (query.error) {
    return (
      <div className="op-state compact">
        <div className="op-error">불러오지 못했습니다{query.status ? ` (${query.status})` : ''}: {query.error}</div>
        <button type="button" className="btn btn-ghost compact" onClick={query.reload}>다시 시도</button>
      </div>
    );
  }
  if (!query.data) return <p className="small muted">{empty}</p>;
  return <>{children}</>;
}

// ---------- 일지 내보내기 (plan §10.1 row 6) ----------

/**
 * [PGN 복사] next to the journal's head: the line on the board with every move's one-line
 * explanation as a comment. A browser without the clipboard API (or one that refused) gets the
 * text in a box to copy by hand.
 */
export function JournalExport({ plies, cursor, name, eco, noteSummary }: {
  plies: LinePly[];
  /** Only the moves up to the cursor are on the board, so only those are exported. */
  cursor: number;
  name: string | null;
  eco: string | null;
  /** The stored note's summary for a line prefix, when there is one (index.tsx keeps them). */
  noteSummary: (seq: string[]) => string | null;
}) {
  const [text, setText] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const id = window.setTimeout(() => setCopied(false), 2000);
    return () => window.clearTimeout(id);
  }, [copied]);

  const moves = plies.slice(0, cursor).map((ply, i) => {
    const note = noteSummary(plies.slice(0, i + 1).map((p) => p.san));
    const line = note ?? ply.annotation?.text ?? '';
    return { san: ply.san, text: line ? firstSentence(line) : '' };
  });

  const copy = () => {
    const pgn = buildStudyPgn({ moves, name, eco });
    const write = navigator.clipboard?.writeText(pgn);
    if (!write) { setText(pgn); return; }
    // A refused write rejects, but an unfocused tab queues it and the promise never settles —
    // so the box also opens on a timeout rather than leaving the click without an answer.
    let settled = false;
    const timer = window.setTimeout(() => { if (!settled) setText(pgn); }, CLIPBOARD_WAIT);
    const done = (ok: boolean) => {
      settled = true;
      window.clearTimeout(timer);
      if (ok) setCopied(true); else setText(pgn);
    };
    write.then(() => done(true), () => done(false));
  };

  return (
    <>
      <div className="op-grow" />
      <button type="button" className="btn btn-ghost compact" disabled={moves.length === 0} onClick={copy}>
        {copied ? '복사했습니다' : 'PGN 복사'}
      </button>
      {text !== null && <PgnBox text={text} onClose={() => setText(null)} />}
    </>
  );
}

function PgnBox({ text, onClose }: { text: string; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); onClose(); } };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="op-deep-veil" onClick={onClose} role="presentation">
      <div className="card op-deep-dialog" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="PGN">
        <div className="h3">PGN</div>
        <p className="small muted">클립보드를 쓸 수 없어 직접 복사하도록 열었습니다.</p>
        <textarea className="op-deep-pgn" readOnly value={text} rows={12} autoFocus onFocus={(e) => e.currentTarget.select()} />
        <div className="op-line-ctrl"><button type="button" className="btn btn-ghost compact" onClick={onClose}>닫기</button></div>
      </div>
    </div>
  );
}
