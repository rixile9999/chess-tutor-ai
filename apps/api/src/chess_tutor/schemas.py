"""API contract shared by every backend module and the web client.

Rules: fields are added, never renamed. Layer 2/3 facts are plain data; prose lives only in
Explanation.sentences and the short *note*/*why*/*summary* strings, and every board fact a
sentence relies on is also present as a Claim so the verifier can check it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from chess_tutor.verify import Claim

Color = Literal["white", "black"]
Classification = Literal["book", "best", "good", "inaccuracy", "mistake", "blunder", "forced"]
AnalysisStatus = Literal["none", "pending", "running", "done", "failed"]


# ---------- games ----------


class MoveInfo(BaseModel):
    ply: int
    san: str
    uci: str
    fen_after: str
    clock: float | None = None
    """Seconds left on the mover's clock after the move, when the PGN has %clk."""


class GameSummary(BaseModel):
    id: int
    source: str
    source_id: str | None = None
    white: str
    black: str
    white_elo: int | None = None
    black_elo: int | None = None
    result: str
    time_control: str | None = None
    played_at: datetime | None = None
    eco: str | None = None
    opening_name: str | None = None
    user_color: Color | None = None
    ply_count: int = 0
    analysis_status: AnalysisStatus = "none"


class GameDetail(GameSummary):
    pgn: str
    initial_fen: str
    moves: list[MoveInfo]


class ImportPGNRequest(BaseModel):
    pgn: str
    username: str | None = None
    """When given, user_color is set on games where this name plays."""


class ImportChesscomRequest(BaseModel):
    username: str
    months: int = Field(default=3, ge=1, le=24)


class ImportLichessRequest(BaseModel):
    username: str
    max_games: int = Field(default=100, ge=1, le=2000)


class ImportResult(BaseModel):
    imported: int
    skipped: int
    game_ids: list[int]
    user_id: int | None = None


# ---------- engine analysis ----------


class Score(BaseModel):
    cp: int | None = None
    mate: int | None = None
    """Both from White's point of view."""

    def as_pawns(self) -> float:
        if self.mate is not None:
            return 100.0 if self.mate > 0 else -100.0
        return (self.cp or 0) / 100.0


class EngineLine(BaseModel):
    rank: int
    score: Score
    pv: list[str]
    """SAN moves."""
    pv_uci: list[str]


class MoveAnalysis(BaseModel):
    ply: int
    san: str
    uci: str
    color: Color
    fen_before: str
    fen_after: str
    eval_before: Score
    eval_after: Score
    best_move_san: str | None = None
    best_move_uci: str | None = None
    classification: Classification
    win_prob_loss: float = 0.0
    """Drop in the mover's win probability caused by the move (0..1)."""
    lines: list[EngineLine] = []
    """Top engine lines in the position before the move."""
    clock: float | None = None


class AnalysisSummary(BaseModel):
    accuracy_white: float = 0.0
    accuracy_black: float = 0.0
    counts: dict[str, dict[str, int]] = {}
    """counts[color][classification]."""
    eval_series: list[float] = []
    """Evaluation in pawns after each ply, clamped to [-10, 10]; index 0 = start position."""


class GameAnalysis(BaseModel):
    game_id: int
    status: AnalysisStatus
    engine: str = "stockfish"
    depth: int = 0
    error: str | None = None
    summary: AnalysisSummary = AnalysisSummary()
    moves: list[MoveAnalysis] = []


# ---------- review (layers 2-4) ----------


class MotifOut(BaseModel):
    kind: str
    mover: str
    attacker: str
    targets: list[str]
    with_check: bool
    safe: bool


class Branch(BaseModel):
    moves: list[str]
    """SAN, starting with the opponent's reply."""
    result: str
    """Short Korean label, e.g. '퀸 상실'."""
    eval: Score


class Refutation(BaseModel):
    main_line: list[str]
    """SAN, starting with the punishing move."""
    branches: list[Branch] = []
    motifs: list[MotifOut] = []
    note: str | None = None
    """One sentence on why a plausible alternative punishment fails, if any."""


class Alternative(BaseModel):
    san: str
    eval: Score
    line: list[str]
    is_best: bool = False
    why: str = ""


class FeatureDiffRow(BaseModel):
    feature: str
    """Korean label: 폰 구조, 기물 활동, 킹 안전, 공간, 통과폰, 열린 파일 ..."""
    a: str
    b: str
    delta: float | None = None
    """Positive favours the mover in a."""


class Comparison(BaseModel):
    a_san: str
    b_san: str
    divergence_ply: int | None = None
    divergence_fen: str | None = None
    rows: list[FeatureDiffRow] = []
    summary: str = ""


class HumanView(BaseModel):
    rating: int
    move_probs: dict[str, float] = {}
    """SAN -> probability for the top moves at this rating."""
    played_prob: float | None = None
    natural_reason: str | None = None
    computer_move: bool = False
    """True when the engine's best move is one humans at this rating essentially never find."""


