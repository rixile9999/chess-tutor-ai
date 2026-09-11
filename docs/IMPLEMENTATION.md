# 구현 계획

작성일: 2026-09-02. `PLAN.md`의 목표와 아키텍처를 실제 코드로 옮기기 위한 결정과 순서.

---

## 1. 기술 스택

| 영역 | 선택 | 근거 |
|---|---|---|
| 백엔드 언어 | Python 3.12 | python-chess, Maia-2(pip), 엔진 UCI 제어, 데이터 처리 생태계가 모두 파이썬에 있다 |
| 웹 프레임워크 | FastAPI + Pydantic v2 | 계층 2·3의 출력(JSON)이 곧 API 스키마이자 LLM 입력이다. 한 곳에서 정의하고 검증한다 |
| 패키지·실행 | uv | 잠금 파일, 파이썬 버전 고정, CI에서 같은 환경 재현 |
| 엔진 | Stockfish 17+ (UCI 서브프로세스, MultiPV, UCI_ShowWDL) | 진실 오라클. GPL 바이너리는 별도 프로세스로 실행한다 |
| 사람 수 모델 | Maia-2 (MIT) 먼저, Maia-3 평가 후 교체 검토 | 레이팅 조건부 수 예측. pip 설치 가능 |
| 데이터베이스 | PostgreSQL 16 + SQLAlchemy 2 + Alembic | 기보, 분석 캐시(JSONB), 프로필, 퍼즐 스케줄 |
| 작업 실행 | 처음엔 프로세스 내 워커, 필요해지면 arq + Redis | 엔진 분석은 CPU 바운드. 인프라를 미리 늘리지 않는다 |
| LLM | Anthropic Python SDK, `claude-opus-5`, structured outputs | 언어화 전용. 입력은 계층 2·3의 사실 JSON뿐, 출력은 문장 + 근거 주장(Claim) 목록 |
| 국면 채팅 | Claude Code 헤드리스(`claude -p`, 구독 로그인) + MCP(Python SDK 2.x, FastAPI에 마운트) | API 키 없이 Max 구독으로 튜터와 문답. 모델은 내장 도구 없이 이 서버의 체스 도구만 쓴다 |
| 프론트엔드 | Vite + React 19 + TypeScript | 목업의 화면 구성이 컴포넌트 트리와 일치한다 |
| 보드 | chessground 9 (GPL-3.0) | Lichess 보드. 화살표, 하이라이트, 좌표 내장 |
| 시각화 | d3 7 | 오프닝 DAG, 히트맵, 브레이크 타임라인 |
| 스타일 | CSS 변수 + 컴포넌트별 CSS | 목업의 토큰을 `apps/web/src/tokens.css`로 옮겼다. 프레임워크 불필요 |
| 테스트 | pytest, vitest, Playwright(후순위) | 계층 2는 국면 단위 단위 테스트가 핵심 |
| 품질 | ruff, mypy(strict), eslint, prettier | |
| CI | GitHub Actions | API 린트+테스트, 웹 빌드 |
| 배포 | 로컬 우선(docker compose로 Postgres) | 웹 서비스 배포는 4단계 이후 결정 |

**선택하지 않은 것.** Node 백엔드(엔진·Maia 생태계 부재), Tailwind(토큰 수가 적어 불필요), 그래프 DB(오프닝 DAG는 Postgres 테이블로 충분), LangChain류(LLM 호출이 한 종류뿐).

---

## 2. 저장소 구조

```
chess-tutor-ai/
  PLAN.md                 목표·아키텍처·단계 (변경 시 여기부터)
  docs/IMPLEMENTATION.md  이 문서
  design/                 UI 목업 소스 (build.mjs → *.dc.html)
  apps/api/               Python 백엔드
    src/chess_tutor/
      engine.py           계층 1: Stockfish UCI 래퍼
      motifs.py           계층 2: 전술 모티프 탐지기
      verify.py           계층 4 가드: 주장 검증기, 수순 재생
      services/chat*.py   국면 채팅: Claude Code 서브프로세스, MCP 체스 도구, 튜터 프롬프트
      services/opening*.py 오프닝 지도 v2: 국면 안내·수의 의도·깊은 해설·더 깊이 (M8)
      services/sentences.py 문장과 그 근거 주장, 검증 게이트 (계층 4 가드와 짝)
      values.py           기물 가치
      api.py              앱 조립 (미들웨어·수명주기·라우터 마운트)
      routers/            HTTP 엔드포인트. 경로 묶음 하나에 파일 하나 (`/openings` 는 openings.py 전부)
    tests/                국면 단위 테스트 (목업의 예시 국면 포함)
  apps/web/               Vite + React 프론트엔드
    src/Board.tsx         chessground 래퍼
    src/pages/openings/   오프닝 지도 v2: 보드·후보·해설 패널·일지 (M8)
    src/tokens.css        디자인 토큰
  docker-compose.yml      Postgres
  .github/workflows/      CI
```

계층 번호는 `PLAN.md` 6절의 아키텍처를 따른다. 새 모듈은 계층 하나에만 속하게 만든다. 계층 2 모듈은 결정론적이어야 하고, LLM을 호출하는 코드는 계층 4에만 둔다.

---

## 3. 핵심 데이터 흐름

```
PGN/API 임포트
  → Game, Position 저장
  → 엔진 분석 (MultiPV, 깊이 N) → EngineLine[] (JSONB 캐시, FEN+깊이 키)
  → 실수 분류 (평가 낙폭)
  → 계층 2: Motif[], StructureTag, FeatureDiff, PlanCandidate[]
  → 계층 3: 분기점 비교, 반사실, Maia 대비 → ExplanationFacts (JSON)
  → 계층 4: LLM 언어화 → {sentences[], claims[]}
  → 검증기: claims를 보드와 대조 → 실패 문장 제거·템플릿 대체
  → Review 저장 → 프론트 렌더
```

검증기는 이미 구현되어 있다(`verify.py`). 언어화 모듈은 문장마다 사용한 사실을 `Claim`으로 함께 내놓아야 하고, 하나라도 틀리면 그 문장은 나가지 않는다.

---

## 4. 데이터 모델 초안

- `users` (chess.com / lichess 계정 연결)
- `games` (pgn, 출처, 시계, 결과, 오프닝 ECO)
- `positions` (fen, game_id, ply) → `engine_lines` (fen, depth, multipv, lines JSONB)
- `reviews` (game_id, ply, classification, facts JSONB, explanation JSONB, verified bool)
- `structures` (자체 폰 구조 분류 체계) / `plans` (구조별 계획 지식베이스)
- `puzzles` (user_id, fen, solution, source_game, due_at, interval) — 간격 반복

