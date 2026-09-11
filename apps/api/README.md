# chess-tutor API

Python backend. Layers 0-4 of the architecture in `../../PLAN.md`: rules (python-chess),
oracles (Stockfish, Maia), concept extraction (motifs, structure), reasoning, and
verified verbalization.

```bash
uv sync --all-groups
uv run pytest
uv run uvicorn chess_tutor.api:app --reload
```

## 오프닝 이름 한글화 (M8d-1)

화면에 보이는 오프닝 이름은 `src/chess_tutor/assets/opening_names_ko.json`(영어 → 한국어
3,174개)에서 옵니다. 런타임 진입점은 `openings.name_ko(name)` 하나이고, 항목이 없으면 영어
이름을 그대로 돌려줍니다. **영어 이름이 여전히 식별자입니다**(TSV의 `name` 열,
`openings.find_rows`, 카탈로그의 `tsv_name`) — 한글은 라벨일 뿐입니다.

자산을 다시 만들려면:

```bash
uv run --directory apps/api python ../../scripts/translate_openings.py --dry-run   # 적용률만
uv run --directory apps/api python ../../scripts/translate_openings.py            # 빠진 것만 번역
uv run --directory apps/api python ../../scripts/translate_openings.py --report 200  # 검수용 표
```

스크립트는 (1) 일반 단어 규칙 사전, (2) 손으로 쓴 고유명사 표, (3) 남은 이름만 headless
`claude -p`(도구·MCP 없음)로 음차합니다. 옆 파일 `opening_names_ko.sources.json`이 이름마다
출처(`rule`/`table`/`llm`)를 적어 두므로, 검수는 `--report 200`으로 자주 나오는 이름을 훑고
어색한 것을 스크립트의 `PROPER` 표에 적은 뒤 다시 돌리면 됩니다(이미 있는 LLM 답은 그대로
두고, 표로 답할 수 있게 된 이름만 덮어씁니다). "Defense"를 언제 "디펜스"로 쓰고 언제
"방어"로 쓰는지 등 번역 규칙은 스크립트 docstring에 있습니다.
