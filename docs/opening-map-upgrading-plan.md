# 인터페이스
1. 메인 체스판
2. 현재상태에서 다음상태로 갈수 있는 네임드 포지션들의 후보들을 깔끔하게 보여주기
    1. 보드상태
    2. 기물 움직임 notation 턴 숫자까지 포함해서
    3. 포지션 이름
3. 현재 상태는 메인체스판에 표시하고 이전상태에서 현재상태로 수를 둔 플레이어의 의도를 텍스트로 써줌. 이때 이전 상태의 텍스트를 지우지 말것. 그러므로 게임종료까지 체스판 상태를 계속 진행시키면 해당 대국에 대한 일련의 해설이 남게됨.
4. 가능하다면 사용자가 데이터베이스 없는, 혹은 좋지 않은 수로 체스판의 상태를 바꿨을때에도 해설 텍스트를 생성해 주었으면 함.
5. 또한 셋업플레이가 가능한 상태에 대해서는 해당 셋업플레이의 정보도 알려주었으면 함.

---

# 구현 계획 (M8 오프닝 지도 v2: 수순 따라가기 + 해설 일지)

작성일: 2026-09-11. 코드 조사 기준: `apps/web/src/pages/openings/*`, `services/openings_map.py`, `services/openings_catalog.py`(M7 작업분 포함), `openings.py`, `services/play_coach.py`, `services/plans.py`, `services/reasoning.py`, `verify.py`, `structure.py`, `routers/openings.py`, `routers/play.py`, `components/Board.tsx`, `lib/chess.ts`.

## 1. 한 줄 요약

오프닝 지도의 "열 탐색기"를 **직접 수를 두는 보드 + 다음 네임드 국면 후보 + 지워지지 않는 해설 일지**로 바꾼다. 어떤 수를 두든(책에 있든 없든) 그 수의 **의도**를 검증된 문장으로 적어 두고, 지금 국면에서 노릴 수 있는 **셋업(시스템 배치)** 의 진행 상황을 함께 보여 준다. 위의 아이시클 개요 스트립과 아래의 히트맵·브레이크 차트는 그대로 둔다.

원칙은 M1~M7과 같다: 문장은 검증기(`verify.verify_all`)를 통과한 것만 보여 주고(원칙 1), 근거는 수순·구조·기록 중 하나를 단다(원칙 3), LLM은 있으면 다듬기만 한다.

## 2. 요구사항 해석

| # | 원문 | 해석 | 근거 데이터 |
|---|---|---|---|
| 1 | 메인 체스판 | 지금의 `FocusBoard`(`MiniBoard`, 보기 전용)를 **수를 둘 수 있는 `Board`**(chessground, 승급 피커 포함)로 바꾼다 | `components/Board.tsx`, `lib/chess.ts`(`legalDests`/`applyUci`), M7의 `PromotionPicker` |
| 2 | 다음 네임드 국면 후보 | 현재 국면에서 TSV 책이 아는 다음 수 전부. 각 후보에 (a) 도착 국면 미니보드 (b) `3.Bb5`처럼 수 번호가 붙은 표기 (c) 도착 국면(또는 그 수가 이끄는 변화)의 이름. 여기에 내 기보의 판수·승률(지도 DAG 간선)과, 토큰이 있으면 마스터 통계를 겹친다 | `openings.next_moves`(전위 포함 3,810줄 전 접두사), `openings_map.build_map`의 간선, Lichess 익스플로러 |
| 3 | 의도 텍스트, 이전 텍스트 보존 | 한 수를 둘 때마다 "이 수는 무엇을 하는가"를 **결정론적 사실 탐지기 → 문장 → 검증기** 순서로 만든다. 텍스트는 **일지(journal)** 에 시간순으로 쌓이고 절대 지우지 않는다. 되돌아가서 다른 수를 두면 "N수로 돌아가 X 대신 Y"라는 항목이 **추가**된다 | `plans.hint_matches_move`, `motifs.detect`, `reasoning._describe_move`, 새 사실 탐지기 |
| 4 | 책에 없는/나쁜 수도 해설 | 사실 탐지기는 책과 무관하게 돈다. 책을 벗어난 수에는 "책 수는 X(이름)" 을 덧붙이고, 얕은 엔진 판정(`play_coach.check`: 분류·손실·최선수·이유)을 붙인다. 책 수에는 엔진을 기본으로 돌리지 않는다(비용, 그리고 원칙 5: 암기가 아니라 이해) | `play_coach.check`, `maia.move_probs` |
| 5 | 셋업 플레이 | **가정:** "셋업 플레이" = 상대 수와 거의 무관하게 정해진 기물 배치를 완성하는 시스템 오프닝(런던, 콜레, KIA, 스톤월, 헤지호그, 킹스 인디언 배치 …). 현재 국면에서 각 셋업이 (완성 / 진행 중 k/n / 막힘) 중 어디인지, 남은 배치와 대표 계획을 보여 준다. 폰 구조 KB(`plans.PLANS`)가 중반용이라 오프닝 단계에는 비어 있는데(`MIN_STRUCTURE_PAWNS=6` 이하이거나 `unclassified`), 셋업 KB가 그 빈자리를 메운다 | 새 `services/setups.py` |

5번 가정이 틀렸다면(예: 전술 트랩·희생 패턴을 뜻했다면) 셋업 KB의 항목 형식이 "목표 기물 배치" 대신 "국면 조건 + 수순"이 되어야 하므로 **M8a 착수 전에 확인**한다. 항목 형식만 바뀌고 탐지·표시 구조는 같다.

## 3. 지금 있는 것과 빈 곳