분석 결과는 FEN + 엔진 버전 + 깊이를 키로 캐시한다. 같은 국면을 다시 계산하지 않는다.

---

## 5. 마일스톤

각 단계는 `PLAN.md` 7절과 같다. 여기서는 완료 기준을 코드 수준으로 적는다.

### M0 스캐폴드 (완료)
- 백엔드 골격, 모티프 탐지기 2종(디스커버드 어택, 포크), 주장 검증기, HTTP API, 테스트 11개
- 프론트 골격, chessground 보드, API 호출
- CI, 라이선스, 문서

상태 표기: **완료** = 코드·테스트·화면이 있고 통합 테스트를 통과. **부분** = 동작하지만 완료 기준의 일부가 남음. 2026-09-02 통합 기준.

### M1 근거 있는 게임 리뷰 (MVP) — 완료
- PGN 임포트(`services/games.py`), Chess.com·Lichess API 임포트(`services/importers.py`, respx 목 테스트). `POST /games/import/{pgn,chesscom,lichess}`
- Stockfish 분석 파이프라인 + 캐시(`services/analysis.py`, `engine_cache` 테이블: FEN+엔진+깊이+MultiPV 키). 프로세스 내 잡 러너(`jobs.py`), `POST /analysis/{id}` → `GET /analysis/{id}` 폴링
- 실수 분류 7종(book/best/good/inaccuracy/mistake/blunder/forced), 승률 손실 기준. 정확도는 lichess 곡선
- 모티프 탐지기 10종(`motifs.py`): 디스커버드 어택, 포크, 핀, 스큐어, 무방비 기물, 수비수 제거, 과부하, 백랭크, 기물 트랩, 메이트 위협
- 정적 특징(`features.py`), 분기점 비교·특징 차이표·반사실(`services/reasoning.py`)
- 언어화(`services/verbalize.py`): 템플릿이 기본. `ANTHROPIC_API_KEY`가 있으면 LLM(structured output)이 문장+Claim을 내고, 검증기에 실패한 문장은 템플릿으로 대체. 리뷰 응답에 `verified_claims/total_claims`가 실린다
- 리뷰 화면(`apps/web/src/pages/review`): 보드·화살표·수 목록·평가 스파크라인·설명/특징/전략 패널
- 남은 것: Lichess 퍼즐 DB로 탐지기 정밀도·재현율 측정(미착수). 게임을 다시 분석해도 `move_reviews` 캐시는 깊이·레이팅 키로만 무효화된다

### M2 Maia — 완료
- Maia-2 지연 로드(`services/maia.py`, `uv sync --extra maia`). 패키지·가중치가 없으면 레이팅 조건부 Stockfish 소프트맥스 → 균등 분포 순으로 폴백하고 응답의 `source`에 어느 백엔드였는지 적는다
- 리뷰의 `human` 뷰: 레이팅별 수 확률, 플레이한 수의 확률, 컴퓨터 수 판정(최선수 확률 3% 미만), 자연스러운 이유(Claim 포함)
- 스파링 `POST /maia/move`, 분포 `POST /maia/probs`, 상태 `GET /maia/status`. 트레이닝 화면의 스파링 탭
- 남은 것: 설명 난이도 조절은 `rating` 파라미터가 사람 뷰에만 반영되고 문장 수준은 바꾸지 않는다. Maia-3 평가 미착수

### M3 전략 계층 — 부분
- 폰 구조 분류 체계 15종 + unclassified(`structure.py`), 계획 지식베이스 14구조·114계획(`services/plans.py`), PV 계획 추출(`PlanSketch`)과 매칭, 반사실 검증(`services/reasoning.py`)
- 전략 탭(`pages/review/StrategyPanel.tsx`): 구조·타임라인·계획·내 수·반사실·개인 기록
- 남은 것: 수작업 라벨 테스트셋(수백 국면)은 없다. 현재 구조 테스트는 대표 국면 13개. 계획 지식베이스는 검토된 적 없는 초안이다

### M4 개인화 — 완료
- 전체 기보 임포트, 프로필 리포트 `GET /profile/{username}`(`services/profile.py`): 단계별 정확도(레이팅대 기준선 대비), 구조별 성적과 브레이크 타이밍, 놓친 모티프, 시간 압박 블런더율, 레퍼토리 구멍. 프로필 화면(`pages/profile`)
- 내 기보 퍼즐 + SM-2 간격 반복(`services/puzzles.py`): `POST /training/puzzles/from-game/{id}`, `GET /training/puzzles/due`, `POST /training/puzzles/{id}/attempt`, `GET /training/summary`. 트레이닝 화면(`pages/training`)
- 남은 것: 레이팅대 기준선(`profile.BASELINES`)은 자리표시자 값이라 Lichess DB로 측정해야 한다. 구조 스터디는 제목만 나온다

### M5 시각화 — 완료
- 오프닝 지도 `GET /openings/map`(`services/openings_map.py`): 국면 키 DAG(전위 병합), 이탈점·타비야 표시, `LICHESS_TOKEN`이 있으면 Lichess 익스플로러 마스터 오버레이. 화면은 아이시클 개요 스트립 + 브레드크럼 + 큰 국면 보드 + 열 탐색기(갈림길 단위, 외길은 접음)
- 기물 목적지 히트맵 `GET /openings/heatmap`, 브레이크 타임라인 `GET /openings/breaks`(브레이크 11종)
- 남은 것: 마스터 오버레이는 토큰 없이는 꺼져 있다(월간 DB 자체 집계 미착수)

