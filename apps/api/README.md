# chess-tutor API

Python backend. Layers 0-4 of the architecture in `../../PLAN.md`: rules (python-chess),
oracles (Stockfish, Maia), concept extraction (motifs, structure), reasoning, and
verified verbalization.

```bash
uv sync --all-groups
uv run pytest
uv run uvicorn chess_tutor.api:app --reload
```