| 필요한 것 | 현재 | 빈 곳 |
|---|---|---|
| 책 다음 수 (전위 포함) | `openings._tree()`: 국면 키 → uci → 이끄는 오프닝. `openings_catalog.book_moves(fen)` → `GET /play/book` | 도착 FEN, 수 번호 라벨, "그 국면 자체가 이름이 있는지" 구분, 이름까지 남은 수가 없음 |
| 내 기록 오버레이 | `GET /openings/map` DAG(노드=국면 키, 간선=판수·승률), 브라우저의 `buildTree` | 새 보드의 현재 FEN → 노드 매칭만 하면 된다(키 = FEN 앞 4필드) |
| 마스터 오버레이 | `openings_map._attach_masters`(상위 10노드, 노드당 3수, `LICHESS_TOKEN` 필요) | 임의 FEN에 대해 부르는 경로 없음 (`fetch_master_moves(fen, token)`은 있음) |
| 수의 의도 문장 | `reasoning._describe_move`(잡기·체크·모티프), `explain_alternative`(교환 정산), `play_coach._plan_sentences`(구조·계획), `_Sentence`+`_assemble`(검증 게이트) | 오프닝 개념(중앙·전개·캐슬링·피안케토·긴장·갬빗·전위·예방)을 말하는 탐지기 없음. `_assemble`이 `play_coach` 내부 함수 |
| 책 이탈 판정 | `openings_map._deviation_ply`(게임 단위), `practice.py`의 드릴 이탈 처리(M7) | 한 수 단위 "책에 있나 / 책 대안은" 조회 없음 (`next_moves`로 바로 만들 수 있음) |
| 엔진 판정 | `play_coach.check(fen_before, san, rating)` 깊이 12 | 그대로 재사용 |
| 셋업(시스템) 지식 | 없음. `plans.PLANS`는 14구조 중반 계획 | 셋업 KB와 도달 가능성 판정 |
| 보드 조작 | `Board`(movable/dests/shapes), `PromotionPicker`(M7), `lib/chess.ts` | 오프닝 페이지는 `MiniBoard`만 씀 |
| 화면 상태 | `focusId` 하나. 트리 탐색은 `Explorer`의 이전/현재/다음/그 다음 열 | 수순(plies)·커서·일지 상태 없음. URL에 국면이 없어 새로고침·공유 불가 |

결론: 서버는 **무상태 조회 2개**(국면 정보, 수 해설)와 **KB 모듈 2개**(사실 탐지기, 셋업)를 더하면 되고, 웹은 `Explorer` 열을 새 패널 3개로 바꾼다.

## 4. 화면 설계

```
┌ 오프닝 지도 ─────────────────────────── 백 · 루이 로페즈 · 내 212판 ┐
│ [레퍼토리 개요: 아이시클 스트립 — 그대로. 칸 클릭 = 그 수순으로 점프] │
├──────────────────────────┬────────────────────────────────────────┤
│  국면 탐색                │ 다음 갈 수 있는 네임드 국면            │
│  ┌──────────────────┐    │ ┌────┐ 3.Bb5  루이 로페즈        C60   │
│  │                  │    │ │ 미니│ 내 41판 56% · 마스터 62%        │
│  │   메인 보드      │    │ └────┘                                 │
│  │   (두기 가능)    │    │ ┌────┐ 3.Bc4  이탈리안 게임      C50   │
│  │                  │    │ │    │ 내 30판 48%                     │
│  └──────────────────┘    │ └────┘                                 │
│  1.e4 e5 2.Nf3 Nc6 ▸ 백  │ ┌────┐ 3.d4   스카치 게임        C44   │
│  ⏮ ◀ ▶ ⏭  뒤집기  처음   │ └────┘ 책에만 · 마스터 58%             │
│  [여기서부터 두기 →/play] │ 기타 4가지 ▾                           │
│                          ├────────────────────────────────────────┤
│  셋업                     │ 셋업 (백 차례)                         │
│  ▮▮▮▯▯ 이탈리안 배치 3/5  │ 진행 중: 이탈리안 배치 3/5 — 남은 배치 c3, d3 │
│  ▮▯▯▯▯ …                 │ 가능: KIA 1/6                          │
│                          │ 막힘: (없음)                            │
├──────────────────────────┴────────────────────────────────────────┤
│ 해설 일지 (지워지지 않음)                                        │
│ 1.e4   킹즈 폰 오프닝. 중앙 d5·f5를 통제하고 f1 비숍과 퀸의 길을 엽니다.  │
│ 1…e5   오픈 게임. 같은 방식으로 맞서 e4 폰의 전진을 막습니다.            │
│ 2.Nf3  e5 폰을 공격하며 첫 기물을 전개합니다. 내 기보 212판 중 198판.    │
│ 2…Nc6  e5를 지킵니다(f3 나이트가 e5를 공격). 계획 없음.                  │
│ 3.Bb5  ⚑루이 로페즈. c6 나이트를 공격해 e5의 수비를 흔듭니다.            │
│ 3…a6   책 수. 이 레이팅대에서 71%가 두는 수. 비숍에게 물러설지 바꿀지 묻습니다 │
│ 4.Bxc6 ⚠ 책 밖(책 수: 4.Ba4 클로즈드 계열). 나이트를 비숍으로 잡고 …     │
│        엔진: 실수는 아님(손실 1.2%). 최선 4.Ba4.   [엔진으로 확인] [질문]│
│ ↶ 3수로 돌아가 4.Bxc6 대신 4.Ba4                                        │
│ 4.Ba4  ⚑모피 방어 … (계속)                                               │
└────────────────────────────────────────────────────────────────────┘
[기물 목적지] [폰 브레이크 시점]  ← 그대로
```

