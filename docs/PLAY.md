# M7 모의 게임 모드 기획

작성일: 2026-09-10. 코드 조사 기준: `apps/web/src/pages/training/Sparring.tsx`, `services/maia.py`, `services/plans.py`, `openings.py`, `routers/*.py`.

---

## 1. 한 줄 요약

보드에서 직접 게임을 두는 화면을 만든다. 양쪽을 손으로 두는 **수동 모드**와, 한쪽을 AI가 맡는 **AI 대국 모드**를 게임 도중 언제든 바꿀 수 있다. 시작 국면은 초기 국면, **오프닝 카탈로그의 타비야**, FEN, 리뷰·오프닝 지도의 국면 중에서 고른다. 게임이 끝나면 저장 → 분석 → 리뷰 → 채팅 → 퍼즐의 기존 파이프라인으로 그대로 흘러간다.

이 프로젝트의 차별점은 "엔진과 두기"가 아니라 **두는 동안과 둔 뒤의 설명**이다. 따라서 대국 자체보다 코치 정책(언제 무엇을 말해 주는가)과 오프닝 연습의 **계획 실행 추적**을 핵심으로 잡는다.

---

## 2. 지금 있는 것과 빈 곳

| 필요한 것 | 현재 | 빈 곳 |
|---|---|---|
| 사용자 vs 사람 같은 상대 | 트레이닝의 스파링 탭. `POST /maia/move`(Maia-2 → 레이팅 조건부 Stockfish 소프트맥스 → 균등 폴백). 레이팅 1000~2200, 물리기, 뒤집기, 리뷰 국면에서 이어 두기 | 게임을 PGN·DB로 남기지 않는다. 분석·리뷰로 이어지지 않는다. 수동 모드 없음. 승급 선택 없음(자동 퀸) |
| 게임 저장 | `POST /games/import/pgn` → `Game`(source `pgn`, 사용자 플랫폼 `local`) | "만든 게임" 경로 없음. `source` 값에 `practice` 추가 필요 |
| 사후 분석 | `POST /analysis/{id}` 잡 → 리뷰·전략 탭·채팅·퍼즐 전부 게임 id 기준 | 저장만 되면 변경 없음 |
| 오프닝 데이터 | `assets/openings_*.tsv`(lichess/chess-openings, CC0, 3,810개, 수순 포함), `openings.lookup(board)` | 연습용 카탈로그(대표 타비야 선별) 없음 |
| 구조·계획 | `structure.classify` 15종, `plans.PLANS` 14구조·114계획, `match_plans`(실행/예정/불가 판정), `break_hints` | 게임 중 힌트·사후 "계획 실행률" API 없음. 리뷰 전략 탭만 씀 |
| 엔진 강도 제한 | 없음. `Engine`은 Threads·Hash만 설정 | Stockfish 18은 `UCI_LimitStrength`/`UCI_Elo`(1320~3190), `Skill Level`(0~20) 지원 |
| 힌트·실수 판정 | 리뷰 파이프라인(`build_move_review`) — 저장된 게임 전용 | FEN만으로 도는 가벼운 판정·힌트 경로 없음 |
| 채팅 | `POST /review/{game}/{ply}/chat`, 프롬프트가 저장된 수 리뷰에서 만들어짐 | 라이브 국면용 세션 없음 |
| 웹 공통 | `Board`(movable/dests/shapes), `legalDests`·`applyUci`(chess.js), `MiniBoard`, 트레이닝 2열 레이아웃 CSS, `useBoardSize` | 시계 없음, 승급 피커 없음, 게임 상태 저장소 없음 |

결론: **새로 짜는 것보다 잇는 것이 많다.** 스파링 탭을 `/play` 화면으로 승격하고, 저장 경로 하나와 힌트·판정 엔드포인트, 오프닝 카탈로그를 더한다.

---

## 3. 사용자 시나리오

