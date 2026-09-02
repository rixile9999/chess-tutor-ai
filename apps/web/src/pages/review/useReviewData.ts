import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../../api/client';
import type { GameAnalysis, GameDetail, MoveReviewOut } from '../../api/types';

export function errorText(e: unknown): string {
  if (e instanceof ApiError) return e.status === 404 ? '찾을 수 없습니다 (404)' : `${e.message} (${e.status})`;
  return e instanceof Error ? e.message : '알 수 없는 오류';
}

export function useGame(gameId: number | null) {
  const [game, setGame] = useState<GameDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setGame(null); setError(null);
    if (gameId === null) return;
    let cancelled = false;
    api.games.get(gameId)
      .then((g) => { if (!cancelled) setGame(g); })
      .catch((e) => { if (!cancelled) setError(errorText(e)); });
    return () => { cancelled = true; };
  }, [gameId]);
  return { game, error, loading: gameId !== null && !game && !error };
}

const ACTIVE = new Set(['none', 'pending', 'running']);

/** Loads the engine analysis; starts it when missing and polls every 2 s until it settles. */
export function useAnalysis(gameId: number | null) {
  const [analysis, setAnalysis] = useState<GameAnalysis | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  useEffect(() => {
    setAnalysis(null); setError(null); setWorking(gameId !== null);
    if (gameId === null) return;
    let cancelled = false, timer = 0, started = false;
    const run = async () => {
      try {
        let a: GameAnalysis | null = null;
        try { a = await api.analysis.get(gameId); }
        catch (e) { if (started || !(e instanceof ApiError && e.status === 404)) throw e; }
        if (cancelled) return;
        if ((!a || a.status === 'none') && !started) {
          started = true;
          a = await api.analysis.start(gameId);
          if (cancelled) return;
        }
        if (a) setAnalysis(a);
        if (!a || ACTIVE.has(a.status)) { timer = window.setTimeout(run, 2000); return; }
        setWorking(false);
      } catch (e) {
        if (!cancelled) { setError(errorText(e)); setWorking(false); }
      }
    };
    void run();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [gameId]);
  return { analysis, error, working };
}

/** Per-ply move review, cached for the session. `enabled` gates fetching until the analysis settled. */
export function useMoveReview(gameId: number | null, ply: number, rating: number | undefined, enabled: boolean) {
  const [cache, setCache] = useState<Record<string, MoveReviewOut>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const inflight = useRef(new Set<string>());
  const key = `${gameId}:${ply}`;
  const cached = cache[key];
  const err = errors[key];
  useEffect(() => {
    if (!enabled || gameId === null || ply < 1 || cached || err || inflight.current.has(key)) return;
    inflight.current.add(key);
    api.review.move(gameId, ply, rating)
      .then((r) => setCache((c) => ({ ...c, [key]: r })))
      .catch((e) => setErrors((c) => ({ ...c, [key]: errorText(e) })))
      .finally(() => inflight.current.delete(key));
  }, [enabled, gameId, ply, rating, key, cached, err]);
  const retry = useCallback(() => setErrors((c) => { const n = { ...c }; delete n[key]; return n; }), [key]);
  return { review: cached ?? null, error: err ?? null, loading: enabled && ply >= 1 && !cached && !err, retry };
}