- **후보 카드 클릭 = 그 수를 둔다.** 마우스를 올리면 메인 보드에 화살표(`Board.shapes`)만 그린다. 보드를 통째로 바꾸던 `preview`는 없앤다.
- **보드에서 직접 둔 수**도 후보 클릭과 같은 경로(`play(uci)`)를 탄다. 책에 없으면 카드 목록엔 없지만 일지엔 ⚠ 항목으로 남는다.
- **일지 항목 클릭**은 커서 이동(보드가 그 국면으로). 커서가 끝이 아닐 때 새 수를 두면 뒤의 수순은 잘리지만 일지는 자르지 않고 "↶ 되돌아가기" 항목을 추가한다.
- **스트립 칸 클릭**은 `pathTo(node)`의 수순으로 점프한다. 그 수순 전체를 한 번에 해설(배치 호출)하고 일지에 한꺼번에 붙인다.
- **셋업 패널**은 차례인 쪽의 셋업만 보여 주고(내 색 기준 토글 가능), "남은 배치"를 누르면 보드에 화살표로 그린다.
- 일지의 **[엔진으로 확인]** 은 책 수에도 `play_coach.check`를 요청해 항목을 갱신한다. **[질문]** 은 M7의 라이브 채팅(`POST /play/chat`)에 그 국면·수·일지 텍스트를 실어 보낸다(있으면 링크만, 없으면 생략).
- URL은 `/openings?color=white&moves=e4,e5,Nf3`로 동기화(`replaceState`). 리뷰 화면의 수 목록에 "오프닝 지도에서 보기"(같은 파라미터) 링크를 하나 단다.
- 폭 900px 이하: 보드 → 후보 → 셋업 → 일지 순 한 열.

## 5. 백엔드 설계

### 5.1 엔드포인트 — 새 라우터 `routers/opening_guide.py` (prefix `/openings`)

M7 작업이 `routers/openings.py`·`services/openings_map.py`·`services/openings_catalog.py`를 아직 커밋하지 않은 채 고치고 있으므로(작업 트리 diff 1,200줄) **그 파일들은 건드리지 않는다.** 새 파일에 같은 prefix로 라우터를 하나 더 만들고, M7이 들어온 뒤 합칠지 정한다. 둘 다 DB를 읽지 않는다(내 기록은 브라우저가 이미 받은 지도에서 겹친다).

| 경로 | 요청 | 응답 | 비고 |
|---|---|---|---|
| `GET /openings/position` | `fen`, `color?`(내 색, 마스터 승률 시점용), `masters?=0/1` | `PositionGuide` | 무상태. `fen`의 국면 키로 `lru_cache`(마스터 부분은 기존 `_master_cache`) |
| `POST /openings/annotate` | `{start_fen?, moves_san[], color?, rating=1500, engine: "off"\|"off_book"\|"always", naturalness: bool, depth?}` | `{annotations: MoveAnnotation[]}` | 한 수면 길이 1. 스트립 점프는 수순 전체. 엔진은 `asyncio.to_thread` |

```python
class NamedCandidate(BaseModel):
    san: str; uci: str
    label: str            # "3.Bb5" / "3…a6"  (openings_map.move_label 재사용)
    fen_after: str
    name: str; eco: str
    named_here: bool      # 도착 국면 자체가 책에 있는가 (아니면 이 수가 속한 줄의 이름)
    to_name: list[str]    # 이름 있는 국면까지 외길일 때 그 남은 SAN (named_here면 [])
    master_games: int | None = None; master_score: float | None = None

class SetupStatus(BaseModel):
    id: str; name: str; side: Color
    status: Literal["completed", "in_progress", "possible", "blocked"]
    done: list[str]       # 채워진 배치 "Bf4"
    remaining: list[str]  # 남은 배치 "Nbd2"
    blocked_by: str | None  # "e3를 먼저 두어 c1 비숍이 갇혔습니다"
    plans: list[str]      # 대표 계획 제목(셋업 KB의 문장)
    typical_against: str | None

class PositionGuide(BaseModel):
    fen: str; side: Color
    name: str | None; eco: str | None; in_book: bool
    structure: StructureInfo
    candidates: list[NamedCandidate]
    setups: list[SetupStatus]

class MoveFact(BaseModel):
    kind: str             # "book" | "name" | "transposition" | "center" | "development" | "castling"
                          # | "fianchetto" | "tension" | "break" | "gambit" | "motif" | "plan"
                          # | "setup" | "prophylaxis" | "naturalness" | "engine"
    text: str
    claims: list[Claim] = []
    verified: bool = True

class MoveAnnotation(BaseModel):
    ply: int; label: str; san: str; uci: str
    fen_before: str; fen_after: str
    in_book: bool; name_before: str | None; name_after: str | None; transposition: bool
    book_alternatives: list[NamedCandidate] = []   # 책 밖일 때만, 최대 3
    facts: list[MoveFact]
    text: str             # 검증 통과 문장만 이어 붙인 것
    engine: PlayCheckResponse | None = None
    naturalness: float | None = None               # maia 확률 (요청했을 때만)
    verified: bool; verified_claims: int; total_claims: int
```

### 5.2 `services/opening_guide.py` — 국면 정보와 후보

- `candidates(board, color, masters)`: `openings.next_moves(board)` 를 돌며 `NamedCandidate`를 만든다. `named_here = openings.lookup(after) is not None`. `to_name`은 도착 국면부터 `next_moves`가 정확히 1개인 동안 따라가 이름 있는 국면을 만나면 채우고, 4플라이 안에 못 만나면 빈 리스트(탐색기의 `stepsOf` 접기와 같은 발상). 정렬: 이름 있는 국면 우선 → 마스터 판수 → SAN.
- `masters=1`이고 토큰이 있으면 `openings_map.fetch_master_moves(fen, token)`를 한 번 불러 `uci`로 합친다. 책에는 없는데 마스터가 두는 수는 `name=""`, `eco=""`로 **후보에 포함**한다(지도의 점선 간선과 같은 취급).
- `guide(fen, color, masters)` → `PositionGuide`: 위 후보 + `structure.classify` + `setups.status_all(board)`.
- 국면 키 기준 `lru_cache(4096)`. 마스터는 기존 프로세스 캐시.

