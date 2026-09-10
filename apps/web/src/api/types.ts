// Mirrors apps/api/src/chess_tutor/schemas.py. Fields are added, never renamed.

export type Color = 'white' | 'black';
export type Classification = 'book' | 'best' | 'good' | 'inaccuracy' | 'mistake' | 'blunder' | 'forced';
export type AnalysisStatus = 'none' | 'pending' | 'running' | 'done' | 'failed';

export interface MoveInfo { ply: number; san: string; uci: string; fen_after: string; clock: number | null }
export interface GameSummary {
  id: number; source: string; source_id: string | null; white: string; black: string;
  white_elo: number | null; black_elo: number | null; result: string; time_control: string | null;
  played_at: string | null; eco: string | null; opening_name: string | null; user_color: Color | null;
  ply_count: number; analysis_status: AnalysisStatus;
}
export interface GameDetail extends GameSummary { pgn: string; initial_fen: string; moves: MoveInfo[] }
/** `errors` holds one Korean sentence per game that was skipped because it could not be read; those games are counted in `skipped` too. */
export interface ImportResult { imported: number; skipped: number; game_ids: number[]; user_id: number | null; errors: string[] }

export interface Score { cp: number | null; mate: number | null }
export interface EngineLine { rank: number; score: Score; pv: string[]; pv_uci: string[] }
export interface MoveAnalysis {
  ply: number; san: string; uci: string; color: Color; fen_before: string; fen_after: string;
  eval_before: Score; eval_after: Score; best_move_san: string | null; best_move_uci: string | null;
  classification: Classification; win_prob_loss: number; lines: EngineLine[]; clock: number | null;
}
export interface AnalysisSummary {
  accuracy_white: number; accuracy_black: number;
  counts: Record<string, Record<string, number>>; eval_series: number[];
}
export interface GameAnalysis {
  game_id: number; status: AnalysisStatus; engine: string; depth: number; error: string | null;
  summary: AnalysisSummary; moves: MoveAnalysis[];
}

export interface MotifOut { kind: string; mover: string; attacker: string; targets: string[]; with_check: boolean; safe: boolean }
export interface Branch { moves: string[]; result: string; eval: Score }
export interface Refutation { main_line: string[]; branches: Branch[]; motifs: MotifOut[]; note: string | null }
export interface Alternative { san: string; eval: Score; line: string[]; is_best: boolean; why: string }
export interface FeatureDiffRow { feature: string; a: string; b: string; delta: number | null }
export interface Comparison {
  a_san: string; b_san: string; divergence_ply: number | null; divergence_fen: string | null;
  rows: FeatureDiffRow[]; summary: string;
}
export interface HumanView {
  rating: number; move_probs: Record<string, number>; played_prob: number | null;
  natural_reason: string | null; computer_move: boolean;
}
export interface Claim { kind: string; fen: string; subject: string | null; object: string | null }
export interface Explanation {
  headline: string; lead: string; sentences: string[]; claims: Claim[]; verified: boolean;
  verified_claims: number; total_claims: number; source: 'llm' | 'template';
}
export interface StructureInfo { key: string; name: string; confidence: number; defining_pawns: string[]; side: Color | 'both' | null }
export interface StructureSpan { key: string; name: string; from_ply: number; to_ply: number }
export interface Plan { title: string; side: Color; condition: string; status: 'pv_match' | 'later' | 'executed' | 'unavailable'; moves_hint: string[] }
export interface YourMove { san: string; classification: Classification; plan_match: boolean; note: string }
export interface Counterfactual { question: string; line: string[]; verdict: string; eval: Score }
export interface StrategyView {
  structure: StructureInfo | null; timeline: StructureSpan[]; plans: Plan[]; your_move: YourMove | null;
  counterfactual: Counterfactual | null; features: FeatureDiffRow[]; record: Record<string, number | null>;
}
export interface Arrow { orig: string; dest: string; color: 'good' | 'bad' | 'ink'; dashed: boolean }
export interface MoveReviewOut {
  game_id: number; ply: number; san: string; color: Color; fen_before: string; fen_after: string;
  classification: Classification; eval_before: Score; eval_after: Score;
  refutation: Refutation | null; alternatives: Alternative[]; comparison: Comparison | null;
  human: HumanView | null; explanation: Explanation; strategy: StrategyView | null;
  arrows: Arrow[]; highlights: string[]; motifs: MotifOut[];
}

