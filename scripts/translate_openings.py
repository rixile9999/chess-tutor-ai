#!/usr/bin/env python3
"""Build ``assets/opening_names_ko.json`` — the Korean name of every opening in the TSV book.

Run (from the repo root)::

    uv run --directory apps/api python ../../scripts/translate_openings.py          # fill the gaps
    uv run --directory apps/api python ../../scripts/translate_openings.py --dry-run  # coverage only
    uv run --directory apps/api python ../../scripts/translate_openings.py --report 200

Three steps, in this order (plan §10.5):

1. **Rule dictionary** (:data:`RULES`) — the ~90 generic words every third name carries
   (Opening, Variation, Gambit, Accepted, Attack, System, Line …).
2. **Proper-noun table** (:data:`PROPER`) — ~300 hand-written names of people, places and
   openings (Ruy Lopez → 루이 로페즈, Najdorf → 나이도르프). Entries may be several words long
   and the longest one wins, so "Giuoco Piano" beats "Giuoco" + "Piano".
3. **Headless Claude Code** — every name that still holds a word neither table knows goes to
   ``claude -p`` in batches of :data:`BATCH`, with both tables in the prompt so the
   transliteration stays consistent with them. JSON in, JSON out, no tools and no MCP.

The script is **idempotent**: steps 1-2 are recomputed on every run (they are free, so a fix in
the tables takes effect immediately and overrides an older LLM answer), and the LLM is only
asked about names that are not already in the output file. ``--refresh-llm`` throws the old LLM
answers away and asks again.

Two files are written next to the TSVs:

* ``opening_names_ko.json`` — ``{english: korean}``, sorted by key. This is what the runtime
  reads (``openings.name_ko``).
* ``opening_names_ko.sources.json`` — ``{english: "rule" | "table" | "llm"}``, so a reviewer can
  see which names were machine-transliterated and are worth a second look.

**The one rule for "Defense"** (plan §10.5 leaves the choice to us; both readings appear in the
mockup, so this is the rule the whole file follows):

* The **first segment** of a name — the family — takes **디펜스**: "Sicilian Defense" →
  시실리안 디펜스, "French Defense: Winawer Variation" → 프렌치 디펜스: 위나워 변화.
* **Any later segment** — a variation inside that family, after a ':' or a ',' — takes
  **방어**: "Ruy Lopez: Morphy Defense" → 루이 로페즈: 모피 방어, "Ruy Lopez: Berlin Defense,
  Improved Steinitz" → 루이 로페즈: 베를린 방어, 개량 스타이니츠.

Read it as "the family is named by its English label, a variation is described in Korean". The
same split is given to the model in the prompt, and `test_openings.py` pins both halves.

Reviewing: ``--report 200`` prints the 200 names that carry the most book positions together
with their Korean and their source. Fix anything wrong in :data:`PROPER` (or :data:`RULES`) and
run the script again — no LLM call is spent on a name the tables can now answer.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "apps" / "api" / "src" / "chess_tutor" / "assets"
OUT = ASSETS / "opening_names_ko.json"
SOURCES = ASSETS / "opening_names_ko.sources.json"

BATCH = 100
"""Names per ``claude -p`` call."""

RETRIES = 2
"""Extra calls a batch gets for the names its answer left out."""

TIMEOUT = 900
"""Seconds one call may take. A batch of 100 names is about two minutes."""

MAX_PHRASE = 4
"""Longest multi-word key looked up in :data:`PROPER`."""

# ---------- step 1: the rule dictionary ----------

RULES: dict[str, str] = {
    # structure words
    "Opening": "오프닝",
    "Game": "게임",
    "Variation": "변화",
    "Variations": "변화",
    "Deviations": "변형",
    "Line": "라인",
    "System": "시스템",
    "Formation": "포메이션",
    "Attack": "어택",
    "Counterattack": "카운터어택",
    "Defensive": "디펜시브",
    "Gambit": "갬빗",
    "Countergambit": "카운터갬빗",
    "Accepted": "억셉티드",
    "Declined": "디클라인드",
    "Deferred": "디퍼드",
    "Delayed": "지연",
    "Accelerated": "악셀러레이티드",
    "Hyperaccelerated": "하이퍼악셀러레이티드",
    "Reversed": "역",
    "Hybrid": "하이브리드",
    "Invitation": "인비테이션",
    "Trap": "함정",
    "Sacrifice": "희생",
    "Endgame": "엔드게임",
    "Mate": "메이트",
    "Check": "체크",
    "Pin": "핀",
    "Unpin": "언핀",
    "Bind": "바인드",
    "Move": "무브",
    "Order": "오더",
    "Transfer": "트랜스퍼",
    "Retreat": "리트리트",
    "Swap": "교환",
    "Push": "푸시",
    "Symmetry": "대칭",
    "Connection": "커넥션",
    "Simul": "시뮬",
    "Castling": "캐슬링",
    "Edge": "에지",
    "Original": "오리지널",
    "The": "",
    # adjectives
    "Classical": "클래시컬",
    "Neo-Classical": "네오 클래시컬",
    "Semi-Classical": "세미 클래시컬",
    "Modern": "모던",
    "Neo-Modern": "네오 모던",
    "Anti-Modern": "안티 모던",
    "Old": "올드",
    "New": "뉴",
    "Normal": "노멀",
    "Standard": "스탠더드",
    "Traditional": "트래디셔널",
    "Main": "메인",
    "Early": "얼리",
    "Quiet": "콰이어트",
    "Sharp": "샤프",
    "Positional": "포지셔널",
    "Closed": "클로즈드",
    "Open": "오픈",
    "Symmetrical": "대칭",
    "Exchange": "익스체인지",
    "Advance": "어드밴스",
    "Central": "센트럴",
    "Center": "센터",
    "Centre": "센터",
    "Double": "더블",
    "Long": "롱",
    "Short": "쇼트",
    "Big": "빅",
    "Full": "풀",
    "Flexible": "플렉시블",
    "Improved": "개량",
    "Rare": "희귀",
    "Wing": "윙",
    "Kingside": "킹사이드",
    "Queenside": "퀸사이드",
    "Eastern": "이스턴",
    "Western": "웨스턴",
    "Pseudo": "슈도",
    "Anti": "안티",
    "Semi": "세미",
    "Neo": "네오",
    "Hyper": "하이퍼",
    # pieces and pawns
    "King": "킹",
    "Queen": "퀸",
    "Rook": "룩",
    "Rooks": "룩",
    "Bishop": "비숍",
    "Knight": "나이트",
    "Knights": "나이츠",
    "Pawn": "폰",
    "Pawns": "폰",
    "Fianchetto": "피안케토",
    "Two": "투",
    "Three": "쓰리",
    "Four": "포",
    "Five": "파이브",
    "with": "",
    "and": "·",
}
"""Generic words. `Defense` is not here: :func:`_defense` decides between 디펜스 and 방어."""

POSSESSIVE: dict[str, str] = {
    "King's": "킹즈",
    "Queen's": "퀸즈",
    "Bishop's": "비숍",
    "Knight's": "나이트",
    "Lion's": "라이언",
    "Beginner's": "초심자",
}
"""Possessive forms of the rule words (a proper noun's ``'s`` is stripped, see :func:`_word`)."""

PREFIXES = ("Anti", "Semi", "Neo", "Pseudo", "Hyper", "Anglo", "Franco", "Nimzo", "Sub")
"""Hyphen prefixes joined to the next part with a space instead of a hyphen."""

# ---------- step 2: the proper-noun table ----------

PROPER: dict[str, str] = {
    # openings and families
    "Ruy Lopez": "루이 로페즈",
    "Spanish": "스패니시",
    "Sicilian": "시실리안",
    "French": "프렌치",
    "Caro-Kann": "카로칸",
    "Caro": "카로",
    "Kann": "칸",
    "Scandinavian": "스칸디나비안",
    "Pirc": "피르츠",
    "Alekhine": "알레힌",
    "Alekhine-Chatard": "알레힌-샤타르",
    "Modern Defense": "모던 디펜스",
    "Nimzowitsch": "님조위치",
    "Nimzo-Indian": "님조 인디언",
    "Nimzo-Larsen": "님조-라르센",
    "Nimzo-Dutch": "님조 더치",
    "Queen's Indian": "퀸즈 인디언",
    "King's Indian": "킹스 인디언",  # services/setups.py와 structure.py의 손글씨 한글에 맞춘다
    "Old Indian": "올드 인디언",
    "Indian": "인디언",
    "Anglo-Indian": "앵글로 인디언",
    "Anglo-Slav": "앵글로 슬라브",
    "Anglo-Dutch": "앵글로 더치",
    "Anglo-Scandinavian": "앵글로 스칸디나비안",
    "Anglo-Grünfeld": "앵글로 그륀펠트",
    "Grünfeld": "그륀펠트",
    "Neo-Grünfeld": "네오 그륀펠트",
    "Anti-Grünfeld": "안티 그륀펠트",
    "Benoni": "베노니",
    "Benoni-Indian": "베노니 인디언",
    "Anti-Benoni": "안티 베노니",
    "Benko": "벤코",
    "Volga": "볼가",
    "Catalan": "카탈란",
    "Neo-Catalan": "네오 카탈란",
    "London": "런던",
    "Colle": "콜레",
    "Torre": "토레",
    "Réti": "레티",
    "Zukertort": "주커토르트",
    "Bird": "버드",
    "Dutch": "더치",
    "Slav": "슬라브",
    "Semi-Slav": "세미슬라브",
    "Anti-Noteboom": "안티 노테붐",
    "Noteboom": "노테붐",
    "Tarrasch": "타라시",
    "Semi-Tarrasch": "세미 타라시",
    "Pseudo-Tarrasch": "슈도 타라시",
    "Petrov": "페트로프",
    "Petrov's": "페트로프",
    "Russian": "러시안",
    "Scotch": "스카치",
    "Italian": "이탈리안",
    "Giuoco Piano": "지오코 피아노",
    "Giuoco Pianissimo": "지오코 피아니시모",
    "Giuoco": "지오코",
    "Piano": "피아노",
    "Pianissimo": "피아니시모",
    "Evans": "에반스",
    "Vienna": "비엔나",
    "Philidor": "필리도르",
    "Anti-Philidor": "안티 필리도르",
    "Latvian": "라트비안",
    "Elephant": "엘리펀트",
    "Englund": "엥글룬드",
    "Albin": "알빈",
    "Chigorin": "치고린",
    "Baltic": "발틱",
    "Stonewall": "스톤월",
    "Leningrad": "레닌그라드",
    "Staunton": "스탠턴",
    "Blackmar-Diemer": "블랙마-디머",
    "Blackmar": "블랙마",
    "Diemer": "디머",
    "Diemer-Duhm": "디머-둠",
    "Veresov": "베레소프",
    "Richter-Veresov": "리히터-베레소프",
    "Jobava": "조바바",
    "Trompowsky": "트롬포프스키",
    "Budapest": "부다페스트",
    "Bogo-Indian": "보고 인디언",
    "Bogoljubow": "보골류보프",
    "Polish": "폴리시",
    "Sokolsky": "소콜스키",
    "Grob": "그롭",
    "Owen": "오웬",
    "St. George": "세인트 조지",
    "Hippopotamus": "히포포타무스",
    "Hedgehog": "헤지호그",
    "Maróczy": "마로치",
    "Panov": "파노프",
    "Panov-Botvinnik": "파노프-보트비닉",
    "Grand Prix": "그랑프리",
    "Smith-Morra": "스미스-모라",
    "Morra": "모라",
    "Alapin": "알라핀",
    "Sveshnikov": "스베시니코프",
    "Anti-Sveshnikov": "안티 스베시니코프",
    "Lasker-Pelikan": "라스커-펠리칸",
    "Pelikan": "펠리칸",
    "Najdorf": "나이도르프",
    "Dragon": "드래곤",
    "Scheveningen": "셰베닝겐",
    "Taimanov": "타이마노프",
    "Kan": "칸",
    "Paulsen": "파울센",
    "Richter-Rauzer": "리히터-라우저",
    "Rauzer": "라우저",
    "Sozin": "소진",
    "Velimirovic": "벨리미로비치",
    "Yugoslav": "유고슬라브",
    "Levenfish": "레벤피시",
    "Nyezhmetdinov-Rossolimo": "네즈메트디노프-로솔리모",
    "Rossolimo": "로솔리모",
    "Moscow": "모스크바",
    "O'Kelly": "오켈리",
    "Kalashnikov": "칼라시니코프",
    "Löwenthal": "뢰벤탈",
    "Four Knights": "포 나이츠",
    "Three Knights": "쓰리 나이츠",
    "Two Knights": "투 나이츠",
    "Halloween": "핼러윈",
    "Belgrade": "베오그라드",
    "Ponziani": "폰지아니",
    "Hungarian": "헝가리언",
    "Danish": "데니시",
    "Göring": "괴링",
    "Urusov": "우루소프",
    "Cochrane": "코크런",
    "Damiano": "다미아노",
    "Greco": "그레코",
    "Napoleon": "나폴레옹",
    "Portuguese": "포르투갈",
    "Czech": "체코",
    "Austrian": "오스트리안",
    "Australian": "오스트레일리안",
    "Argentine": "아르헨티나",
    "Mexican": "멕시칸",
    "Chinese": "차이니즈",
    "Siberian": "시베리안",
    "Norwegian": "노르웨이",
    "Swiss": "스위스",
    "American": "아메리칸",
    "Ukrainian": "우크라이니안",
    "Venezolana": "베네수엘라",
    "Basque": "바스크",
    "Irish": "아이리시",
    "English": "잉글리시",
    "Franco-Sicilian": "프랑코 시실리안",
    "Franco-Hiva": "프랑코-히바",
    "Keoni-Hiva": "케오니-히바",
    "Pterodactyl": "프테로닥틸",
    "Rhamphorhynchus": "람포링쿠스",
    "Pteranodon": "프테라노돈",
    "Quetzalcoatlus": "케찰코아틀루스",
    "Siroccopteryx": "시로코프테릭스",
    "Anhanguera": "안항구에라",
    "Mokele": "모켈레",
    "Mbembe": "음벰베",
    "Lion": "라이언",
    "Lizard": "리자드",
    "Snake": "스네이크",
    "Raptor": "랩터",
    "Horsefly": "호스플라이",
    "Sleipnir": "슬레이프니르",
    "Amazon": "아마존",
    "Kangaroo": "캥거루",
    "Beefeater": "비프이터",
    "Omega": "오메가",
    "Paleface": "페일페이스",
    "Frankenstein-Dracula": "프랑켄슈타인-드라큘라",
    "Creepy Crawly": "크리피 크롤리",
    "Drunken": "드렁큰",
    "Wayward": "웨이워드",
    "Hillbilly": "힐빌리",
    "Corkscrew": "코크스크루",
    "Sodium": "소듐",
    "Amar": "아마르",
    "Ware": "웨어",
    "Kádas": "카다시",
    "Van't Kruijs": "판트 크라위스",
    "Van Geet": "판 헤이트",
    "Clemenz": "클레멘츠",
    "Mieses": "미제스",
    "Saragossa": "사라고사",
    "Anderssen": "안데르센",
    "Anderssen's": "안데르센",
    "Barnes": "반스",
    "Desprez": "데프레",
    "Durkin": "더킨",
    "Hungarian Opening": "헝가리언 오프닝",
    "King's Gambit": "킹즈 갬빗",
    "Queen's Gambit": "퀸즈 갬빗",
    "Queen's Pawn": "퀸즈 폰",
    "King's Pawn": "킹즈 폰",
    "Bishop's Opening": "비숍 오프닝",
    "Center Game": "센터 게임",
    "Four Pawns": "포 폰",
    # people
    "Morphy": "모피",
    "Berlin": "베를린",
    "Anti-Berlin": "안티 베를린",
    "Steinitz": "스타이니츠",
    "Schliemann": "슐리만",
    "Jaenisch": "야에니시",
    "Marshall": "마샬",
    "Zaitsev": "자이체프",
    "Breyer": "브레이어",
    "Smyslov": "스미슬로프",
    "Worrall": "워럴",
    "Archangel": "아르한겔스크",
    "Arkhangelsk": "아르한겔스크",
    "Karpov": "카르포프",
    "Kasparov": "카스파로프",
    "Kasparov-Petrosian": "카스파로프-페트로시안",
    "Kramnik": "크람니크",
    "Carlsen": "칼센",
    "Fischer": "피셔",
    "Tal": "탈",
    "Spassky": "스파스키",
    "Petrosian": "페트로시안",
    "Botvinnik": "보트비닉",
    "Larsen": "라르센",
    "Keres": "케레스",
    "Korchnoi": "코르치노이",
    "Polugaevsky": "폴루가예프스키",
    "Boleslavsky": "볼레슬라프스키",
    "Averbakh": "아베르바흐",
    "Semi-Averbakh": "세미 아베르바흐",
    "Sämisch": "제미시",
    "Pseudo-Sämisch": "슈도 제미시",
    "Panno": "판노",
    "Gligoric": "글리고리치",
    "Bayonet": "베이오넷",
    "Mar del Plata": "마르델플라타",
    "Rubinstein": "루빈스타인",
    "Winawer": "위나워",
    "MacCutcheon": "맥커천",
    "McCutcheon": "맥커천",
    "Burn": "번",
    "Meran": "메란",
    "Chebanenko": "체바넨코",
    "Schlechter": "슐레히터",
    "Lasker": "라스커",
    "Capablanca": "카파블랑카",
    "Alatortsev": "알라토르체프",
    "Ragozin": "라고진",
    "Vienna Variation": "비엔나 변화",
    "Tartakower": "타르타코버",
    "Orthodox": "오소독스",
    "Cambridge Springs": "케임브리지 스프링스",
    "Carlsbad": "카를스바트",
    "Kieseritzky": "키에세리츠키",
    "Muzio": "무치오",
    "Allgaier": "알가이어",
    "Salvio": "살비오",
    "Polerio": "폴레리오",
    "Cunningham": "커닝엄",
    "Falkbeer": "팔크비어",
    "Lolli": "롤리",
    "Bledow": "블레도프",
    "Hamppe": "함페",
    "Hamppe-Allgaier": "함페-알가이어",
    "Hamppe-Muzio": "함페-무치오",
    "Max Lange": "막스 랑게",
    "Lange": "랑게",
    "Bernstein": "번스타인",
    "Bogoljubow Variation": "보골류보프 변화",
    "Blackburne": "블랙번",
    "Pillsbury": "필스버리",
    "Maróczy": "마로치",
    "Réti Opening": "레티 오프닝",
    "Mikenas": "미케나스",
    "Mikenas-Carls": "미케나스-칼스",
    "Flohr-Mikenas-Carls": "플로어-미케나스-칼스",
    "Flohr": "플로어",
    "Euwe": "에우베",
    "Janowski": "야노프스키",
    "Charousek": "하로우셰크",
    "Spielmann": "슈필만",
    "Stoltz": "스톨츠",
    "Teichmann": "타이히만",
    "Yates": "예이츠",
    "Opocensky": "오포첸스키",
    "Adorjan": "아도리안",
    "Byrne": "번",
    "Browne": "브라운",
    "Seirawan": "세이라완",
    "Suttles": "서틀스",
    "Ljubojevic": "류보예비치",
    "Unzicker": "운치커",
    "Uhlmann": "울만",
    "Ivanchuk": "이반추크",
    "Shabalov": "샤발로프",
    "Romanishin": "로마니신",
    "Sosonko": "소손코",
    "Chekhover": "체호베르",
    "Kholmov": "홀모프",
    "Geller": "겔레르",
    "Simagin": "시마긴",
    "Lipnitsky": "립니츠키",
    "Makogonov": "마코고노프",
    "Reshevsky": "레셰프스키",
    "Levitsky": "레비츠키",
    "Rabinovich": "라비노비치",
    "Romanovsky": "로마노프스키",
    "Bronstein": "브론슈타인",
    "Krause": "크라우제",
    "Wolf": "볼프",
    "Prins": "프린스",
    "Schmid": "슈미트",
    "Schmidt": "슈미트",
    "Kotov": "코토프",
    "Stein": "슈타인",
    "Gurgenidze": "구르게니제",
    "Vitolins": "비톨린시",
    "Trifunovic": "트리푸노비치",
    "Pachman": "파흐만",
    "Kupreichik": "쿠프레이치크",
    "Bastrikov": "바스트리코프",
    "Morozevich": "모로제비치",
    "Guimard": "기마르",
    "Hanham": "핸험",
    "Cozio": "코지오",
    "Cordel": "코르델",
    "Mason": "메이슨",
    "Miles": "마일스",
    "Basman": "바스먼",
    "Keene": "킨",
    "Myers": "마이어스",
    "Williams": "윌리엄스",
    "Fritz": "프리츠",
    "Ulvestad": "울베스타드",
    "Traxler": "트락슬러",
    "Wilkes-Barre": "윌크스배리",
    "Fried Liver": "프라이드 리버",
    "Boden": "보든",
    "Boden-Kieseritzky": "보든-키에세리츠키",
    "Milner-Barry": "밀너-배리",
    "Nimzowitsch Defense": "님조위치 디펜스",
    "Kmoch": "크모흐",
    "Duras": "두라스",
    "Tennison": "테니슨",
    "Stafford": "스태퍼드",
    "From's": "프롬",
    "From": "프롬",
    "Tayler": "테일러",
    "Zilbermints": "질베르민츠",
    "Hobbs-Zilbermints": "홉스-질베르민츠",
    "Zilbermints-Benoni": "질베르민츠 베노니",
    "Lemberger": "렘베르거",
    "Gedult": "게둘트",
    "Soller": "졸러",
    "Hartlaub": "하르트라우프",
    "Fajarowicz": "파야로비치",
    "Blumenfeld": "블루멘펠트",
    "Hromádka": "흐로마트카",
    "Döry": "되리",
    "Wagner": "바그너",
    "Lazard": "라자르",
    "Santasiere": "산타시에레",
    "Barcza": "바르차",
    "Kadas": "카다시",
    "Lisitsyn": "리시친",
    "Krejcik": "크레이치크",
    "Borg": "보르그",
    "Rat": "랫",
    "Robatsch": "로바치",
    "Gunderam": "군데람",
    "Leonhardt": "레온하르트",
    "Harrwitz": "하르비츠",
    "Horwitz": "호르비츠",
    "Mayet": "마예트",
    "Rosentreter": "로젠트레터",
    "Gunsberg": "군스베르크",
    "Bardeleben": "바르델레벤",
    "Schallopp": "샬로프",
    "Fraser": "프레이저",
    "Rosenthal": "로젠탈",
    "Showalter": "쇼월터",
    "Rice": "라이스",
    "Rellstab": "렐슈타프",
    "Brentano": "브렌타노",
    "Sarratt": "사라트",
    "Abbazia": "아바치아",
    "Stanley": "스탠리",
    "Kennedy": "케네디",
    "Wade": "웨이드",
    "Thorold": "소롤드",
    "Pierce": "피어스",
    "Kaufmann": "카우프만",
    "Ghulam-Kassim": "굴람-카심",
    "MacLeod": "매클라우드",
    "McDonnell": "맥도널",
    "Bourdonnais": "부르도네",
    "La Bourdonnais": "라부르도네",
    "Svenonius": "스베노니우스",
    "Sörensen": "쇠렌센",
    "Canal": "카날",
    "Fingerslip": "핑거슬립",
    "Norwalde": "노르발데",
    "Dubois": "뒤부아",
    "Steiner": "슈타이너",
    "Alburt": "알부르트",
    "Mongredien": "몽그레디앙",
    "Ross": "로스",
    "Carr": "카",
    "Tate": "테이트",
    "Welling": "벨링",
    "Gipslis": "깁슬리스",
    "Schiller-Pytel": "실러-피텔",
    "Goteborg": "예테보리",
    "Barmen": "바르멘",
    "Bled": "블레드",
    "Hastings": "헤이스팅스",
    "Warsaw": "바르샤바",
    "Stockholm": "스톡홀름",
    "Amsterdam": "암스테르담",
    "Manhattan": "맨해튼",
    "Brooklyn": "브루클린",
    "Paris": "파리",
    "Graz": "그라츠",
    "Venice": "베네치아",
    "Dresden": "드레스덴",
    "Romford": "롬퍼드",
    "Novosibirsk": "노보시비르스크",
    "Petersburg": "페테르부르크",
    "St. Petersburg": "상트페테르부르크",
    "York": "요크",
    "Arctic": "아크틱",
    "Marienbad": "마리엔바트",
    "Valencian": "발렌시아",
    "Calabrese": "칼라브레제",
    "Rio de Janeiro": "리우데자네이루",
    "del Rio": "델 리오",
    "New York": "뉴욕",
    "Botvinnik System Reversed": "역 보트비닉 시스템",
    "Rio": "리우",
    "Ilyin-Zhenevsky": "일린-제네프스키",
    "Alekhine Defense": "알레힌 디펜스",
    "Poisoned Pawn": "포이즌드 폰",
    "Meadow Hay": "메도 헤이",
    "Barry": "배리",
    "Jaffe": "자페",
    "Colorado": "콜로라도",
    "Franco": "프랑코",
    "Halasz-McDonnell": "홀로시-맥도널",
    "Halasz": "홀로시",
    "Lanc-Arnold": "란츠-아르놀트",
    "Banzai-Leong": "반자이-레옹",
    "Basman-Palatnik": "바스먼-팔라트니크",
    "Duz-Khotimirsky": "두즈-호티미르스키",
    "Gibbins-Weidenhagen": "기빈스-바이덴하겐",
    "Busch-Gass": "부시-가스",
    "Halasz-Diemer": "홀로시-디머",
    "Gianutio": "자누티오",
    "Silberschmidt": "질버슈미트",
    "Keidansky": "케이단스키",
    "Murrey": "머리",
    "Potter": "포터",
    "Glek": "글레크",
    "Schurig": "슈리히",
    "Hein": "하인",
    "Young": "영",
    "Neumann": "노이만",
    "Riviere": "리비에르",
    "Lasa": "라자",
    "Süchting": "쥐히팅",
    "Chistyakov": "치스탸코프",
    "Sherbakov": "셰르바코프",
    "Grigoriev": "그리고리예프",
    "Westerinen": "베스테리넨",
    "Kurajica": "쿠라이차",
    "Bellon": "베욘",
    "Landau": "란다우",
    "Van der Wiel": "판 데르 빌",
    "Wiel": "빌",
    "Hennig": "헤니히",
    "Rasa-Studier": "라사-스투디어",
    "Kronberger": "크론베르거",
    "Hergert": "헤르게르트",
    "Hector": "헥토르",
    "Haberditz": "하버디츠",
    "Pollock": "폴록",
    "Henneberger": "헤네베르거",
    "Stahlberg": "스톨베리",
    "Paoli": "파올리",
    "Noa": "노아",
    "Pfeiffer": "파이퍼",
    "Schuehler": "쉴러",
    "Laroche": "라로슈",
    "Bugayev": "부가예프",
    "Mujannah": "무잔나",
    "Clam": "클램",
    "Lewis": "루이스",
    "Compromised": "컴프로마이즈드",
    "Harding": "하딩",
    "Erben": "에르벤",
    "Goldsmith": "골드스미스",
    "Smith": "스미스",
    "Mindeno": "민데노",
    "Nescafe Frappe": "네스카페 프라페",
    "Nei": "네이",
    "Prickly Pawn": "프리클리 폰",
    "Shy": "샤이",
    "Folly": "폴리",
    "Spike": "스파이크",
    "Storm": "스톰",
    "Wind": "윈드",
    "Cave": "케이브",
    "Claw": "클로",
    "Whip": "윕",
    "Hunt": "헌트",
    "Outflank": "아웃플랭크",
    "Intermezzo": "인터메조",
    "Millennium": "밀레니엄",
    "Chameleon": "카멜레온",
    "Matsukevich": "마추케비치",
    "Mengarini": "멘가리니",
    "Abrahams": "에이브러햄스",
    "Balogh": "발로그",
    "Senechaud": "세네쇼",
    "Labahn": "라반",
    "Burille": "뷰릴",
    "Charlick": "찰릭",
    "Zvjaginsev": "즈뱌긴체프",
    "Nyezhmetdinov": "네즈메트디노프",
    "Lopez": "로페즈",
    "Agincourt": "아쟁쿠르",
    "Richter": "리히터",
    "Czerniak": "체르니아크",
    "Berger": "베르거",
    "Panteldakis": "판텔다키스",
    "Brinckmann": "브링크만",
    "Van": "판",
    "von": "폰",
    "de": "드",
    "der": "데어",
    "del": "델",
    "El": "엘",
    "Columpio": "콜룸피오",
    "Lutikov": "루티코프",
    "l'Hermet": "레르메",
    "Bücker": "뷔커",
    "San": "산",
    "Zurich": "취리히",
    "Wall": "월",
    "Waller": "월러",
    "Gibbon": "기번",
    "Knorre": "크노레",
    "Weiss": "바이스",
    "Dubov": "두보프",
    "Kloss": "클로스",
    "Mortimer": "모티머",
    "Wormald": "워멀드",
    "Howell": "하월",
    "Ekstrom": "엑스트룀",
    "Dilworth": "딜워스",
    "Motzko": "모츠코",
    "Weinsbach": "바인스바흐",
    "Zeller": "첼러",
    "Popiel": "포피엘",
    "Morris": "모리스",
    "Rapport-Jobava": "라포르-조바바",
    "Rapport": "라포르",
    "Schneider": "슈나이더",
    "Kennedy Variation": "케네디 변화",
}
"""Proper nouns, longest key first. Everything else goes to the model."""

# ---------- tokens that pass through ----------

SAN = re.compile(
    r"^(?:O-O(?:-O)?|[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?|\.\.|[a-h]-file)$"
)
"""A move (or a file) inside a name — 'with Bf5', 'Delayed .. Nc6' — is left as it is."""

SEPARATOR = re.compile(r"(\s*[:,]\s*)")


@dataclass
class Result:
    """One translated name: the Korean, where it came from, and what was left untranslated."""

    korean: str
    source: str
    unknown: list[str] = field(default_factory=list)


def _defense(head: bool) -> str:
    """The one rule for "Defense": 디펜스 for the family, 방어 for a variation (module docstring)."""
    return "디펜스" if head else "방어"


def _word(token: str, head: bool, used_table: list[bool]) -> str | None:
    """One word, or None when neither table knows it."""
    if not token:
        return ""
    if SAN.match(token) or token.isdigit():
        return token
    if token in PROPER:
        used_table.append(True)
        return PROPER[token]
    if token in ("Defense", "Defence", "Defenses"):
        return _defense(head)
    if token in RULES:
        return RULES[token]
    if token in POSSESSIVE:
        return POSSESSIVE[token]
    if token.endswith("'s") and token[:-2] in PROPER:
        used_table.append(True)
        return PROPER[token[:-2]]
    if "-" in token:
        parts = token.split("-")
        out = [_word(part, head, used_table) for part in parts]
        if all(part is not None for part in out):
            joiner = " " if parts[0] in PREFIXES else "-"
            return joiner.join(str(part) for part in out)
    return None


def _chunk(text: str, head: bool, used_table: list[bool], unknown: list[str]) -> str:
    """One comma/colon-free piece of a name, longest phrase first."""
    words = text.split()
    out: list[str] = []
    i = 0
    while i < len(words):
        phrase_hit = False
        for size in range(min(MAX_PHRASE, len(words) - i), 1, -1):
            phrase = " ".join(words[i : i + size])
            if phrase in PROPER:
                used_table.append(True)
                out.append(PROPER[phrase])
                i += size
                phrase_hit = True
                break
        if phrase_hit:
            continue
        word = _word(words[i], head, used_table)
        if word is None:
            unknown.append(words[i])
            out.append(words[i])
        elif word:
            out.append(word)
        i += 1
    return " ".join(out)


def translate(name: str) -> Result:
    """Steps 1-2 for one name. ``source`` is 'rule', 'table', or 'llm' when a word is missing."""
    used_table: list[bool] = []
    unknown: list[str] = []
    pieces = SEPARATOR.split(name)
    out: list[str] = []
    head = True
    for piece in pieces:
        if SEPARATOR.fullmatch(piece):
            out.append(piece.strip() + " ")
            head = False
            continue
        out.append(_chunk(piece, head, used_table, unknown))
    korean = "".join(out).strip()
    source = "llm" if unknown else "table" if used_table else "rule"
    return Result(korean, source, unknown)


# ---------- step 3: headless Claude Code ----------

PROMPT = """\
당신은 체스 오프닝 이름을 한국어로 옮기는 번역기입니다. 아래 영어 이름들을 한국어로 옮기세요.

