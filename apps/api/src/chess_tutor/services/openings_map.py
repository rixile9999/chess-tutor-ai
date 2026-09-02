"""Opening visualisations built from the user's own games (layer 5 on top of layer 1 data).

Three deterministic views over a list of games:

* :func:`build_map` merges the games into a DAG of positions (transpositions collapse into one
  node because nodes are keyed by :func:`chess_tutor.openings.position_key`), with the user's
  record on every node and edge, book names, tabiya and deviation flags, and optionally the
  master moves the user never plays (Lichess explorer, only when a token is configured).
* :func:`piece_heatmap` gives the distribution of the square one piece ends up on.
* :func:`break_timing` gives histograms of when the classic pawn breaks first happen.

Nothing here calls an LLM. Every number is a count over the games passed in.
"""

from __future__ import annotations

import io
import logging
from collections import Counter, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import chess
import chess.pgn
import httpx

from chess_tutor.config import get_settings
from chess_tutor.openings import Opening, lookup, position_key
from chess_tutor.schemas import (
    BreakTiming,
    Color,
    OpeningEdge,
    OpeningMap,
    OpeningNode,
    PieceHeatmap,
)

log = logging.getLogger(__name__)

Outcome = Literal["win", "draw", "loss"]

EXPLORER_URL = "https://explorer.lichess.ovh/masters"
MASTER_NODES = 10
"""How many of the busiest nodes get master moves attached."""
MASTER_MOVES_PER_NODE = 3
BOOK_LOOKAHEAD = 50
"""Plies scanned for book positions when deciding where a game left the book."""

ROOT_LABEL = "시작 국면"
TABIYA_SUFFIX = " 타비야"
DEVIATION_SUFFIX = " 책 이탈"

PIECE_NAMES_KO = {
    chess.KING: "킹",
    chess.QUEEN: "퀸",
    chess.ROOK: "룩",
    chess.BISHOP: "비숍",
    chess.KNIGHT: "나이트",
    chess.PAWN: "폰",
}
COLOR_NAMES_KO: dict[str, str] = {"white": "백", "black": "흑"}


# ---------- game loading ----------


class GameLike(Protocol):
    """What we need from a models.Game row (or any stand-in in tests)."""

    pgn: str
    user_color: str | None
    result: str


@dataclass(frozen=True)
class ParsedGame:
    moves: tuple[chess.Move, ...]
    user_color: str | None
    outcome: Outcome | None
    """Result from the user's point of view; None when unknown or user_color is unset."""


_RESULTS = {"1-0", "0-1", "1/2-1/2"}


def _outcome(result: str | None, user_color: str | None) -> Outcome | None:
    if result not in _RESULTS or user_color not in ("white", "black"):
        return None
    if result == "1/2-1/2":
        return "draw"
    white_won = result == "1-0"
    return "win" if white_won == (user_color == "white") else "loss"


def parse_game(game: GameLike) -> ParsedGame | None:
    """Read the mainline of one PGN. Games that do not start from the standard position, or
    whose PGN cannot be read at all, are skipped (None)."""
    try:
        node = chess.pgn.read_game(io.StringIO(game.pgn or ""))
    except (ValueError, IndexError):
        return None
    if node is None or node.board().fen() != chess.STARTING_FEN:
        return None
    moves = tuple(node.mainline_moves())
    if not moves:
        return None
    result = getattr(game, "result", None)
    if result not in _RESULTS:
        result = node.headers.get("Result")
    user_color = getattr(game, "user_color", None)
    return ParsedGame(moves=moves, user_color=user_color, outcome=_outcome(result, user_color))


def parse_games(games: Iterable[GameLike], color: Color) -> list[ParsedGame]:
    """Parse every game the user played with `color`; unreadable games are dropped."""
    parsed: list[ParsedGame] = []
    for game in games:
        if getattr(game, "user_color", None) != color:
            continue
        pg = parse_game(game)
        if pg is not None:
            parsed.append(pg)
    return parsed


# ---------- opening map ----------


@dataclass
class _Node:
    key: str
    fen: str
    depth: int
    opening: Opening | None
    games: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    labels: Counter[str] = field(default_factory=Counter)
    """Move label votes (a transposition can be reached by different last moves)."""
    sans: Counter[str] = field(default_factory=Counter)
    deviation_votes: int = 0