### 5.3 `services/opening_intent.py` — 수의 의도 (요구 3·4의 핵심)

입력 `(board_before, move, ctx)` → `list[MoveFact]`. 각 탐지기는 python-chess 사실만 말하고 근거 칸을 `Claim`으로 단다. 검증기 종류가 `attacks/defends/is_check/checkmate/piece_on/square_empty/legal_move` 7개뿐이므로 문장은 이 7개로 표현되는 사실만 담는다(칸 이름이 나오는 문장은 반드시 그 칸에 대한 claim을 동반: `verbalize.unclaimed_squares`와 같은 규칙).

| kind | 판정 | 문장 예 | claims |
|---|---|---|---|
| book / name | `lookup(after)`; 이름이 바뀌면 "⚑ {이름}이 됩니다". 책에 없으면 "책 밖. 책 수는 {상위 3개}" | 루이 로페즈가 됩니다 | legal_move(대안들) |
| transposition | 도착 국면이 책에 있고, 책 줄의 접두사 순서와 실제 수순이 다를 때(`find_rows`로 그 이름의 줄을 재생해 비교) | 다른 수순으로 같은 국면에 합류합니다 | — |
| center | 폰/기물이 두고 난 뒤 d4·e4·d5·e5 중 새로 공격하게 된 칸 | d5·f5를 통제합니다 | attacks(from→sq) |
| development | 마이너 기물이 원래 칸을 떠남; 몇 번째 전개인지; 퀸 조기 출동(전개 기물 ≤1일 때)이면 "퀸을 일찍 꺼냅니다" 경고 | 두 번째 기물을 전개합니다 | piece_on |
| castling / king_safety | 캐슬링, 또는 캐슬링 권리를 잃는 킹·룩 이동 | 킹을 안전한 곳으로 옮기고 룩을 연결합니다 | piece_on(킹·룩) |
| fianchetto | g3/b3/g6/b6 폰 뒤 대각 비숍 배치, 또는 그 준비 폰 수 | 비숍을 긴 대각선에 놓습니다 | piece_on |
| tension / break | `reasoning._is_break` (폰이 상대 폰과 맞닿음) / 긴장 해소(잡기·전진) | c4로 d5 폰에 긴장을 만듭니다 | attacks |
| gambit | 두고 난 뒤 자기 폰이 공격받고 방어 수가 공격 수보다 적을 때 | 폰을 내어 주고 전개를 앞당깁니다 | attacks, defends |
| motif | `motifs.detect` 중 pin·fork·discovered_attack·attack_on_piece | c6 나이트를 묶습니다 | attacks |
| plan | `structure.classify` 가 이름 있는 구조를 주면 `plans.plan_specs`의 힌트와 `hint_matches_move` | 계획 "소수 공격"의 첫 수입니다 | — (계획 제목은 KB 문구) |
| setup | `setups.advance(board_before, move)`: 이 수로 진행된 셋업과 k/n, 또는 이 수가 어떤 셋업을 막았는지 | 런던 시스템 배치 2/6 | piece_on |
| prophylaxis | 직전 국면에서 상대의 책 후보 중 도착 칸 X로 가는 수가 있었고, 이 수 뒤 X가 새로 공격되거나 막히면 | …Bb4 핀을 미리 막습니다 | attacks |
| naturalness | `maia.move_probs(fen_before, rating)` 의 이 수 확률 (요청 시) | 이 레이팅대에서 71%가 두는 수 | — |
| engine | `play_coach.check` 결과를 요약 (책 밖이거나 요청 시) | 손실 1.2%, 최선 4.Ba4 | check의 claims |

문장 조립은 `play_coach._Sentence/_assemble`을 그대로 쓰되, 두 모듈이 같이 쓰도록 **`services/sentences.py`로 옮기는 일은 M7 커밋 뒤**로 미룬다(그 전엔 `opening_intent`에 동일 헬퍼를 두고 M8d에서 합친다).

우선순위와 길이: 한 수에 문장 2~4개. `name → motif/tension/gambit → center/development/castling/fianchetto → plan/setup → naturalness → engine` 순으로 담고, `center`와 `development`가 둘 다 잡히면 한 문장으로 합친다("e5 폰을 공격하며 첫 기물을 전개합니다").

`annotate(req)`: `start_fen`부터 `moves_san`을 재생하며 수마다 `facts → text`를 만든다. 엔진은 `engine="always"`거나(`"off_book"`이고 책 밖일 때) `play_coach.check`를 부른다. 한 요청의 엔진 호출은 최대 `ANNOTATE_ENGINE_CAP=6`수(스트립 점프로 20수를 한꺼번에 보낼 때의 상한; 넘는 수는 `engine=None`으로 두고 웹이 [엔진으로 확인]을 제공).

### 5.4 `services/setups.py` — 셋업 KB와 도달 판정 (요구 5)

```python
@dataclass(frozen=True)
class SetupSpec:
    id: str; name: str; side: Side
    targets: tuple[tuple[str, chess.PieceType], ...]   # (("d4", PAWN), ("f4", BISHOP), ...)
    before: tuple[tuple[str, str], ...] = ()            # ("f4", "e3"): Bf4가 e3보다 먼저
    plans: tuple[str, ...] = ()                         # 완성 뒤의 대표 계획(우리 문장)
    against: str | None = None                          # "…d5 계열", "…c5 계열" 같은 적용 조건
```