### M6 국면 채팅 (튜터에게 질문) — 완료 (2026-09-04)
- 리뷰 화면 네 번째 탭. 학생이 이 수에 대해 질문하거나 추천 수에 반론하면 튜터가 보드를 움직이며 답한다. 보드에서 기물을 직접 움직이면 그 수가 질문이 된다(`ReviewPanel`·`ChatPanel.tsx`, `Board`는 채팅 탭에서만 movable)
- 실행 경로: `POST /review/{game}/{ply}/chat` → `services/chat.run_turn`이 `claude -p --output-format stream-json --include-partial-messages --tools "" --strict-mcp-config --mcp-config … --allowedTools mcp__chess__* --system-prompt-file … --session-id|--resume <id>`를 자기 프로세스 그룹으로 띄운다. 질문은 stdin, 프롬프트는 파일(argv 크기 한도 회피). 첫 질문은 `--session-id`, 이후는 `--resume`. CLI의 init 줄이 오는 순간부터 그 id는 CLI 것이므로 타임아웃이나 탭 닫힘 뒤에도 다음 질문은 `--resume`한다("already in use"가 오면 한 번 `--resume`으로 재시도). `--bare`는 구독 로그인을 읽지 않으므로 쓰지 않는다. 환경에서 `ANTHROPIC_API_KEY`는 제거해 구독이 쓰이게 한다
- 동시성: 같은 대화의 두 번째 질문은 0.5초만 기다린 뒤 `error` 이벤트로 거절되고(라우터의 409는 빠른 경로), 프로세스 슬롯(`CHAT_CONCURRENCY`)은 60초 대기. 이벤트마다 실행 번호가 붙어 죽인 프로세스가 남긴 이벤트는 다음 답에 섞이지 않는다. stderr는 따로 읽어 파이프가 막히지 않는다
- 도구(`services/chat_tools.py`, MCP `/mcp/`에 stateless HTTP로 마운트): `analyse`, `compare`(두 수를 같은 깊이로, 승률 손실·등급·응수 줄·특징 차이표), `motifs`, `maia_probs`, `features`, `show_board`. `show_board`는 요청 헤더 `X-Chat-Session`으로 세션을 찾아 보드 상태를 SSE 스트림에 끼워 넣는다. 도구가 던진 `ValueError`(불법 수, 잘못된 FEN)는 `ToolError`로 모델에게 그대로 전달된다
- 프롬프트(`services/chat_prompt.py`): 역할, 근거 규칙(수치는 facts·도구 결과만), 반론 절차 6단계(compare → 결론 → 응수 줄을 보드로 재생 → 모티프/특징 → 의도 인정과 대조 → 재반론), 형식(보드와 문단을 번갈아). `<facts>`에는 `MoveReviewOut`에서 claims를 뺀 JSON과 앞뒤 수순
- 근거 표시: 답변 문장에 나온 칸 중 facts·도구 결과·FEN의 기물 칸 어디에도 없는 칸은 `text_end.unverified`로 내려가 "근거 미확인 칸" 배지가 붙는다. 삭제하지 않는다
- 기록: `chat_turns` 테이블(세션, 역할, 블록 JSON). Claude Code 쪽 대화 원본은 `~/.cache/chess-tutor/chat`을 cwd로 한 세션 파일에 있다
- 실측(2026-09-04, game 596 ply 16 "왜 Qf6가 블런더인가요?"): 첫 답 56초·도구 8회·보드 3장, 보드에서 Nc4를 두어 던진 재반론은 34초·도구 5회. 미확인 칸 0개
- 테스트: `tests/test_chat.py` 41개(타임아웃, 동시 질문 거절, max-turns 경고, id 충돌 재시도, 낡은 보드 이벤트 폐기, 워커 스레드 push 포함). `tests/fixtures/fake_claude.py`가 stream-json을 재생하므로 CI에 구독이 필요 없다. MCP 마운트는 TestClient(lifespan 실행)로만 검증한다(ASGITransport는 lifespan을 돌리지 않는다)
- 남은 것: 대화 기록은 브라우저 상태에만 있어 새로고침하면 사라진다(`chat_turns`에서 복원 미구현). 오프닝 지도 국면에서는 아직 못 쓴다. 첫 답까지 30~60초라 상주 프로세스(`--input-format stream-json`)로 줄이는 안이 남아 있다

### M7 모의 게임 (수동 / AI 대국 / 오프닝 연습) — 완료 (2026-09-11)
- 기획서 [PLAY.md](PLAY.md). 화면 하나(`/play`)에서 양쪽을 손으로 두거나 한쪽을 AI에게 맡기고, 오프닝 카탈로그의 타비야에서 시작하거나 수순을 드릴한다. 게임이 끝나면 저장 → 분석 → 리뷰 → 채팅 → 퍼즐의 기존 파이프라인으로 그대로 넘어간다. 트레이닝의 스파링 탭은 `/play` 로 보내는 링크가 됐다. 단계 M7a~M7d 를 모두 붙였다
- 실행 경로: 왼쪽 레일의 **대국** → `/play`(`pages/play/index.tsx`). 상태는 리듀서 하나(`pages/play/state.ts`)다. 수동 / AI가 백 / AI가 흑을 게임 도중에 바꿀 수 있고, 커서를 뒤로 옮긴 채 두면 뒤의 수를 잘라내며(한 번 확인), 물리기는 사용자 차례까지 되돌린다. 승급 피커(`components/PromotionPicker`)를 만들어 퍼즐의 자동 퀸도 이걸로 바꿨다. 진행 중 게임은 `localStorage`(`chess-tutor:play:current`)에 있어 새로고침을 견딘다. 오른쪽 패널은 수 목록 · 코치 · 튜터에게 질문 · 오프닝 · 설정 탭
- 엔드포인트(`routers/play.py`. 라우터는 얇고 서비스 네 개에 위임한다):

| 경로 | 하는 일 |
|---|---|
| `POST /play/move` | 상대의 수(`play_opponent`). `maia` 는 1100~2000 버킷으로 클램프하고 `opp_rating` 으로 사용자 레이팅까지 조건부로 준다. `stockfish` 는 `PlayEngine`(`UCI_LimitStrength`+`UCI_Elo` 1320~3190, movetime). **평가치는 돌려주지 않는다**. Stockfish 바이너리가 없으면 Maia 체인으로 폴백하고 실제로 답한 백엔드를 `source` 에 적는다 |
| `POST /play/hint` | 3단계 힌트(`play_coach`). 1단계는 구조와 계획만 말하고 수를 말하지 않는다, 2단계는 Maia 상위 후보와 이유, 3단계는 엔진 최선수·주변화·모티프. 문장은 `verify.verify_all` 을 통과한 것만 남는다 |
| `POST /play/check` | 방금 둔 수를 깊이 12로 재서 분류(실수 알림). 최선수가 컴퓨터 수면 그 레이팅대의 자연스러운 대안을 함께 낸다 |
| `POST /play/games` | PGN 조립 → `source="practice"` Game 행 → 분석 잡(`practice.save`) |
| `GET /play/report/{id}` | 타비야 대국 사후 계획 리포트(`practice.plan_report`). 분석 결과를 쓰므로 끝날 때까지 기다린다 |
| `GET /play/openings`, `/play/openings/{id}` | 카탈로그 카드와 상세(수순·타비야 FEN·구조·양쪽 계획·내 기록) |
| `GET /play/book` | 이 국면에서 책이 아는 다음 수들(전위 포함) |
| `POST /play/chat` | 저장 전 라이브 국면 채팅. M6 의 실행 경로를 그대로 쓰되 프롬프트만 다르다 |