1. **리뷰에서 이어 두기.** 리뷰 중 "여기서 내가 두면?" → `/play?game=596&ply=24&color=white` → 그 국면부터 Maia 1500과 둔다. 끝나면 저장해서 원본 게임과 나란히 리뷰.
2. **오프닝 타비야 대국.** 카탈로그에서 "칼스바드(QGD 익스체인지)"를 백으로 고른다. 타비야에서 시작해 Maia와 둔다. 끝나면 "이 구조의 계획 3개 중 소수 공격 실행, e4 브레이크 미실행" 리포트.
3. **오프닝 수순 드릴.** 같은 오프닝을 1수부터. AI가 책 응수를 두고, 내가 책에서 벗어나면 책 수와 이유를 보여 준 뒤 다시 두거나 그대로 진행(Maia 응수)한다. 타비야에 닿으면 자유 대국.
4. **수동 분석판.** 친구 게임을 실시간으로 입력하거나 책의 수순을 따라 둔다. 필요하면 어느 국면에서든 "여기서부터 AI가 흑" 을 켠다.
5. **프로필의 레퍼토리 구멍 → 연습.** 프로필이 "프렌치 어드밴스에서 3승 9패"라면 그 항목에 "연습하기" 버튼이 붙어 카탈로그 항목으로 간다.

---

## 4. 모드 설계

### 4.1 시작 국면

| 선택지 | 진입 |
|---|---|
| 초기 국면 | `/play` |
| 오프닝 카탈로그 | `/play?opening=carlsbad-exchange&color=white&drill=1` |
| FEN 입력 | 화면의 "국면 설정" 대화상자 → `/play?fen=…` |
| 리뷰·오프닝 지도·퍼즐에서 이어 두기 | `/play?game=&ply=&color=` 또는 `?fen=` (지금의 스파링 링크와 호환) |

### 4.2 진행 방식: 수동 ↔ AI

- 상단 토글 하나: **수동** / **AI가 백** / **AI가 흑**. 언제든 바꿀 수 있고, AI 차례가 되면 즉시 둔다. AI 응수는 "생각 중" 표시 후 300ms 이상 지연을 둬 사람이 따라갈 수 있게 한다.
- 상대 종류(`OpponentSpec`):
  - `maia` — 레이팅 1100~2000(Maia-2 버킷). 기본값. 사람 같은 실수를 한다(PLAN 3.4 용도 3).
  - `stockfish` — `UCI_Elo` 1320~3190 + `movetime`. 2000 초과 상대, 또는 maia2 미설치 환경용. 현재 폴백인 소프트맥스 백엔드보다 강하지만 실수 패턴이 부자연스럽다.
  - (나중) 개인 모델. 프로필의 실수 분포를 흉내 내는 상대는 Maia4All류 연구가 필요해 미결로 둔다.
- 상대 레이팅 기본값은 사용자의 최근 레이팅(`User.rating_rapid`)이 있으면 그 값, 없으면 설정의 `default_rating`.
- 수 되돌리기(물리기): 사용자 차례로 돌아올 때까지 pop. 횟수는 PGN 헤더에 남긴다.
- 게임 중 뒤로 탐색(←/→, Home/End)은 커서만 움직인다. 커서가 끝이 아닌 상태에서 수를 두면 뒤의 수를 잘라내되 한 번 확인한다. 변화수 트리는 만들지 않는다(리뷰 화면과 역할이 겹친다).
- 종료 판정은 chess.js: 체크메이트, 스테일메이트, 3회 반복, 50수, 기물 부족. 그 외 **기권**, **무승부 제안**(AI는 최근 두 수의 엔진 평가가 ±30cp 안이고 30수 이후일 때 수락).

### 4.3 코치 정책

게임 중 도움을 어디까지 줄지 프리셋 세 개로 정하고, 세부 항목은 개별로 바꿀 수 있다.

