"""Human move model (layer 1 oracle) with a rating-conditioned engine fallback.

A backend answers one question: at this rating, how likely is each legal move? Three
backends exist, tried in order:

* ``MaiaBackend``: Maia-2 (pip package ``maia2``, MIT). Weights are downloaded to a
  cache directory on first use, so loading is lazy and every failure turns into a
  fallback rather than an error.
* ``EngineBackend``: Stockfish MultiPV scored from the mover's side, turned into a
  distribution with softmax(score_delta / T). The temperature T grows as the rating drops
  (about 80 cp at 1500, 30 cp at 2200), so weaker ratings spread probability over worse
  moves. Legal moves outside the analysed lines share a small floor.
* ``RandomBackend``: uniform over legal moves; the last resort when no engine is found.

``human_view`` adds a layer-2 style ``natural_reason``: a Korean sentence built only from
python-chess facts about the played move (capture, check, defence, retreat, development
...). Each fact used is also returned as a ``Claim`` so the verifier can check it.
No LLM is involved anywhere in this module.
"""

from __future__ import annotations

import atexit
import contextlib
import importlib.util
import io
import logging
import math
import os
import random
import re
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal, Protocol

import chess

from chess_tutor.config import get_settings
from chess_tutor.engine import Engine, find_stockfish
from chess_tutor.schemas import HumanView
from chess_tutor.values import value_at
from chess_tutor.verify import Claim

log = logging.getLogger(__name__)

Source = Literal["maia", "engine", "random"]

COMPUTER_MOVE_THRESHOLD = 0.03
"""Best move probability below which the engine's choice counts as a 'computer move'."""

ENGINE_DEPTH = 8
ENGINE_MULTIPV = 6
MATE_CP = 2000
"""Mate scores and extreme evaluations are clamped to +-MATE_CP before the softmax."""
FLOOR_SHARE = 0.05
"""Legal moves outside the analysed lines share at most this fraction of the mass."""
TOP_MOVES = 5
PRECISION = 6
"""Decimal places kept in reported probabilities; tiny values stay visible."""

PIECE_KR: dict[chess.PieceType, str] = {
    chess.PAWN: "폰",
    chess.KNIGHT: "나이트",
    chess.BISHOP: "비숍",
    chess.ROOK: "룩",
    chess.QUEEN: "퀸",
    chess.KING: "킹",
}

_HOME_SQUARES: dict[tuple[chess.Color, chess.PieceType], frozenset[chess.Square]] = {
    (chess.WHITE, chess.KNIGHT): frozenset({chess.B1, chess.G1}),
    (chess.WHITE, chess.BISHOP): frozenset({chess.C1, chess.F1}),
    (chess.BLACK, chess.KNIGHT): frozenset({chess.B8, chess.G8}),
    (chess.BLACK, chess.BISHOP): frozenset({chess.C8, chess.F8}),
}


class BackendUnavailable(RuntimeError):
    """The backend cannot answer right now (missing package, failed download, dead engine)."""


class Backend(Protocol):
    name: Source

    def is_available(self) -> bool: ...

    def move_probs(self, fen: str, rating: int, include: Iterable[str] = ()) -> dict[str, float]:
        """SAN -> probability over legal moves, highest first, summing to 1.

        ``include`` lists SAN moves whose probability must come from a real evaluation
        even when they fall outside the backend's usual candidate set. Raises ValueError
        for a bad FEN or SAN and BackendUnavailable when the backend itself fails.
        """
        ...


# ---------- temperature ----------


def temperature(rating: int) -> float:
    """Softmax temperature in centipawns: 80 at 1500, 30 at 2200, linear in between and
    clamped to [12, 200] outside."""
    r = min(max(rating, 400), 3200)
    return min(max(80.0 - (r - 1500) * (50.0 / 700.0), 12.0), 200.0)


def _mover_cp(score_cp: int | None, mate: int | None, mover: chess.Color) -> int:
    """Clamp a White-relative engine score to the mover's side in [-MATE_CP, MATE_CP]."""
    if mate is not None:
        cp = (MATE_CP - min(abs(mate), 50)) * (1 if mate > 0 else -1)
    else:
        cp = score_cp or 0
    if mover == chess.BLACK:
        cp = -cp
    return max(-MATE_CP, min(MATE_CP, cp))