@dataclass
class _Edge:
    source: str
    target: str
    san: str
    games: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0


@dataclass(frozen=True)
class _Step:
    key: str
    fen: str
    san: str
    opening: Opening | None


def _score(wins: int, draws: int, games: int) -> float:
    return round((wins + 0.5 * draws) / games, 4) if games else 0.0


def move_label(ply: int, san: str) -> str:
    """'3.Nf3' for White's third move, '2…e6' for Black's second."""
    if ply % 2 == 1:
        return f"{(ply + 1) // 2}.{san}"
    return f"{ply // 2}…{san}"


def _walk(moves: tuple[chess.Move, ...], limit: int) -> list[_Step]:
    board = chess.Board()
    steps: list[_Step] = []
    for move in moves[:limit]:
        san = board.san(move)
        board.push(move)
        steps.append(_Step(position_key(board), board.fen(), san, lookup(board)))
    return steps


def _deviation_ply(steps: list[_Step], user_color: str) -> int | None:
    """1-based ply of the user's move that left the opening book for good, or None.

    'Left for good' means no later position of the game is in the book, so a gap in the book
    (a position the TSV does not name, followed by one it does) is not a deviation."""
    last_book = 0
    for ply, step in enumerate(steps, 1):
        if step.opening is not None:
            last_book = ply
    if last_book == 0 or last_book >= len(steps):
        return None
    ply = last_book + 1
    user_moves_on_odd = user_color == "white"
    if (ply % 2 == 1) != user_moves_on_odd:
        return None
    return ply


def _tally(node_or_edge: _Node | _Edge, outcome: Outcome | None) -> None:
    node_or_edge.games += 1
    if outcome == "win":
        node_or_edge.wins += 1
    elif outcome == "draw":
        node_or_edge.draws += 1
    elif outcome == "loss":
        node_or_edge.losses += 1


def build_map(
    games: Iterable[GameLike],
    color: Color,
    depth: int = 12,
    min_games: int = 2,
    *,
    use_masters: bool | None = None,
) -> OpeningMap:
    """Merge the user's games (played as `color`) into a position DAG `depth` plies deep.

    Nodes are keyed by position, so move orders that transpose share a node. Records are from
    the user's point of view: score = (wins + 0.5 * draws) / games. Nodes seen in fewer than
    `min_games` games are pruned (the root stays), as is anything no longer reachable from the
    root afterwards.

    Flags: `is_deviation` marks the first node on a path where the user's own move left the
    book (see :func:`_deviation_ply`); `is_tabiya` marks nodes at depth >= 8 that are either
    busy (games >= max(3, 10% of the games)) or reached from two or more parents.

    `use_masters`: None consults settings (a Lichess token enables it), True/False force it.
    Master moves become `master_only` edges (and nodes) with the master score from the user's
    point of view and games = 0."""
    root_board = chess.Board()
    root_key = position_key(root_board)
    nodes: dict[str, _Node] = {root_key: _Node(root_key, root_board.fen(), 0, None)}
    edges: dict[tuple[str, str], _Edge] = {}
    parsed = parse_games(games, color)

    for game in parsed:
        steps = _walk(game.moves, max(depth, BOOK_LOOKAHEAD))
        dev_ply = _deviation_ply(steps, color)
        _tally(nodes[root_key], game.outcome)
        seen_nodes = {root_key}
        seen_edges: set[tuple[str, str]] = set()
        prev = root_key
        for ply, step in enumerate(steps[:depth], 1):
            node = nodes.get(step.key)
            if node is None:
                node = nodes[step.key] = _Node(step.key, step.fen, ply, step.opening)
            if step.key not in seen_nodes:
                seen_nodes.add(step.key)
                _tally(node, game.outcome)
                node.labels[move_label(ply, step.san)] += 1
                node.sans[step.san] += 1
                if ply == dev_ply:
                    node.deviation_votes += 1
            edge_key = (prev, step.key)
            edge = edges.get(edge_key)
            if edge is None:
                edge = edges[edge_key] = _Edge(prev, step.key, step.san)
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                _tally(edge, game.outcome)
            prev = step.key

    total = len(parsed)
    kept = {k for k, n in nodes.items() if n.games >= min_games or k == root_key}
    kept = _reachable(root_key, kept, edges)
    kept_edges = [e for (s, t), e in edges.items() if s in kept and t in kept]
    parents: dict[str, set[str]] = {}
    for e in kept_edges:
        parents.setdefault(e.target, set()).add(e.source)

    busy = max(3.0, 0.1 * total)
    out_nodes: list[OpeningNode] = []
    for key in kept:
        n = nodes[key]
        is_root = key == root_key
        is_tabiya = n.depth >= 8 and (n.games >= busy or len(parents.get(key, ())) >= 2)
        is_deviation = n.deviation_votes > 0 and n.games >= min_games
        if is_root:
            label = ROOT_LABEL
        else:
            label = n.labels.most_common(1)[0][0]
            if is_deviation:
                label += DEVIATION_SUFFIX
            elif is_tabiya:
                label += TABIYA_SUFFIX
        out_nodes.append(
            OpeningNode(
                id=key,
                label=label,
                san=None if is_root else n.sans.most_common(1)[0][0],
                fen=n.fen,
                depth=n.depth,
                games=n.games,
                wins=n.wins,
                draws=n.draws,
                losses=n.losses,
                score=_score(n.wins, n.draws, n.games),
                name=n.opening.name if n.opening else None,
                eco=n.opening.eco if n.opening else None,
                is_tabiya=is_tabiya,
                is_deviation=is_deviation,
            )
        )
    out_edges = [
        OpeningEdge(
            source=e.source,
            target=e.target,
            san=e.san,
            games=e.games,
            score=_score(e.wins, e.draws, e.games),
        )
        for e in kept_edges
    ]

    if use_masters is None:
        use_masters = _explorer_token() is not None
    if use_masters:
        _attach_masters(out_nodes, out_edges, color)

    out_nodes.sort(key=lambda n: (n.depth, -n.games, n.id))
    depth_of = {n.id: n.depth for n in out_nodes}
    out_edges.sort(key=lambda e: (depth_of.get(e.source, 0), -e.games, e.source, e.target))
    return OpeningMap(
        color=color, root=root_key, nodes=out_nodes, edges=out_edges, total_games=total
    )


