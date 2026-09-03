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
      values.py           기물 가치
      api.py              HTTP 엔드포인트
    tests/                국면 단위 테스트 (목업의 예시 국면 포함)
  apps/web/               Vite + React 프론트엔드
    src/Board.tsx         chessground 래퍼
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

환경 변수는 `.env.example` 참고. `STOCKFISH_PATH`가 없으면 PATH의 `stockfish`를 찾고, 그것도 없으면 엔진 테스트는 건너뛴다. `ANTHROPIC_API_KEY`가 없으면 설명은 템플릿으로만 나온다. Maia-2 가중치는 첫 사용 때 `~/.cache/chess-tutor/maia2`에 내려받는다(`MAIA_MODEL_DIR`로 변경).

---

## 7. 라이선스

**AGPL-3.0-or-later.** 이유:
- chessground는 GPL-3.0, lichess-puzzler(태거 출발점)는 AGPL-3.0, Stockfish는 GPL-3.0. GPL-3.0 코드는 AGPL-3.0 프로젝트와 결합할 수 있다.
- 웹 서비스로 배포할 가능성이 있다. AGPL은 서비스 형태에서도 소스 공개를 보장한다.
- Maia-2는 MIT라 제약이 없다.

MIT로 가고 싶다면: chessground 대신 MIT 보드 라이브러리를 쓰고, 태거를 Lichess 코드 없이 처음부터 작성해야 한다. 외부 기여자가 생기기 전에 바꾸는 편이 쉽다.

---

## 8. 후속 과제와 미결 (2026-09-02 기준)

플랫폼의 다섯 단계는 모두 동작하는 수준으로 구현되어 있다. 아래는 이번 빌드에서 의도적으로 남긴 것들이다.

**측정이 필요한 것**
- 모티프 탐지기 10종은 손으로 만든 국면으로만 검증했다. Lichess 퍼즐 DB(테마 70개)로 정밀도·재현율을 재는 스크립트가 필요하다.
- 폰 구조 분류기는 규칙 기반이며 라벨 테스트셋이 없다. 수백 국면을 수작업 라벨링해 `tests/`에 고정한다.
- 프로필의 레이팅대 기준선(`services/profile.py`의 `BASELINES`)과 시간 압박 기준 0.09는 자리표시자 값이다. Lichess 월간 DB로 측정해 바꾼다.
- 브레이크 타이밍의 마스터 중앙값과 오프닝 지도의 마스터 오버레이는 `LICHESS_TOKEN`이 있을 때만 켜진다. 월간 DB 자체 집계는 미착수.

**아직 실측하지 않은 경로**
- LLM 언어화(`services/verbalize.llm_explanation`)는 `ANTHROPIC_API_KEY`가 없어 템플릿 경로만 검증했다. 키를 넣고 LLM → 검증기 → 템플릿 폴백을 실제로 돌려봐야 한다.
- Postgres 경로(`asyncpg` extra, docker compose)는 미검증이다. 모든 테스트는 SQLite로 돈다. Alembic 마이그레이션도 아직 없다(`create_all`).
- 웹 단위 테스트가 없다(vitest 미도입). Playwright로 실서버 연결 스모크만 했다.

**제품 상 알려진 간극**
- 승급 시 기물 선택 UI가 없다(자동 퀸). 퍼즐 정답이 언더프로모션이면 정답 기물을 적용한다.
- 마지막 수에서 저지른 실수(기권 직전)는 다음 국면 분석이 없어 퍼즐이 되지 않는다.
- 리뷰 캐시(`move_reviews`)는 레이팅과 깊이로만 구분한다. 엔진 버전이 바뀌어도 자동 무효화되지 않는다.
- 분석은 프로세스 내 워커 한 개로 돈다. 동시 사용자가 늘면 arq + Redis로 분리한다.
- 개발 서버는 `--reload` 없이 띄우면 코드 변경이 반영되지 않는다. `.claude/launch.json`의 api 설정 또는 `uvicorn ... --reload`를 쓴다.