초기 항목(백 8 · 흑 6, 모두 백 시점으로 적고 흑은 거울로 만들지 **않는다** — 흑 셋업은 원래 흑 국면이므로 그대로 적는다):
런던(d4 Bf4 e3 Nf3 c3 Nbd2 Bd3) · 콜레-쥐케르토르트(d4 Nf3 e3 Bd3 b3 Bb2 Nbd2) · 콜레-콜타노프스키(d4 Nf3 e3 Bd3 c3 Nbd2) · 스톤월 어택(d4 e3 f4 Bd3 Nf3 Nbd2) · KIA(Nf3 g3 Bg2 O-O d3 Nbd2 e4) · 토레(d4 Nf3 Bg5 e3 c3 Nbd2 Bd3) · 레티 더블 피안케토(Nf3 g3 Bg2 b3 Bb2 O-O) · 마로치 바인드(e4 c4 Nc3 Be2 Be3 Nd4) / 흑: 킹스 인디언 배치(…Nf6 …g6 …Bg7 …d6 …O-O) · 피르츠/모던(…d6 …Nf6 …g6 …Bg7) · 헤지호그(…a6 …b6 …Bb7 …d6 …e6 …Be7 …Nbd7) · 스톤월 더치(…f5 …e6 …d5 …c6 …Bd6) · 레닌그라드 더치(…f5 …Nf6 …g6 …Bg7 …d6 …O-O) · 퀸즈 인디언 배치(…Nf6 …e6 …b6 …Bb7 …Be7).

판정 `status(spec, board)`:
- 목표 칸마다 **done**(그 칸에 그 기물) / **possible** / **blocked**.
- possible/blocked는 결정론적 규칙 하나: 폰은 같은 파일 뒤쪽에 내 폰이 있고 그 사이가 비어 있을 때 possible. 기물은 **상대 반응을 무시하고 빈 칸으로 2수 안에** 그 칸에 닿는 같은 종류의 기물이 있을 때 possible(이미 다른 목표 칸에 쓰인 기물은 제외). 캐슬링 목표는 `board.has_castling_rights`.
- `before` 제약 위반(예: c1 비숍이 아직 c1인데 e3 폰이 이미 있음)은 blocked, 이유 문장을 KB 문구로 만든다.
- 종합: 전부 done → completed; blocked 하나라도 → blocked(이유 첨부); done ≥ 1 → in_progress(k/n); 아니면 possible. 초기 국면에서는 possible만 나온다.
- `status_all(board)`: 차례인 쪽 우선, `completed → in_progress(k 내림차순) → possible → blocked` 순. 웹은 위 3개 + 완성만 펼치고 나머지는 접는다.
- `advance(board_before, move)`: 이 수로 done이 하나 늘어난 셋업(→ "setup" 사실), 혹은 새로 blocked 된 셋업(→ "이 수로 런던 배치는 물 건너갑니다": 배우는 사람에게 가장 유용한 문장).

셋업의 계획 문장은 `plans.PLANS`와 같은 규칙(칸·기물만, 색 단어 없음)으로 적고, 완성된 셋업이 있으면 `MoveFact(kind="plan")` 대신 셋업 계획을 일지에 한 번 적는다("런던 배치 완성. 대표 계획: Ne5 + Qf3/h4, c4 브레이크").

### 5.5 성능·캐시·설정

- `/position`은 엔진 없음, 수 ms. 마스터 조회만 네트워크(3초 타임아웃, 실패해도 후보는 나온다).
- `/annotate` 한 수: 사실 탐지 < 5ms, `maia.move_probs` ~50ms(maia2) 또는 소프트맥스 폴백 시 엔진 1회, `check` 깊이 12 ~0.3–1s. 엔진 요청은 `engine.pool.borrow()`로 한 번만 빌린다.
- 설정 추가 없음. 기존 `play_depth`, `lichess_token`, maia 설정을 그대로 쓴다.

## 6. 웹 설계

### 6.1 상태 — `pages/openings/line.ts` (React 없음, `model.ts`와 같은 규칙)

```ts
type LinePly = { san: string; uci: string; fen: string; label: string; annotation: MoveAnnotation | null };
type JournalEntry =
  | { kind: 'move'; ply: number; annotation: MoveAnnotation; pending?: boolean }
  | { kind: 'rewind'; toPly: number; instead: string; was: string }   // "↶ 3수로 돌아가 4.Bxc6 대신 4.Ba4"
  | { kind: 'jump'; sans: string[] };                                 // 스트립 점프
type LineState = { startFen: string; plies: LinePly[]; cursor: number; journal: JournalEntry[] };
actions: play(uci) | goto(cursor) | jumpTo(sans[]) | annotated(ply, annotation) | reset()
```

- `play`: 커서 < 끝이면 `plies`를 자르고 `rewind` 항목을 먼저 넣은 뒤 새 수를 넣는다. 일지는 절대 `splice`하지 않는다(요구 3).
- `moves` URL 파라미터는 `plies`의 SAN 목록. 첫 로드에 `?moves=`가 있으면 `jumpTo`로 복원(배치 해설 1회).
- 해설은 낙관적으로: 수를 두면 `pending` 항목을 먼저 넣고 `POST /annotate` 응답으로 채운다. 실패하면 항목에 "해설 실패 · 다시" 버튼.

### 6.2 컴포넌트