규칙:
1. 사람·지명·오프닝 고유명사는 한국에서 통용되는 음차를 씁니다(Ruy Lopez → 루이 로페즈, \
Najdorf → 나이도르프, Grünfeld → 그륀펠트).
2. 아래 사전에 있는 단어는 반드시 사전의 번역을 그대로 씁니다.
3. "Defense"는 이름의 **첫 구간**(콜론·쉼표 앞의 오프닝 가문 이름)에서는 "디펜스", \
콜론이나 쉼표 뒤의 변화 이름 안에서는 "방어"로 옮깁니다. \
예: "Sicilian Defense: Najdorf Variation" → "시실리안 디펜스: 나이도르프 변화", \
"Ruy Lopez: Morphy Defense" → "루이 로페즈: 모피 방어".
4. 콜론(:)과 쉼표(,)의 구조를 그대로 유지합니다.
5. 기물 표기와 좌표(Nf3, Bf5, d6, ..)는 번역하지 않고 그대로 둡니다.
6. 설명·괄호·원문 병기를 덧붙이지 않습니다. 이름만 옮깁니다.

사전(영어 → 한국어):
{glossary}

번역할 이름(JSON 배열):
{names}

출력: 위 배열의 모든 이름을 키로 갖는 JSON 객체 하나만, 다른 말 없이 출력하세요. \
형식은 {{"영어 이름": "한국어 이름", ...}} 입니다.
"""


def _claude_command(model: str) -> list[str]:
    """The same launcher the chat uses (services/chat.py): no tools, no MCP, never --bare."""
    try:
        sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))
        from chess_tutor.config import get_settings

        settings = get_settings()
        command, default_model = settings.chat_claude_command, settings.chat_model
    except Exception:  # the script also runs outside the API's virtualenv
        command, default_model = os.environ.get("CHAT_CLAUDE_COMMAND", "claude"), "opus"
    return [
        *shlex.split(command),
        "-p",
        "--output-format",
        "json",
        "--tools",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        json.dumps({"mcpServers": {}}),
        "--model",
        model or default_model,
    ]


def _glossary() -> str:
    """Both tables as the model sees them, the proper nouns first."""
    lines = [f"{en} = {ko}" for en, ko in sorted(PROPER.items())]
    lines += [f"{en} = {ko}" for en, ko in sorted(RULES.items()) if ko]
    lines += [f"{en} = {ko}" for en, ko in sorted(POSSESSIVE.items())]
    return "\n".join(lines)


def _extract(text: str) -> dict[str, str]:
    """The JSON object in the model's answer, fences and stray prose tolerated."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        loaded: Any = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {str(k): str(v).strip() for k, v in loaded.items() if str(v).strip()}