def _reachable(root: str, kept: set[str], edges: dict[tuple[str, str], _Edge]) -> set[str]:
    children: dict[str, list[str]] = {}
    for s, t in edges:
        if s in kept and t in kept:
            children.setdefault(s, []).append(t)
    seen = {root}
    queue = deque([root])
    while queue:
        for child in children.get(queue.popleft(), ()):
            if child not in seen:
                seen.add(child)
                queue.append(child)
    return seen


# ---------- master overlay (Lichess explorer) ----------

_master_cache: dict[str, list[dict[str, Any]]] = {}


def _explorer_token() -> str | None:
    return get_settings().lichess_token


def fetch_master_moves(fen: str, token: str) -> list[dict[str, Any]]:
    """Top master moves for a FEN from the Lichess explorer; [] on any failure. Cached per
    process so a page reload does not hit the API again."""
    cached = _master_cache.get(fen)
    if cached is not None:
        return cached
    try:
        resp = httpx.get(
            EXPLORER_URL,
            params={"fen": fen, "moves": 8, "topGames": 0, "recentGames": 0},
            headers={"Authorization": f"Bearer {token}"},
            timeout=3.0,
        )
        resp.raise_for_status()
        moves = resp.json().get("moves", [])
        if not isinstance(moves, list):
            moves = []
    except Exception as exc:  # noqa: BLE001 - the overlay is optional, never fail the map
        log.warning("lichess explorer unavailable for %s: %s", fen, exc)
        return []
    _master_cache[fen] = moves
    return moves