| 파일 | 역할 |
|---|---|
| `LineBoard.tsx` | `Board` + `PromotionPicker` + 커서 컨트롤(⏮◀▶⏭, 뒤집기, 처음). `legalDests(fen)`으로 움직임 제한, 후보 hover 화살표를 `shapes`로 |
| `Candidates.tsx` | `PositionGuide.candidates` × 지도 DAG 간선(내 판수·승률) 병합. 카드 = `MiniBoard(fen_after)` + 라벨 + 이름/ECO + 기록 줄. 6개 초과는 "기타 n가지" 접기(`Column`의 `COL_LIMIT` 관행) |
| `SetupPanel.tsx` | `PositionGuide.setups`. 진행 바, 남은 배치 칩(클릭 → 보드 화살표), 막힘 이유 |
| `Journal.tsx` | 일지. 항목 클릭 = `goto`. 사실 종류별 배지(⚑이름 / ⚠책 밖 / 계획 / 셋업 / 엔진). [엔진으로 확인] [질문] |
| `useGuide.ts` | `fen → PositionGuide` 조회(`useQuery` 재사용, 국면 키로 메모) |
| `index.tsx` | 카드 배치 변경. `Strip` 유지, `Explorer`·`FocusBoard` 제거. `focusId` → `line` 상태. 스트립 `pathIds`는 `plies`의 국면 키로 계산 |
| `model.ts` | `buildTree`·`pathTo`·`stripLayout` 유지. `stepsOf`·`currentStep`·`importantAncestor`는 후보 병합에 안 쓰이면 삭제 |
| `api/client.ts`, `api/types.ts` | `openings.position(fen, color, masters)`, `openings.annotate(body)` + 스키마 타입 |
| `openings.css` | `op-line-*`, `op-cand-*`, `op-setup-*`, `op-journal-*`. 트레이닝/대국 페이지의 2열 CSS 변수를 재사용 |

### 6.3 다른 화면과의 연결 (작게)

- 리뷰 수 목록: "오프닝 지도에서 보기" → `/openings?color=&moves=`(최대 24플라이).
- 오프닝 지도: "여기서부터 두기" → `/play?fen=`(M7 진입점, 이미 계획됨).
- 프로필의 레퍼토리 구멍 항목에서 `/openings?moves=` 링크(있으면).

## 7. 마일스톤

| 단계 | 내용 | 산출물 | 테스트/수용 기준 |
|---|---|---|---|
| **M8a 국면 정보 + 셋업** | `services/setups.py`, `services/opening_guide.py`, `routers/opening_guide.py`(`/position`), 스키마 | 후보·셋업 JSON | `tests/test_setups.py`: 초기 국면에서 런던 possible / `1.d4 d5 2.Bf4` 뒤 in_progress 2/7 / `1.d4 d5 2.e3` 뒤 blocked(이유 문장) / 완성 국면 completed / 흑 KID 배치 진행. `tests/test_opening_guide.py`: 시작 국면 후보에 e4·d4·c4·Nf3, 루이 로페즈 국면에서 `3…a6`의 `named_here`, 전위 국면(오켈리 순서)에서 같은 후보, 마스터는 `respx`로 스텁 |
| **M8b 수 해설** | `services/opening_intent.py`, `/annotate`, 엔진·마이아 경로 | 일지용 JSON | `tests/test_opening_intent.py`: 손으로 만든 국면별 사실 — 1.e4(center), 2.Nf3(development+attacks e5), 3.Bb5(motif pin/attack + name), 4.Bxc6(book_alternatives 3개, engine 스텁), 2.g3+Bg2(fianchetto), 3.c4 vs d5(tension), 2.f4 킹즈 갬빗(gambit), 오켈리 전위(transposition), 칼스바드 국면의 b4(plan). 모든 문장이 `verify_all` 통과(`verified == True`), 칸 이름이 있는 문장은 claim 동반. 엔진 상한 6수 확인 |
| **M8c 웹** | `line.ts`, 컴포넌트 5개, `index.tsx` 개편, URL 동기화, 리뷰 링크 | 화면 | `pnpm lint`, `tsc --noEmit`, `pnpm build`. Playwright 스모크: 수 두기 → 일지 항목 추가 → 되돌아가 다른 수 → rewind 항목이 남고 이전 항목 유지 → 스트립 클릭 → 배치 해설 → 새로고침 후 `?moves=` 복원 |
| **M8d 마무리** | `_Sentence/_assemble`을 `services/sentences.py`로 공유, `routers/openings.py`와 라우터 합치기, [질문] 연결(`/play/chat`), LLM 다듬기(`verbalize`의 LLM→검증→템플릿 폴백을 `MoveFact` 묶음에 적용, `ANTHROPIC_API_KEY` 있을 때만), 문서(`docs/IMPLEMENTATION.md` §7·§8) | — | 기존 테스트 전부 녹색, 문서 갱신 |

순서 이유: M8a·M8b는 서로 독립이라 병렬 가능하고 둘 다 무상태여서 M7의 미커밋 파일과 충돌하지 않는다. M8c는 둘 다 필요하다. M8d의 공유 헬퍼 이동과 라우터 합치기는 M7 커밋 뒤에만 한다.

작업량 감: 서버 ~900줄(셋업 KB 250, 의도 탐지기 350, 가이드·라우터·스키마 300) + 테스트 ~500줄, 웹 ~900줄(상태 150, 컴포넌트 550, index 개편·CSS 200). 삭제: `Explorer.tsx` 298줄, `FocusBoard.tsx` 61줄.

## 8. 리스크와 미결