def ask(names: list[str], command: list[str], workdir: Path) -> tuple[dict[str, str], int]:
    """One batch. Returns what came back and how many calls it took (retries included)."""
    got: dict[str, str] = {}
    calls = 0
    todo = list(names)
    for _ in range(RETRIES + 1):
        if not todo:
            break
        prompt = PROMPT.format(
            glossary=_glossary(), names=json.dumps(todo, ensure_ascii=False, indent=0)
        )
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        env.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
        env.setdefault("DISABLE_AUTOUPDATER", "1")
        calls += 1
        try:
            proc = subprocess.run(
                command,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=TIMEOUT,
                cwd=str(workdir),
                env=env,
            )
        except subprocess.TimeoutExpired:
            print(f"  ! 시간 초과, {len(todo)}개 남음", flush=True)
            continue
        if proc.returncode != 0:
            print(f"  ! claude 실패({proc.returncode}): {proc.stderr[:200]}", flush=True)
            continue
        try:
            payload = json.loads(proc.stdout)
            answer = str(payload.get("result", ""))
        except json.JSONDecodeError:
            answer = proc.stdout
        got.update({k: v for k, v in _extract(answer).items() if k in todo})
        todo = [name for name in todo if name not in got]
    if todo:
        print(f"  ! 답을 못 받은 이름 {len(todo)}개: {todo[:3]}", flush=True)
    return got, calls