- 오프닝 카탈로그(`services/openings_catalog.py`): 손으로 적는 데이터는 **34줄의 이름뿐**이다. 각 항목은 `(eco, tsv_name, ply)` 로 `assets/openings_*.tsv` 의 행 하나를 지목하고(`tests/test_openings_catalog.py` 가 34개 전부 해석되는지 확인한다), 수순·타비야 FEN·폰 구조·양쪽 계획은 전부 거기서 파생된다. 오타는 배포가 아니라 테스트에서 걸린다. **책 따라가기**는 모든 TSV 행의 모든 접두사로 만든 트리(`openings._tree()`)라 전위도 잡는다. 내 기록은 임포트 게임이면 타비야의 위치 키로, 연습 게임이면 `OpeningId` 헤더로 센다
- 라이브 채팅(M7d): 저장된 게임이 없으므로 세션 키는 FEN 이다(국면이 바뀌면 새 대화). 프롬프트(`chat_prompt.build_live_prompt`)에는 지금까지의 수순·폰 구조·양쪽 계획이 들어가고 엔진 수치는 들어가지 않는다. 코치 프리셋 "진지하게"에서는 탭 자체가 숨고, 질문 한 번은 힌트 한 번으로 센다
- 데이터 모델 결정
  - **새 테이블이 없다.** 연습 게임은 `source="practice"` 인 보통 `Game` 행이고 나머지는 PGN 헤더가 담는다: `Mode`(`manual`/`ai-white`/`ai-black`), `Opponent`(`maia:1500` 꼴), `CoachPreset`, `Hints`, `Takebacks`, `Alerts`, `PracticeMode`(`free`/`drill`/`tabiya`), `OpeningId`, `Termination`, 시계가 있으면 수마다 `%clk`
  - `games.parse_pgn` 이 PGN 해시로 `source_id` 를 만들기 때문에 같은 수순을 두 번 저장하면 뒤엣것이 "건너뜀"이 된다. 그래서 저장마다 초 단위 UTC 시각과 `PracticeNonce`(uuid4) 헤더를 붙여 매번 새 행이 되게 한다
  - 수동 게임(`user_color` 가 없는, 양쪽을 손으로 둔 게임)은 사용자를 백, "수동"을 흑으로 적는다. 둘 중 하나가 사용자 이름이어야 `upsert_games` 가 계정에 붙일 수 있고, 백은 그 선택의 임의적인 절반이다. 대국이 아니었다는 사실은 `Mode` 헤더가 말한다
  - `PlayEngine` 은 풀 밖의 전용 프로세스다. `UCI_Elo` 는 탐색 결과를 바꾸는데 풀 엔진의 캐시 이름(`analysis.cache_name`)에는 그 옵션이 들어가지 않으므로, 약화된 엔진을 풀에 넣으면 분석 캐시가 오염된다. 한 프로세스가 모든 레이팅을 처리하고 Elo 가 바뀔 때만 재설정한다
  - `chat_turns.game_id` 는 라이브 채팅 때문에 nullable 이 됐다. `create_all` 은 이미 있는 테이블을 고치지 않으므로 시작할 때 `db._allow_live_chat_turns` 가 컬럼을 직접 바꾼다(SQLite 는 테이블 재작성, 그 밖은 `ALTER … DROP NOT NULL`)
  - 프로필과 오프닝 지도는 `practice` 게임을 기본 제외한다(`?include_practice=1` 로 포함). 퍼즐 생성은 허용한다. `GET /games?source=` 로 걸러 볼 수 있다
  - 구조 커버리지: 카탈로그 34개 중 24개(**70.6%**)만 `unclassified` 가 아닌 구조로 분류된다. M7b 의 완료 기준 80% 에 못 미친다(→ §8)
- 테스트: API **384개**(M7 전용 71개 — `test_play_opponent` 14, `test_play_coach` 16, `test_practice` 19, `test_openings_catalog` 14, `test_play_chat` 6, `test_db_migrations` 2. 여기에 `test_e2e`·`test_openings`·`test_games`·`test_review` 의 추가분이 더 있다). 웹은 vitest **62개**(4파일). 그중 `play-state.test.ts` 39개가 리듀서를 덮는다: 커서 뒤에서 두면 잘라내기, 물리기, 책 이탈 감지, 승급, 종료 판정, 시계 틱
- 실측(2026-09-11, 로컬 종단 스모크)
  - Maia 상대 수: 첫 요청 1.1초(가중치 로드), 이후 p50 **14ms**. Stockfish 상대는 movetime 0.5초에 왕복 0.5~0.7초
  - 힌트 L1 **2ms** · L2 **20ms** · L3 **0.2~0.4초**(깊이 12). 실수 판정 `POST /play/check` **0.19초** — 기획의 "2초 이내"를 넉넉히 만족한다
  - `GET /play/book` 첫 호출 **2.5초**(TSV 3,810줄의 접두사 인덱스), 이후 **2ms**. 지금은 시작할 때 스레드로 미리 만들어(`openings.warm`) 첫 요청도 2ms 다
  - 저장 `POST /play/games` **31ms**. 18플라이 게임의 분석은 기본 깊이로 약 **85초**
  - 라이브 채팅 한 답 **25.7초**(도구 3회). M6 리뷰 채팅의 첫 답 56초보다 짧은데 재료가 적어서다
- 남은 것: 드릴 성공률은 헤더에 남을 뿐 아직 집계되지 않는다. 구조 커버리지 70.6%, 계획 실행 판정과 리포트의 구조 선택에 알려진 오차가 있다(§8). 무승부 수락 기준(±30cp·30수)은 여전히 자리표시자고, 개인 모델 상대와 간격 반복 오프닝 덱은 미결이다