- **셋업 플레이의 뜻**(§2). 확인 전에는 시스템 오프닝으로 간다.
- **오프닝 단계의 의도 문장이 얇을 수 있다.** 탐지기는 "무엇을 하는가"는 말해도 "왜 이 변화가 좋은가"는 못 말한다(TSV에는 이름뿐). 보완 순서: (1) 셋업 KB의 계획 문장 (2) 마이아 확률로 "이 레이팅대의 자연스러운 수"를 근거로 제시 (3) LLM 다듬기(M8d) (4) 필요하면 카탈로그 30개 항목에 한해 손으로 쓴 "이 변화의 요점" 문장을 `openings_catalog.CatalogEntry`에 추가 — 손 데이터가 늘어나므로 마지막 수단.
- **검증기 종류가 7개**라 "공간을 얻습니다", "구조가 굳습니다" 같은 문장은 근거를 달 수 없어 못 쓴다. 필요하면 `verify.py`에 `pawn_structure`류 종류를 추가하는 별도 작업.
- **엔진 비용.** 배치 해설 + `engine="always"`는 20수면 10초. 기본을 `off_book`으로 두고 상한 6수를 지킨다.
- **마스터 오버레이**는 토큰이 있을 때만. 후보 카드의 마스터 줄은 없으면 숨긴다.
- **M7과의 동시 작업.** 이 계획은 새 파일만 만든다. `openings.py`(`next_moves`, `find_rows`)는 M7 작업분에 의존하므로 **M7이 커밋된 뒤 브랜치를 딴다.**
- 일지가 길어지면(50수 이상) 가상 스크롤이 필요할 수 있다. 우선은 접기(오래된 항목 10개 단위)로 충분.

## 9. 시안 반영 변경 (2026-09-11, 위 섹션보다 우선)

시안 `docs/opening-map-v2-mockup.html`(브라우저로 열면 동작한다)이 확정안이다. 위 §4·§5·§6과 다른 점은 아래가 이긴다.

### 9.1 화면 (§4 수정)

```
[레퍼토리 개요 스트립 — 그대로]
┌ 국면 탐색 ─────────────────────────────────────────────────────────┐
│ ① 메인 보드(두기 가능) 440px │ ③ 해설 패널 (보드 옆구리, 나머지 폭)   │
│   수순 · 이름 · 내 기록      │   수 라벨 · 이름 배지 · [요약|보통|깊이] │
│   ⏮◀▶⏭ 뒤집기 새수순 두기→  │   요약 2~3문장                          │
│ ⑤ 셋업 (진행 바, 남은 배치)  │   ▸ 왜 이 수인가 / 왜 책이 아닌가        │
│                              │   ▸ 상대의 응수와 계획                   │
│                              │   ▸ 대안과 비교 / 책 수와 비교           │
│                              │   ▸ 전형적인 실수와 함정 (수 → 보드 미리보기)│
│                              │   ▸ 내 기보에서   ▸ 더 깊이(엔진·마스터·질문)│
│                              │   근거: 책 · 구조 KB · 엔진 · 마이아 · LLM │
├──────────────────────────────┴──────────────────────────────────────┤
│ ② 다음 갈 수 있는 네임드 국면  n가지 · 내 기보 k가지  [내 판수|마스터|이름] │
│   그리드(auto-fill 212px) · 전부 표시 · 영역 안 스크롤(max-height 372px) │
└─────────────────────────────────────────────────────────────────────┘
[③ 해설 일지 — 시간순 색인. 한 줄 요약 + 배지, 클릭 = goto + 패널 전환. ↶ 되돌아가기 항목 유지]
[기물 목적지] [폰 브레이크] — 그대로
```

- 해설 패널은 `focusPly`(기본 = 커서)의 수를 보여 준다. 일지 항목 클릭은 `goto(ply)`이고 패널도 따라간다.
- 깊이 토글 `brief | normal | deep`: brief = 요약만, normal = + 왜/상대 응수, deep = 전부 펼침. `localStorage`에 기억.
- 함정 수순의 각 수는 버튼이고, 누르면 보드가 그 국면을 **미리보기**로 보여 준다(`state.preview = {fen, title, last}`; 보드 클릭 또는 "현재 국면으로"로 해제). 미리보기는 수순·일지를 바꾸지 않는다.
- 후보 카드: 미니보드 72px + `4.Ba4` 라벨 + 이름(이름 없는 중간 국면은 "…뒤 {이름}") + ECO + 내 판수·승률 막대 + 마스터 승률·레이팅대 선호도. 마스터 전용은 점선 테두리. 호버 = 보드 화살표. "기타 n가지" 접기는 없다.
- 검증 표시: 검증기가 확인한 문장은 점선 밑줄 + ✓(`.v`), 나머지는 책·LLM 견해. 패널 하단에 근거 배지.

### 9.2 깊은 해설 — `services/opening_notes.py` + 테이블 `opening_notes` (§5.3에 추가)

`opening_intent`의 결정론적 사실은 요약 줄과 배지에만 쓴다. 패널의 본문(왜 이 수인가, 상대 응수, 대안 비교, 함정, 내 기보)은 **LLM이 도구로 확인하며 쓴 구조화 노트**이고, (국면 키, 수) 당 한 번 만들어 저장한다.

```python
class TrapLine(BaseModel):
    title: str
    line_san: list[str]        # 이 국면(수를 둔 뒤)부터의 수순. 서버가 재생해 전부 합법인 것만 저장
    text: str                  # [[...]] 표기는 verify 통과 문장

class OpeningNote(BaseModel):
    position_key: str          # 수를 두기 전 국면 키
    san: str
    in_book: bool
    summary: str               # 2~3문장, [[...]] 검증 표기 포함
    why: list[str]             # 문단. 책 밖이면 "왜 책이 아닌가"
    replies: list[tuple[str, str]]       # (SAN, 설명)
    alternatives: list[tuple[str, str]]  # (SAN, 설명). 책 밖이면 책 수와 비교
    traps: list[TrapLine]
    mine: str | None           # 내 기록 문장은 서버가 지도 DAG 숫자로 채운다(LLM이 쓰지 않음)
    engine: str | None         # 책 밖일 때 play_coach.check 요약(서버가 채움)
    sources: list[str]         # ["book", "plans", "engine", "maia", "llm"]
    verified_claims: int; total_claims: int
    model: str; created_at: datetime
```