export interface PhaseAccuracy {
  opening: number; middlegame: number; endgame: number;
  delta_opening: number | null; delta_middlegame: number | null; delta_endgame: number | null;
  opening_moves?: number; middlegame_moves?: number; endgame_moves?: number; baseline_band?: string;
}
export interface StructureStat { key: string; name: string; games: number; win_rate: number; avg_loss_cp: number }
export interface MotifMiss { kind: string; count: number }
export interface TimeStats { blunder_rate_under_30s: number; blunder_rate_over_30s: number; baseline: number; moves_under_30s: number }
export interface TrainingSummary { due_puzzles: number; motif_sets: MotifMiss[]; studies: string[] }
export interface RepertoireHole { label: string; games: number; deviation_rate: number; avg_loss_cp: number; win_rate: number }
export interface ProfileReport {
  username: string; platform: string; rating_rapid: number | null; rating_blitz: number | null;
  window_from: string | null; window_to: string | null; games: number; analyzed_games: number;
  summary_text: string; phase_accuracy: PhaseAccuracy | null; structures: StructureStat[];
  motifs_missed: MotifMiss[]; time: TimeStats | null; training: TrainingSummary | null;
  repertoire_holes: RepertoireHole[];
}

export interface OpeningNode {
  id: string; label: string; san: string | null; fen: string; depth: number; games: number;
  wins: number; draws: number; losses: number; score: number; name: string | null; eco: string | null;
  is_tabiya: boolean; is_deviation: boolean; master_only: boolean;
}
export interface OpeningEdge { source: string; target: string; san: string; games: number; score: number; master_only: boolean }
export interface OpeningMap { color: Color; root: string; nodes: OpeningNode[]; edges: OpeningEdge[]; total_games: number }
export interface PieceHeatmap { piece: string; squares: Record<string, number>; games: number; through_move: number }
export interface BreakTiming {
  label: string; side: Color; histogram: number[]; from_move: number; to_move: number;
  my_avg: number | null; master_median: number | null;
}

export interface PuzzleOut {
  id: number; fen: string; orientation: Color; solution: string[]; motif: string | null;
  source_game_id: number | null; source_ply: number | null; due_at: string; interval_days: number; reps: number;
}
export interface SparringMoveResponse { san: string; uci: string; probs: Record<string, number>; source: 'maia' | 'engine' | 'random' }

// ---------- play (M7 practice games) ----------
export type OpponentKind = 'maia' | 'stockfish';
export type PlaySource = 'maia' | 'engine' | 'random' | 'stockfish' | 'book';
export type HintLevel = 1 | 2 | 3;
export type PracticeMode = 'free' | 'drill' | 'tabiya';
export type CoachPreset = 'serious' | 'learning' | 'free';
export interface OpponentSpec { kind: OpponentKind; rating: number }
export interface PlayMoveResponse { san: string; uci: string; source: PlaySource; probs: Record<string, number>; think_ms: number }
export interface HintCandidate { san: string; uci: string; prob: number | null; reason: string; claims: Claim[] }
export interface HintBest { san: string; uci: string; pv: string[]; score: Score; reason: string; motifs: MotifOut[]; claims: Claim[]; computer_move: boolean }
export interface PlayHintResponse {
  level: HintLevel; side: Color; structure: StructureInfo; plans: Plan[]; candidates: HintCandidate[]; best: HintBest | null;
  text: string; source: 'maia' | 'engine' | 'random' | null; verified: boolean; verified_claims: number; total_claims: number;
}
export interface PlayCheckResponse {
  san: string; uci: string; classification: Classification; win_loss: number; eval_before: Score; eval_after: Score;
  best_san: string; best_uci: string; pv: string[]; reason: string; claims: Claim[]; verified: boolean; computer_move: boolean;
  alternative_san: string | null; alternative_reason: string;
}
export interface CoachStats { preset: CoachPreset; hints: number; takebacks: number; alerts: number }
export interface PracticeGameIn {
  username: string; user_color: Color | null; start_fen: string; moves_san: string[]; result: '1-0' | '0-1' | '1/2-1/2' | '*';
  termination: string | null; opponent: OpponentSpec | null; coach: CoachStats; opening_id: string | null; practice_mode: PracticeMode;
  clocks: number[] | null; time_control: string | null; analyse: boolean;
}
export interface PracticeGameOut { game_id: number; analysis_status: AnalysisStatus; pgn: string }
export interface OpeningRecord { games: number; score: number | null; practice_games: number; practice_score: number | null }
export interface OpeningCard {
  id: string; family: string; family_label: string; name: string; name_en: string; eco: string; line_san: string[]; tabiya_fen: string;
  structure: StructureInfo; sides: Color[]; record: OpeningRecord | null;
}
export interface OpeningDetail extends OpeningCard { plans_white: Plan[]; plans_black: Plan[]; fens: string[] }
export interface BookMove { san: string; uci: string; eco: string; name: string }
export interface BookMoves { fen: string; opening: BookMove | null; moves: BookMove[] }
export interface PlanReport {
  game_id: number; side: Color; structure: StructureInfo | null; executed: Plan[]; pv_match: Plan[]; later: Plan[]; unavailable: Plan[];
  breaks: string[]; opening_id: string | null; opening_name: string | null; practice_mode: PracticeMode | null; summary: string;
}