### M8 오프닝 지도 v2 (수순 따라가기 + 해설 일지) — 완료 (2026-09-11)
- 기획서 [opening-map-upgrading-plan.md](opening-map-upgrading-plan.md). 오프닝 지도의 "열 탐색기"를 **직접 수를 두는 보드 + 다음 네임드 국면 후보 + 지워지지 않는 해설 일지**로 바꿨다. 어떤 수를 두든(책에 있든 없든) 그 수의 의도를 검증된 문장으로 적고, 셋업(시스템 배치) 진행과 깊은 해설·튜터 문답을 같은 패널에 붙인다. 단계는 M8a(국면 안내·셋업) → M8b(수의 의도·깊은 노트) → M8c(웹) → M8d(이름 한글화·스트리밍·질문·더 깊이·정리) 다
- 실행 경로: 왼쪽 레일의 **오프닝** → `/openings`(`pages/openings/index.tsx`). 수순·커서·일지·미리보기는 리듀서 하나(`pages/openings/line.ts`)에 있고 URL(`?color=&moves=`)에 실려 새로고침·공유를 견딘다. 위의 아이시클 개요 스트립과 아래의 히트맵·브레이크 차트는 그대로다
- 엔드포인트: **`routers/openings.py` 하나**가 `/openings` 전부를 낸다(M8d-5 에서 `opening_guide`·`opening_deeper` 라우터를 합쳤다. 모듈 docstring 이 목록이고 `test_api.py` 가 경로를 고정한다)

| 경로 | 하는 일 |
|---|---|
| `GET /openings/map`, `/heatmap`, `/breaks` | M5 그대로. 내 기보 위의 DAG·기물 목적지·폰 브레이크 |
| `GET /openings/position` | 이 국면의 다음 네임드 국면 후보(전위 포함), 폰 구조, 셋업 14종의 진행. 책 조회만 하므로 **1.7ms**, `masters=1` 일 때만 네트워크를 탄다 (`opening_guide`) |
| `POST /openings/annotate` | 수순 전체를 한 수씩 해설. 결정론적 탐지기(중앙·전개·캐슬링·피안케토·긴장·갬빗·전위·예방·모티프·계획·셋업)가 만든 문장을 `verify_all` 로 거르고 우선순위대로 최대 4문장을 남긴다. 마이아·엔진은 요청할 때만, 엔진은 한 요청에 6수까지 (`opening_intent`) |
| `GET /openings/note`, `POST /openings/note` | (국면 키, 수)당 하나인 깊은 해설. 없으면 `{"status":"missing"}`. 쓰기는 headless Claude Code(`claude -p`, 구독 로그인, chess MCP 도구만)가 하고 서버가 `[[…]]` 문장을 검증해 실패한 것은 "견해"로 강등한다 (`opening_notes`) |
| `POST /openings/note/stream` | 같은 생성을 SSE 로 중계한다: `stage`(책·엔진·구조·마이아 재료 준비) → `tool`(모델의 도구 호출) → `section`(요약·왜·응수·대안·함정, 도착 즉시 검증) → `note`(저장본). 끊으면 프로세스를 죽이고 아무것도 저장하지 않는다 |
| `POST /openings/note/addendum` | 튜터 답 하나를 그 수의 해설에 남긴다("해설에 반영"). 채팅이 보여 준 접지 표시(미확인 칸)를 그대로 저장하고 재검증하지 않는다 |
| `GET /openings/lines`, `/openings/masters` | "더 깊이"를 열 때만 부르는 엔진 라인 3개(깊이 12, `EngineCache` 재사용 — 첫 호출 **0.49초**, 이후 **2ms**)와 마스터 통계(토큰이 없으면 `available:false`) (`opening_deeper`) |
| `POST /play/chat` (`opening` 필드) | 해설 패널의 "튜터에게 질문". M6·M7 의 채팅 경로를 그대로 쓰고 프롬프트에 "지금 보는 해설" 블록(수·이름·요약·인용 문장)을 더한다. 세션 키는 `fen_before+san` 이라 수마다 대화가 따로 이어진다 |

- 서비스: `opening_guide.py`(후보·구조·셋업 213줄) · `opening_intent.py`(사실 탐지기와 `/annotate` 859줄) · `setups.py`(시스템 오프닝 KB 14종과 도달 판정 594줄) · `opening_notes.py`(노트 생성·스트리밍·검증·저장·시드 1,269줄) · `opening_deeper.py`(엔진 라인·마스터 정규화 140줄) · **`sentences.py`**(`Sentence`/`check`/`assemble` — 문장과 그 근거를 함께 들고 검증기를 통과한 것만 남기는 게이트. `play_coach` 와 `opening_intent` 가 각자 갖고 있던 사본을 M8d-5 에서 합쳤다)
- 오프닝 이름 한글화(M8d-1): `assets/opening_names_ko.json`(영어 → 한국어 **3,174개**, `scripts/translate_openings.py` 가 규칙 사전 → 고유명사 표 → headless 음차 순으로 만든다). 런타임 진입점은 `openings.name_ko(name)` 하나이고 **영어 이름이 여전히 식별자다**(TSV 의 `name`, `find_rows`, 카탈로그의 `tsv_name`). 후보 카드·일지 배지·해설 패널 헤더는 한글을 보여 주고 영어 이름을 `title` 로 달아 음차를 확인할 수 있게 한다(`PositionGuide.name_en`, `NamedCandidate.name_en`, `MoveAnnotation.name_after_en`)
- 웹: `LineBoard`(두기 가능한 메인 보드 + 미리보기 칩) · `Candidates`(후보 그리드·정렬·마스터 겹치기) · `ExplainPanel`(요약/보통/깊이 토글, 스트리밍 단계 체크리스트, 섹션별 "검증 n/n", 함정 미리보기, 근거 배지) · `NoteChat`(인라인 튜터 채팅·제안 질문·해설에 반영) · `DeeperPanel`(엔진 라인·마스터) · `Journal`(한 줄 색인, 되돌아간 항목도 남는다) · `SetupPanel` · `pgn.ts`(주석 달린 PGN 복사) · `line.ts`(수순·커서·일지·미리보기 리듀서) · `api/noteStream.ts`(SSE 파서)
- 데이터 모델: 새 테이블은 `opening_notes` 하나다(`position_key`, `san`, `lang`, `json`, `model`, `verified_claims`, `total_claims`; `(position_key, san, lang)` 유니크). 시안의 루이 로페즈 노트는 `assets/opening_notes_seed.json` 시드로 시작할 때 적재해 LLM 없이도 시연된다. 일지·수순은 서버에 저장하지 않는다(URL 과 브라우저 상태)
- 테스트: API **524개**(M8 전용 116개 — `test_opening_intent` 26, `test_opening_notes` 22, `test_opening_note_stream` 17, `test_setups` 15, `test_opening_guide` 14, `test_opening_chat` 11, `test_opening_deeper` 11, 여기에 `test_openings`·`test_api` 의 추가분). 웹은 vitest **93개**(7파일 — `openings-line` 16, `openings` 12, `openings-pgn` 9, `openings-note-stream` 3 이 M8 몫(40개)). 노트 생성 테스트는 `claude` 를 고정 NDJSON 을 내는 스텁 스크립트로 바꿔 돌린다
- 실측(2026-09-11, 실서버 스모크. `rixile9` 백 305판, `LICHESS_TOKEN` 없음)
  - `GET /openings/position` **1.7ms**, `POST /openings/annotate`(8플라이 + 마이아) 첫 호출 **1.2초**·이후 **0.12초**, `GET /openings/lines` 첫 **0.49초**·캐시 **2ms**, `GET /openings/note` **2.5ms**
  - `POST /openings/note/stream` 한 번(4…Nf6, 시드 없는 국면) **198.6초** · 도구 호출 12회 · 검증 **15/15**. 첫 섹션(`mine`)은 재료 준비가 끝난 30초 안에 도착하고 그 뒤로 섹션이 순서대로 붙는다. 기획의 "10초 안팎" 예상보다 훨씬 길다(→ §8)
  - 해설 문맥을 단 튜터 답 한 번 **54.5초**(도구 6회, 보드 2개). "해설에 반영"은 즉시 저장된다