| 프리셋 | 실수 알림 | 물리기 | 힌트 | 용도 |
|---|---|---|---|---|
| 진지하게 | 끔 | 끔 | 끔 | 실전과 같은 조건. 프로필 통계에 그대로 반영 |
| 배우면서 (기본) | 블런더만 | 켬 | 켬 | 학습용. 통계에는 "도움 받음" 표시 |
| 자유 | 끔 | 켬 | 켬 | 수동 분석판 |

**힌트는 3단계**이고, 단계마다 무엇을 보여 줬는지 기록한다. 원칙 1·3에 따라 모든 힌트는 수순 또는 구조 근거를 달고 검증기를 통과한 문장만 낸다.

1. **구조와 계획** — 수를 말하지 않는다. "지금 구조는 칼스바드. 백의 전형적 계획은 소수 공격(b4-b5), e4 브레이크, f3-e4." (`plans.plan_specs`)
2. **자연스러운 후보** — Maia 상위 2~3수와 한 줄 이유. "이 레이팅대에서는 Nf3(41%)·Bd3(22%)를 둔다." (`maia.move_probs` + `reasoning.explain_alternative`)
3. **최선수와 이유** — 엔진 최선수, 주변화, 모티프. (`analysis.get_lines`, `motifs.detect`)

**실수 알림**은 사용자의 수 직후 얕은 깊이(12)로 승률 손실을 재서 블런더/실수 문턱을 넘으면 배지를 띄운다: "이 수는 큰 실수일 수 있어요 — 물리기 / 그대로 두기 / 이유 보기". AI 응수는 알림과 병렬로 계산하고, 물리기를 누르면 두 플라이를 되돌린다. 원칙 4: 최선수가 컴퓨터 수(Maia 확률 3% 미만)이면 "이유 보기"는 레벨에 맞는 대안을 함께 낸다.

### 4.4 오프닝 연습

**카탈로그.** 손으로 고른 대표 오프닝 30개 안팎. 각 항목은 ECO 코드와 TSV의 이름만 들고 있고, 수순·타비야 FEN은 `assets/openings_*.tsv`에서 가져온다(직접 적지 않으므로 오타가 없고, 테스트로 전부 존재를 확인한다). 타비야 국면의 폰 구조는 `structure.classify`로 계산하고, 그 구조의 계획은 `plans.PLANS`에서 온다. 즉 **카탈로그에 손으로 적는 데이터는 이름 30줄뿐**이다.

초기 후보(가족별). 아래 이름은 모두 `assets/openings_*.tsv`에 있는 항목과 대조했다(2026-09-10):

| 가족 | 항목 | 닿는 구조(예상) |
|---|---|---|
| 1.e4 e5 | 이탈리안(지오코 피아노) C53, 루이 로페즈 클로즈드 C84, 스카치 C45, 페트로프 C42 | 오픈 센터, 닫힌 센터 |
| 시실리안 | 나이도르프 B90, 드래곤 B70, 셰베닝겐 B80, 스베시니코프 B33, 알라핀 B22, 악셀러레이티드 드래곤 마로치 바인드 B36 | 셰베닝겐, 볼레슬랍스키 홀, 마로치 |
| 1.e4 기타 | 프렌치 어드밴스 C02, 프렌치 위나워 C15, 카로칸 어드밴스 B12, 카로칸 클래시컬 B18, 스칸디나비안 B01, 피르츠 B07 | 프렌치 사슬, 슬라브/카로칸 |
| 1.d4 d5 | QGD 익스체인지(칼스바드) D35, QGD 오소독스 D63, 슬라브 D15, 세미슬라브 메란 D47, QGA D20, 타라시 D34, 런던 D02 | 칼스바드, 슬라브/카로칸, IQP, 대칭 d폰 |
| 1.d4 인디언 | 님조 인디언 E32, 퀸즈 인디언 E12, 킹스 인디언 클래시컬 E97, 그륀펠트 익스체인지 D85, 모던 베노니 A60, 카탈란 E04 | KID, 베노니, 행잉 폰, 오픈 센터 |
| 플랭크 | 잉글리시 대칭(헤지호그) A30, 레티 A05 | 헤지호그, 대칭 d폰 |

