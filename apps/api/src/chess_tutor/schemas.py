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
    errors: list[str] = []
    """One Korean sentence per game that was skipped because it could not be read."""


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
    multipv: int = 0
    """MultiPV width the search used. 0 in an analysis stored before this field existed; the
    depth lives on GameAnalysis."""


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
    line: list[str] = []
    """SAN moves after the move that show the threat (e.g. the mating move)."""
    label: str = ""
    """Korean name of the motif kind (motifs.KOREAN_NAMES)."""
    description: str = ""
    """Short Korean phrase built from the squares above (motifs.describe)."""


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
    claims: list[Claim] = []
    """Board facts `why` states, so the verifier can check the sentence it becomes."""


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
    source: Literal["maia", "engine", "random"] | None = None
    """Which backend produced move_probs: Maia-2, the rating-conditioned engine fallback, or
    uniform sampling when neither is available."""
    claims: list[Claim] = []
    """Board facts natural_reason was built from, in verifier form."""


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


class PlanSketch(BaseModel):
    """What one engine line does for the side to move, read off the PV (layer 3)."""

    side: Color
    piece_destinations: dict[str, str] = {}
    """'Nf6' (piece letter + start square) -> final square reached in the line."""
    pawn_breaks: list[str] = []
    """SAN of pawn advances that attack or run into an enemy pawn."""
    exchanges: list[str] = []
    """'Nxd5 exd5' pairs: a capture and the immediate recapture on the same square."""
    king_moves: list[str] = []
    plies: int = 0
    """How many PV plies were read."""
    summary: str = ""
    """Korean, built only from the fields above."""


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
    opening_moves: int = 0
    middlegame_moves: int = 0
    endgame_moves: int = 0
    """How many of the user's moves each accuracy was averaged over (0 means no data)."""
    baseline_band: str | None = None
    """Rating band the deltas are measured against, e.g. '1400-1600'."""


class StructureStat(BaseModel):
    key: str
    name: str
    games: int
    win_rate: float
    avg_loss_cp: float
    wins: int = 0
    losses: int = 0
    break_label: str | None = None
    """The user's most frequent pawn break in this structure (openings_map.BREAKS label)."""
    avg_break_move: float | None = None
    """Average move number of that break over the games where it happened."""


class MotifMiss(BaseModel):
    kind: str
    count: int


class TimeStats(BaseModel):
    blunder_rate_under_30s: float
    blunder_rate_over_30s: float
    baseline: float = 0.09
    moves_under_30s: int = 0
    moves_over_30s: int = 0


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


# ---------- opening map: position guide (M8a) ----------


class NamedCandidate(BaseModel):
    """One move the opening book (or the master explorer) knows from a position."""

    san: str
    uci: str
    label: str
    """Move number and SAN, '3.Bb5' or '3…a6' (services.openings_map.move_label)."""
    fen_after: str
    name: str = ""
    eco: str = ""
    named_here: bool = False
    """True when the position the move reaches is itself named in the book; otherwise the name
    is the one of the line the move belongs to."""
    to_name: list[str] = []
    """SAN moves still to play before a named position, when the line is forced up to it."""
    master_games: int | None = None
    master_score: float | None = None
    """Master result from the requested colour's point of view, (wins + 0.5 draws) / games."""
    master_only: bool = False
    """The book does not know this move; the master explorer does."""


class SetupStatus(BaseModel):
    """How far one system opening (services.setups) has come in a position."""

    id: str
    name: str
    side: Color
    status: Literal["completed", "in_progress", "possible", "blocked"]
    done: list[str] = []
    """Target squares already filled, as 'd4' / 'Bf4' / 'O-O'."""
    remaining: list[str] = []
    blocked_by: str | None = None
    """Why the setup is out of reach, in the knowledge base's words."""
    plans: list[str] = []
    typical_against: str | None = None


