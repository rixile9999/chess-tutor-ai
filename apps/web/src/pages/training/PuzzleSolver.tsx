import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Chess, type Square } from 'chess.js';
import type { Key } from 'chessground/types';
import { api } from '../../api/client';
import type { PuzzleOut } from '../../api/types';
import { Board, type BoardShape } from '../../components/Board';
import { MiniBoard } from '../../components/MiniBoard';
import { applyUci, legalDests, sideToMove } from '../../lib/chess';
import { motifLabel, plyLabel } from '../../lib/labels';
import { IconArrow, IconBulb, IconCheck, IconClock, IconSkip } from './icons';
import { SIDE_LABEL, errorText, formatSeconds, useBoardSize } from './util';

type Props = { queue: PuzzleOut[]; onFinished: (id: number) => void };

/** Picks the head of the due queue; keying on the id resets the solver state per puzzle. */
export function PuzzleSolver({ queue, onFinished }: Props) {
  const puzzle = queue[0];
  return <Solver key={puzzle.id} puzzle={puzzle} remaining={queue.length} upcoming={queue.slice(1, 5)} onFinished={onFinished} />;
}

type Step = { uci: string; san: string; fen: string; from: string; to: string };
type Phase = 'solving' | 'wrong' | 'solved' | 'failed';
type Run = {
  fen: string;
  k: number; // number of solution steps applied; solution[k] is the next expected move
  wrong: number;
  phase: Phase;
  busy: boolean; // opponent reply pending
  lastMove: [string, string] | null;
  hint: boolean;
  nonce: number; // bumps to force the board back to `fen` after a wrong move
  view: number | null; // solution step shown on the board while reviewing (failed state)
  finishedAt: number | null;
};
type Action =
  | { type: 'advance'; to: number; lastMove: [string, string]; total: number; fen: string }
  | { type: 'wrong' }
  | { type: 'hint' }
  | { type: 'view'; i: number | null };

function reduce(run: Run, a: Action): Run {
  switch (a.type) {
    case 'advance': {
      const done = a.to >= a.total;
      return {
        ...run, fen: a.fen, k: a.to, lastMove: a.lastMove, hint: false,
        phase: done ? 'solved' : 'solving', busy: !done && a.to % 2 === 1,
        finishedAt: done ? Date.now() : run.finishedAt,
      };
    }
    case 'wrong': {
      const wrong = run.wrong + 1;
      const failed = wrong >= 2;
      return { ...run, wrong, nonce: run.nonce + 1, hint: false, phase: failed ? 'failed' : 'wrong', finishedAt: failed ? Date.now() : run.finishedAt };
    }
    case 'hint': return { ...run, hint: true };
    case 'view': return { ...run, view: a.i };
  }
}

function walk(fen: string, ucis: string[]): Step[] {
  const out: Step[] = [];
  let cur = fen;
  for (const uci of ucis) {
    const r = applyUci(cur, uci);
    if (!r) break;
    out.push({ uci, san: r.san, fen: r.fen, from: uci.slice(0, 2), to: uci.slice(2, 4) });
    cur = r.fen;
  }
  return out;
}

const HINT_SVG = '<circle cx="50" cy="50" r="41" fill="none" stroke="#2478a6" stroke-width="8" opacity="0.9"/>';