# ---------- the book ----------


def book_names() -> Counter[str]:
    """Every distinct opening name in the TSVs, counted by how many book positions carry it."""
    names: Counter[str] = Counter()
    for path in sorted(ASSETS.glob("openings_*.tsv")):
        lines = path.read_text(encoding="utf-8").splitlines()
        header = lines[0].split("\t")
        column = header.index("name")
        for line in lines[1:]:
            if not line.strip():
                continue
            names[line.split("\t")[column]] += 1
    return names


def _load(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in loaded.items()} if isinstance(loaded, dict) else {}


def _write(path: Path, data: dict[str, str]) -> None:
    ordered = {key: data[key] for key in sorted(data)}
    path.write_text(json.dumps(ordered, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="LLM을 부르지 않고 규칙 적용률만")
    parser.add_argument("--refresh-llm", action="store_true", help="기존 LLM 번역을 버리고 다시")
    parser.add_argument("--model", default="", help="기본값은 설정의 chat_model")
    parser.add_argument("--jobs", type=int, default=4, help="동시에 돌릴 batch 수")
    parser.add_argument("--max-batches", type=int, default=0, help="0이면 전부")
    parser.add_argument("--report", type=int, default=0, help="빈도 상위 N개를 표로 출력하고 끝")
    args = parser.parse_args()

    names = book_names()
    existing = _load(OUT)
    sources = _load(SOURCES)

    korean: dict[str, str] = {}
    source: dict[str, str] = {}
    missing: list[str] = []
    for name in names:
        result = translate(name)
        if result.source != "llm":
            korean[name], source[name] = result.korean, result.source
            continue
        kept = existing.get(name)
        if kept and not args.refresh_llm and sources.get(name, "llm") == "llm":
            korean[name], source[name] = kept, "llm"
            continue
        missing.append(name)

    if args.report:
        for name, count in names.most_common(args.report):
            print(f"{count:>4}  {source.get(name, '-'):<5} {name}\n      → {korean.get(name, '')}")
        return 0

    print(
        f"이름 {len(names)}개(책 줄 {sum(names.values())}개) · "
        f"규칙 {sum(1 for s in source.values() if s == 'rule')}개 · "
        f"표 {sum(1 for s in source.values() if s == 'table')}개 · "
        f"기존 LLM {sum(1 for s in source.values() if s == 'llm')}개 · "
        f"물어볼 이름 {len(missing)}개",
        flush=True,
    )
    if args.dry_run:
        unknown = Counter(word for name in missing for word in translate(name).unknown)
        print("모르는 단어 상위 40개:", unknown.most_common(40))
        return 0

    calls = 0
    if missing:
        batches = [missing[i : i + BATCH] for i in range(0, len(missing), BATCH)]
        if args.max_batches:
            batches = batches[: args.max_batches]
        command = _claude_command(args.model)
        workdir = Path.home() / ".cache" / "chess-tutor" / "translate"
        workdir.mkdir(parents=True, exist_ok=True)
        print(f"claude 호출 {len(batches)}묶음 (묶음당 {BATCH}개, 동시 {args.jobs})", flush=True)
        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            for i, (got, used) in enumerate(
                pool.map(lambda batch: ask(batch, command, workdir), batches)
            ):
                calls += used
                for name, value in got.items():
                    korean[name], source[name] = value, "llm"
                print(f"  묶음 {i + 1}/{len(batches)}: {len(got)}개 · 호출 {used}회", flush=True)

    left = [name for name in names if name not in korean]
    _write(OUT, korean)
    _write(SOURCES, source)
    print(
        f"저장 {OUT.relative_to(ROOT)} · {len(korean)}개 "
        f"(규칙 {sum(1 for s in source.values() if s == 'rule')}, "
        f"표 {sum(1 for s in source.values() if s == 'table')}, "
        f"LLM {sum(1 for s in source.values() if s == 'llm')}) · "
        f"claude 호출 {calls}회 · 빠진 이름 {len(left)}개"
    )
    if left:
        print("빠진 이름:", left[:10])
    return 1 if left else 0


if __name__ == "__main__":
    raise SystemExit(main())