class PositionGuide(BaseModel):
    """Everything the opening map shows about one position, without touching the engine."""

    fen: str
    side: Color
    name: str | None = None
    eco: str | None = None
    in_book: bool = False
    structure: StructureInfo
    candidates: list[NamedCandidate] = []
    setups: list[SetupStatus] = []


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


class MoveProbsResponse(BaseModel):
    rating: int
    move_probs: dict[str, float] = {}
    """SAN -> probability over legal moves at this rating, highest first."""
    source: Literal["maia", "engine", "random"]


class MaiaStatus(BaseModel):
    maia2_available: bool
    """The maia2 package is installed and enabled (weights load lazily on first use)."""
    backend: Literal["maia", "engine", "random"]
    """Backend the next request will use."""
    maia_loaded: bool = False
    maia_error: str | None = None
    stockfish_available: bool = False


# ---------- play (M7 practice games) ----------

OpponentKind = Literal["maia", "stockfish"]
PlaySource = Literal["maia", "engine", "random", "stockfish", "book"]
HintLevel = Literal[1, 2, 3]
PracticeMode = Literal["free", "drill", "tabiya"]
CoachPreset = Literal["serious", "learning", "free"]


class OpponentSpec(BaseModel):
    kind: OpponentKind = "maia"
    rating: int = Field(default=1500, ge=800, le=3200)
    """Maia: clamped to its 1100-2000 buckets. Stockfish: UCI_Elo, clamped to 1320-3190."""


class PlayMoveRequest(BaseModel):
    fen: str
    opponent: OpponentSpec = Field(default_factory=OpponentSpec)
    user_rating: int | None = None
    """Rating of the human side; Maia conditions on both players when it is given."""
    seed: int | None = None


class PlayMoveResponse(BaseModel):
    san: str
    uci: str
    source: PlaySource
    probs: dict[str, float] = {}
    """Maia/engine backends: SAN -> probability at the opponent's rating. Empty for stockfish."""
    think_ms: int = 0


class HintCandidate(BaseModel):
    san: str
    uci: str
    prob: float | None = None
    reason: str = ""
    claims: list[Claim] = []


class HintBest(BaseModel):
    san: str
    uci: str
    pv: list[str] = []
    score: Score
    reason: str = ""
    motifs: list[MotifOut] = []
    claims: list[Claim] = []
    computer_move: bool = False
    """Best move a player of this rating would rarely find (Maia probability < 3%)."""


class PlayHintRequest(BaseModel):
    fen: str
    level: HintLevel = 1
    rating: int = Field(default=1500, ge=400, le=3200)
    """Rating the candidates are conditioned on. Bounded so a stray value cannot reach Maia."""
    depth: int | None = None
    start_fen: str | None = None
    moves_san: list[str] = []
    """Game so far from start_fen, so plans can be marked executed/later."""


class PlayHintResponse(BaseModel):
    level: HintLevel
    side: Color
    structure: StructureInfo
    plans: list[Plan] = []
    candidates: list[HintCandidate] = []
    best: HintBest | None = None
    text: str = ""
    """Korean summary for the level, built from the fields above and verified."""
    source: Literal["maia", "engine", "random"] | None = None
    verified: bool = True
    verified_claims: int = 0
    total_claims: int = 0


class PlayCheckRequest(BaseModel):
    fen_before: str
    san: str
    rating: int = Field(default=1500, ge=400, le=3200)
    depth: int | None = None


class PlayCheckResponse(BaseModel):
    san: str
    uci: str
    classification: Classification
    win_loss: float
    eval_before: Score
    eval_after: Score
    best_san: str
    best_uci: str
    pv: list[str] = []
    reason: str = ""
    claims: list[Claim] = []
    verified: bool = True
    computer_move: bool = False
    alternative_san: str | None = None
    """Natural move at this rating that keeps most of the eval, when the best is a computer move."""
    alternative_reason: str = ""


class CoachStats(BaseModel):
    preset: CoachPreset = "learning"
    hints: int = 0
    takebacks: int = 0
    alerts: int = 0


