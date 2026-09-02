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

cd apps/api && uv sync --all-groups && uv run pytest
uv run uvicorn chess_tutor.api:app --reload

cd ../web && pnpm install && pnpm dev
```

## 원칙

1. 전술 주장은 반드시 수순과 함께.
2. LLM은 판단하지 않는다. 사실 JSON을 문장으로 옮길 뿐이다.
3. 문장 속 칸·기물·공격 관계는 검증기를 통과해야 출력된다.
4. 설명할 수 없는 엔진 수는 "컴퓨터 수"로 표시한다.
5. 계획은 폰 구조 단위로 가르친다.

## 라이선스

AGPL-3.0-or-later. 근거는 [docs/IMPLEMENTATION.md](docs/IMPLEMENTATION.md) 7절.