def _distribution(
    board: chess.Board, scores: dict[chess.Move, int], temp: float
) -> dict[str, float]:
    """softmax(score_delta / temp) over the scored moves; other legal moves get a floor."""
    best = max(scores.values())
    weights: dict[chess.Move, float] = {
        mv: math.exp((cp - best) / temp) for mv, cp in scores.items()
    }
    analysed_mass = sum(weights.values())
    rest = [mv for mv in board.legal_moves if mv not in weights]
    if rest:
        floor = min(min(weights.values()), analysed_mass * FLOOR_SHARE / len(rest))
        for mv in rest:
            weights[mv] = floor
    total = sum(weights.values())
    ordered = sorted(weights.items(), key=lambda kv: -kv[1])
    return {board.san(mv): w / total for mv, w in ordered}


# ---------- backends ----------


class RandomBackend:
    """Uniform over legal moves. Always available; source 'random'."""

    name: Source = "random"

    def is_available(self) -> bool:
        return True

    def move_probs(self, fen: str, rating: int, include: Iterable[str] = ()) -> dict[str, float]:
        board = chess.Board(fen)
        for san in include:
            board.parse_san(san)
        moves = list(board.legal_moves)
        if not moves:
            return {}
        p = 1.0 / len(moves)
        return {board.san(mv): p for mv in moves}


class EngineBackend:
    """Rating-conditioned sampling over Stockfish MultiPV lines."""

    name: Source = "engine"

    def __init__(
        self,
        depth: int = ENGINE_DEPTH,
        multipv: int = ENGINE_MULTIPV,
        path: str | None = None,
    ) -> None:
        self.depth = depth
        self.multipv = multipv
        self._path = path
        self._engine: Engine | None = None
        self._error: str | None = None
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        return self._error is None and (self._path or find_stockfish()) is not None

    def close(self) -> None:
        with self._lock:
            if self._engine is not None:
                with contextlib.suppress(Exception):
                    self._engine.close()
                self._engine = None

    def _get(self) -> Engine:
        if self._engine is None:
            try:
                self._engine = Engine(self._path)
            except Exception as exc:  # noqa: BLE001 - any failure means "no engine"
                self._error = str(exc)
                raise BackendUnavailable(f"stockfish unavailable: {exc}") from exc
        return self._engine

    def _analyse(self, board: chess.Board, depth: int, multipv: int) -> list[Any]:
        with self._lock:
            engine = self._get()
            try:
                return engine.analyse(board, depth=depth, multipv=multipv)
            except Exception as exc:  # noqa: BLE001 - a dead engine process
                self._error = str(exc)
                with contextlib.suppress(Exception):
                    engine.close()
                self._engine = None
                raise BackendUnavailable(f"stockfish failed: {exc}") from exc

    def score_move(self, board: chess.Board, move: chess.Move) -> int:
        """Mover-relative centipawns for one move: the opponent's best reply, one ply
        shallower, seen from the mover's side."""
        after = board.copy()
        after.push(move)
        if after.is_checkmate():
            return MATE_CP
        if after.is_game_over():
            return 0
        lines = self._analyse(after, max(1, self.depth - 1), 1)
        if not lines:
            return 0
        return _mover_cp(lines[0].score_cp, lines[0].mate, board.turn)

    def scores(self, board: chess.Board, include: Iterable[str] = ()) -> dict[chess.Move, int]:
        """Mover-relative centipawns for the top MultiPV moves plus every ``include`` move."""
        legal = list(board.legal_moves)
        if not legal:
            return {}
        scores: dict[chess.Move, int] = {}
        for line in self._analyse(board, self.depth, min(self.multipv, len(legal))):
            if line.pv:
                scores[line.pv[0]] = _mover_cp(line.score_cp, line.mate, board.turn)
        for san in include:
            mv = board.parse_san(san)
            if mv not in scores:
                scores[mv] = self.score_move(board, mv)
        if not scores:
            raise BackendUnavailable("stockfish returned no lines")
        return scores

    def move_probs(self, fen: str, rating: int, include: Iterable[str] = ()) -> dict[str, float]:
        board = chess.Board(fen)
        scores = self.scores(board, include)
        if not scores:
            return {}
        return _distribution(board, scores, temperature(rating))


