import chess
import pytest

from chess_tutor.openings import classify_game, lookup, name_ko, names_ko, rows


def test_lookup_by_transposition() -> None:
    a = chess.Board()
    for san in ["e4", "c5", "Nf3", "d6"]:
        a.push_san(san)
    b = chess.Board()
    for san in ["Nf3", "c5", "e4", "d6"]:
        b.push_san(san)
    assert lookup(a) is not None
    assert lookup(a) == lookup(b)
    assert "Sicilian" in lookup(a).name


def test_classify_game_reports_where_the_book_ends() -> None:
    board = chess.Board()
    moves = []
    for san in [
        "e4",
        "c5",
        "Nf3",
        "e6",
        "d4",
        "cxd4",
        "Nxd4",
        "Nf6",
        "Nc3",
        "d6",
        "a3",
        "h6",
        "h3",
        "a6",
    ]:
        mv = board.parse_san(san)
        moves.append(mv)
        board.push(mv)
    opening, left_at = classify_game(moves)
    assert opening is not None and opening.eco.startswith("B8")
    assert 10 <= left_at <= 14


# ---------- Korean names (plan §10.5) ----------

SPOT_CHECKS: dict[str, str] = {
    "King's Pawn Game": "킹즈 폰 게임",
    "Ruy Lopez": "루이 로페즈",
    "Ruy Lopez: Morphy Defense": "루이 로페즈: 모피 방어",
    "Ruy Lopez: Exchange Variation": "루이 로페즈: 익스체인지 변화",
    "Ruy Lopez: Berlin Defense": "루이 로페즈: 베를린 방어",
    "Sicilian Defense": "시실리안 디펜스",
    "Sicilian Defense: Najdorf Variation": "시실리안 디펜스: 나이도르프 변화",
    "Sicilian Defense: Dragon Variation, Yugoslav Attack": (
        "시실리안 디펜스: 드래곤 변화, 유고슬라브 어택"
    ),
    "French Defense: Winawer Variation": "프렌치 디펜스: 위나워 변화",
    "Caro-Kann Defense: Advance Variation": "카로칸 디펜스: 어드밴스 변화",
    "Scandinavian Defense": "스칸디나비안 디펜스",
    "Pirc Defense": "피르츠 디펜스",
    "Alekhine Defense": "알레힌 디펜스",
    "Italian Game: Giuoco Piano": "이탈리안 게임: 지오코 피아노",
    "Queen's Gambit Declined: Exchange Variation": "퀸즈 갬빗 디클라인드: 익스체인지 변화",
    "Queen's Gambit Accepted": "퀸즈 갬빗 억셉티드",
    "Nimzo-Indian Defense: Classical Variation": "님조 인디언 디펜스: 클래시컬 변화",
    # 킹스, not 킹즈: services/setups.py and structure.py already call this family that.
    "King's Indian Defense: Fianchetto Variation": "킹스 인디언 디펜스: 피안케토 변화",
    "Grünfeld Defense: Exchange Variation": "그륀펠트 디펜스: 익스체인지 변화",
    "English Opening": "잉글리시 오프닝",
}
"""Twenty names pinned by hand. Half of them also pin the "Defense" rule below."""


def test_every_book_name_has_a_korean_name() -> None:
    """The asset is built from these very TSVs, so a name without an entry means the asset is
    stale (`scripts/translate_openings.py` was not re-run after the book changed)."""
    table = names_ko()
    missing = sorted({row.name for row in rows() if not table.get(row.name, "").strip()})
    assert not missing, f"{len(missing)} names without a Korean name, e.g. {missing[:5]}"


@pytest.mark.parametrize(("english", "korean"), sorted(SPOT_CHECKS.items()))
def test_the_pinned_names_read_the_way_they_should(english: str, korean: str) -> None:
    assert name_ko(english) == korean


def test_defense_is_the_family_in_english_letters_and_the_variation_in_korean() -> None:
    """The one rule (scripts/translate_openings.py): 디펜스 for the family that opens the name,
    방어 for a Defense named inside it."""
    assert name_ko("Sicilian Defense: Alapin Variation").startswith("시실리안 디펜스:")
    assert name_ko("Ruy Lopez: Morphy Defense").endswith("모피 방어")
    assert name_ko("Semi-Slav Defense: Chigorin Defense") == "세미슬라브 디펜스: 치고린 방어"


def test_an_unknown_name_stays_as_it_is() -> None:
    """A name from outside the bundled book (a master-explorer line, a newer TSV) is shown in
    English rather than dropped."""
    assert (
        name_ko("Nonexistent Opening: Imaginary Gambit") == "Nonexistent Opening: Imaginary Gambit"
    )
    assert name_ko("") == ""