class PracticeGameIn(BaseModel):
    username: str
    user_color: Color | None = None
    """None: manual game, both sides by hand."""
    start_fen: str = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    moves_san: list[str] = []
    result: Literal["1-0", "0-1", "1/2-1/2", "*"] = "*"
    termination: str | None = None
    """checkmate | stalemate | repetition | fifty | material | resign | draw | unfinished"""
    opponent: OpponentSpec | None = None
    coach: CoachStats = CoachStats()
    opening_id: str | None = None
    practice_mode: PracticeMode = "free"
    clocks: list[float] | None = None
    """Seconds left for the mover after each move, when the game had clocks."""
    time_control: str | None = None
    analyse: bool = True


class PracticeGameOut(BaseModel):
    game_id: int
    analysis_status: AnalysisStatus = "none"
    pgn: str


class OpeningRecord(BaseModel):
    games: int = 0
    score: float | None = None
    """Points per game (1 win, 0.5 draw) in imported games that reached the tabiya."""
    practice_games: int = 0
    practice_score: float | None = None


class OpeningCard(BaseModel):
    id: str
    family: str
    family_label: str
    name: str
    name_en: str
    eco: str
    line_san: list[str]
    tabiya_fen: str
    structure: StructureInfo
    sides: list[Color] = ["white", "black"]
    record: OpeningRecord | None = None


class OpeningDetail(OpeningCard):
    plans_white: list[Plan] = []
    plans_black: list[Plan] = []
    fens: list[str] = []
    """FEN after each move of line_san (index 0 = start)."""


class BookMove(BaseModel):
    san: str
    uci: str
    eco: str
    name: str


class BookMoves(BaseModel):
    fen: str
    opening: BookMove | None = None
    """Name of the current position when it is itself in the book (san/uci empty)."""
    moves: list[BookMove] = []


class PlanReport(BaseModel):
    game_id: int
    side: Color
    structure: StructureInfo | None = None
    executed: list[Plan] = []
    pv_match: list[Plan] = []
    later: list[Plan] = []
    unavailable: list[Plan] = []
    breaks: list[str] = []
    """Pawn breaks of the side that were played, as 'e4 (12수)'."""
    opening_id: str | None = None
    opening_name: str | None = None
    practice_mode: PracticeMode | None = None
    summary: str = ""


# ---------- opening map: move intent and deep notes (M8b) ----------

FactKind = Literal[
    "book",
    "name",
    "transposition",
    "center",
    "development",
    "castling",
    "fianchetto",
    "tension",
    "break",
    "gambit",
    "motif",
    "plan",
    "setup",
    "prophylaxis",
    "naturalness",
    "engine",
]
EngineMode = Literal["off", "off_book", "always"]
"""When /openings/annotate runs the engine: never, only off book, or on every move."""


class MoveFact(BaseModel):
    """One thing a move does, as a deterministic detector saw it (services.opening_intent).

    `text` is a whole Korean sentence and `claims` are the board facts it states; a fact whose
    claims do not all hold is reported with `verified=False` and left out of
    MoveAnnotation.text."""

    kind: FactKind
    text: str
    claims: list[Claim] = []
    verified: bool = True


class MoveAnnotation(BaseModel):
    """Everything the opening map's journal says about one move."""

    ply: int
    label: str
    """Move number and SAN, '3.Bb5' or '3…a6'."""
    san: str
    uci: str
    fen_before: str
    fen_after: str
    in_book: bool = False
    name_before: str | None = None
    name_after: str | None = None
    transposition: bool = False
    """The position the move reaches is in the book, but the move order is not the book's."""
    book_alternatives: list[NamedCandidate] = []
    """Book moves instead of this one, at most three; empty when the move is itself in book."""
    facts: list[MoveFact] = []
    text: str = ""
    """The verified sentences, in the order the journal reads them; the first one is the
    one-line summary the journal shows (plan §9.3)."""
    engine: PlayCheckResponse | None = None
    naturalness: float | None = None
    """Probability of this move at the requested rating (Maia), only when asked for."""
    verified: bool = True
    verified_claims: int = 0
    total_claims: int = 0