카탈로그 화면은 가족별 그리드. 각 카드에 `MiniBoard`(타비야), 구조 라벨, 내 기록(오프닝 지도 노드의 games/score, 연습 게임 성적), "백으로/흑으로" 버튼.

**서브모드 두 개.**

- **타비야 대국** (핵심). 타비야에서 시작해 AI와 둔다. 끝나면 **계획 실행 리포트**: `plans.match_plans(structure, side, pvs, board, played_moves)`로 이 구조의 권장 계획 중 실행한 것(`executed`), 미룬 것(`later`), 불가능해진 것(`unavailable`)을 나누고, 브레이크 타이밍을 `GET /openings/breaks`의 마스터 중앙값과 비교한다. 리뷰 전략 탭이 같은 재료를 쓰므로 리포트는 리뷰 화면의 게임 단위 요약으로 붙인다.
- **수순 드릴.** 1수부터. 내 차례엔 책 수를 기다리고, AI 차례엔 카탈로그 수순을 둔다. 내가 벗어나면 "책 수는 Nf3. 이유: …(구조·계획 근거)"를 보여 주고 **다시 두기 / 그대로 진행**을 고른다. 그대로 가면 그 시점부터 Maia가 응수한다. 타비야에 닿으면 자동으로 타비야 대국이 된다. 드릴은 짧다(타비야까지 8~12수). 수순 암기가 목적이 아니라 타비야까지 데려다주는 장치다(원칙 5).
- 수동 모드의 **책 따라가기** 토글: 현재 국면에서 TSV에 있는 다음 수를 화살표로 표시한다(`openings.lookup`이 위치 키로 조회하므로 전위도 잡는다). 학습 보기용.

**진도.** 연습 게임은 헤더 `Opening`·`ECO`·`PracticeMode`(`drill`|`tabiya`)로 저장되므로 항목별 드릴 성공률·대국 성적·계획 실행률을 집계할 수 있다. 간격 반복 덱(퍼즐의 SM-2 재사용)은 미결로 둔다.

### 4.5 종료와 저장

- 끝나면(또는 "지금 저장") PGN을 만들어 `POST /play/games`로 보낸다. 서버가 헤더를 채우고(`Event "chess-tutor practice"`, `White`/`Black`에 사용자명과 `Maia 1500`처럼 상대 이름, `WhiteElo`/`BlackElo`, `Result`, `Opening`/`ECO`, `Mode`, `Opponent`, `Hints`, `Takebacks`, `Alerts`, `PracticeMode`, 시계가 있으면 `%clk`), `source="practice"`로 `upsert_games`에 넣은 뒤 분석 잡을 바로 올린다. 응답의 `game_id`로 `/review/{id}`에 간다.
- 저장하지 않은 진행 중 게임은 `localStorage`(`chess-tutor:play:current`)에 두어 새로고침에도 남긴다. 저장하면 지운다.
- 프로필·오프닝 지도는 기본적으로 `practice` 게임을 **제외**한다(도움 받은 게임이 통계를 흐린다). `?include_practice=1`로 포함. 트레이닝 퍼즐 생성은 허용한다(내 실수는 내 실수다).

### 4.6 화면

```
┌ 대국 ─────────────────────────────────────────────────────────────┐
│ [수동] [AI가 백] [AI가 흑]   상대: Maia 1500 ▾   코치: 배우면서 ▾  │
├───────────────────────────┬────────────────────────────────────────┤
│                           │ 수 목록 │ 코치 │ 오프닝 │ 설정          │
│        보드 (520px)       │ 1. d4 d5  2. c4 e6  3. Nc3 Nf6 …       │
│   (평가 막대는 코치 탭의  │                                        │
│    힌트 3단계에서만 표시) │ ⚠ 12…Bxh2+ 는 큰 실수일 수 있어요      │
│                           │   [물리기] [그대로] [이유 보기]         │
├───────────────────────────┤                                        │
│ ⏮ ◀ ▶ ⏭  뒤집기  물리기   │ 힌트: [구조·계획] [자연스러운 수] [최선] │
│ 국면 설정  기권  무승부   │ 이번 게임: 힌트 1회, 물리기 0회         │
│ PGN 복사  저장해서 리뷰   │                                        │
└───────────────────────────┴────────────────────────────────────────┘
```