class Explanation(BaseModel):
    headline: str
    lead: str
    sentences: list[str] = []
    claims: list[Claim] = []
    verified: bool = False
    verified_claims: int = 0
    total_claims: int = 0
    source: Literal["llm", "template"] = "template"


class StructureInfo(BaseModel):
    key: str
    name: str
    confidence: float
    defining_pawns: list[str] = []
    side: Color | Literal["both"] | None = None


class StructureSpan(BaseModel):
    key: str
    name: str
    from_ply: int
    to_ply: int


class Plan(BaseModel):
    title: str
    side: Color
    condition: str
    status: Literal["pv_match", "later", "executed", "unavailable"] = "later"
    moves_hint: list[str] = []


class YourMove(BaseModel):
    san: str
    classification: Classification
    plan_match: bool
    note: str


class Counterfactual(BaseModel):
    question: str
    line: list[str]
    verdict: str
    eval: Score


class StrategyView(BaseModel):
    structure: StructureInfo | None = None
    timeline: list[StructureSpan] = []
    plans: list[Plan] = []
    your_move: YourMove | None = None
    counterfactual: Counterfactual | None = None
    features: list[FeatureDiffRow] = []
    record: dict[str, float | int | None] = {}
    """Personal record in this structure: games, win_rate, avg_break_move ..."""


class Arrow(BaseModel):
    orig: str
    dest: str
    color: Literal["good", "bad", "ink"] = "ink"
    dashed: bool = False


class MoveReviewOut(BaseModel):
    game_id: int
    ply: int
    san: str
    color: Color
    fen_before: str
    fen_after: str
    classification: Classification
    eval_before: Score
    eval_after: Score
    refutation: Refutation | None = None
    alternatives: list[Alternative] = []
    comparison: Comparison | None = None
    human: HumanView | None = None
    explanation: Explanation
    strategy: StrategyView | None = None
    arrows: list[Arrow] = []
    highlights: list[str] = []
    motifs: list[MotifOut] = []


# ---------- profile ----------


class PhaseAccuracy(BaseModel):
    opening: float
    middlegame: float
    endgame: float
    delta_opening: float | None = None
    delta_middlegame: float | None = None
    delta_endgame: float | None = None


class StructureStat(BaseModel):
    key: str
    name: str
    games: int
    win_rate: float
    avg_loss_cp: float


class MotifMiss(BaseModel):
    kind: str
    count: int


class TimeStats(BaseModel):
    blunder_rate_under_30s: float
    blunder_rate_over_30s: float
    baseline: float = 0.09
    moves_under_30s: int = 0


class TrainingSummary(BaseModel):
    due_puzzles: int
    motif_sets: list[MotifMiss] = []
    studies: list[str] = []


class RepertoireHole(BaseModel):
    label: str
    games: int
    deviation_rate: float
    avg_loss_cp: float
    win_rate: float


class ProfileReport(BaseModel):
    username: str
    platform: str
    rating_rapid: int | None = None
    rating_blitz: int | None = None
    window_from: datetime | None = None
    window_to: datetime | None = None
    games: int = 0
    analyzed_games: int = 0
    summary_text: str = ""
    phase_accuracy: PhaseAccuracy | None = None
    structures: list[StructureStat] = []
    motifs_missed: list[MotifMiss] = []
    time: TimeStats | None = None
    training: TrainingSummary | None = None
    repertoire_holes: list[RepertoireHole] = []


# ---------- openings ----------


class OpeningNode(BaseModel):
    id: str
    """Position key (FEN without move counters)."""
    label: str
    san: str | None = None
    fen: str
    depth: int
    games: int
    wins: int
    draws: int
    losses: int
    score: float
    name: str | None = None
    eco: str | None = None
    is_tabiya: bool = False
    is_deviation: bool = False
    master_only: bool = False


class OpeningEdge(BaseModel):
    source: str
    target: str
    san: str
    games: int
    score: float
    master_only: bool = False


class OpeningMap(BaseModel):
    color: Color
    root: str
    nodes: list[OpeningNode]
    edges: list[OpeningEdge]
    total_games: int


class PieceHeatmap(BaseModel):
    piece: str
    """e.g. 'black bishop f8'."""
    squares: dict[str, float]
    games: int
    through_move: int


class BreakTiming(BaseModel):
    label: str
    side: Color
    histogram: list[int]
    """Counts for move numbers from_move..to_move inclusive."""
    from_move: int = 10
    to_move: int = 30
    my_avg: float | None = None
    master_median: float | None = None


# ---------- training ----------


class PuzzleOut(BaseModel):
    id: int
    fen: str
    orientation: Color
    solution: list[str]
    """UCI, solver's move first."""
    motif: str | None = None
    source_game_id: int | None = None
    source_ply: int | None = None
    due_at: datetime
    interval_days: float
    reps: int


class PuzzleAttemptIn(BaseModel):
    correct: bool
    seconds: float = 0.0


class SparringMoveRequest(BaseModel):
    fen: str
    rating: int = 1500


class SparringMoveResponse(BaseModel):
    san: str
    uci: str
    probs: dict[str, float] = {}
    source: Literal["maia", "engine", "random"]
