# chess-tutor-ai

설명이 먼저인 체스 튜터. 엔진의 숫자를 사람의 개념으로 번역하고, 모든 문장을 보드와 대조해 검증한다.

- 목표와 아키텍처: [PLAN.md](PLAN.md)
- 구현 계획과 기술 스택: [docs/IMPLEMENTATION.md](docs/IMPLEMENTATION.md)
- 취지와 조사 노트: [draft.md](draft.md), [research-notes.md](research-notes.md)
- UI 목업 소스: [design/](design/) (`node design/build.mjs`로 화면 생성)

## 구조

```
apps/api   Python 3.12 · FastAPI · python-chess · Stockfish(UCI) · Maia-2
apps/web   Vite · React 19 · TypeScript · chessground
```

## 시작하기

```bash
brew install stockfish

scripts/server.sh setup      # uv sync + pnpm install + 도구 점검 (Maia-2까지: --maia)
scripts/server.sh dev        # API(8000) + 웹(5173)을 띄우고 로그를 따라간다. Ctrl-C 로 모두 종료
scripts/server.sh status     # 서비스·포트·헬스 확인. 그 밖의 명령은 scripts/server.sh help
```

손으로 띄우려면:

```bash
cd apps/api && uv sync --all-groups && uv run pytest -q     # Maia-2까지: --extra maia
uv run uvicorn chess_tutor.api:app --reload                 # http://localhost:8000/docs

cd ../web && pnpm install && pnpm dev                       # http://localhost:5173
```

첫 화면에서 PGN을 붙여 넣거나 chess.com/lichess 아이디로 기보를 가져온 뒤 분석 → 리뷰 → 프로필 → 오프닝 지도 → 트레이닝 순으로 쓴다. 엔진 분석은 Stockfish만 있으면 되고, `ANTHROPIC_API_KEY`(LLM 언어화)와 Maia-2 가중치는 없어도 템플릿·Stockfish 폴백으로 동작한다. 자세한 명령은 [docs/IMPLEMENTATION.md](docs/IMPLEMENTATION.md) 6절.

## 원칙

1. 전술 주장은 반드시 수순과 함께.
2. LLM은 판단하지 않는다. 사실 JSON을 문장으로 옮길 뿐이다.
3. 문장 속 칸·기물·공격 관계는 검증기를 통과해야 출력된다.
4. 설명할 수 없는 엔진 수는 "컴퓨터 수"로 표시한다.
5. 계획은 폰 구조 단위로 가르친다.

## 라이선스

AGPL-3.0-or-later. 근거는 [docs/IMPLEMENTATION.md](docs/IMPLEMENTATION.md) 7절.