- 트레이닝 화면의 2열 CSS(`tr-page/tr-body/tr-left/tr-board/tr-controls/tr-panel/tr-movelist`)를 그대로 쓴다.
- 승급 피커를 드디어 만든다(§8 알려진 간극). 보드 위에 4기물 팝오버. `Board` 호출자들의 자동 퀸 코드를 이 컴포넌트로 바꾼다.
- 왼쪽 레일에 "대국" 항목 추가. 트레이닝의 스파링 탭은 `/play`로 보내는 링크로 바꾸고 한 릴리스 뒤 제거.

---

## 5. 백엔드 설계

### 5.1 엔드포인트 (`routers/play.py`)

| 경로 | 요청 | 응답 | 비고 |
|---|---|---|---|
| `POST /play/move` | `{fen, opponent: OpponentSpec, user_rating?, seed?}` | `{san, uci, source, probs?, think_ms}` | `/maia/move`의 상위 호환. 평가치는 돌려주지 않는다(대국 중 엔진 보조 방지) |
| `POST /play/hint` | `{fen, level: 1\|2\|3, rating, side, opening_id?}` | `{level, structure, plans[], candidates[], best?, claims, verified}` | 레벨별 재료는 4.3 참조. 문장은 `verify`를 통과한 것만 |
| `POST /play/check` | `{fen_before, san, rating, depth=12}` | `{classification, win_loss, best_san, pv, reason, claims, verified, computer_move}` | 실수 알림. `EngineCache`로 캐시 |
| `POST /play/games` | `PracticeGameIn` | `{game_id, analysis_status}` | PGN 조립·저장·분석 큐 |
| `GET /play/openings` | `?username=` | `OpeningCard[]` | 카탈로그 + 내 기록 |
| `GET /play/openings/{id}` | | `OpeningDetail{line_san[], tabiya_fen, structure, plans{white,black}, breaks}` | 드릴·타비야 대국 시작 데이터 |
| `GET /review/{game_id}/plan-report` | | `PlanReport` | 타비야 대국 사후 리포트. 분석 완료 후 |

스키마 초안:

```
OpponentSpec { kind: "maia" | "stockfish", rating: int }        # maia 1100-2000, stockfish 1320-3190
PracticeGameIn {
  username: str, user_color: "white" | "black" | null,           # null = 수동(양쪽)
  start_fen: str, moves_san: list[str], result: "1-0"|"0-1"|"1/2-1/2"|"*",
  opponent: OpponentSpec | null, coach: {preset, hints: int, takebacks: int, alerts: int},
  opening_id: str | null, practice_mode: "free"|"drill"|"tabiya", clocks: list[int] | null,
}
PlanReport { structure, side, executed: Plan[], later: Plan[], unavailable: Plan[], break_timing: {...}, summary: str }
```

### 5.2 엔진과 Maia

- `engine.py`에 `PlayEngine`: 풀과 별개의 전용 인스턴스(옵션이 다르면 분석 캐시 정체성이 깨지므로 풀에 넣지 않는다). `UCI_LimitStrength=true, UCI_Elo=N`, `play(board, movetime_ms)`. 스파링 소프트맥스 백엔드처럼 락으로 직렬화.
- `maia.Backend.move_probs`에 `opp_rating` 인자 추가. 지금은 self·opp 레이팅을 같은 값으로 넘긴다(`maia.py:321`). 상대로 둘 때는 self=상대 레이팅, opp=사용자 레이팅이 맞다.
- Maia 응답 지연은 실측이 없다. 첫 로드(가중치 로드) 수 초, 이후 수당 CPU 추론 시간을 M7a에서 잰다. 1초를 넘으면 클라이언트에 "생각 중" 애니메이션과 취소를 둔다.