### 통합 상태 (2026-09-02)
- 백엔드: `ruff format`·`ruff check`·`mypy --strict` 통과, pytest 271개 약 22초(엔진 테스트는 깊이 ≤ 8). `tests/test_e2e.py`가 TestClient로 임포트 → 분석 → 리뷰 → 프로필 → 오프닝 지도 → 퍼즐 → 스파링을 한 번에 돈다
- 웹: `pnpm lint`, `tsc --noEmit`, `pnpm build` 통과. 라우트 `/games`, `/review/:gameId/:ply`, `/profile/:username`, `/openings`, `/training`
- 실서버 스모크: uvicorn 기동 후 `/health`, `/docs`와 위 흐름 전부 200. Maia-2 가중치가 있으면 사람 뷰와 스파링의 `source`가 `maia`로 나온다
- 실수·블런더 판정은 깊이 +6에서 `fen_before`와 `fen_after`를 다시 재서 확인한 뒤 확정한다. `eval_before`와 `eval_after`가 서로 다른 탐색에서 나오는 탓에 지평선 바로 너머의 강제 수순이 손실로 잡히던 문제를 없앤다(오페라 게임 15.Bxd7+는 깊이 12에서 블런더로 찍혔다). 둔 수가 부모 국면의 MultiPV 안에 있으면 그 줄의 점수를 `eval_after`로 쓴다. 라이브 API 재확인(2026-09-02): 같은 기보를 `duke2`로 다시 임포트해 깊이 12로 분석하면 15.Bxd7+는 `best`(승률 손실 0)로 나오고 백의 mistake·blunder는 0개다
- 탐색을 바꾸는 엔진 옵션(Threads, Hash)은 캐시 정체성에 들어간다: `engine_cache.engine`과 `analyses.engine`이 `stockfish-18-t2-h256` 꼴이라 설정이 바뀌면 옛 줄을 재사용하지 않는다. `?depth=`를 준 요청은 저장된 깊이가 다르면 다시 분석한다
- CI(`.github/workflows/ci.yml`): API는 stockfish 설치 → ruff format/check → mypy → pytest, 웹은 eslint → build. 러너에 `apt-get install stockfish`로 엔진을 깔고 `STOCKFISH_PATH=/usr/games/stockfish`를 주므로(데비안 바이너리는 PATH에 없다) `test_e2e`를 포함한 엔진 테스트가 CI에서도 전부 돈다. 두 잡 모두 `timeout-minutes: 20`
- 재검 탐색이 둔 수를 얕은 MultiPV의 최선보다 높게 매길 수 있으므로 `best`인 수의 SAN이 `best_move_san`과 다를 수 있다. 이때 리드 문장은 "엔진 최선 수와 같습니다"가 아니라 "엔진 최선 Bxf6과 차이가 없습니다"로 나간다(`services/verbalize.py`)
- 리뷰 스모크: `GET /review/2/20`(10… cxb5 실수)은 `verified: true`, 주장 25/25, 문장에 칸 나열이 없다. `ANTHROPIC_API_KEY`가 없으면 `source`는 `template`
- 알려진 간극: Alembic 마이그레이션 없음(`create_all`). 웹 단위 테스트(vitest) 없음. 엔진 `Threads=2`는 깊이 8의 차선 PV가 실행마다 달라지므로 테스트에서는 `tests/conftest.py`가 `ENGINE_THREADS=1`로 고정한다. 운영 기본값은 그대로 2이고, 차선 수에 의존하는 단언은 구조적으로 쓴다(같은 프로세스 안의 이전 탐색에는 영향받지 않는다: 탐색마다 `ucinewgame`)

---

## 6. 개발 환경

`scripts/server.sh` 가 아래 명령을 감싼다. 서비스는 각자 프로세스 그룹으로 떠서 `stop` 하면
uvicorn 의 reload 자식·Stockfish·vite 의 node 까지 함께 내려간다. pid 와 로그는 `.run/` 에 두고
**서비스와 포트로 이름을 짓는다**: 기본 포트는 짧은 이름(`.run/api.pid`, `.run/logs/api.log`),
다른 포트는 포트가 붙는다(`.run/api-8012.pid`, `.run/logs/api-8012.log`). 그래서
`API_PORT=8012 scripts/server.sh start api` 가 8000 의 서버를 "이미 실행 중"으로 오해하지 않고,
`stop api` 가 엉뚱한 쪽을 죽이지 않는다. `status`·`logs`·`stop` 모두 같은 규칙을 쓴다.

```bash
scripts/server.sh setup [--maia]     # uv sync + pnpm install + 도구 점검
scripts/server.sh dev                # api + web 을 띄우고 로그 follow. Ctrl-C 로 모두 종료
scripts/server.sh start|stop|restart [api|web|design]   # 백그라운드 관리. stop -f 는 외부 프로세스도 종료
scripts/server.sh status | health | logs [-f] | open
scripts/server.sh test [api|web] | lint | fmt | ci       # ci 는 .github/workflows/ci.yml 과 같은 검사
scripts/server.sh db url | up | down | shell | reset     # Postgres(docker) / SQLite
scripts/server.sh doctor             # 도구·의존성·환경 변수·포트 진단
# 포트·호스트는 API_PORT, WEB_PORT, API_HOST, WEB_HOST, API_RELOAD=0 등으로 바꾼다. 루트 .env 도 읽는다.
```