class MaiaBackend:
    """Maia-2 through the ``maia2`` package. The model loads lazily under a lock."""

    name: Source = "maia"

    def __init__(
        self, model_type: str | None = None, model_dir: str | os.PathLike[str] | None = None
    ):
        self.model_type = model_type or os.environ.get("MAIA_MODEL_TYPE", "rapid")
        self.model_dir = Path(
            model_dir
            or os.environ.get("MAIA_MODEL_DIR")
            or Path.home() / ".cache" / "chess-tutor" / "maia2"
        )
        self._model: Any = None
        self._prepared: Any = None
        self._inference: Any = None
        self._error: str | None = None
        self._lock = threading.Lock()

    @staticmethod
    def installed() -> bool:
        try:
            return importlib.util.find_spec("maia2") is not None
        except (ImportError, ValueError):
            return False

    @property
    def loaded(self) -> bool:
        return self._model is not None

    @property
    def error(self) -> str | None:
        return self._error

    def is_available(self) -> bool:
        return self._error is None and get_settings().maia_enabled and self.installed()

    def load(self) -> bool:
        """Import maia2 and load (downloading if needed) the weights. False on any failure."""
        with self._lock:
            if self._model is not None:
                return True
            if self._error is not None or not self.is_available():
                return False
            try:
                from maia2 import inference, model

                with contextlib.redirect_stdout(io.StringIO()):
                    loaded = model.from_pretrained(
                        type=self.model_type, device="cpu", save_root=str(self.model_dir)
                    )
                self._prepared = inference.prepare()
                self._inference = inference
                self._model = loaded
            except BaseException as exc:  # noqa: BLE001 - torch/gdown raise anything
                if isinstance(exc, KeyboardInterrupt):
                    raise
                self._error = f"{type(exc).__name__}: {exc}"
                log.warning("maia2 unavailable, falling back to engine: %s", self._error)
                return False
            return True

    def move_probs(self, fen: str, rating: int, include: Iterable[str] = ()) -> dict[str, float]:
        board = chess.Board(fen)
        for san in include:
            board.parse_san(san)
        if not any(board.legal_moves):
            return {}
        if not self.load():
            raise BackendUnavailable(self._error or "maia2 not available")
        try:
            with self._lock:
                uci_probs, _win = self._inference.inference_each(
                    self._model, self._prepared, board.fen(), rating, rating
                )
        except Exception as exc:  # noqa: BLE001 - inference failure -> fallback
            self._error = f"{type(exc).__name__}: {exc}"
            log.warning("maia2 inference failed, falling back: %s", self._error)
            raise BackendUnavailable(self._error) from exc
        probs: dict[chess.Move, float] = {}
        for uci, p in uci_probs.items():
            mv = chess.Move.from_uci(uci)
            if board.is_legal(mv):
                probs[mv] = float(p)
        for mv in board.legal_moves:
            probs.setdefault(mv, 0.0)
        total = sum(probs.values())
        if total <= 0:
            raise BackendUnavailable("maia2 returned an empty distribution")
        ordered = sorted(probs.items(), key=lambda kv: -kv[1])
        return {board.san(mv): p / total for mv, p in ordered}


# ---------- backend registry ----------

_default_engine = EngineBackend()
_default_backends: list[Backend] = [MaiaBackend(), _default_engine, RandomBackend()]
_active: list[Backend] | None = None
atexit.register(_default_engine.close)


def backends() -> list[Backend]:
    return list(_active if _active is not None else _default_backends)


def use_backends(*chain: Backend) -> None:
    """Override the backend chain (tests). Call with no arguments to restore the default."""
    global _active
    _active = list(chain) or None


def current_backend() -> Backend:
    for b in backends():
        if b.is_available():
            return b
    return RandomBackend()