def _attach_masters(
    nodes: list[OpeningNode],
    edges: list[OpeningEdge],
    color: Color,
    fetch: Callable[[str, str], list[dict[str, Any]]] = fetch_master_moves,
) -> None:
    token = _explorer_token()
    if token is None:
        return
    existing = {(e.source, e.target) for e in edges}
    known = {n.id for n in nodes}
    top = sorted(nodes, key=lambda n: (-n.games, n.depth, n.id))[:MASTER_NODES]
    for node in top:
        added = 0
        for mv in fetch(node.fen, token):
            if added >= MASTER_MOVES_PER_NODE:
                break
            try:
                board = chess.Board(node.fen)
                move = chess.Move.from_uci(str(mv.get("uci", "")))
                if move not in board.legal_moves:
                    continue
                san = board.san(move)
                board.push(move)
            except (ValueError, TypeError):
                continue
            key = position_key(board)
            if (node.id, key) in existing:
                continue
            white, draws, black = (int(mv.get(k, 0) or 0) for k in ("white", "draws", "black"))
            total = white + draws + black
            wins = white if color == "white" else black
            score = _score(wins, draws, total)
            existing.add((node.id, key))
            edges.append(
                OpeningEdge(
                    source=node.id, target=key, san=san, games=0, score=score, master_only=True
                )
            )
            if key not in known:
                known.add(key)
                op = lookup(board)
                nodes.append(
                    OpeningNode(
                        id=key,
                        label=move_label(node.depth + 1, san),
                        san=san,
                        fen=board.fen(),
                        depth=node.depth + 1,
                        games=0,
                        wins=0,
                        draws=0,
                        losses=0,
                        score=score,
                        name=op.name if op else None,
                        eco=op.eco if op else None,
                        master_only=True,
                    )
                )
            added += 1


# ---------- piece destination heatmap ----------


def parse_piece(piece: str) -> tuple[chess.Color, chess.Square]:
    """'bf8' -> (BLACK, F8). The square must hold a piece of that colour at the start."""
    text = piece.strip().lower()
    if len(text) != 3 or text[0] not in "wb":
        raise ValueError("기물은 색 문자(w/b)와 시작 칸으로 지정합니다. 예: bf8")
    colour = chess.WHITE if text[0] == "w" else chess.BLACK
    try:
        square = chess.parse_square(text[1:])
    except ValueError as exc:
        raise ValueError(f"{text[1:]}는 칸 이름이 아닙니다") from exc
    start = chess.Board().piece_at(square)
    if start is None or start.color != colour:
        raise ValueError(
            f"시작 국면의 {text[1:]}에는 {COLOR_NAMES_KO[_cn(colour)]} 기물이 없습니다"
        )
    return colour, square


def _cn(colour: chess.Color) -> str:
    return "white" if colour == chess.WHITE else "black"


def piece_label(colour: chess.Color, square: chess.Square) -> str:
    piece = chess.Board().piece_at(square)
    name = PIECE_NAMES_KO[piece.piece_type] if piece else "기물"
    return f"{COLOR_NAMES_KO[_cn(colour)]} {chess.square_name(square)} {name}"


def track_piece(
    moves: tuple[chess.Move, ...] | list[chess.Move], start: chess.Square, through_move: int
) -> chess.Square:
    """Square the piece that started on `start` stands on after Black's `through_move`-th move
    (or at the end of a shorter game). A captured piece reports the square it was taken on."""
    board = chess.Board()
    sq = start
    for move in moves[: 2 * through_move]:
        if move.to_square == sq:
            return sq
        if board.is_en_passant(move):
            captured = chess.square(
                chess.square_file(move.to_square), chess.square_rank(move.from_square)
            )
            if captured == sq:
                return sq
        if move.from_square == sq:
            sq = move.to_square
        elif board.is_castling(move):
            rank = chess.square_rank(move.from_square)
            if board.is_kingside_castling(move):
                rook_from, rook_to = chess.square(7, rank), chess.square(5, rank)
            else:
                rook_from, rook_to = chess.square(0, rank), chess.square(3, rank)
            if sq == rook_from:
                sq = rook_to
        board.push(move)
    return sq


def piece_heatmap(
    games: Iterable[GameLike], color: Color, piece: str, through_move: int = 15
) -> PieceHeatmap:
    """Where the piece that starts on `piece` (colour letter + square, e.g. 'bf8') stands after
    move `through_move`, as a distribution over squares that sums to 1."""
    colour, start = parse_piece(piece)
    counts: Counter[str] = Counter()
    n = 0
    for game in parse_games(games, color):
        counts[chess.square_name(track_piece(game.moves, start, through_move))] += 1
        n += 1
    squares = {
        name: round(c / n, 4) for name, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    }
    if squares:
        # Re-normalise so rounding never leaves the sum off by a hair.
        biggest = next(iter(squares))
        squares[biggest] = round(squares[biggest] + (1.0 - sum(squares.values())), 4)
    return PieceHeatmap(
        piece=piece_label(colour, start), squares=squares, games=n, through_move=through_move
    )


# ---------- pawn break timing ----------


@dataclass(frozen=True)
class PawnBreak:
    label: str
    side: Color
    target: chess.Square


