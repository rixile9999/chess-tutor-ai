# 리서치 노트 (2026-09-02 조사)

draft.md의 질문에 답하기 위해 조사한 선행 제품, 연구, 데이터 소스 정리. 검증 못 한 항목은 (미검증)으로 표시.

## 1. 선행 제품

| 제품 | 하는 일 | 설명 생성 방식 | 개인화 | 상태 |
|---|---|---|---|---|
| Chess.com Game Review + Coach | 엔진 기반 수 분류 + 수별 코치 코멘트 | 특징 탐지 기반 템플릿으로 추정. LLM 사용은 GamesBeat 기사 단일 출처 (미검증) | 게임 단위만, 게임 간 기억 없음 | 운영 중. CEO가 2026-08 인터뷰에서 채팅형 주간 리뷰 코치 개발 중이라 언급 |
| Lichess Learn from your mistakes | 평가 급락 지점을 퀴즈로 제시 | 자연어 없음 | 없음 | 운영 중, 무료 |
| Lichess Insights | ACPL, 수 시간, 오프닝별 통계 | 차트/표 | 통계만 | 운영 중 |
| Aimchess | 6개 영역 점수, 내 실수로 퍼즐 생성 | 통계 + 엔진 퍼즐, 자연어 없음 | 있음 | Chess.com 소유, 2024년 이후 정체 |
| DecodeChess | 위협/계획/기물 역할을 화살표+문장으로 | 규칙 기반, 방식 비공개 | 없음 | 쇠퇴 중 |
| Noctie.ai | 사람처럼 두는 상대 + 실수 덱 간격 반복 | 등급 라벨 템플릿 | 있음 | 운영 중 |
| Maia Platform (maiachess.com, 2025-07) | 레이팅 인식 게임 분석, 드릴 | Maia-2 + 엔진 | 레이팅 조건부 | 운영 중, 프론트 오픈소스 |
| ChessRoots | 오프닝을 노드-링크 그래프로. 엣지 색 = 레이팅/승률. 유저명 트레이스 가능 | 해당 없음 | 유저 오버레이 | 운영 중, 무료 15 요청/일 |
| ChessBase 26 Opening Report (2025-11) | 오프닝 자동 리포트, 몬테카를로 기물 경로 계획 | 자동 생성 | 없음 | 유료 |
| Chessvia "Chessy", Chessigma Supercoach | 채팅형 LLM 코치, 최근 게임 읽음 | 엔진 + LLM (비공개) | 일부 | 2025-26 신생 |
| 오픈소스 LLM-ChessCoach 등 | Stockfish 계산 + LLM 서술 | 엔진 + LLM | 없음 | 소규모 |

링크:
- https://www.chess.com/news/view/choose-your-coach-on-chesscom
- https://lichess.org/@/lichess/blog/learn-from-your-mistakes/WFvLpiQA
- https://aimchess.com/
- https://decodechess.com/
- https://noctie.ai/
- https://www.maiachess.com/ , https://github.com/csslab/maia-platform-frontend
- https://www.chessroots.com/
- https://www.chessvia.ai/ , https://www.chessigma.com/supercoach
- https://github.com/ai-chess-training/LLM-ChessCoach

공통 결론: 검증된 모든 제품이 "엔진이 계산하고 LLM은 서술만" 구조. 게임 간 개인화 기억 + 자연어 중장기 계획 + 그래프 오프닝 시각화를 모두 갖춘 제품은 없음.

## 2. 연구

### 사람 수 모델 (Maia 계열, 토론토대 CSSLab)
- Maia-1 (KDD 2020): 레이팅대별 lc0 가중치 9개. https://github.com/CSSLab/maia-chess
- Maia-2 (NeurIPS 2024): 양측 Elo 조건부 단일 PyTorch 모델. `pip install maia2`. MIT. https://github.com/CSSLab/maia2
- Maia-3 (2026): 트랜스포머, UCI 엔진으로 실행, HF 체크포인트. AGPL. README가 신규 프로젝트에 Maia-3 권장. https://github.com/CSSLab/maia3
- 개인 플레이어 모델: maia-individual (KDD 2022, 가중치 비공개), Maia4All (2025, 20게임으로 개인 모델, 코드 없음). https://arxiv.org/abs/2507.21488