- **생성 경로**: `services/chat.py`와 같은 headless Claude Code(`claude -p`, 구독 로그인, `--strict-mcp-config`로 chess MCP 도구만). 프롬프트는 국면 FEN, 둔 수, 책 후보(`opening_guide.candidates`), 구조·계획(`plans.plan_specs`), 마이아 상위 3수, 엔진 3라인(깊이 12)을 **사실 블록**으로 주고, 위 스키마의 JSON(`--output-format json`, 스키마 강제)을 요구한다. 칸·기물을 말하는 문장은 `[[…]]`로 감싸고 `claims`를 달게 하며, 서버가 `verify_all`로 확인해 실패한 문장은 `[[ ]]`를 벗겨 "견해"로 강등한다(문장 삭제가 아니라 표기 강등: 해설의 흐름을 끊지 않기 위해). `line_san`은 재생해 불법이면 그 함정을 버린다.
- **API**: `GET /openings/note?fen=&san=` → 있으면 노트, 없으면 `{status:"missing"}`. `POST /openings/note` `{fen, san, username?}` → 생성 후 저장, 이미 있으면 그대로 반환, `regenerate:true`면 새로 생성. 생성은 10초 안팎이므로 202 + 폴링 대신 **동기 응답**(단일 사용자, `chat_concurrency` 슬롯 재사용)으로 시작하고, 웹은 "책 수·엔진 라인·구조 계획을 확인하며 쓰는 중…" 상태를 보여 준다. 스트리밍은 M8d.
- **`mine`은 항상 서버가 채운다**: 지도 DAG(`build_map`)의 그 간선 games/score, 그리고 그 국면 뒤 내가 자주 지는 다음 수 1개("…Na5 라인에서 3승 5패"). 사용자명이 없으면 None.
- 테이블: `opening_notes(id, position_key, san, lang='ko', json, model, verified_claims, total_claims, created_at)`; `(position_key, san, lang)` 유니크. `db.py`의 `create_all` 규칙 그대로.
- 테스트: `chat_claude_command`를 스텁 스크립트로 바꿔 고정 JSON을 내게 하고, (1) 저장·재사용 (2) `[[…]]` 검증 실패 문장 강등 (3) 불법 함정 수순 제거 (4) `mine` 채움 (5) 책 밖 `engine` 채움을 확인한다.
- 시안의 루이 로페즈 8국면 노트(`docs/opening-map-v2-mockup.html`의 `NOTES`)는 **시드 데이터**로 `assets/opening_notes_seed.json`에 넣어 첫 실행 때 적재한다(LLM 없이도 시연 가능).

### 9.3 `/annotate`와 일지 (§5.3·§6.1 수정)

- `MoveAnnotation.text`는 한 줄 요약(첫 문장)만 담당한다. 노트가 있으면 웹은 노트의 `summary` 첫 문장을 일지에 쓰고, 없으면 `annotation.text`.
- 일지 항목: 라벨 · 배지 최대 4개(+ 엔진 배지) · 한 줄 · "자세히 →"(포커스 중이면 "◀ 옆 패널에 표시 중"). 항목별 [엔진으로 확인][질문] 버튼은 패널의 "더 깊이"로 옮긴다.
- 일지 `move` 항목은 `seq`(그 수까지의 SAN 목록)를 저장한다. 되돌아간 뒤 흐려진 항목도 자기 수순의 노트를 가리켜야 하기 때문.

### 9.4 컴포넌트 (§6.2 수정)

| 파일 | 변경 |
|---|---|
| `ExplainPanel.tsx` (신규) | 위 패널. `useNote(seq)`로 노트 조회, 없으면 요약+배지+[깊은 해설 만들기]. 섹션은 `<details>`, 깊이 토글, 함정 미리보기, 근거 배지 |
| `Candidates.tsx` | 그리드 + 영역 스크롤 + 정렬 토글(내 판수/마스터/이름) + 하단 페이드. 접기 없음 |
| `Journal.tsx` | 한 줄 색인. `focusPly` 표시 |
| `LineBoard.tsx` | `preview` 상태 지원(칩 "미리보기 · 제목", 클릭 해제) |
| `line.ts` | `focusPly`, `depth`, `preview`, `sort` 추가. `journal.move.seq` |
| `api/client.ts` | `openings.note(fen, san)`, `openings.makeNote(body)` |

### 9.5 마일스톤 (§7 수정)

| 단계 | 내용 |
|---|---|
| **M8a** | 변경 없음: `setups.py`, `opening_guide.py`, `/position`, 스키마, 테스트 |
| **M8b** | `opening_intent.py` + `/annotate`(요약·배지용, §5.3) **+ `opening_notes.py` + `/openings/note` + 테이블 + 시드 + 테스트(§9.2)** |
| **M8c** | 웹: §9.1·§9.4. 시안의 동작을 그대로 옮긴다(시안 JS가 곧 명세) |
| **M8d** | 노트 스트리밍, `[질문]`을 `/play/chat`에 노트 문맥과 함께 연결, `sentences.py` 공유, 라우터 합치기, 문서 |

작업 규칙: 브랜치 `m8-opening-map`(워크트리 `chess-tutor-ai-m8`). 단계마다 커밋. `openings.py`·`openings_map.py`·`openings_catalog.py`·`routers/openings.py`·`play_coach.py`는 다른 작업이 병렬로 고치고 있으므로 **읽기만** 하고 새 파일에 쓴다. 웹 `pages/openings/index.tsx`·`Explorer.tsx`·`FocusBoard.tsx`·`openings.css`는 이 작업이 소유한다.