```bash
# 엔진
brew install stockfish            # macOS

# 백엔드
cd apps/api
uv sync --all-groups              # Maia-2까지: uv sync --all-groups --extra maia
uv run ruff format src tests && uv run ruff check src tests
uv run pytest -q                  # 약 22초, Stockfish 필요
uv run uvicorn chess_tutor.api:app --reload   # http://localhost:8000/docs

# 프론트엔드
cd apps/web
pnpm install
pnpm lint && pnpm exec tsc --noEmit && pnpm build
pnpm dev                          # http://localhost:5173, /api → 8000 프록시

# DB: 기본은 apps/api/chess_tutor.db (SQLite). Postgres를 쓰려면
docker compose up -d db           # 그리고 .env의 DATABASE_URL
```

국면 채팅을 쓰려면 Claude Code가 설치되고 로그인돼 있어야 한다(`claude` 실행 후 `/login`, Max 구독). `GET /chat/status`가 실행 파일을 찾았는지 알려준다. 채팅 서브프로세스가 이 서버의 `/mcp/`에 접속하므로 API 포트를 바꾸면 `CHAT_MCP_URL`도 바꾼다.

환경 변수는 `.env.example` 참고. `STOCKFISH_PATH`가 없으면 PATH의 `stockfish`를 찾고, 그것도 없으면 엔진 테스트는 건너뛴다. `ANTHROPIC_API_KEY`가 없으면 설명은 템플릿으로만 나온다. Maia-2 가중치는 첫 사용 때 `~/.cache/chess-tutor/maia2`에 내려받는다(`MAIA_MODEL_DIR`로 변경).

---

## 7. 라이선스

**AGPL-3.0-or-later.** 이유:
- chessground는 GPL-3.0, lichess-puzzler(태거 출발점)는 AGPL-3.0, Stockfish는 GPL-3.0. GPL-3.0 코드는 AGPL-3.0 프로젝트와 결합할 수 있다.
- 웹 서비스로 배포할 가능성이 있다. AGPL은 서비스 형태에서도 소스 공개를 보장한다.
- Maia-2는 MIT라 제약이 없다.

MIT로 가고 싶다면: chessground 대신 MIT 보드 라이브러리를 쓰고, 태거를 Lichess 코드 없이 처음부터 작성해야 한다. 외부 기여자가 생기기 전에 바꾸는 편이 쉽다.

---

## 8. 후속 과제와 미결 (2026-09-11 기준)

플랫폼의 다섯 단계는 모두 동작하는 수준으로 구현되어 있다. 아래는 이번 빌드에서 의도적으로 남긴 것들이다.

**측정이 필요한 것**
- 모티프 탐지기 10종은 손으로 만든 국면으로만 검증했다. Lichess 퍼즐 DB(테마 70개)로 정밀도·재현율을 재는 스크립트가 필요하다.
- 폰 구조 분류기는 규칙 기반이며 라벨 테스트셋이 없다. 수백 국면을 수작업 라벨링해 `tests/`에 고정한다.
- 프로필의 레이팅대 기준선(`services/profile.py`의 `BASELINES`)과 시간 압박 기준 0.09는 자리표시자 값이다. Lichess 월간 DB로 측정해 바꾼다.
- 브레이크 타이밍의 마스터 중앙값, 후보 카드의 마스터 승률, `GET /openings/masters` 는 모두 `LICHESS_TOKEN`이 있을 때만 켜진다. 토큰이 없으면 화면에 "마스터 DB 연결 없음(LICHESS_TOKEN)"이 뜨고 나머지는 그대로 동작한다(2026-09-11 스모크가 이 경로였다). 월간 DB 자체 집계는 미착수.

**아직 실측하지 않은 경로**
- LLM 언어화(`services/verbalize.llm_explanation`)는 `ANTHROPIC_API_KEY`가 없어 템플릿 경로만 검증했다. 키를 넣고 LLM → 검증기 → 템플릿 폴백을 실제로 돌려봐야 한다.
- Postgres 경로(`asyncpg` extra, docker compose)는 미검증이다. 모든 테스트는 SQLite로 돈다. Alembic 마이그레이션도 아직 없다(`create_all`).
- 웹 단위 테스트는 vitest 7파일 93개까지 늘었지만 여전히 순수 로직(리듀서·포매터·SSE 파서·PGN)만 덮는다. 컴포넌트 렌더링 테스트와 **저장소에 남는** Playwright 종단 테스트는 아직 없고, 화면은 매 마일스톤마다 손으로 스모크한다.

**국면 채팅**
- 새로고침하면 대화가 사라진다(ply 이동과 탭 전환은 유지된다). `chat_turns`에서 복원하는 `GET /chat/sessions/{id}`가 필요하다.
- 근거 표시는 우회 가능하다: 도구 결과에 나온 칸은 모두 "확인됨"으로 치므로, 게임과 무관한 FEN을 분석하면 그 칸들도 확인된 것으로 남는다. `fen_before`에서 도달 가능한 국면만 접지하는 쪽이 정확하다.
- 웹 ESLint에 `eslint-plugin-react-hooks`가 없어 effect 의존성 검사가 꺼져 있다.
- 답이 도구 호출을 다 마친 뒤에야 첫 문장이 나올 때가 있다. 프롬프트는 "보드마다 문단"을 요구하지만 강제는 아니다. 상주 프로세스로 기동 지연을 줄이는 것과 함께 검토한다.
- 구독 정책: Agent SDK 문서는 서드파티 제품에 claude.ai 로그인을 쓰는 것을 금지한다. 이 채팅은 본인 로컬 도구로만 쓴다. 서비스로 열려면 `ANTHROPIC_API_KEY` 경로(SDK 백엔드)를 추가해야 한다.