### 5.3 데이터 모델

- `Game.source`에 `practice` 추가(문자열 컬럼이라 마이그레이션 없음, `platform_for` → `local`). `GET /games?source=`, 프로필의 `include_practice`.
- 헤더는 `Game.headers` JSON에 그대로 들어간다. 새 테이블 없음.
- 카탈로그는 코드 상수(`services/openings_catalog.py`): `{id, family, name_ko, eco, tsv_name, practice_sides}`.

---

## 6. 프론트 설계

- 라우트 `/play`, `pages/play/index.tsx`. 상태는 리듀서 하나(`usePlayGame`):
  ```
  PlayState { startFen, plies: Ply[], cursor, control: 'manual'|'ai-white'|'ai-black',
              opponent, coach, opening: {id, line, drillUntil} | null, status: 'playing'|'over'|'saved',
              stats: {hints, takebacks, alerts}, pendingAlert: CheckResult | null }
  ```
  `Ply`는 스파링의 것에 `by: 'user'|'ai'|'book'`과 `hint_level`을 더한다.
- 컴포넌트: `PlayPage`, `ControlBar`(수동/AI 토글·상대·코치), `CoachPanel`, `OpeningPicker`(카탈로그 그리드), `PromotionPicker`(공용 `components/`), `MoveListLocal`(스파링의 rows 빌더를 분리), 나중에 `Clock`.
- `api/client.ts`에 `play.*`와 `analysis.position`, `maia.probs` 추가.
- 스파링과 퍼즐의 자동 퀸 코드를 `PromotionPicker`로 교체.
- vitest 도입(이미 `apps/web/tests/*.test.ts` 3개가 러너 없이 있다). 리듀서 테스트: 커서 뒤에서 두면 잘라내기, 물리기, 책 이탈 감지, 승급, 종료 판정.

---

## 7. 단계별 계획

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| **M7a 코어** | `/play` 화면, 수동↔AI 토글, Maia 상대(기존 `/maia/move`), 승급 피커, 물리기·탐색·종료 판정, `POST /play/games` 저장 → 리뷰 링크, localStorage 복원, 스파링 탭 → 링크 | 초기 국면에서 Maia와 한 판 두고 저장하면 `/review/{id}`가 열리고 분석이 끝난다. `test_play.py`: 저장 → `source=practice` → 분석 → 리뷰 200. vitest 리듀서 테스트 통과 |
| **M7b 오프닝 연습** | 카탈로그 상수·`GET /play/openings*`, 카탈로그 화면, 타비야 대국, 수순 드릴(책 이탈 처리), 책 따라가기 토글, 리뷰의 계획 실행 리포트 | 카탈로그 전 항목이 TSV에서 해석되고 타비야 FEN이 합법. 항목의 80% 이상이 `unclassified`가 아닌 구조로 분류. 칼스바드 타비야 대국 뒤 리포트에 소수 공격이 executed/later 중 하나로 나온다 |
| **M7c 코치** | `POST /play/hint`(3단계)·`POST /play/check`, 코치 프리셋, 실수 알림 UI, 헤더 기록, 프로필 `include_practice` | 힌트 문장의 검증 통과율 100%(실패 문장은 템플릿 대체). 알림 왕복 2초 이내(깊이 12, 캐시 적중 시 즉시) |
| **M7d 확장** | Stockfish Elo 상대(`PlayEngine`), Maia opp_rating 분리, 시계(`%clk` 저장 → 프로필의 시간 압박 지표에 연결), 기권·무승부, 프로필 레퍼토리 구멍 → "연습하기", 라이브 국면 채팅 세션 | 각 항목 테스트. 채팅은 `chat_prompt`에 게임 없는 프롬프트 빌더가 생기고 `tests/test_chat.py`에 라이브 국면 케이스 추가 |