def status() -> dict[str, Any]:
    maia = next((b for b in backends() if isinstance(b, MaiaBackend)), None)
    return {
        "maia2_available": maia is not None and maia.is_available(),
        "backend": current_backend().name,
        "maia_loaded": maia is not None and maia.loaded,
        "maia_error": maia.error if maia is not None else None,
        "stockfish_available": any(
            isinstance(b, EngineBackend) and b.is_available() for b in backends()
        ),
    }


def move_probs(
    fen: str, rating: int, include: Iterable[str] = (), backend: Backend | None = None
) -> tuple[dict[str, float], Source]:
    """Probabilities from the first backend that answers, with its name."""
    chain = [backend] if backend is not None else [b for b in backends() if b.is_available()]
    errors: list[str] = []
    for b in chain:
        try:
            return b.move_probs(fen, rating, list(include)), b.name
        except BackendUnavailable as exc:
            errors.append(f"{b.name}: {exc}")
    if backend is None:
        return RandomBackend().move_probs(fen, rating, list(include)), "random"
    raise BackendUnavailable("; ".join(errors))


def choose_move(
    fen: str, rating: int, seed: int | None = None, backend: Backend | None = None
) -> tuple[str, str, dict[str, float], Source]:
    """Sample a move for the side to move: (san, uci, probs, source).

    Raises ValueError when the FEN is invalid or the game is over.
    """
    board = chess.Board(fen)
    probs, source = move_probs(fen, rating, backend=backend)
    if not probs:
        raise ValueError("no legal moves in this position")
    rng = random.Random(seed)
    sans = list(probs)
    san = rng.choices(sans, weights=[probs[s] for s in sans], k=1)[0]
    uci = board.parse_san(san).uci()
    return san, uci, {s: round(p, PRECISION) for s, p in probs.items()}, source


# ---------- human view ----------


def _batchim(word: str) -> tuple[bool, bool]:
    """(has final consonant, final consonant is ㄹ) for the last character, so Korean
    particles attach correctly to piece names, squares and SAN-style labels."""
    if not word:
        return False, False
    last = word[-1]
    if "가" <= last <= "힣":
        final = (ord(last) - 0xAC00) % 28
        return final != 0, final == 8
    if last.isdigit():
        return last in "013678", last in "178"
    return False, False


def _josa(word: str, kind: str) -> str:
    has, rieul = _batchim(word)
    match kind:
        case "을":
            return word + ("을" if has else "를")
        case "이":
            return word + ("이" if has else "가")
        case "으로":
            return word + ("로" if (not has or rieul) else "으로")
    raise ValueError(kind)


def _named(board: chess.Board, sq: chess.Square) -> str:
    """'e7 비숍' style label for the piece on sq."""
    piece = board.piece_at(sq)
    assert piece is not None
    return f"{chess.square_name(sq)} {PIECE_KR[piece.piece_type]}"


def _label(board: chess.Board, sq: chess.Square) -> str:
    """'Nd5' style label (mockup style) for the piece on sq; pawns read 'd5 폰'."""
    piece = board.piece_at(sq)
    assert piece is not None
    if piece.piece_type == chess.PAWN:
        return f"{chess.square_name(sq)} 폰"
    return f"{piece.symbol().upper()}{chess.square_name(sq)}"


def _threat(board: chess.Board, sq: chess.Square, color: chess.Color) -> chess.Square | None:
    """Square of the cheapest enemy attacker when the piece of ``color`` on ``sq`` is en
    prise: attacked by a cheaper piece, or attacked while undefended. None otherwise."""
    attackers = board.attackers(not color, sq)
    if not attackers:
        return None
    cheapest = min(attackers, key=lambda a: value_at(board, a))
    if value_at(board, cheapest) < value_at(board, sq):
        return cheapest
    if not board.attackers(color, sq):
        return cheapest
    return None


_SAN_DEST = re.compile(r"([a-h][1-8])(?:=[QRBN])?[+#]?$")


def _san_destination(san: str) -> chess.Square | None:
    m = _SAN_DEST.search(san)
    return chess.parse_square(m.group(1)) if m else None