BREAKS: tuple[PawnBreak, ...] = (
    PawnBreak("…d5", "black", chess.D5),
    PawnBreak("…b5", "black", chess.B5),
    PawnBreak("…e5", "black", chess.E5),
    PawnBreak("…f5", "black", chess.F5),
    PawnBreak("…c5", "black", chess.C5),
    PawnBreak("e4-e5", "white", chess.E5),
    PawnBreak("d4-d5", "white", chess.D5),
    PawnBreak("f4-f5", "white", chess.F5),
    PawnBreak("g2-g4", "white", chess.G4),
    PawnBreak("c4-c5", "white", chess.C5),
    PawnBreak("b2-b4", "white", chess.B4),
)
"""A break is a non-capturing pawn push by that side landing on the target square; the label
names the usual origin but g3-g4 counts for 'g2-g4' just the same."""

BREAK_FROM_MOVE = 10
BREAK_TO_MOVE = 30
STRUCTURE_MOVE = 12
"""Move after which the position is classified when a structure filter is requested."""


def first_breaks(moves: tuple[chess.Move, ...] | list[chess.Move]) -> dict[str, int]:
    """Break label -> move number of its first occurrence in the game."""
    board = chess.Board()
    found: dict[str, int] = {}
    for ply, move in enumerate(moves, 1):
        piece = board.piece_at(move.from_square)
        if piece is not None and piece.piece_type == chess.PAWN and not board.is_capture(move):
            side = _cn(piece.color)
            for brk in BREAKS:
                if brk.side == side and brk.target == move.to_square and brk.label not in found:
                    found[brk.label] = (ply + 1) // 2
        board.push(move)
    return found


def _structure_key(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        key = value.get("key")
        return str(key) if key is not None else None
    if isinstance(value, list | tuple):
        return _structure_key(value[0]) if value else None
    key = getattr(value, "key", None)
    return str(key) if key is not None else None


def _structure_filter(structure: str | None) -> Callable[[ParsedGame], bool] | None:
    """Predicate keeping games whose position at move STRUCTURE_MOVE classifies as `structure`.
    None when no filter applies (no structure asked, or the classifier module is absent)."""
    if not structure:
        return None
    try:
        from chess_tutor.structure import classify
    except ImportError:
        log.warning("chess_tutor.structure is unavailable; ignoring structure=%s", structure)
        return None
    wanted = structure.strip().lower()

    def keep(game: ParsedGame) -> bool:
        board = chess.Board()
        for move in game.moves[: 2 * STRUCTURE_MOVE]:
            board.push(move)
        try:
            key = _structure_key(classify(board))
        except Exception as exc:  # noqa: BLE001 - a classifier bug must not break the chart
            log.warning("structure classify failed: %s", exc)
            return False
        return key is not None and key.lower() == wanted

    return keep


def break_timing(
    games: Iterable[GameLike], color: Color, structure: str | None = None
) -> list[BreakTiming]:
    """For every catalogued break, a histogram of the move number where it first happened
    (moves BREAK_FROM_MOVE..BREAK_TO_MOVE). `my_avg` is filled for the user's own breaks only;
    `master_median` stays None (no master data here)."""
    parsed = parse_games(games, color)
    keep = _structure_filter(structure)
    if keep is not None:
        parsed = [g for g in parsed if keep(g)]
    width = BREAK_TO_MOVE - BREAK_FROM_MOVE + 1
    hists: dict[str, list[int]] = {b.label: [0] * width for b in BREAKS}
    for game in parsed:
        for label, move_no in first_breaks(game.moves).items():
            if BREAK_FROM_MOVE <= move_no <= BREAK_TO_MOVE:
                hists[label][move_no - BREAK_FROM_MOVE] += 1
    out: list[BreakTiming] = []
    for brk in BREAKS:
        hist = hists[brk.label]
        my_avg: float | None = None
        if brk.side == color and sum(hist) > 0:
            weighted = sum((BREAK_FROM_MOVE + i) * c for i, c in enumerate(hist))
            my_avg = round(weighted / sum(hist), 2)
        out.append(
            BreakTiming(
                label=brk.label,
                side=brk.side,
                histogram=hist,
                from_move=BREAK_FROM_MOVE,
                to_move=BREAK_TO_MOVE,
                my_avg=my_avg,
                master_median=None,
            )
        )
    return out
