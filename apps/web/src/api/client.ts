import type * as T from './types';

const BASE = '/api';

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function request<R>(path: string, init?: RequestInit): Promise<R> {
  const res = await fetch(BASE + path, { headers: { 'content-type': 'application/json' }, ...init });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* ignore */ }
    throw new ApiError(res.status, typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return res.status === 204 ? (undefined as R) : ((await res.json()) as R);
}

const get = <R,>(path: string) => request<R>(path);
const post = <R,>(path: string, body?: unknown) =>
  request<R>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  health: () => get<{ status: string; version: string }>('/health'),

  games: {
    list: (params: { user?: string; limit?: number; offset?: number } = {}) => {
      const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)]));
      return get<T.GameSummary[]>(`/games${q.size ? `?${q}` : ''}`);
    },
    get: (id: number) => get<T.GameDetail>(`/games/${id}`),
    importPgn: (pgn: string, username?: string) => post<T.ImportResult>('/games/import/pgn', { pgn, username }),
    importChesscom: (username: string, months = 3) => post<T.ImportResult>('/games/import/chesscom', { username, months }),
    importLichess: (username: string, max_games = 100) => post<T.ImportResult>('/games/import/lichess', { username, max_games }),
  },

  analysis: {
    start: (gameId: number) => post<T.GameAnalysis>(`/analysis/${gameId}`),
    get: (gameId: number) => get<T.GameAnalysis>(`/analysis/${gameId}`),
  },

  review: {
    move: (gameId: number, ply: number, rating?: number) =>
      get<T.MoveReviewOut>(`/review/${gameId}/${ply}${rating ? `?rating=${rating}` : ''}`),
  },

  profile: {
    report: (username: string, params: { days?: number } = {}) =>
      get<T.ProfileReport>(`/profile/${encodeURIComponent(username)}${params.days ? `?days=${params.days}` : ''}`),
  },

  openings: {
    map: (username: string, color: T.Color, depth = 12) =>
      get<T.OpeningMap>(`/openings/map?username=${encodeURIComponent(username)}&color=${color}&depth=${depth}`),
    heatmap: (username: string, color: T.Color, piece: string, throughMove = 15) =>
      get<T.PieceHeatmap>(`/openings/heatmap?username=${encodeURIComponent(username)}&color=${color}&piece=${piece}&through_move=${throughMove}`),
    breaks: (username: string, color: T.Color, structure?: string) =>
      get<T.BreakTiming[]>(`/openings/breaks?username=${encodeURIComponent(username)}&color=${color}${structure ? `&structure=${structure}` : ''}`),
  },

  training: {
    due: (username?: string) => get<T.PuzzleOut[]>(`/training/puzzles/due${username ? `?username=${encodeURIComponent(username)}` : ''}`),
    generate: (gameId: number) => post<T.PuzzleOut[]>(`/training/puzzles/from-game/${gameId}`),
    attempt: (puzzleId: number, correct: boolean, seconds: number) =>
      post<T.PuzzleOut>(`/training/puzzles/${puzzleId}/attempt`, { correct, seconds }),
  },

  maia: {
    move: (fen: string, rating: number) => post<T.SparringMoveResponse>('/maia/move', { fen, rating }),
  },
};

export type { T };