def natural_reason(
    board: chess.Board, move: chess.Move, last_san: str | None = None
) -> tuple[str, list[Claim]]:
    """One Korean sentence on what the move does, with the board facts it used as claims.

    ``last_san`` is the opponent's previous move; when it captured on the square the move
    now captures on, the move is described as a recapture. Deterministic, no engine.
    """
    if not board.is_legal(move):
        raise ValueError(f"illegal move {move.uci()} in {board.fen()}")
    color = board.turn
    fen = board.fen()
    piece = board.piece_at(move.from_square)
    assert piece is not None
    mover_kr = PIECE_KR[piece.piece_type]
    to_name = chess.square_name(move.to_square)
    from_name = chess.square_name(move.from_square)
    after = board.copy()
    after.push(move)
    fen_after = after.fen()
    claims: list[Claim] = [Claim(kind="legal_move", fen=fen, object=board.san(move))]

    check_tail = ""
    if after.is_check() and not after.is_checkmate():
        claims.append(Claim(kind="is_check", fen=fen_after))
        check_tail = " 체크입니다."

    if after.is_checkmate():
        claims.append(Claim(kind="is_check", fen=fen_after))
        return f"{_josa(mover_kr, '으로')} 체크메이트를 만들었습니다.", claims

    if move.promotion:
        promo = PIECE_KR[move.promotion]
        return f"폰을 {to_name}에서 {_josa(promo, '으로')} 승격했습니다." + check_tail, claims

    if board.is_capture(move):
        cap_sq = move.to_square
        if board.is_en_passant(move):
            cap_sq = chess.square(
                chess.square_file(move.to_square), chess.square_rank(move.from_square)
            )
        captured = board.piece_at(cap_sq)
        assert captured is not None
        claims.append(
            Claim(
                kind="piece_on",
                fen=fen,
                subject=chess.square_name(cap_sq),
                object=captured.symbol(),
            )
        )
        target = _named(board, cap_sq)
        recapture = (
            last_san is not None
            and "x" in last_san
            and _san_destination(last_san) == move.to_square
        )
        if recapture:
            captured_kr = _josa(PIECE_KR[captured.piece_type], "을")
            text = f"{to_name}에서 {captured_kr} {_josa(mover_kr, '으로')} 되잡았습니다."
        elif check_tail:
            text = f"{_josa(target, '을')} {_josa(mover_kr, '으로')} 잡으면서 체크를 걸었습니다."
            return text, claims
        else:
            text = f"{_josa(target, '을')} {_josa(mover_kr, '으로')} 잡았습니다."
        return text + check_tail, claims

    if board.is_castling(move):
        side = (
            "킹사이드"
            if chess.square_file(move.to_square) > chess.square_file(move.from_square)
            else "퀸사이드"
        )
        return f"{side} 캐슬링으로 킹을 {_josa(to_name, '으로')} 옮겼습니다." + check_tail, claims

    if board.is_check():
        claims.append(Claim(kind="is_check", fen=fen))
        if piece.piece_type == chess.KING:
            return f"체크를 받은 킹을 {_josa(to_name, '으로')} 피했습니다.", claims
        claims.append(Claim(kind="piece_on", fen=fen_after, subject=to_name, object=piece.symbol()))
        return f"{_josa(mover_kr, '으로')} 사이를 막아 체크를 벗어났습니다.", claims

    if check_tail:
        return f"{_josa(mover_kr, '을')} {_josa(to_name, '으로')} 옮겨 체크를 걸었습니다.", claims

    # Defence: a friendly piece that was en prise is no longer, thanks to this move
    # (the mover now covers it, or stands between it and the attacker).
    defended: list[tuple[int, chess.Square, chess.Square, str]] = []
    for sq in chess.SquareSet(board.occupied_co[color]):
        if sq == move.from_square or board.piece_type_at(sq) == chess.KING:
            continue
        attacker = _threat(board, sq, color)
        if attacker is None or _threat(after, sq, color) is not None:
            continue
        covers = move.to_square in after.attackers(
            color, sq
        ) and move.from_square not in board.attackers(color, sq)
        blocks = move.to_square in chess.SquareSet(chess.between(attacker, sq))
        if covers:
            defended.append((value_at(board, sq), sq, attacker, "covers"))
        elif blocks:
            defended.append((value_at(board, sq), sq, attacker, "blocks"))
    if defended:
        _, sq, attacker, how = max(defended)
        sq_name = chess.square_name(sq)
        claims.append(
            Claim(kind="attacks", fen=fen, subject=chess.square_name(attacker), object=sq_name)
        )
        head = f"{_josa(_label(board, attacker), '이')} {_josa(_named(board, sq), '을')} 공격하자"
        if how == "covers":
            claims.append(Claim(kind="defends", fen=fen_after, subject=to_name, object=sq_name))
            return f"{head} {_josa(mover_kr, '으로')} 지켰습니다.", claims
        claims.append(Claim(kind="piece_on", fen=fen_after, subject=to_name, object=piece.symbol()))
        return f"{head} {_josa(mover_kr, '으로')} 사이를 막았습니다.", claims

    # Retreat: the mover itself was en prise and is safe on its new square.
    attacker = _threat(board, move.from_square, color)
    if attacker is not None and _threat(after, move.to_square, color) is None:
        claims.append(
            Claim(kind="attacks", fen=fen, subject=chess.square_name(attacker), object=from_name)
        )
        attacker_kr = _josa(_label(board, attacker), "이")
        mover_named = _josa(_named(board, move.from_square), "을")
        text = f"{attacker_kr} 공격하던 {mover_named} {_josa(to_name, '으로')} 피했습니다."
        return text, claims

    # Threat: the mover now attacks an enemy piece that is en prise because of it.
    targets = [
        sq
        for sq in after.attacks(move.to_square) & after.occupied_co[not color]
        if after.piece_type_at(sq) != chess.KING and _threat(after, sq, not color) == move.to_square
    ]
    if targets:
        sq = max(targets, key=lambda s: value_at(after, s))
        claims.append(
            Claim(kind="attacks", fen=fen_after, subject=to_name, object=chess.square_name(sq))
        )
        verb = "밀어" if piece.piece_type == chess.PAWN else "옮겨"
        target_kr = _josa(_named(after, sq), "을")
        text = f"{_josa(mover_kr, '을')} {_josa(to_name, '으로')} {verb} {target_kr} 공격했습니다."
        return text, claims

    if move.from_square in _HOME_SQUARES.get((color, piece.piece_type), frozenset()):
        claims.append(Claim(kind="piece_on", fen=fen, subject=from_name, object=piece.symbol()))
        return f"{_josa(mover_kr, '을')} {_josa(to_name, '으로')} 전개했습니다.", claims

    if piece.piece_type == chess.PAWN:
        return f"폰을 {_josa(to_name, '으로')} 밀었습니다.", claims
    return f"{_josa(mover_kr, '을')} {_josa(to_name, '으로')} 옮겼습니다.", claims