### 개념 추출 (엔진 내부 → 사람 개념)
- McGrath 2022 PNAS, AlphaZero 개념 프로빙. https://arxiv.org/abs/2111.09259
- Schut 2023, AlphaZero에서 새 개념 발견 후 GM에게 전수. 코드 없음. https://arxiv.org/abs/2310.16410
- lc0 기반 오픈소스 재현: ii-map https://github.com/patrik-ha/ii-map , Leela-SAEs https://github.com/JacklE0niden/Leela-SAEs

### LLM 설명/해설
- Kim et al. NAACL 2025, 개념 유도 해설 (Stockfish-8 평가 항목 20개를 개념으로). 코드 있음. https://github.com/ml-postech/concept-guided-chess-commentary
- ACT-Eval (2026-08): 해설을 원자적 주장으로 분해해 python-chess/Stockfish 도구로 검증. 도구 없는 GPT-5.4는 주장의 22%가 오류. https://github.com/hebbarashwin/act_eval
- C1 (CSSLab 2026): Stockfish depth-24 PV를 사고 사슬 설명으로 증류. https://github.com/CSSLab/C1
- ChessQA 벤치마크 (2025). https://arxiv.org/abs/2510.23948
- ChessGPT (2023), Jhamtani 2018 GameKnot 해설 데이터 (재배포 안 됨).

### 전술 모티프 탐지
- lichess-puzzler tagger cook.py: 규칙 기반 약 50개 테마. 가장 좋은 출발점. https://github.com/ornicar/lichess-puzzler/tree/master/tagger
- CARA: 44개 패턴, 데스크톱 앱, GPL. https://github.com/pguntermann/CARA

### 폰 구조 분류
- 오픈소스 분류기/데이터셋 없음. 직접 만들어야 함. 분류 체계 참고: Soltis 17개, Flores Rios 28개.

### Stockfish 정적 평가 분해
- 16.1 (2024-02)부터 classical eval 항목표 제거. 항목별 특징이 필요하면 Stockfish 15.1 이하를 보조 엔진으로 쓰거나 python-chess로 직접 계산.

## 3. 데이터 소스와 툴

| 항목 | 사실 | 링크 |
|---|---|---|
| Lichess 게임 DB | 약 80억 판, 월별 pgn.zst, 약 6%에 엔진 평가, 2017-04 이후 시계 포함. CC0 | https://database.lichess.org/ |
| Lichess 퍼즐 DB | 약 606만 개, 테마 70개, CC0 | https://database.lichess.org/#puzzles |
| Lichess 오프닝 익스플로러 API | masters/lichess/player. 2026-04부터 로그인 필수 | https://lichess.org/api#tag/Opening-Explorer |
| Lichess 게임 export API | 시계/평가/정확도 옵션. 무인증 20게임/초 | https://lichess.org/api#tag/Games/operation/apiGamesUser |
| Chess.com 공개 API | 월별 아카이브, PGN에 시계 포함, 평가 엔드포인트 없음. 약관 D조: AI 학습 목적 수집 금지 | https://www.chess.com/news/view/published-data-api |
| chess-openings TSV | ECO+이름+수순 3,810행, CC0 | https://github.com/lichess-org/chess-openings |
| Syzygy | 7기물까지. 7기물 세트 16.7 TiB. Lichess tablebase API로 대체 가능 | https://github.com/lichess-org/lila-tablebase |
| TWIC | 개인 용도만 허용, 재배포 불가 | https://theweekinchess.com/twic |
| Stockfish 18 | MultiPV, Syzygy, UCI_ShowWDL | https://github.com/official-stockfish/Stockfish |
| python-chess 1.11.2 | UCI, Syzygy, PGN | https://python-chess.readthedocs.io/ |
| chessground 10.1.1 | Lichess 보드 UI, GPL | https://github.com/lichess-org/chessground |
| lc0 0.32.1 | Maia-1 실행용 | https://github.com/LeelaChessZero/lc0 |
