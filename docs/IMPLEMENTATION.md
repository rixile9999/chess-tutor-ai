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

### M1 근거 있는 게임 리뷰 (MVP)
- PGN 임포트, Chess.com·Lichess API 임포트
- Stockfish 분석 파이프라인 + 캐시
- 실수 분류(최선/좋음/부정확/실수/블런더)
- 모티프 탐지기 확장: 핀, 스큐어, 수비수 제거, 과부하, 백랭크, 트랩. Lichess 퍼즐 DB로 정밀도·재현율 측정
- 정적 특징 계산(폰 구조, 킹 안전, 활동성, 열린 파일, 아웃포스트) — python-chess 자체 구현
- 분기점 비교, 특징 차이표
- 언어화 + 검증기 연결
- 리뷰 화면(목업 1번) 구현
- 완료 기준: `PLAN.md`의 예시 1·2가 해결됨. 검증 통과율 측정 가능

### M2 Maia
- Maia-2 로드, 레이팅별 수 분포
- "자연스러운 수 vs 정답" 대비, 컴퓨터 수 판정, 설명 난이도 조절
- 스파링(국면 이어 두기)

### M3 전략 계층
- 폰 구조 분류 체계 고정 → 분류기 → 수작업 라벨 테스트셋(수백 국면)
- 계획 지식베이스(구조 5개부터) + PV 계획 추출 + 매칭
- 반사실 검증
- 전략 탭(목업 2번)

### M4 개인화
- 전체 기보 임포트, 프로필, 약점 리포트(목업 3번)
- 내 기보 퍼즐 + 간격 반복, 레퍼토리 구멍

### M5 시각화
- 오프닝 지도(목업 4번): 오버레이 DAG + 보드 스냅샷, 기물 목적지 히트맵, 브레이크 타임라인

---

## 6. 개발 환경

```bash
# 엔진
brew install stockfish            # macOS

# 백엔드
cd apps/api
uv sync --all-groups
uv run pytest
uv run uvicorn chess_tutor.api:app --reload   # http://localhost:8000/docs

# 프론트엔드
cd apps/web
pnpm install
pnpm dev                          # http://localhost:5173, /api → 8000 프록시

# DB (M1부터)
docker compose up -d db
```

환경 변수는 `.env.example` 참고. `STOCKFISH_PATH`가 없으면 엔진 테스트는 건너뛴다.

---

## 7. 라이선스

**AGPL-3.0-or-later.** 이유:
- chessground는 GPL-3.0, lichess-puzzler(태거 출발점)는 AGPL-3.0, Stockfish는 GPL-3.0. GPL-3.0 코드는 AGPL-3.0 프로젝트와 결합할 수 있다.
- 웹 서비스로 배포할 가능성이 있다. AGPL은 서비스 형태에서도 소스 공개를 보장한다.
- Maia-2는 MIT라 제약이 없다.

MIT로 가고 싶다면: chessground 대신 MIT 보드 라이브러리를 쓰고, 태거를 Lichess 코드 없이 처음부터 작성해야 한다. 외부 기여자가 생기기 전에 바꾸는 편이 쉽다.

---

## 8. 리스크와 미결

- python-chess 자체 특징 계산의 정확도. Stockfish 15.1 보조 엔진의 평가표와 비교해 보정한다.
- 폰 구조 분류 체계와 라벨링 비용. M3 전에 체계를 먼저 문서로 고정한다.
- LLM 비용과 지연. 사실 JSON이 입력이므로 프롬프트가 짧다. 캐싱과 낮은 effort로 시작해 측정한다.
- Lichess 익스플로러 API 인증. M5에서 OAuth 토큰 또는 월간 DB 자체 집계.