function Solver({ puzzle, remaining, upcoming, onFinished }: { puzzle: PuzzleOut; remaining: number; upcoming: PuzzleOut[]; onFinished: (id: number) => void }) {
  const solution = useMemo(() => walk(puzzle.fen, puzzle.solution ?? []), [puzzle]);
  const broken = solution.length === 0 || solution.length !== (puzzle.solution ?? []).length;
  const { ref, size } = useBoardSize();
  const [run, dispatch] = useReducer(reduce, puzzle.fen, (fen): Run => ({
    fen, k: 0, wrong: 0, phase: 'solving', busy: false, lastMove: null, hint: false, nonce: 0, view: null, finishedAt: null,
  }));
  const startedAt = useRef(Date.now());
  const recorded = useRef(false);
  const [elapsed, setElapsed] = useState(0);
  const [shake, setShake] = useState(false);
  const [result, setResult] = useState<PuzzleOut | null>(null);
  const [attemptError, setAttemptError] = useState<string | null>(null);
  const [settled, setSettled] = useState(false);
  const [minWait, setMinWait] = useState(false);

  const active = run.phase === 'solving' || run.phase === 'wrong';
  const solverSide = sideToMove(puzzle.fen);

  // Timer.
  useEffect(() => {
    if (run.finishedAt !== null) { setElapsed(Math.floor((run.finishedAt - startedAt.current) / 1000)); return; }
    const t = setInterval(() => setElapsed(Math.floor((Date.now() - startedAt.current) / 1000)), 500);
    return () => clearInterval(t);
  }, [run.finishedAt]);

  // Opponent reply after a correct move.
  useEffect(() => {
    if (!run.busy) return;
    const step = solution[run.k];
    if (!step) return;
    const t = setTimeout(() => dispatch({ type: 'advance', to: run.k + 1, lastMove: [step.from, step.to], total: solution.length, fen: step.fen }), 380);
    return () => clearTimeout(t);
  }, [run.busy, run.k, solution]);

  // Shake on a wrong try.
  useEffect(() => {
    if (run.wrong === 0) return;
    setShake(true);
    const t = setTimeout(() => setShake(false), 450);
    return () => clearTimeout(t);
  }, [run.wrong]);

  // Record the attempt once the puzzle is solved or failed.
  useEffect(() => {
    if (run.finishedAt === null || recorded.current) return;
    recorded.current = true;
    const seconds = Math.round((run.finishedAt - startedAt.current) / 100) / 10;
    api.training.attempt(puzzle.id, run.phase === 'solved', seconds)
      .then((p) => setResult(p))
      .catch((e) => setAttemptError(errorText(e)))
      .finally(() => setSettled(true));
  }, [run.finishedAt, run.phase, puzzle.id]);

  // Solved: move on after a short pause, once the attempt is recorded.
  useEffect(() => {
    if (run.phase !== 'solved') return;
    const t = setTimeout(() => setMinWait(true), 1600);
    return () => clearTimeout(t);
  }, [run.phase]);
  useEffect(() => {
    if (run.phase === 'solved' && settled && minWait && !attemptError) onFinished(puzzle.id);
  }, [run.phase, settled, minWait, attemptError, onFinished, puzzle.id]);

  const boardFen = run.view !== null ? (solution[run.view]?.fen ?? run.fen) : run.fen;
  const boardLast = run.view !== null ? (solution[run.view] ? [solution[run.view].from, solution[run.view].to] as [string, string] : null) : run.lastMove;

  const movable = useMemo(() => {
    if (!active || run.busy || run.view !== null || broken) return null;
    return { color: sideToMove(run.fen), dests: legalDests(run.fen) };
    // nonce forces a fresh object so the board snaps back after a wrong move.
  }, [active, run.busy, run.view, run.fen, run.nonce, broken]);

  const shapes = useMemo<BoardShape[]>(() => {
    const step = solution[run.k];
    if (!run.hint || !active || !step) return [];
    return [{ orig: step.from as Key, customSvg: { html: HINT_SVG } }];
  }, [run.hint, run.k, active, solution]);

  const onMove = useCallback((orig: string, dest: string) => {
    if (!active || run.busy || run.view !== null) return;
    const expected = solution[run.k];
    if (!expected) return;
    let promo = false;
    try { promo = new Chess(run.fen).get(orig as Square)?.type === 'p' && (dest[1] === '8' || dest[1] === '1'); } catch { /* ignore */ }
    const uci = orig + dest + (promo ? 'q' : '');
    const ok = uci === expected.uci || (promo && expected.uci.slice(0, 4) === orig + dest);
    if (ok) dispatch({ type: 'advance', to: run.k + 1, lastMove: [orig, dest], total: solution.length, fen: expected.fen });
    else dispatch({ type: 'wrong' });
  }, [active, run.busy, run.view, run.k, run.fen, solution]);

  const startPly = useMemo(() => {
    const parts = puzzle.fen.split(' ');
    const moveNo = Number(parts[5]) || 1;
    return (moveNo - 1) * 2 + (parts[1] === 'b' ? 2 : 1);
  }, [puzzle.fen]);

  const sourceLink = puzzle.source_game_id != null
    ? `/review/${puzzle.source_game_id}${puzzle.source_ply != null ? `/${puzzle.source_ply}` : ''}`
    : null;

  const chips = (upto: number, clickable: boolean) => solution.slice(0, upto).map((s, i) => {
    const cls = `tr-mv${i % 2 === 1 ? ' reply' : ''}${run.view === i ? ' active' : ''}${clickable && run.view !== null && i > run.view ? ' dim' : ''}`;
    const label = <><span className="n">{plyLabel(startPly + i)}</span>{s.san}</>;
    return clickable
      ? <button key={i} type="button" className={cls} onClick={() => dispatch({ type: 'view', i: run.view === i ? null : i })}>{label}</button>
      : <span key={i} className={cls}>{label}</span>;
  });

  const title = run.phase === 'solved' ? '정답입니다'
    : run.phase === 'failed' ? '정답 수순을 확인하세요'
    : <><span className={`side ${solverSide}`} />{SIDE_LABEL[solverSide]} 차례입니다</>;
  const subtitle = run.phase === 'solved' ? `${elapsed}초 만에 풀었습니다.`
    : run.phase === 'failed' ? '수순을 누르면 그 국면이 보드에 표시됩니다.'
    : run.busy ? '상대가 응수합니다.'
    : `내 기보에서 나온 국면입니다. 가장 좋은 수를 찾으세요.${solution.length > 1 ? ' 상대의 응수는 자동으로 진행됩니다.' : ''}`;

  return (
    <div className="tr-body">
      <div className="tr-left" ref={ref}>
        <div className={`tr-board${shake ? ' shake' : ''}`}>
          <Board fen={boardFen} orientation={puzzle.orientation ?? solverSide} size={size} movable={movable} onMove={onMove} shapes={shapes} lastMove={boardLast} />
        </div>
        <div className="tr-controls">
          <button type="button" className="btn btn-ghost compact" onClick={() => dispatch({ type: 'hint' })} disabled={!active || run.hint || run.busy || broken}>
            <IconBulb /> 힌트
          </button>
          {run.hint && active && <span className="small muted">움직일 기물의 칸을 표시했습니다</span>}
          <div className="spacer" />
          {run.view !== null && (
            <button type="button" className="btn btn-ghost compact" onClick={() => dispatch({ type: 'view', i: null })}>현재 국면으로</button>
          )}
        </div>
      </div>

      <div className="card tr-panel">
        <div className="tr-panel-head">
          <span className="eyebrow">내 기보 퍼즐</span>
          <span className="small muted">남은 문제 <span className="mono">{remaining}</span>개</span>
          <div className="spacer" />
          <span className={`tr-timer mono${active ? '' : ' done'}`}><IconClock /> {formatSeconds(elapsed)}</span>
        </div>

        <div className="tr-task">
          <div className="tr-task-title">{title}</div>
          <div className="tr-task-sub">{subtitle}</div>
        </div>

        <div className="tr-line">
          {puzzle.motif && <span className="chip">{motifLabel(puzzle.motif)}</span>}
          <span className="chip">복습 {puzzle.reps ?? 0}회</span>
          <span className="chip">간격 {formatDays(puzzle.interval_days)}</span>
          {sourceLink && <Link to={sourceLink}>출처 게임 리뷰 <IconArrow /></Link>}
        </div>

        {broken && (
          <div className="tr-msg note">
            이 문제의 정답 수순을 재생할 수 없습니다. 다음 문제로 넘어갑니다.
            <button type="button" className="btn btn-ghost compact" onClick={() => onFinished(puzzle.id)}><IconSkip /> 건너뛰기</button>
          </div>
        )}

        {run.phase === 'wrong' && (
          <div className="tr-msg bad"><b>다시 생각해 보세요.</b> 기회가 한 번 남았습니다.</div>
        )}
        {run.phase === 'failed' && (
          <div className="tr-msg bad"><b>두 번 틀렸습니다.</b> 정답 수순을 확인하고 다음 문제로 넘어가세요.</div>
        )}
        {run.phase === 'solved' && (
          <div className="tr-msg good">
            <IconCheck />
            <span><b>정답입니다.</b> {elapsed}초{result && ` · 다음 복습은 ${formatDays(result.interval_days)} 후`}{!settled && ' · 기록 중'}</span>
          </div>
        )}
        {attemptError && <div className="tr-msg note">결과를 기록하지 못했습니다: {attemptError}</div>}

        {run.phase === 'failed' ? (
          <div className="tr-section">
            <div className="tr-section-head"><span className="h3">정답 수순</span><span className="small muted">눌러서 보드에 표시</span></div>
            <div className="tr-line">{chips(solution.length, true)}</div>
          </div>
        ) : run.k > 0 ? (
          <div className="tr-section">
            <div className="tr-section-head"><span className="h3">지금까지의 수순</span></div>
            <div className="tr-line">{chips(run.k, false)}</div>
          </div>
        ) : null}

        {!active && (
          <div className="tr-actions">
            <button type="button" className="btn btn-primary" onClick={() => onFinished(puzzle.id)}>
              {remaining > 1 ? '다음 문제' : '오늘의 복습 마치기'} <IconArrow />
            </button>
            {run.phase === 'failed' && result && <span className="small muted">다음 복습은 {formatDays(result.interval_days)} 후입니다</span>}
          </div>
        )}

        {upcoming.length > 0 && (
          <div className="tr-section">
            <div className="tr-section-head"><span className="eyebrow">대기 중인 문제</span><span className="small muted">{remaining - 1}개</span></div>
            <div className="tr-queue">
              {upcoming.map((p) => (
                <div key={p.id} className="tr-queue-item">
                  <MiniBoard fen={p.fen} size={56} orientation={p.orientation ?? sideToMove(p.fen)} />
                  <div className="meta">
                    <b>{p.motif ? motifLabel(p.motif) : '전술 문제'} · {SIDE_LABEL[p.orientation ?? sideToMove(p.fen)]} 차례</b>
                    <span className="muted">복습 {p.reps ?? 0}회 · 간격 {formatDays(p.interval_days)}{p.source_game_id != null ? ` · 게임 #${p.source_game_id}` : ''}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function formatDays(d: number | null | undefined): string {
  if (d == null || !Number.isFinite(d)) return '-';
  if (d < 1) return `${Math.max(1, Math.round(d * 24))}시간`;
  return `${Math.round(d)}일`;
}
