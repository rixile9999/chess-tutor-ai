import { useCallback, useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../../api/client';
import type { Color, GameAnalysis, GameDetail, Score } from '../../api/types';
import { Board } from '../../components/Board';
import { Controls } from './Controls';
import { EvalBar } from './EvalBar';
import { MoveList } from './MoveList';
import { ReviewPanel, type Tab } from './ReviewPanel';
import { Sparkline } from './Sparkline';
import { arrowShapes, sideLabel, type Preview } from './shared';
import { errorText, useAnalysis, useGame, useMoveReview } from './useReviewData';
import './review.css';

const EMPTY_SHAPES: ReturnType<typeof arrowShapes> = [];

/** Last blunder or mistake by the user, else the final ply. */
function defaultPly(game: GameDetail, analysis: GameAnalysis | null): number {
  const n = game.moves.length;
  if (!analysis || analysis.status !== 'done' || !game.user_color) return n;
  const bad = (analysis.moves ?? []).filter((m) => m.color === game.user_color && (m.classification === 'blunder' || m.classification === 'mistake'));
  return bad.length ? bad[bad.length - 1].ply : n;
}

/** "600" -> "10+0", "180+2" -> "3+2"; anything else is shown as-is. */
function timeControl(tc: string | null): string | null {
  const m = tc?.match(/^(\d+)(?:\+(\d+))?$/);
  return m ? `${Math.round(Number(m[1]) / 60)}+${m[2] ?? 0}` : tc;
}

function TopMeta({ game }: { game: GameDetail }) {
  const opp = game.user_color === 'white' ? game.black_elo : game.user_color === 'black' ? game.white_elo : null;
  const bits = [timeControl(game.time_control), game.played_at?.slice(0, 10), game.user_color ? sideLabel(game.user_color) : `${game.white} vs ${game.black}`, game.result].filter(Boolean);
  return (
    <div className="rv-meta">
      <b>게임 리뷰</b>
      <span>{bits.join(' · ')}</span>
      {opp && <span className="chip">상대 {opp}</span>}
    </div>
  );
}

export default function ReviewPage() {
  const params = useParams();
  const navigate = useNavigate();
  const gameId = params.gameId && /^\d+$/.test(params.gameId) ? Number(params.gameId) : null;
  const plyParam = params.ply && /^\d+$/.test(params.ply) ? Number(params.ply) : null;

  const { game, error: gameError, loading: gameLoading } = useGame(gameId);
  const { analysis, error: analysisError, working } = useAnalysis(gameId);
  const plyCount = game?.moves.length ?? 0;
  const ply = Math.max(0, Math.min(plyCount, plyParam ?? (game ? defaultPly(game, analysis) : 0)));
  const rating = game ? (game.user_color === 'black' ? game.black_elo : game.white_elo) ?? undefined : undefined;
  const { review, loading, error, retry } = useMoveReview(gameId, ply, rating, !!game && !working);

  const [orientation, setOrientation] = useState<Color>('white');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [tab, setTab] = useState<Tab>('move');
  const [toast, setToast] = useState<string | null>(null);
  const [metaEl, setMetaEl] = useState<HTMLElement | null>(null);

  useEffect(() => { setMetaEl(document.getElementById('topbar-meta')); }, []);
  useEffect(() => { if (game) setOrientation(game.user_color ?? 'white'); }, [game]);
  useEffect(() => { setPreview(null); }, [ply, gameId]);
  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 3200);
    return () => window.clearTimeout(t);
  }, [toast]);
  // Once the analysis settled and the URL has no ply, pin the default ply into the URL so it can be shared.
  useEffect(() => {
    if (game && gameId !== null && plyParam === null && !working) navigate(`/review/${gameId}/${defaultPly(game, analysis)}`, { replace: true });
  }, [game, gameId, plyParam, working, analysis, navigate]);

  const goTo = useCallback((p: number) => {
    if (gameId === null) return;
    navigate(`/review/${gameId}/${Math.max(0, Math.min(plyCount, p))}`);
  }, [gameId, plyCount, navigate]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
      if (e.key === 'ArrowLeft') { e.preventDefault(); goTo(ply - 1); }
      else if (e.key === 'ArrowRight') { e.preventDefault(); goTo(ply + 1); }
      else if (e.key === 'Home') { e.preventDefault(); goTo(0); }
      else if (e.key === 'End') { e.preventDefault(); goTo(plyCount); }
      else if (e.key === 'Escape') setPreview(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [goTo, ply, plyCount]);

  const move = game && ply > 0 ? game.moves[ply - 1] ?? null : null;
  const baseFen = game ? (ply === 0 ? game.initial_fen : move?.fen_after ?? game.initial_fen) : '';
  const fen = preview?.fen ?? baseFen;
  const lastMove = useMemo<[string, string] | null>(() => {
    if (preview) return preview.lastMove;
    return move?.uci && move.uci.length >= 4 ? [move.uci.slice(0, 2), move.uci.slice(2, 4)] : null;
  }, [preview, move]);
  const shapes = useMemo(() => {
    if (preview) return preview.shapes;
    if (review && review.ply === ply) return arrowShapes(review.arrows, review.highlights);
    return EMPTY_SHAPES;
  }, [preview, review, ply]);
  const score = useMemo<Score | null>(() => {
    if (analysis?.status === 'done' && analysis.moves?.length) {
      if (ply === 0) return analysis.moves[0].eval_before;
      const m = analysis.moves.find((x) => x.ply === ply);
      if (m) return m.eval_after;
    }
    return review && review.ply === ply ? review.eval_after : null;
  }, [analysis, review, ply]);

  const savePuzzle = useCallback(async () => {
    if (gameId === null) return;
    setToast('퍼즐을 만드는 중');
    try {
      const puzzles = await api.training.generate(gameId);
      setToast(puzzles.length ? `퍼즐 ${puzzles.length}개를 저장했습니다. 훈련 탭에서 풀 수 있습니다.` : '이 게임에서 만들 퍼즐이 없습니다.');
    } catch (e) { setToast(`퍼즐 저장 실패 · ${errorText(e)}`); }
  }, [gameId]);

  if (gameId === null) {
    return (
      <div className="card rv-page-card">
        <h2 className="h3">게임 리뷰</h2>
        <p className="muted" style={{ margin: 0 }}>기보 목록에서 게임을 고르면 수마다 설명과 반박 수순을 볼 수 있습니다.</p>
        <div><Link className="btn btn-primary" to="/games">기보 목록으로</Link></div>
      </div>
    );
  }
  if (gameError) {
    return (
      <div className="card rv-page-card">
        <h2 className="h3">게임을 불러오지 못했습니다</h2>
        <p className="muted" style={{ margin: 0 }}>#{gameId} · {gameError}</p>
        <div><Link className="btn btn-ghost" to="/games">기보 목록으로</Link></div>
      </div>
    );
  }
  if (gameLoading || !game) {
    return (
      <div className="rv-page">
        <div className="rv-left"><div className="rv-skel" style={{ height: 520, width: 544 }} /><div className="rv-skel" style={{ height: 36 }} /><div className="rv-skel" style={{ flex: 1 }} /></div>
        <div className="card rv-panel"><div className="rv-skel" style={{ height: 30, width: 280 }} /><div className="rv-skel" style={{ height: 16, width: '80%' }} /></div>
      </div>
    );
  }

  return (
    <div className="rv-page">
      {metaEl && createPortal(<TopMeta game={game} />, metaEl)}
      <div className="rv-left">
        <div className="rv-board-row">
          <EvalBar score={score} orientation={orientation} />
          <div className="rv-board"><Board fen={fen} orientation={orientation} size={520} shapes={shapes} lastMove={lastMove} /></div>
        </div>
        <Controls ply={ply} plyCount={plyCount} san={move?.san ?? null} fen={fen} preview={preview}
          onGo={goTo} onFlip={() => setOrientation((o) => (o === 'white' ? 'black' : 'white'))} onRestore={() => setPreview(null)} />
        <MoveList game={game} analysis={analysis} ply={ply} onSelect={goTo} />
        <Sparkline game={game} analysis={analysis} review={review && review.ply === ply ? review : null} ply={ply} onSelect={goTo} />
      </div>
      <ReviewPanel game={game} analysis={analysis} analysisWorking={working} analysisError={analysisError}
        ply={ply} review={review && review.ply === ply ? review : null} loading={loading} error={error} retry={retry}
        tab={tab} onTab={setTab} preview={preview} onPreview={setPreview} rating={rating} boardFen={fen} onSavePuzzle={savePuzzle} />
      {toast && <div className="rv-toast" role="status">{toast}</div>}
    </div>
  );
}