예상 규모: M7a 1~2일, M7b 2일, M7c 2일, M7d 2~3일. M7a와 M7b를 먼저 붙이면 "오프닝 종류별로 연습"이 이미 돈다. 코치 없이도 사후 리뷰가 붙기 때문이다.

---

## 8. 반론과 결정

1. **"AI 대국은 chess.com의 컴퓨터 대국과 뭐가 다른가."** 대국 자체는 다르지 않다. 다른 것은 (a) 코치 정책과 구조 기반 힌트, (b) 타비야 대국의 계획 실행 리포트, (c) 저장 즉시 근거 있는 리뷰·채팅으로 이어지는 점이다. M7a만 만들고 멈추면 차별점이 없으므로 M7b까지를 한 묶음으로 본다.
2. **"게임 중 실수 알림은 엔진 의존을 키운다."** 맞다. 그래서 "진지하게"는 알림·힌트를 끄고, 기본 프리셋도 블런더만 알린다. 도움 여부는 헤더에 남아 프로필이 구분한다. 평가 막대는 대국 중 보이지 않는다.
3. **"오프닝 드릴은 PLAN.md가 거부한 수순 암기다."** 드릴은 타비야까지만(8~12수)이고 이탈 시 이유를 구조·계획으로 설명한다. 측정 지표도 "수순 재현율"이 아니라 타비야 이후의 **계획 실행률**과 성적이다.
4. **"Maia-2는 1100~2000만 지원하고 self/opp 레이팅을 구분하지 않는다."** 2000 초과는 Stockfish Elo로, 1100 미만은 Maia 1100으로 클램프한다. opp_rating 분리는 한 줄 변경이라 M7d에 넣는다. 약화된 Stockfish는 무작위 블런더가 부자연스러우니 기본 상대는 Maia로 둔다(PLAN 3.4).
5. **"수동 모드는 누가 쓰나."** 분석판 용도(친구 게임 입력, 책 수순 따라 두기, 국면 설정)이고, AI 모드와 같은 리듀서를 쓰므로 추가 비용이 거의 없다. 변화수 트리는 만들지 않는다.
6. **"TSV에는 인기도가 없어 AI의 책 응수가 한 가지뿐이다."** 드릴에서는 카탈로그 수순만 책으로 삼는다. 여러 갈래를 주려면 `LICHESS_TOKEN`의 익스플로러나 내 기보의 오프닝 지도를 붙인다. 미결.
7. **"연습 게임이 프로필을 흐린다."** 기본 제외, 옵션 포함. 퍼즐 생성은 허용.

---

## 9. 측정

- 오프닝별: 드릴 이탈 지점 분포, 타비야 대국 성적, 계획 실행률(executed / (executed+later)), 브레이크 타이밍 vs 마스터 중앙값.
- 같은 오프닝의 임포트 게임 성적과 연습 게임 성적 비교(연습이 실전으로 옮겨 가는지).
- 힌트 문장 검증 통과율, 실수 알림의 오탐(재검 깊이 18에서 등급이 바뀐 비율).
- Maia·Stockfish 응답 지연 p50/p95.

---

## 10. 미결

- 카탈로그 30개의 최종 목록과 각 항목의 TSV 이름 매핑(테스트가 강제).
- 무승부 수락 기준(±30cp, 30수)의 근거 없음. 자리표시자.
- 시계는 사용자 시간 관리 연습용일 뿐 Maia는 시간을 모른다. 상대 시계는 장식이다.
- 라이브 국면 채팅은 답을 알려 주는 셈이라 코치 프리셋 "진지하게"에서는 막는다.
- 간격 반복 오프닝 덱(퍼즐 SM-2 재사용) 여부.