def human_view(
    fen: str,
    played_san: str,
    best_san: str | None,
    rating: int,
    last_san: str | None = None,
    backend: Backend | None = None,
    top: int = TOP_MOVES,
) -> HumanView:
    """How a player of this rating sees the position: move probabilities, how likely the
    played move was, whether the engine's best move is a 'computer move', and what the
    played move naturally does. Raises ValueError for a bad FEN or an illegal SAN."""
    board = chess.Board(fen)
    played = board.parse_san(played_san)
    played_key = board.san(played)
    best_key = board.san(board.parse_san(best_san)) if best_san else None
    include = [played_key] + ([best_key] if best_key and best_key != played_key else [])
    probs, source = move_probs(fen, rating, include=include, backend=backend)

    shown = dict(list(probs.items())[:top])
    for key in (played_key, best_key):
        if key is not None and key in probs:
            shown[key] = probs[key]
    best_prob = probs.get(best_key) if best_key else None
    reason, claims = natural_reason(board, played, last_san=last_san)
    return HumanView(
        rating=rating,
        move_probs={k: round(v, PRECISION) for k, v in shown.items()},
        played_prob=round(probs[played_key], PRECISION) if played_key in probs else None,
        natural_reason=reason,
        computer_move=best_prob is not None and best_prob < COMPUTER_MOVE_THRESHOLD,
        source=source,
        claims=claims,
    )