class AnnotateRequest(BaseModel):
    start_fen: str | None = None
    """Standard starting position when omitted."""
    moves_san: list[str] = []
    color: Color = "white"
    rating: int = 1500
    engine: EngineMode = "off_book"
    naturalness: bool = False
    depth: int | None = None


class AnnotateResponse(BaseModel):
    annotations: list[MoveAnnotation] = []


class ChatBoard(BaseModel):
    """A board the tutor drew with `show_board`, as the chat stream sends it (M8d-3)."""

    type: Literal["board"] = "board"
    n: int = 0
    start_fen: str = ""
    fen: str = ""
    moves: list[str] = []
    last_move: tuple[str, str] | None = None
    arrows: list[Arrow] = []
    highlights: list[str] = []
    caption: str = ""


class Addendum(BaseModel):
    """One question the student asked about this move and the answer they kept.

    The answer is stored exactly as the chat showed it, `unverified` included: the chat's own
    grounding is what the panel repeats, and nothing is verified a second time (plan §10.3)."""

    question: str
    answer: str
    boards: list[ChatBoard] = []
    unverified: list[str] = []
    """Squares the answer named that no fact or tool result had grounded."""
    created_at: datetime


class TrapLine(BaseModel):
    """A trap or a typical mistake, as moves from the position after the annotated move."""

    title: str
    line_san: list[str] = []
    """Replayed by the server; a line with an illegal move is dropped, never stored."""
    text: str = ""


class OpeningNote(BaseModel):
    """The deep explanation of one move, written once per (position, move) and stored.

    Prose comes from Claude Code (services.opening_notes); `[[…]]` marks a sentence the
    verifier confirmed on the board, and a sentence whose claims failed keeps its text without
    the marks. `mine` and `engine` are filled by the server, never by the model."""

    position_key: str
    """Position the move was played in, as openings.position_key."""
    san: str
    in_book: bool = False
    summary: str = ""
    why: list[str] = []
    replies: list[tuple[str, str]] = []
    """(SAN, explanation) pairs, serialised as ["a6", "…"]."""
    alternatives: list[tuple[str, str]] = []
    traps: list[TrapLine] = []
    mine: str | None = None
    engine: str | None = None
    sources: list[str] = []
    verified_claims: int = 0
    total_claims: int = 0
    model: str = ""
    created_at: datetime
    addenda: list[Addendum] = []
    """Answers the student kept from the tutor chat, oldest first (M8d-3)."""
    questions: list[str] = []
    """Question chips the panel offers, made from this note by the server on every read
    (`opening_notes.suggested_questions`); never written by the model."""


class NoteMissing(BaseModel):
    """GET /openings/note when nothing has been written for this move yet."""

    status: Literal["missing"] = "missing"


class NoteRequest(BaseModel):
    fen: str
    san: str
    username: str | None = None
    """Whose games fill the note's `mine` line; without it the line stays empty."""
    regenerate: bool = False


# ---------- the tutor's answers kept on a note (M8d-3) ----------


class AddendumRequest(BaseModel):
    fen: str
    """Position the move was played in — the note's own key."""
    san: str
    question: str = Field(min_length=1, max_length=4000)
    answer: str = Field(min_length=1, max_length=20000)
    boards: list[ChatBoard] = []
    unverified: list[str] = []


class OpeningContext(BaseModel):
    """The note the student is reading while asking, attached to a live chat question.

    The conversation is keyed by (fen_before, san): the question is about that move, so the
    same position with a different move is a different conversation (plan §10.3)."""

    fen_before: str
    san: str
    note_summary: str = ""
    section: str | None = None
    """Which section of the note the question came from, when it came from one."""
    quote: str | None = None
    """A sentence of the note the student quoted."""
