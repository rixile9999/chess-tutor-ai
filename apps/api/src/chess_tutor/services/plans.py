"""Strategy knowledge base and plan matching (layer 3).

PLANS holds, per pawn structure and per side, a handful of typical plans written in our own
words: a title, the condition under which the plan makes sense, and hint moves in SAN-like
form. Hint syntax (parsed by :func:`parse_hint`):

* ``'...d5'`` / ``'d5'``      a pawn to d5 (the leading dots are decoration for Black)
* ``'Nd5'``, ``'Bxf6'``       piece letter + destination; captures are ignored when matching
* ``'b4-b5'`` / ``'f4-f5'``   several steps in sequence (all steps count as the plan's moves)
* ``'O-O'`` / ``'O-O-O'``     castling
* free words (``'minority attack b4-b5'``) are skipped, only SAN-like tokens count

Matching is deterministic: a hint move equals a board move when the piece type and the
destination square agree (captures, checks and promotions are ignored). The knowledge base
is written in the canonical orientation of each structure (for example White owns the IQP);
:func:`mirrored` says when a position holds the same structure with colours swapped and
:func:`match_plans` then flips sides and squares.

Prose rule for entries: titles and conditions name squares and pieces only, never a colour
word or a rank number, so that mirroring stays truthful.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import chess

from chess_tutor.schemas import Color, Plan

Side = Literal["white", "black"]
PlanStatus = Literal["pv_match", "later", "executed", "unavailable"]

STRUCTURE_NAMES: dict[str, str] = {
    "hedgehog": "헤지호그",
    "iqp": "고립 퀸폰(IQP)",
    "hanging_pawns": "행잉 폰",
    "carlsbad": "칼스바드 구조",
    "maroczy": "마로치 바인드",
    "french_chain": "프렌치 폰 체인",
    "kid": "킹스 인디언 구조",
    "benoni": "베노니 구조",
    "open_center": "오픈 센터",
    "slav_caro": "슬라브/카로칸 구조",
    "stonewall": "스톤월",
    "scheveningen": "셰베닝겐",
    "closed_center": "닫힌 센터",
    "boleslavsky_hole": "볼레슬라프스키 구멍(d5)",
}


@dataclass(frozen=True)
class PlanSpec:
    title: str
    condition: str
    hints: list[str]


def _p(title: str, condition: str, hints: list[str]) -> PlanSpec:
    return PlanSpec(title, condition, hints)


PLANS: dict[str, dict[Side, list[PlanSpec]]] = {
    "hedgehog": {
        "black": [
            _p(
                "...d5 브레이크",
                "d5 칸을 공격하는 기물 수가 지키는 기물 수 이상이고, "
                "e6 폰이 되잡을 준비가 됐을 때. 중앙이 열리면 b7 비숍과 c8 룩이 살아납니다",
                ["...d5"],
            ),
            _p(
                "...b5 브레이크",
                "...a6가 먼저 놓여 cxb5 뒤 axb5로 되잡을 수 있을 때. c4 폰을 흔들어 c파일을 엽니다",
                ["...a6 ...b5"],
            ),
            _p(
                "세 번째 줄 뒤 기물 정렬",
                "...Qb8, ...Rc8, ...Rfd8(또는 ...Rfe8), ...Bf8로 폰 뒤에 기물을 모아 두고 "
                "브레이크의 순간을 기다립니다. 급할 것이 없을 때의 기본 배치",
                ["...Qb8", "...Rc8", "...Rd8", "...Bf8"],
            ),
            _p(
                "...Ne5로 f3 비숍 교환 유도",
                "d5를 지키는 f3 비숍을 빼내면 ...d5 브레이크의 계산이 유리해집니다",
                ["...Ne5"],
            ),
            _p(
                "...Kh8, ...Rg8, ...g5 킹사이드 역습",
                "상대가 퀸사이드에 기물을 몰아 두었고 f4 폰이 없을 때. "
                "g5로 e4를 지키는 기물을 밀어냅니다",
                ["...Kh8 ...Rg8 ...g5"],
            ),
        ],
        "white": [
            _p(
                "e4-e5 밀어내기",
                "...d5를 영구히 막고 공간을 넓힙니다. f4로 지원한 뒤에 두어야 e5 폰이 버팁니다",
                ["f4 e5"],
            ),
            _p(
                "Nd5 점프 또는 희생",
                "e7 비숍과 f6 나이트를 동시에 건드립니다. exd5 뒤 e6 폰이 밀리면 f6가 고정됩니다",
                ["Nd5"],
            ),
            _p(
                "킹사이드 공간 확장 g4-g5",
                "g5로 f6 나이트를 밀어내면 d5의 수비수가 하나 줄어듭니다. 킹이 안전할 때만",
                ["g4-g5"],
            ),
            _p(
                "f4-f5로 e6 압박",
                "f5가 e6와 부딪히면 exf5 뒤 d5 칸이 완전히 넘어옵니다",
                ["f4-f5"],
            ),
            _p(
                "a4-a5로 b6 고정",
                "...b5 브레이크를 미리 막고 b6 폰을 c파일 룩의 목표로 남깁니다",
                ["a4-a5"],
            ),
        ],
    },
    "iqp": {
        "white": [
            _p(
                "d4-d5 브레이크",
                "d5 칸에 상대 기물이 없고, 밀었을 때 열리는 대각선과 파일이 우리 기물을 향할 때. "
                "고립폰을 약점에서 무기로 바꾸는 순간",
                ["d5"],
            ),
            _p(
                "Ne5 전초와 킹사이드 공격",
                "e5 나이트를 f7과 g6를 겨냥하는 창끝으로 씁니다. Qd3, Bc2 배터리가 뒤따릅니다",
                ["Ne5", "Qd3", "Bc2"],
            ),
            _p(
                "룩 리프트 Rd3-g3/h3",
                "e5 나이트가 서 있고 킹사이드 폰이 밀리지 않았을 때 "
                "룩을 세 번째 줄로 올려 공격에 더합니다",
                ["Rd3-g3", "Rd3-h3"],
            ),
            _p(
                "Bg5로 f6 나이트 압박",
                "d5의 수비수를 묶거나 없애 d4-d5를 준비합니다",
                ["Bg5", "Bxf6"],
            ),
        ],
        "black": [
            _p(
                "d5 봉쇄",
                "나이트를 d5에 세워 고립폰을 고정합니다. 봉쇄된 폰은 뒤의 기물을 가립니다",
                ["...Nd5"],
            ),
            _p(
                "d4 압박: ...Bf6, ...Qd6, ...Rd8",
                "고립폰을 공격 기물로 묶어 상대가 수비에 매이게 합니다",
                ["...Bf6", "...Qd6", "...Rd8"],
            ),
            _p(
                "...b6, ...Bb7로 d5 통제",
                "d5 칸을 한 번 더 지켜 d4-d5 브레이크를 막습니다",
                ["...b6 ...Bb7"],
            ),
            _p(
                "기물 교환으로 엔드게임 지향",
                "기물이 줄수록 고립폰의 공격력은 사라지고 약점만 남습니다. 특히 나이트 교환",
                ["...Nxe5", "...Bxf3", "...Nxc3"],
            ),
        ],
    },
    "hanging_pawns": {
        "white": [
            _p(
                "d4-d5 브레이크",
                "d5를 지키는 상대 기물이 적을 때. 밀고 나면 c4 폰과 함께 통과폰 쌍이 됩니다",
                ["d5"],
            ),
            _p(
                "c4-c5로 공간 확장",
                "b6 폰을 건드려 퀸사이드를 엽니다. d4가 뒤처지지 않도록 지원이 먼저",
                ["c5"],
            ),
            _p(
                "Ne5와 룩 지원",
                "e5 나이트를 세우고 Rd1, Rc1으로 두 폰 뒤를 받칩니다",
                ["Ne5", "Rd1", "Rc1"],
            ),
            _p(
                "Bd3, Qe2 킹사이드 배터리",
                "폰 쌍이 중앙을 잡는 동안 비숍과 퀸으로 h7을 겨냥합니다",
                ["Bd3", "Qe2", "Qd3"],
            ),
        ],
        "black": [
            _p(
                "...b5 또는 ...e5로 폰 하나 밀어내기",
                "c4나 d4 폰이 밀리면 뒤에 남는 폰이 고립되거나 뒤처집니다",
                ["...b5", "...e5"],
            ),
            _p(
                "c4 폰 압박: ...Rc8, ...Ba6, ...Qc7",
                "c파일에 기물을 모아 c4를 움직이지 못하게 묶습니다",
                ["...Rc8", "...Ba6", "...Qc7"],
            ),
            _p(
                "d4 압박: ...Nc6, ...Bb7",
                "두 폰이 서로 지키지 못하는 순간을 노려 d4를 공격합니다",
                ["...Nc6", "...Bb7"],
            ),
            _p(
                "...Ne4 전초",
                "c3와 d2를 동시에 겨냥하는 나이트로 폰 쌍의 지원을 끊습니다",
                ["...Ne4"],
            ),
        ],
    },
    "carlsbad": {
        "white": [
            _p(
                "소수 공격 b4-b5",
                "c파일이 반쯤 열려 있고 b5xc6 뒤 c6 폰이 약점으로 남을 때. "
                "a4가 함께 갈 때가 많습니다",
                ["minority attack b4-b5"],
            ),
            _p(
                "f3, e4 중앙 브레이크",
                "Nge2로 나이트를 재배치한 뒤 e4로 중앙을 엽니다. "
                "d5 폰이 교환되면 d4가 고립되니 계산이 먼저",
                ["Ne2", "f3 e4"],
            ),
            _p(
                "Ne5와 f4 킹사이드",
                "e5 나이트를 f4로 받치고 킹사이드 폰을 밀어 공격합니다",
                ["Ne5", "f4"],
            ),
            _p(
                "룩 배치 Rb1, Rc1",
                "b파일과 c파일에 룩을 두어 소수 공격의 뒤를 받칩니다",
                ["Rb1", "Rc1"],
            ),
        ],
        "black": [
            _p(
                "...Ne4 전초",
                "e4 나이트로 f2를 겨냥하고 소수 공격에 쓰일 기물을 킹사이드에 붙잡아 둡니다",
                ["...Ne4"],
            ),
            _p(
                "킹사이드 공격 ...f5, ...g5",
                "...Ne4가 서 있을 때 f5로 지원하고 g5로 나이트를 밀어냅니다",
                ["...f5", "...g5"],
            ),
            _p(
                "나이트 재배치 ...Nf8-g6/e6",
                "d7 나이트를 f8을 거쳐 g6나 e6로 옮겨 e4 전초와 킹사이드를 함께 지원합니다",
                ["...Nf8-g6", "...Nf8-e6"],
            ),
            _p(
                "...a5로 b4 늦추기",
                "소수 공격의 첫 수 b4를 어렵게 만들어 시간을 법니다",
                ["...a5"],
            ),
        ],
    },
    "maroczy": {
        "white": [
            _p(
                "Nd5 점령",
                "c4와 e4 폰이 지키는 d5에 나이트를 세웁니다. 교환되면 exd5로 e파일 압박",
                ["Nd5"],
            ),
            _p(
                "퀸사이드 확장 b2-b4",
                "Rc1, a3 뒤 b4로 c5 칸을 잡고 ...b5 브레이크의 여지를 없앱니다",
                ["b4", "a4"],
            ),
            _p(
                "f4-f5로 e6 압박",
                "f5가 e6와 부딪히면 d5 칸이 완전히 넘어옵니다",
                ["f4-f5"],
            ),
            _p(
                "e4-e5 밀어내기",
                "f6 나이트를 쫓아 d6를 약점으로 만듭니다. d6가 지켜지지 않을 때만",
                ["e5"],
            ),
        ],
        "black": [
            _p(
                "...b5 브레이크",
                "...a6와 ...Rb8을 두어 준비한 뒤 c4 폰을 흔듭니다. 바인드를 깨는 첫 번째 방법",
                ["...a6 ...b5"],
            ),
            _p(
                "...f5 브레이크",
                "e4 폰을 건드려 바인드를 깨는 두 번째 방법. "
                "킹사이드가 열리니 e5 칸을 먼저 지켜야 합니다",
                ["...f5"],
            ),
            _p(
                "...Nd7-c5 재배치와 ...Bxc3",
                "c5 나이트로 e4를 겨냥하고 c3 나이트를 교환해 d5 통제를 약화시킵니다",
                ["...Nd7-c5", "...Bxc3"],
            ),
            _p(
                "c파일 압박 ...Qa5, ...Rc8",
                "c4 폰과 c3 나이트를 c파일에서 묶어 두어 상대의 자유를 줄입니다",
                ["...Qa5", "...Rc8"],
            ),
        ],
    },
    "french_chain": {
        "white": [
            _p(
                "킹사이드 공격 Bd3, Qg4, f4-f5",
                "e5 폰이 f6와 d6를 통제하는 동안 킹사이드에 기물을 모읍니다",
                ["Bd3", "Qg4", "f4-f5"],
            ),
            _p(
                "d4 유지: Be2, Nc3, O-O",
                "c5 압박에 대비해 d4를 지키는 기물을 먼저 갖춥니다",
                ["Be2", "O-O", "Nc3"],
            ),
            _p(
                "Ng5와 Qh5로 h7 공격",
                "f6 나이트가 없을 때 h7이 약점입니다",
                ["Ng5", "Qh5"],
            ),
            _p(
                "a3, b4로 c5 압박",
                "c5 폰을 밀어내거나 교환시켜 d4의 부담을 덜고 퀸사이드 공간을 얻습니다",
                ["a3 b4"],
            ),
        ],
        "black": [
            _p(
                "...c5 브레이크로 d4 공격",
                "체인의 밑동 d4를 ...Nc6, ...Qb6로 함께 압박합니다",
                ["...c5", "...Nc6", "...Qb6"],
            ),
            _p(
                "...f6 브레이크로 e5 공격",
                "e5 폰이 교환되면 e파일이 열리고 e6 폰이 뒤처질 수 있으니 계산이 먼저",
                ["...f6"],
            ),
            _p(
                "나쁜 비숍 교환 ...Bd7-b5",
                "e6 폰 뒤에 갇힌 비숍을 b5로 빼내어 교환합니다",
                ["...Bd7-b5"],
            ),
            _p(
                "...Nh6-f5로 d4 압박",
                "f5 나이트가 d4를 겨냥해 상대가 d4 방어에 묶이게 합니다",
                ["...Nh6-f5", "...Ne7-f5"],
            ),
        ],
    },
    "kid": {
        "white": [
            _p(
                "c4-c5 브레이크",
                "퀸사이드에서 먼저 열어 c파일을 잡습니다. b4가 앞서면 더 안전합니다",
                ["b4", "c5"],
            ),
            _p(
                "나이트 재배치 Ne1-d3",
                "d3 나이트가 c5 브레이크를 지원하고 f4 칸도 지킵니다",
                ["Ne1-d3", "Nd2"],
            ),
            _p(
                "g2-g4로 ...f5 억제",
                "킹사이드 폰 진격의 첫 수를 미리 막아 상대 공격을 늦춥니다",
                ["g4"],
            ),
            _p(
                "c파일 침투 Nb5, Rc7",
                "c파일이 열린 뒤 나이트와 룩으로 c7과 d6를 파고듭니다",
                ["Rc1", "Nb5", "Rc7"],
            ),
        ],
        "black": [
            _p(
                "...f5 브레이크",
                "...Ne8나 ...Nd7로 f6 나이트를 옮긴 뒤 f5로 킹사이드를 엽니다",
                ["...Ne8 ...f5", "...Nd7 ...f5"],
            ),
            _p(
                "...f4 후 ...g5-g4 폰 폭풍",
                "e5 폰이 중앙을 잠근 채로 킹사이드 폰을 밀어 상대 킹을 엽니다",
                ["...f4", "...g5-g4"],
            ),
            _p(
                "수비 재배치 ...Rf7, ...Bf8, ...Rg7",
                "퀸사이드 침투를 받아 내면서 킹사이드 공격을 이어 갑니다",
                ["...Rf7", "...Bf8", "...Rg7"],
            ),
            _p(
                "...a5, ...c5로 퀸사이드 늦추기",
                "b4와 c5 브레이크를 어렵게 만들어 시간을 법니다",
                ["...a5", "...c5"],
            ),
        ],
    },
    "benoni": {
        "white": [
            _p(
                "e4-e5 브레이크",
                "f4로 준비한 뒤 e5로 d6를 흔듭니다. 성공하면 d5 폰이 통과폰이 됩니다",
                ["f4", "e5"],
            ),
            _p(
                "a4와 Nc4로 b5 억제",
                "...b5를 막고 c4 나이트로 d6를 압박합니다",
                ["a4", "Nc4"],
            ),
            _p(
                "Bf4로 d6 압박",
                "d6가 약점이 되면 e5 브레이크의 계산이 좋아집니다",
                ["Bf4"],
            ),
            _p(
                "킹사이드 폰 진격 f4-f5",
                "Bd3, h3 뒤 f5로 e6 칸과 g6를 흔듭니다",
                ["f4-f5", "Bd3", "h3"],
            ),
        ],
        "black": [
            _p(
                "...b5 브레이크",
                "...a6와 ...Rb8로 준비한 뒤 퀸사이드 다수 폰을 움직입니다",
                ["...a6 ...b5"],
            ),
            _p(
                "...Nd7-e5 중앙 전초",
                "e5 나이트로 c4와 f3를 겨냥합니다. f4에 밀리면 g4로",
                ["...Nd7-e5"],
            ),
            _p(
                "e4 압박 ...Re8와 ...f5",
                "e파일 룩과 g7 비숍으로 e4를 노리고 f5로 흔듭니다",
                ["...Re8", "...f5"],
            ),
            _p(
                "...c4 후 ...Nc5",
                "c4 폰으로 공간을 얻고 c5 나이트로 e4와 d3를 겨냥합니다",
                ["...c4 ...Nc5"],
            ),
        ],
    },
    "open_center": {
        "white": [
            _p(
                "열린 파일 점령 Rd1, Rc1",
                "중앙 폰이 없을 때 파일을 먼저 잡는 쪽이 주도권을 쥡니다",
                ["Rd1", "Rc1", "Re1"],
            ),
            _p(
                "7열 침투 Rd7, Rc7",
                "열린 파일을 지나 룩을 7열로 넣어 폰과 킹을 함께 압박합니다",
                ["Rd7", "Rc7"],
            ),
            _p(
                "기물 활동 우위로 킹 공격",
                "Ng5, Qh5, Qg4처럼 열린 대각선과 파일을 타고 킹을 직접 겨냥합니다",
                ["Ng5", "Qh5", "Qg4"],
            ),
            _p(
                "킹 중앙화",
                "기물이 줄면 킹을 e2, f2로 올려 엔드게임에 대비합니다",
                ["Kf1-e2", "Kf2"],
            ),
        ],
        "black": [
            _p(
                "열린 파일 점령 ...Rd8, ...Rc8",
                "중앙 폰이 없을 때 파일을 먼저 잡는 쪽이 주도권을 쥡니다",
                ["...Rd8", "...Rc8", "...Re8"],
            ),
            _p(
                "2열 침투 ...Rd2, ...Rc2",
                "열린 파일을 지나 룩을 2열로 넣어 폰과 킹을 함께 압박합니다",
                ["...Rd2", "...Rc2"],
            ),
            _p(
                "기물 활동 우위로 킹 공격",
                "...Ng4, ...Ne4, ...Qh4처럼 열린 선을 타고 킹을 직접 겨냥합니다",
                ["...Ng4", "...Ne4", "...Qh4"],
            ),
            _p(
                "킹 중앙화",
                "기물이 줄면 킹을 e7, f7로 올려 엔드게임에 대비합니다",
                ["...Kf8-e7", "...Kf7"],
            ),
        ],
    },
    "slav_caro": {
        "white": [
            _p(
                "중앙과 퀸사이드 공간 e4, c5",
                "e4로 d5를 건드리거나 c5로 퀸사이드를 잠급니다",
                ["e4", "c5"],
            ),
            _p(
                "Ne5 전초와 f4 지원",
                "e5 나이트를 f4로 받쳐 킹사이드 공격의 발판으로 씁니다",
                ["Ne5", "f4"],
            ),
            _p(
                "Qb3로 b7 압박",
                "c8 비숍이 f5나 g4로 나간 뒤 b7이 비면 퀸으로 찌릅니다",
                ["Qb3"],
            ),
            _p(
                "b4-b5로 c6 공격",
                "c6 폰을 흔들어 d5의 지원을 끊습니다",
                ["b4-b5"],
            ),
        ],
        "black": [
            _p(
                "...c5 브레이크",
                "d4를 건드려 중앙을 엽니다. 기물 전개가 끝난 뒤가 안전합니다",
                ["...c5"],
            ),
            _p(
                "...e5 브레이크",
                "...Nd7와 ...Qc7로 준비한 뒤 e5로 중앙을 엽니다",
                ["...Nd7", "...Qc7", "...e5"],
            ),
            _p(
                "밝은 칸 비숍 전개 ...Bf5/...Bg4 후 ...e6",
                "e6를 두기 전에 비숍을 먼저 빼내야 나쁜 비숍이 되지 않습니다",
                ["...Bf5", "...Bg4", "...e6"],
            ),
            _p(
                "...dxc4 후 ...b5로 폰 유지",
                "c4 폰을 잡고 ...b5, ...a6로 지켜 퀸사이드에서 폰을 하나 더 가집니다",
                ["...dxc4 ...b5", "...a6"],
            ),
        ],
    },
    "stonewall": {
        "white": [
            _p(
                "어두운 칸 비숍 교환 Bf4 또는 Ba3",
                "d6 비숍은 스톤월의 가장 좋은 기물입니다. 교환하면 e5 칸이 약해집니다",
                ["Bf4", "Ba3", "Bxd6"],
            ),
            _p(
                "Ne5 전초",
                "d5와 f5 사이 e5는 폰이 닿지 않는 칸입니다. 나이트를 세워 둡니다",
                ["Ne5"],
            ),
            _p(
                "퀸사이드 공간 c4-c5, b4-b5",
                "상대 기물이 킹사이드에 몰린 동안 퀸사이드에서 폰을 밀어 c6를 공격합니다",
                ["c5", "b4-b5"],
            ),
            _p(
                "f3와 e4 브레이크",
                "e4로 중앙을 열면 f5 폰이 약점으로 드러납니다",
                ["f3 e4"],
            ),
        ],
        "black": [
            _p(
                "...Ne4 전초",
                "d5와 f5가 지키는 e4에 나이트를 세워 킹사이드 공격의 발판으로 씁니다",
                ["...Ne4"],
            ),
            _p(
                "킹사이드 공격 ...g5, ...Rf6-h6",
                "f5 폰 뒤에서 룩을 f6을 거쳐 h6로 올리고 g5로 폰을 밉니다",
                ["...g5", "...Rf6-h6", "...Qh5"],
            ),
            _p(
                "나쁜 비숍 교환 ...Bd7-e8-h5",
                "폰 사슬에 갇힌 비숍을 h5로 빼내어 교환합니다",
                ["...Bd7-e8-h5"],
            ),
            _p(
                "...b6, ...Bb7 후 ...c5",
                "비숍을 긴 대각선에 두고 c5로 중앙을 열어 d4를 공격합니다",
                ["...b6 ...Bb7", "...c5"],
            ),
        ],
    },
    "scheveningen": {
        "white": [
            _p(
                "f4-f5로 e6 압박",
                "e6 폰이 밀리면 d5 칸이 넘어오고 e파일이 열립니다",
                ["f4-f5"],
            ),
            _p(
                "킹사이드 폰 폭풍 g4-g5",
                "g5로 f6 나이트를 쫓아 킹의 수비수를 줄입니다. h4가 뒤따릅니다",
                ["g4-g5", "h4"],
            ),
            _p(
                "e4-e5 브레이크",
                "f6 나이트를 밀어내고 d6를 약점으로 만듭니다. f4가 e5를 받칠 때",
                ["e5"],
            ),
            _p(
                "Qf3, Qg3와 Bd3 배터리",
                "퀸과 비숍을 킹사이드로 돌려 h7과 g7을 겨냥합니다",
                ["Qf3", "Qg3", "Bd3"],
            ),
        ],
        "black": [
            _p(
                "...b5 후 ...b4로 c3 나이트 쫓기",
                "e4의 수비수를 밀어내 ...d5 브레이크를 준비합니다",
                ["...b5-b4"],
            ),
            _p(
                "...d5 브레이크",
                "e4를 건드려 중앙을 엽니다. e4 폰을 지키는 기물보다 공격하는 기물이 많을 때",
                ["...d5"],
            ),
            _p(
                "...e5 브레이크",
                "d4 나이트를 쫓아 f4 폰과 부딪히게 합니다. d5 칸이 약해지니 계산이 먼저",
                ["...e5"],
            ),
            _p(
                "e4 압박 ...Nc5, ...Bd7-c6",
                "c5 나이트와 c6 비숍으로 e4를 두 번 겨냥합니다",
                ["...Nc5", "...Bd7-c6"],
            ),
        ],
    },
    "closed_center": {
        "white": [
            _p(
                "f4-f5 브레이크",
                "중앙이 잠겨 있으면 측면에서 엽니다. f5가 e6이나 g6와 부딪힐 때",
                ["f4-f5"],
            ),
            _p(
                "퀸사이드 공간 b4, c5",
                "폰을 밀어 c파일을 열고 룩을 넣습니다",
                ["b4", "c5"],
            ),
            _p(
                "느린 재배치 Nd2-f1-g3",
                "잠긴 중앙에서는 기물을 가장 좋은 칸으로 천천히 옮길 시간이 있습니다",
                ["Nf1-g3", "Nh2"],
            ),
            _p(
                "킹사이드 폰 진격 g4-g5",
                "h4와 함께 g5로 f6 나이트를 쫓아 킹을 엽니다",
                ["g4-g5", "h4"],
            ),
        ],
        "black": [
            _p(
                "...f5 브레이크",
                "잠긴 중앙을 측면에서 엽니다. e4 폰이 f5와 부딪힐 때",
                ["...f5"],
            ),
            _p(
                "퀸사이드 브레이크 ...c5, ...b5",
                "c5나 b5로 폰을 밀어 c파일이나 b파일을 엽니다",
                ["...c5", "...b5"],
            ),
            _p(
                "느린 재배치 ...Nd7-f8-g6",
                "나이트를 f8을 거쳐 g6로 옮겨 f4와 e5를 지킵니다",
                ["...Nf8-g6", "...Nh5"],
            ),
            _p(
                "킹사이드 폰 진격 ...g5-g4",
                "g4로 f3 나이트를 쫓아 킹을 엽니다",
                ["...g5-g4"],
            ),
        ],
    },
    "boleslavsky_hole": {
        "white": [
            _p(
                "Nd5 점령",
                "e5 폰이 비운 d5에 나이트를 세웁니다. 교환되면 exd5로 e파일 압박",
                ["Nd5"],
            ),
            _p(
                "Bg5xf6로 d5 수비수 제거",
                "f6 나이트를 없애면 d5는 온전히 우리 칸이 됩니다",
                ["Bg5", "Bxf6"],
            ),
            _p(
                "f2-f4 브레이크로 e5 압박",
                "e5 폰을 흔들어 d5 통제를 굳히고 f파일을 엽니다",
                ["f4"],
            ),
            _p(
                "a4-a5로 퀸사이드 고정",
                "...b5를 막고 b6를 약점으로 남깁니다",
                ["a4-a5"],
            ),
        ],
        "black": [
            _p(
                "...d5 브레이크",
                "...Be6와 ...Be7 뒤에 d5로 구멍을 스스로 메웁니다. e4를 건드려 중앙을 엽니다",
                ["...Be6 ...d5"],
            ),
            _p(
                "...b5, ...Bb7로 e4 압박 후 ...b4",
                "e4를 두 번 겨냥하고 b4로 c3 나이트를 쫓아 d5 통제를 약화시킵니다",
                ["...b5", "...Bb7", "...b4"],
            ),
            _p(
                "...f5 브레이크",
                "e4를 건드려 d5 나이트의 지원을 끊습니다. 킹이 안전할 때만",
                ["...f5"],
            ),
            _p(
                "d5 통제 재배치 ...Nc5, ...Nb6, ...Ne7",
                "나이트로 d5를 겨냥하거나 d5 나이트를 교환합니다",
                ["...Nd7-c5", "...Nb6", "...Ne7"],
            ),
        ],
    },
}

# ---------- hint parsing ----------

_PIECE_LETTERS = {
    "K": chess.KING,
    "Q": chess.QUEEN,
    "R": chess.ROOK,
    "B": chess.BISHOP,
    "N": chess.KNIGHT,
}
_MOVE_TOKEN = re.compile(r"^([KQRBN])?x?([a-h][1-8])$")
_CASTLE = re.compile(r"O-O-O|O-O")
_SQUARE_IN_TEXT = re.compile(r"([a-h])([1-8])(?![0-9])")


@dataclass(frozen=True)
class HintMove:
    piece_type: chess.PieceType
    to_square: chess.Square | None
    castle: str | None = None
    """'O-O' or 'O-O-O' when the hint is a castling move."""

    def matches(self, board: chess.Board, move: chess.Move) -> bool:
        """True when the move is by a piece of this type to this square (captures ignored)."""
        piece = board.piece_at(move.from_square)
        if piece is None:
            return False
        if self.castle is not None:
            if piece.piece_type != chess.KING or not board.is_castling(move):
                return False
            return board.is_kingside_castling(move) == (self.castle == "O-O")
        return piece.piece_type == self.piece_type and move.to_square == self.to_square


def parse_hint(hint: str) -> list[HintMove]:
    """Steps of a hint in order. Free text is skipped; an unparseable hint yields []."""
    steps: list[HintMove] = []
    text = hint.replace("…", "...")
    for castle in _CASTLE.findall(text):
        steps.append(HintMove(chess.KING, None, castle))
    text = _CASTLE.sub(" ", text)
    for token in re.split(r"[\s\-]+", text):
        token = token.lstrip(".").rstrip("+#!?")
        m = _MOVE_TOKEN.match(token)
        if not m:
            continue
        piece_type = _PIECE_LETTERS.get(m.group(1) or "", chess.PAWN)
        steps.append(HintMove(piece_type, chess.parse_square(m.group(2))))
    return steps


def _mirror_text(text: str, to_side: Side) -> str:
    """Flip every square mention (rank r -> 9 - r); drop the '...' decoration for White."""
    text = text.replace("…", "...")
    flipped = _SQUARE_IN_TEXT.sub(lambda m: f"{m.group(1)}{9 - int(m.group(2))}", text)
    return flipped.replace("...", "") if to_side == "white" else flipped


def _mirror_hint(hint: str, to_side: Side) -> str:
    flipped = _mirror_text(hint, to_side).replace("...", "")
    return f"...{flipped}" if to_side == "black" else flipped


# ---------- orientation ----------


def _files_with_pawns(board: chess.Board, color: chess.Color) -> set[int]:
    return {chess.square_file(sq) for sq in board.pieces(chess.PAWN, color)}


def _has_isolated_d_pawn(board: chess.Board, color: chess.Color) -> bool:
    files = _files_with_pawns(board, color)
    return 3 in files and 2 not in files and 4 not in files


def _has_hanging_cd(board: chess.Board, color: chess.Color) -> bool:
    files = _files_with_pawns(board, color)
    return {2, 3} <= files and 1 not in files and 4 not in files


def _has_stonewall(board: chess.Board, color: chess.Color) -> bool:
    ranks = (3, 2, 3) if color == chess.WHITE else (4, 5, 4)  # d4 e3 f4 / d5 e6 f5
    squares = (chess.square(3, ranks[0]), chess.square(4, ranks[1]), chess.square(5, ranks[2]))
    return all(board.piece_at(sq) == chess.Piece(chess.PAWN, color) for sq in squares)


def mirrored(structure_key: str, board: chess.Board) -> bool:
    """True when the board holds the structure with colours swapped relative to PLANS.

    Decided from the pawns on the board, not from the classifier's side label, so the
    answer is traceable to the position itself."""
    match structure_key:
        case "iqp":
            return not _has_isolated_d_pawn(board, chess.WHITE) and _has_isolated_d_pawn(
                board, chess.BLACK
            )
        case "hanging_pawns":
            return not _has_hanging_cd(board, chess.WHITE) and _has_hanging_cd(board, chess.BLACK)
        case "carlsbad":
            white_files = _files_with_pawns(board, chess.WHITE)
            black_files = _files_with_pawns(board, chess.BLACK)
            return 2 in white_files and 2 not in black_files
        case "stonewall":
            return not _has_stonewall(board, chess.BLACK) and _has_stonewall(board, chess.WHITE)
    return False


# ---------- matching ----------


def _other(side: Side) -> Side:
    return "black" if side == "white" else "white"


def _side_color(side: Side) -> chess.Color:
    return chess.WHITE if side == "white" else chess.BLACK


def walk(board: chess.Board, moves: list[chess.Move]) -> list[tuple[chess.Board, chess.Move]]:
    """(position, move) pairs along a line, stopping at the first illegal move."""
    out: list[tuple[chess.Board, chess.Move]] = []
    b = board.copy()
    for m in moves:
        if not b.is_legal(m):
            break
        out.append((b.copy(stack=False), m))
        b.push(m)
    return out


def _legal_for(board: chess.Board, color: chess.Color) -> tuple[chess.Board, list[chess.Move]]:
    b = board.copy(stack=False)
    if b.turn != color:
        b.push(chess.Move.null())
    return b, list(b.legal_moves)


def plan_specs(structure_key: str, side: Side, mirror: bool = False) -> list[PlanSpec]:
    """Plans for one side, already mirrored when asked."""
    kb = PLANS.get(structure_key)
    if kb is None:
        return []
    source_side = _other(side) if mirror else side
    if not mirror:
        return list(kb.get(source_side, []))
    return [
        PlanSpec(
            _mirror_text(spec.title, side),
            _mirror_text(spec.condition, side),
            [_mirror_hint(h, side) for h in spec.hints],
        )
        for spec in kb.get(source_side, [])
    ]


def hint_matches_move(hint: str, board: chess.Board, move: chess.Move) -> bool:
    return any(step.matches(board, move) for step in parse_hint(hint))


def match_plans(
    structure_key: str,
    side: Side,
    pvs: list[list[chess.Move]],
    board: chess.Board,
    played_moves_so_far: list[chess.Move],
    *,
    mirror: bool = False,
    start_board: chess.Board | None = None,
) -> list[Plan]:
    """Plans of ``side`` in this structure with a status decided from the data at hand.

    executed    every step of one hint was already played by this side in the game
    pv_match    a hint step occurs in one of the engine lines, played by this side
    unavailable no hint step is legal now for this side and none is in any line
    later       otherwise
    """
    color = _side_color(side)
    played = [
        (b, m)
        for b, m in walk((start_board or chess.Board()).copy(), played_moves_so_far)
        if b.turn == color
    ]
    in_lines = [(b, m) for pv in pvs for b, m in walk(board, pv) if b.turn == color]
    legal_board, legal = _legal_for(board, color)

    plans: list[Plan] = []
    for spec in plan_specs(structure_key, side, mirror):
        hints = list(spec.hints)
        parsed = [parse_hint(h) for h in hints]
        steps = [s for group in parsed for s in group]
        executed = any(
            group and all(any(s.matches(b, m) for b, m in played) for s in group)
            for group in parsed
        )
        in_pv = any(s.matches(b, m) for s in steps for b, m in in_lines)
        legal_now = any(s.matches(legal_board, m) for s in steps for m in legal)
        status: PlanStatus
        if executed:
            status = "executed"
        elif in_pv:
            status = "pv_match"
        elif not legal_now:
            status = "unavailable"
        else:
            status = "later"
        plans.append(
            Plan(
                title=spec.title,
                side=side,
                condition=spec.condition,
                status=status,
                moves_hint=hints,
            )
        )
    return plans


def break_hints(plan: Plan) -> list[str]:
    """Hints of a plan whose first step is a pawn move (the plan's break)."""
    out = []
    for hint in plan.moves_hint:
        steps = parse_hint(hint)
        if steps and steps[0].piece_type == chess.PAWN and steps[0].to_square is not None:
            out.append(hint)
    return out


def structure_name(key: str) -> str:
    return STRUCTURE_NAMES.get(key, key)


def side_of(color: chess.Color) -> Color:
    return "white" if color == chess.WHITE else "black"