**모의 게임 (M7, 2026-09-11 스모크에서 나온 것)**
- 계획 실행 판정(`plans.match_plans`)이 **준비 수를 실행으로 센다**. 소수 공격의 `b4` 만 두고 `b5` 를 두지 않아도 리포트에는 실행으로 올라간다. 계획마다 "완료 조건"을 따로 두거나 실행을 단계로 나눠야 한다.
- 리포트의 구조를 `OpeningId` 가 있으면 **무조건 카탈로그 타비야에서** 읽는다(`practice.structure_board`). 드릴에서 타비야에 닿기 전에 이탈해 다른 구조가 됐어도 타비야의 구조와 계획으로 채점한다. 실제로 지나간 국면인지 먼저 확인해야 한다.
- 카탈로그의 **내 기록이 실제 컬렉션에서 비어 있다**. 임포트 게임은 타비야의 위치 키가 정확히 일치해야 세는데, 609 게임을 훑어 카탈로그 수순과 가장 깊이 겹친 지점이 7플라이였다. 타비야(10~20플라이)까지 닿는 게임이 거의 없다. 접두사 매칭이나 오프닝 이름 매칭으로 바꾸거나, 타비야를 더 얕게 잡아야 한다.
- 카탈로그 34개 중 **10개가 `unclassified`**(커버리지 70.6%, M7b 기준 80% 미달): 스카치 클래시컬, 시실리안 드래곤, 카로칸 클래시컬, 스칸디나비안, 피르츠, 세미슬라브 메란, 님조 클래시컬, 그륀펠트 익스체인지, 카탈란 오픈, 레티 더블 피안케토. 같은 오프닝의 1~3플라이 깊은 TSV 행으로 바꿔도 하나도 분류되지 않는다(카로칸만 10플라이 더 들어간 ply 24 의 로브론 시스템에서 `slav_caro` 가 된다). 분류기 쪽 문제다: 이 타비야들은 구조를 정하는 폰 교환이 아직 일어나지 않았거나(님조·레티·카탈란), 그 구조가 15종 안에 없다(드래곤·피르츠·그륀펠트 익스체인지). 분류기에 구조를 더하는 편이 카탈로그를 손보는 것보다 낫다.
- `PlanReport` 에 **분석 상태 필드가 없다**. `GET /play/report/{id}` 는 분석이 끝날 때까지 기다리므로 긴 게임에서 수 분간 막힌다. 프론트는 그래서 `/analysis/{id}` 의 `status === 'done'` 을 보고서야 카드를 띄운다(`ReviewPanel` → `PlanReportCard`). 리포트 자체가 상태를 돌려주는 쪽이 옳다.
- 라이브 채팅의 `analyse` 도구가 한 번 **엔진 경합으로 타임아웃**했다. 채팅 도구는 깊이 18로 재는데 풀에는 엔진이 2개뿐이라, 같은 시간에 게임 분석이 돌면 도구가 기다리다 끊긴다. 도구 깊이를 낮추거나 풀을 늘려야 한다.
- **드릴 성공률이 집계되지 않는다.** `PracticeMode`·`OpeningId` 헤더에 재료는 다 있지만 항목별 이탈 지점·성공률을 세는 곳이 없다.
- **Alembic 은 여전히 없다.** M7 에서 `chat_turns.game_id` 를 nullable 로 바꿔야 했고, `create_all` 이 기존 테이블을 고치지 않으므로 시작할 때 `db._allow_live_chat_turns` 가 손으로 패치한다(SQLite 는 테이블 재작성). 이런 변경이 한 번 더 필요해지면 마이그레이션을 도입한다.

**오프닝 지도 v2 (M8, 2026-09-11 스모크에서 나온 것)**
- **깊은 해설 한 번이 3분 넘게 걸린다.** 시드 없는 국면(4…Nf6) 한 개를 쓰는 데 **198.6초**·도구 12회였다. 기획서 §9.2 의 "10초 안팎"과 자릿수가 다르다. 스트리밍 덕에 화면은 비어 있지 않지만, 프롬프트를 줄이거나 섹션을 나눠 생성하는 쪽을 검토한다. 비용은 사용자가 버튼으로 결정하므로 급하지는 않다.
- **메인 보드에 해설의 화살표가 흐르지 않는다.** 보드가 그리는 도형은 후보 카드 호버와 표시한 칸뿐이고, 노트 문장의 주장(공격·수비 칸)이나 튜터가 보여 준 화살표는 채팅 카드 안에만 있다. 섹션이 도착할 때마다 그 문장의 `Claim` 을 보드에 겹쳐 주면 읽는 순서와 보는 순서가 맞는다.
- **"해설에 반영"은 답을 재검증하지 않는다.** 채팅의 접지 표시(미확인 칸 목록)를 그대로 저장하고 화면에 같은 표시를 한다(기획서 §10.7 이 고른 선택). 노트 본문은 `[[…]]` 검증을 거치는데 addendum 만 예외라, 같은 패널 안에서 근거의 무게가 다르다.
- **이름 번역의 긴 꼬리가 검수되지 않았다.** 3,174개 중 사람이 훑은 것은 자주 나오는 상위 200개뿐이고 나머지는 규칙·음차다. 화면은 영어 이름을 `title` 로 달아 확인할 수 있게 해 두었지만(후보 카드·일지 배지·패널 헤더), 어색한 음차를 모으는 절차는 아직 `--report` 를 손으로 돌리는 것이다.
- **TSV 가 이름 짓지 않은 국면은 패널 헤더에 이름이 없다.** 1.e4 e5 2.Nf3 Nc6 3.Bb5 a6 **4.Ba4** 가 그렇다: 후보 카드는 전부 루이 로페즈 갈래를 보여 주는데 `lookup` 은 그 국면 자체의 행을 못 찾아 `PositionGuide.name` 이 비고, 헤더 배지가 사라진다(수의 `in_book` 은 정상이라 "책 밖" 표시는 뜨지 않는다). 가장 가까운 상위 이름을 물려주는 편이 읽기에 낫다.
- **레퍼토리 개요 스트립의 이름은 아직 영어다.** 한글화(기획서 §10.5)는 후보·일지·해설·카탈로그에 적용했고, 스트립은 지도 DAG(`openings_map`)의 노드 이름을 그대로 쓴다.
- 노트는 (국면 키, 수, 언어)당 하나라 **다시 만들기 말고는 갱신 경로가 없다.** 책이나 탐지기가 바뀌어도 저장된 노트는 그대로다. 모델·생성 시각은 남아 있으니 무효화 규칙을 붙일 수 있다.

**제품 상 알려진 간극**
- 마지막 수에서 저지른 실수(기권 직전)는 다음 국면 분석이 없어 퍼즐이 되지 않는다.
- 리뷰 캐시(`move_reviews`)는 레이팅과 깊이로만 구분한다. 엔진 버전이 바뀌어도 자동 무효화되지 않는다.
- 분석은 프로세스 내 워커 한 개로 돈다. 동시 사용자가 늘면 arq + Redis로 분리한다.
- 개발 서버는 `--reload` 없이 띄우면 코드 변경이 반영되지 않는다. `scripts/server.sh dev`(기본 `--reload`), `.claude/launch.json`의 api 설정 또는 `uvicorn ... --reload`를 쓴다.
