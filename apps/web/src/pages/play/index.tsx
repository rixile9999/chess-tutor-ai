import { useCallback, useEffect, useMemo, useReducer, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { Chess, type Square } from 'chess.js';
import type { Key } from 'chessground/types';
import { api } from '../../api/client';
import type { BookMoves, Color, HintLevel, OpeningCard, OpponentKind } from '../../api/types';
import { Board, type BoardShape } from '../../components/Board';
import { PromotionPicker, type PromotionPiece } from '../../components/PromotionPicker';
import { legalDests, sideToMove } from '../../lib/chess';
import { getUsername, setUsername } from '../../lib/user';
import { IconArrow, IconFlip, IconRestart, IconUndo } from '../training/icons';
import { SIDE_LABEL, errorText, useBoardSize } from '../training/util';
import '../training/training.css';
import './play.css';
import { AlertBanner, CoachPanel, type HintState } from './CoachPanel';
import { MoveList } from './MoveList';
import { OpeningTab } from './OpeningPicker';
import { OPPONENT_RANGE, SettingsPanel, clampRating, opponentName } from './SettingsPanel';
import { buildPgn } from './pgn';
import * as S from './state';

const STORE_KEY = 'chess-tutor:play:current';
const TABS = [
  { id: 'moves', label: '수 목록' },
  { id: 'coach', label: '코치' },
  { id: 'opening', label: '오프닝' },
  { id: 'settings', label: '설정' },
] as const;
type Tab = (typeof TABS)[number]['id'];

function clearStore() { try { localStorage.removeItem(STORE_KEY); } catch { /* ignore */ } }

/** Restore an unfinished game (4.5). Anything unreadable is dropped rather than crashing the page. */
function loadStored(): S.PlayState | null {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { v?: number; state?: S.PlayState } | null;
    const st = parsed && parsed.v === 1 ? parsed.state : null;
    if (!st || !S.validFen(st.startFen) || !Array.isArray(st.plies) || st.plies.length === 0) return null;
    if (st.plies.some((p) => !p || typeof p.san !== 'string' || !S.validFen(p.fen))) return null;
    return S.initialState(st);
  } catch { return null; }
}

function colorParamOf(raw: string | null): Color | null {
  return raw === 'white' || raw === 'black' ? raw : null;
}

/** Start position and table settings from the query (4.1), else the game left in localStorage. */
type Boot = { state: S.PlayState; started: boolean };

function makeInitial(params: URLSearchParams): Boot {
  const fen = S.validFen(params.get('fen'));
  const openingId = params.get('opening');
  const stored = fen || openingId ? null : loadStored();
  if (stored) return { state: stored, started: true };
  const kind: OpponentKind = params.get('opp') === 'stockfish' ? 'stockfish' : 'maia';
  const raw = Number(params.get('rating'));
  const rating = clampRating(kind, Number.isFinite(raw) && raw > 0 ? raw : OPPONENT_RANGE[kind].min + 400);
  const startFen = fen ?? S.START_FEN;
  const userColor = colorParamOf(params.get('color')) ?? sideToMove(startFen);
  return {
    state: S.initialState({ startFen, control: S.controlForUser(userColor), opponent: { kind, rating } }),
    started: !!fen || !!openingId,
  };
}

export default function PlayPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const bootRef = useRef<Boot | null>(null);
  if (!bootRef.current) bootRef.current = makeInitial(params);
  const boot: Boot = bootRef.current;
  const [state, dispatch] = useReducer(S.playReducer, boot.state);
  const [started, setStarted] = useState(boot.started);

  const [username, setUser] = useState<string | null>(() => getUsername());
  const [tab, setTab] = useState<Tab>('moves');
  const [orientation, setOrientation] = useState<Color>(() => S.userColorOf(boot.state.control) ?? 'white');
  const [thinking, setThinking] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiRetry, setAiRetry] = useState(0);
  const [hint, setHint] = useState<HintState>({ level: null, loading: false, error: null, data: null });
  const [bookOn, setBookOn] = useState(false);
  const [book, setBook] = useState<BookMoves | null>(null);
  const [bookError, setBookError] = useState<string | null>(null);
  const [promo, setPromo] = useState<{ orig: string; dest: string; color: Color } | null>(null);
  const [confirmCut, setConfirmCut] = useState<string | null>(null);
  const [fenDialog, setFenDialog] = useState<string | null>(null);
  const [pgnDialog, setPgnDialog] = useState<string | null>(null);
  const [needName, setNeedName] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [openingError, setOpeningError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [boardNonce, setBoardNonce] = useState(0);
  const [metaEl, setMetaEl] = useState<HTMLElement | null>(null);
  const { ref, size } = useBoardSize();
  /** Latest AI request id — a stale reply is dropped instead of landing on a changed position. */
  const reqRef = useRef(0);
  const hintReq = useRef(0);

  const live = S.liveFen(state);
  const cursorFen = S.fenAt(state, state.cursor);
  const turn = sideToMove(live);
  const cursorTurn = sideToMove(cursorFen);
  const userColor = S.userColorOf(state.control);
  const aiColor = S.aiColorOf(state.control);
  const atLive = state.cursor === state.plies.length;
  const over = state.status !== 'playing';
  const drill = S.drillActive(state);
  const userRating = state.opponent.rating;
  const gameParam = params.get('game');
  const plyParam = params.get('ply');

  useEffect(() => { setMetaEl(document.getElementById('topbar-meta')); }, []);
  useEffect(() => { if (userColor) setOrientation(userColor); }, [userColor]);
  useEffect(() => { if (!toast) return; const t = window.setTimeout(() => setToast(null), 4000); return () => window.clearTimeout(t); }, [toast]);
  useEffect(() => { setHint({ level: null, loading: false, error: null, data: null }); }, [live]);

  // Entry params (4.1). The initial render already consumed `fen`; an opening needs a fetch.
  const entryKey = `${params.get('fen') ?? ''}|${params.get('opening') ?? ''}|${params.get('color') ?? ''}|${params.get('drill') ?? ''}`;
  const applied = useRef(params.get('opening') ? '' : entryKey);
  useEffect(() => {
    if (applied.current === entryKey) return;
    applied.current = entryKey;
    const openingId = params.get('opening');
    const color = colorParamOf(params.get('color'));
    const fen = S.validFen(params.get('fen'));
    if (openingId) {
      let cancelled = false;
      setOpeningError(null);
      api.play.opening(openingId, username)
        .then((d) => {
          if (cancelled) return;
          const line = Array.isArray(d.line_san) ? d.line_san : [];
          dispatch({
            type: 'loadOpening',
            opening: { id: d.id, name: d.name || openingId, line, tabiyaFen: d.tabiya_fen, drill: params.get('drill') === '1' },
            color: color ?? (d.sides?.[0] ?? 'white'),
          });
          setStarted(true);
        })
        .catch((e) => { if (!cancelled) { setOpeningError(errorText(e)); setStarted(true); } });
      return () => { cancelled = true; };
    }
    if (fen) {
      dispatch({ type: 'restart', startFen: fen, opening: null, control: S.controlForUser(color ?? sideToMove(fen)) });
      setStarted(true);
    }
  }, [entryKey, params, username]);

  // The AI answers whenever it is its turn: the book in a drill, otherwise POST /play/move.
  const aiTurn = !over && aiColor !== null && turn === aiColor && !state.drillDeviation && started;
  const bookSan = drill && state.opening ? state.opening.line[state.plies.length] : undefined;
  useEffect(() => {
    if (!aiTurn) { setThinking(false); return; }
    let cancelled = false;
    let timer: number | undefined;
    const id = Date.now() + Math.random();
    reqRef.current = id;
    const startedAt = Date.now();
    setThinking(true);
    setAiError(null);
    // A reply that lands sooner than 300ms is held back so the move is readable (4.2).
    const settle = (run: () => void) => {
      timer = window.setTimeout(() => {
        if (cancelled || reqRef.current !== id) return;
        setThinking(false);
        run();
      }, Math.max(0, 300 - (Date.now() - startedAt)));
    };
    if (bookSan) { settle(() => dispatch({ type: 'bookMove', san: bookSan })); }
    else {
      const fen = live;
      const take = (res: { san: string; uci: string; probs?: Record<string, number> | null; source?: string | null }) => settle(() => dispatch({
        type: 'aiMove',
        uci: res.uci || null,
        san: res.san || null,
        source: (res.source ?? null) as S.Ply['source'],
        prob: res.probs && typeof res.probs === 'object' ? res.probs[res.san] ?? null : null,
      }));
      api.play.move(fen, state.opponent, userRating)
        .then(take)
        // /play/move is not on the server yet; /maia/move is the temporary opponent.
        .catch(() => api.maia.move(fen, Math.min(2200, Math.max(1000, state.opponent.rating))).then(take))
        .catch((e) => { if (!cancelled && reqRef.current === id) { setThinking(false); setAiError(errorText(e)); } });
    }
    return () => { cancelled = true; if (timer) window.clearTimeout(timer); };
  }, [aiTurn, live, bookSan, state.opponent, userRating, aiRetry]);

  // 실수 알림 (4.3): runs in parallel with the AI reply and never blocks the game.
  const lastPly = state.plies.length ? state.plies[state.plies.length - 1] : null;
  const checkKey = lastPly && lastPly.by === 'user' ? `${state.plies.length}:${lastPly.uci}` : null;
  const checkedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!checkKey || state.coach.alerts === 'off' || state.status !== 'playing') return;
    if (checkedRef.current === checkKey) return;
    checkedRef.current = checkKey;
    const n = state.plies.length;
    const before = n >= 2 ? state.plies[n - 2].fen : state.startFen;
    let cancelled = false;
    api.play.check(before, state.plies[n - 1].san, userRating)
      .then((res) => {
        if (cancelled) return;
        const bad = res.classification === 'blunder' || (state.coach.alerts === 'mistake' && res.classification === 'mistake');
        if (bad) dispatch({ type: 'alert', check: res });
      })
      .catch(() => { /* the alert is optional help; a missing endpoint must not interrupt the game */ });
    return () => { cancelled = true; };
  }, [checkKey, state.coach.alerts, state.status, state.plies, state.startFen, userRating]);

  // 책 따라가기 (4.4): book moves for the position on the board.
  useEffect(() => {
    if (!bookOn || state.control !== 'manual') { setBook(null); setBookError(null); return; }
    let cancelled = false;
    setBookError(null);
    api.play.book(cursorFen)
      .then((b) => { if (!cancelled) setBook(b && Array.isArray(b.moves) ? b : { fen: cursorFen, opening: null, moves: [] }); })
      .catch((e) => { if (!cancelled) { setBook(null); setBookError(errorText(e)); } });
    return () => { cancelled = true; };
  }, [bookOn, cursorFen, state.control]);

  // Keep the unfinished game across reloads (4.5).
  useEffect(() => {
    if (state.status === 'saved' || state.plies.length === 0) return;
    try { localStorage.setItem(STORE_KEY, JSON.stringify({ v: 1, state })); } catch { /* quota or private mode */ }
  }, [state]);

  const setCursor = useCallback((c: number) => dispatch({ type: 'setCursor', cursor: c }), []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
      if (e.key === 'ArrowLeft') { e.preventDefault(); setCursor(state.cursor - 1); }
      else if (e.key === 'ArrowRight') { e.preventDefault(); setCursor(state.cursor + 1); }
      else if (e.key === 'Home') { e.preventDefault(); setCursor(0); }
      else if (e.key === 'End') { e.preventDefault(); setCursor(state.plies.length); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [setCursor, state.cursor, state.plies.length]);

  const hintLevelForMove = (): HintLevel | null => {
    if (state.hintFen !== cursorFen || !state.hintsShown.length) return null;
    return Math.max(...state.hintsShown) as HintLevel;
  };

  const submitUci = useCallback((uci: string) => {
    if (state.cursor < state.plies.length) { setConfirmCut(uci); return; }
    dispatch({ type: 'move', uci, hintLevel: hintLevelForMove() });
  }, [state.cursor, state.plies.length, state.hintFen, state.hintsShown, cursorFen]);

  const movable = useMemo(() => {
    if (over || state.drillDeviation || !started) return null;
    if (aiColor && cursorTurn === aiColor) return null;
    return { color: cursorTurn, dests: legalDests(cursorFen) };
  }, [over, state.drillDeviation, started, aiColor, cursorTurn, cursorFen]);

  const onMove = useCallback((orig: string, dest: string) => {
    if (!movable) return;
    let isPromo = false;
    try { isPromo = new Chess(cursorFen).get(orig as Square)?.type === 'p' && (dest[1] === '8' || dest[1] === '1'); } catch { /* ignore */ }
    if (isPromo) { setPromo({ orig, dest, color: cursorTurn }); return; }
    submitUci(orig + dest);
  }, [movable, cursorFen, cursorTurn, submitUci]);

  const cancelBoardMove = () => { setPromo(null); setConfirmCut(null); setBoardNonce((n) => n + 1); };

  const requestHint = useCallback((level: HintLevel) => {
    const fen = live;
    hintReq.current += 1;
    const id = hintReq.current;
    setHint({ level, loading: true, error: null, data: null });
    api.play.hint({ fen, level, rating: userRating, start_fen: state.startFen, moves_san: state.plies.map((p) => p.san) })
      .then((data) => {
        if (id !== hintReq.current) return;
        setHint({ level, loading: false, error: null, data });
        dispatch({ type: 'hintShown', level, fen });
      })
      .catch((e) => { if (id === hintReq.current) setHint({ level, loading: false, error: errorText(e), data: null }); });
  }, [live, userRating, state.startFen, state.plies]);

  const shapes = useMemo<BoardShape[]>(() => {
    const out: BoardShape[] = [];
    const best = hint.level === 3 ? hint.data?.best?.uci : null;
    if (best && best.length >= 4 && atLive) out.push({ orig: best.slice(0, 2) as Key, dest: best.slice(2, 4) as Key, brush: 'blue' });
    for (const m of bookOn && book ? book.moves.slice(0, 5) : []) {
      if (m.uci && m.uci.length >= 4) out.push({ orig: m.uci.slice(0, 2) as Key, dest: m.uci.slice(2, 4) as Key, brush: 'green' });
    }
    return out;
  }, [hint.level, hint.data, atLive, bookOn, book]);

  const lastMove = useMemo<[string, string] | null>(() => {
    const p = state.cursor > 0 ? state.plies[state.cursor - 1] : null;
    return p && p.uci.length >= 4 ? [p.uci.slice(0, 2), p.uci.slice(2, 4)] : null;
  }, [state.cursor, state.plies]);

  const sans = useMemo(() => state.plies.map((p) => p.san), [state.plies]);
  const pgn = useCallback(() => buildPgn({
    startFen: state.startFen,
    plies: state.plies,
    result: state.result,
    headers: {
      Date: new Date().toISOString().slice(0, 10).replace(/-/g, '.'),
      White: userColor === 'black' ? opponentName(state.opponent) : username ?? '나',
      Black: userColor === 'black' || state.control === 'manual' ? username ?? '나' : opponentName(state.opponent),
      Termination: state.termination,
      Opening: state.opening?.name ?? null,
      Mode: S.CONTROL_LABEL[state.control],
      Opponent: state.control === 'manual' ? null : opponentName(state.opponent),
      PracticeMode: S.practiceModeOf(state),
      Hints: state.stats.hints,
      Takebacks: state.stats.takebacks,
      Alerts: state.stats.alerts,
    },
  }), [state, username, userColor]);

  const copyPgn = () => {
    const text = pgn();
    navigator.clipboard?.writeText(text).then(() => setToast('PGN을 클립보드에 복사했습니다.'), () => setPgnDialog(text));
    if (!navigator.clipboard) setPgnDialog(text);
  };

  const save = () => {
    if (!username) { setNeedName(true); return; }
    setSaving(true);
    setSaveError(null);
    api.play.save({
      username,
      user_color: userColor,
      start_fen: state.startFen,
      moves_san: sans,
      result: state.result,
      termination: state.termination,
      opponent: state.control === 'manual' ? null : state.opponent,
      coach: { preset: state.coach.preset, hints: state.stats.hints, takebacks: state.stats.takebacks, alerts: state.stats.alerts },
      opening_id: state.opening?.id ?? null,
      practice_mode: S.practiceModeOf(state),
      clocks: null,
      time_control: null,
      analyse: true,
    })
      .then((res) => {
        clearStore();
        dispatch({ type: 'saved', gameId: res.game_id });
        navigate(`/review/${res.game_id}`);
      })
      .catch((e) => setSaveError(errorText(e)))
      .finally(() => setSaving(false));
  };

  const newGame = (startFen: string, opening: S.OpeningState | null = null) => {
    clearStore();
    checkedRef.current = null;
    dispatch({ type: 'restart', startFen, opening });
    setStarted(true);
    setAiError(null);
    setSaveError(null);
  };

  const pickOpening = (card: OpeningCard, color: Color, wantDrill: boolean) => {
    clearStore();
    checkedRef.current = null;
    dispatch({
      type: 'loadOpening',
      opening: { id: card.id, name: card.name, line: card.line_san ?? [], tabiyaFen: card.tabiya_fen, drill: wantDrill },
      color,
    });
    setStarted(true);
    setTab('moves');
  };

  /**
   * 무승부 제안. Docs 4.2 wants "±30cp over the last two moves and move 30+", but the client is
   * deliberately blind to the evaluation during a game (8.2), so only the move-number half is checked.
   */
  const offerDraw = () => {
    if (state.control === 'manual') { dispatch({ type: 'acceptDraw' }); return; }
    const moveNo = S.moveNumberOf(live);
    if (moveNo >= 30) { dispatch({ type: 'acceptDraw' }); setToast('상대가 무승부를 받아들였습니다.'); }
    else { dispatch({ type: 'declineDraw' }); setToast(`상대가 거절했습니다 — 30수 이후에만 받아들입니다 (지금 ${moveNo}수).`); }
  };

  const canTakeback = state.coach.takebacks && state.plies.length > 0;
  const canHint = state.coach.hints && state.status === 'playing' && atLive && (userColor === null || turn === userColor);
  const hintReason = !state.coach.hints ? '지금 프리셋은 힌트를 끕니다. 설정 탭에서 켤 수 있습니다.'
    : state.status !== 'playing' ? '게임이 끝났습니다.'
    : !atLive ? '지난 국면을 보는 중입니다. 마지막 수로 돌아오면 힌트를 받을 수 있습니다.'
    : '상대 차례입니다.';

  const endText = over
    ? `${state.termination ?? '종료'} · ${state.result === '1/2-1/2' ? '무승부' : `${SIDE_LABEL[state.result === '1-0' ? 'white' : 'black']} 승`}`
    : null;
  const statusText = endText
    ?? (thinking ? `${state.control === 'manual' ? '상대' : opponentName(state.opponent)}가 생각하는 중`
      : state.drillDeviation ? '책에서 벗어났습니다'
      : userColor === null ? `${SIDE_LABEL[turn]} 차례 · 수동`
      : turn === userColor ? `당신 차례 · ${SIDE_LABEL[userColor]}` : '상대 차례');

  const reviewLink = gameParam ? `/review/${gameParam}${plyParam ? `/${plyParam}` : ''}` : null;

  return (
    <div className="tr-page pl-page">
      {metaEl && createPortal(
        <>
          <span style={{ color: 'var(--ink)', fontWeight: 600 }}>대국</span>
          <span>{S.CONTROL_LABEL[state.control]}{state.control === 'manual' ? '' : ` · ${opponentName(state.opponent)}`}</span>
          <span className="chip" style={{ height: 22 }}>코치 {S.PRESET_LABEL[state.coach.preset]}</span>
          {state.opening && <span className="chip" style={{ height: 22 }}>{state.opening.name}</span>}
        </>,
        metaEl,
      )}

      <div className="tr-head">
        <div className="tr-title">대국</div>
        <div className="tr-sub">두는 동안 코치가 붙습니다. 끝나면 저장해서 그대로 리뷰로 이어집니다.</div>
        <div className="spacer" />
        {username && <span className="chip">{username}</span>}
      </div>

      {!started ? (
        <StartCard
          onStart={() => newGame(S.START_FEN)}
          onCatalog={() => { setStarted(true); setTab('opening'); }}
          onFen={() => setFenDialog(S.START_FEN)}
        />
      ) : null}

      <div className="tr-body" style={started ? undefined : { display: 'none' }}>
        <div className="tr-left" ref={ref}>
          <div className="pl-seg pl-seg-wide">
            {(['manual', 'ai-white', 'ai-black'] as S.Control[]).map((c) => (
              <button key={c} type="button" className={`pl-seg-btn${state.control === c ? ' on' : ''}`} onClick={() => dispatch({ type: 'setControl', control: c })}>
                {S.CONTROL_LABEL[c]}
              </button>
            ))}
          </div>

          <div className="tr-board">
            <Board
              key={`${state.startFen}:${boardNonce}`}
              fen={cursorFen}
              orientation={orientation}
              size={size}
              movable={movable}
              onMove={onMove}
              shapes={shapes}
              lastMove={lastMove}
            />
            {promo && (
              <PromotionPicker
                color={promo.color}
                orientation={orientation}
                square={promo.dest}
                size={size}
                onPick={(p: PromotionPiece) => { const { orig, dest } = promo; setPromo(null); submitUci(orig + dest + p); }}
                onCancel={cancelBoardMove}
              />
            )}
          </div>

          <div className="tr-controls">
            <button type="button" className="pl-nav" onClick={() => setCursor(0)} disabled={state.cursor === 0} title="처음 (Home)">⏮</button>
            <button type="button" className="pl-nav" onClick={() => setCursor(state.cursor - 1)} disabled={state.cursor === 0} title="이전 (←)">◀</button>
            <button type="button" className="pl-nav" onClick={() => setCursor(state.cursor + 1)} disabled={atLive} title="다음 (→)">▶</button>
            <button type="button" className="pl-nav" onClick={() => setCursor(state.plies.length)} disabled={atLive} title="마지막 (End)">⏭</button>
            <button type="button" className="btn btn-ghost compact" onClick={() => setOrientation((o) => (o === 'white' ? 'black' : 'white'))}><IconFlip /> 뒤집기</button>
            <button
              type="button"
              className="btn btn-ghost compact"
              onClick={() => dispatch({ type: 'takeback' })}
              disabled={!canTakeback}
              title={state.coach.takebacks ? '내 차례로 돌아갑니다' : '지금 프리셋은 물리기를 막습니다'}
            >
              <IconUndo /> 물리기
            </button>
            <div className="spacer" />
            <span className={`tr-status pl-status${over ? ' over' : ''}`}>
              <span className={`dot${thinking ? ' wait' : ''}`} />
              <span style={{ fontWeight: over ? 700 : 500 }}>{statusText}</span>
            </span>
          </div>

          <div className="tr-controls">
            <button type="button" className="btn btn-ghost compact" onClick={() => setFenDialog(state.startFen)}>국면 설정</button>
            <button type="button" className="btn btn-ghost compact" onClick={() => dispatch({ type: 'resign', side: userColor ?? turn })} disabled={over}>기권</button>
            <button type="button" className="btn btn-ghost compact" onClick={offerDraw} disabled={over}>무승부 제안</button>
            <button type="button" className="btn btn-ghost compact" onClick={copyPgn} disabled={state.plies.length === 0}>PGN 복사</button>
            <div className="spacer" />
            {over && <button type="button" className="btn btn-ghost compact" onClick={() => newGame(state.startFen, state.opening)}><IconRestart /> 다시 두기</button>}
            <button type="button" className="btn btn-primary compact" onClick={save} disabled={saving || state.plies.length === 0 || state.status === 'saved'}>
              {saving ? '저장하는 중' : '저장해서 리뷰'}
            </button>
          </div>

          {needName && !username && (
            <form
              className="tr-msg note"
              onSubmit={(e) => {
                e.preventDefault();
                const v = new FormData(e.currentTarget).get('name');
                if (typeof v === 'string' && v.trim()) { setUsername(v.trim()); setUser(v.trim()); setNeedName(false); }
              }}
            >
              <span>누구의 게임으로 저장할까요</span>
              <input name="name" className="tr-input" placeholder="사용자명" autoFocus />
              <button type="submit" className="btn btn-primary compact">저장</button>
            </form>
          )}
          {saveError && (
            <div className="tr-msg bad">
              <span><b>저장하지 못했습니다.</b> {saveError}</span>
              <button type="button" className="btn btn-ghost compact" onClick={save}>다시 시도</button>
            </div>
          )}
          {state.status === 'saved' && state.savedGameId !== null && (
            <div className="tr-msg good">
              <span><b>저장했습니다.</b> 분석이 끝나면 리뷰에서 볼 수 있습니다.</span>
              <Link to={`/review/${state.savedGameId}`}>리뷰로 <IconArrow /></Link>
            </div>
          )}
          {aiError && (
            <div className="tr-msg bad">
              <span><b>상대의 응수를 받지 못했습니다.</b> {aiError}</span>
              <button type="button" className="btn btn-ghost compact" onClick={() => setAiRetry((n) => n + 1)}>다시 요청</button>
            </div>
          )}
          {openingError && (
            <div className="tr-msg bad"><span><b>오프닝을 불러오지 못했습니다.</b> {openingError}</span></div>
          )}
          {state.drillDeviation && (
            <div className="tr-msg note pl-drill">
              <span>
                책 수는 <span className="mv">{state.drillDeviation.expected}</span>입니다. 당신은 <span className="mv">{state.drillDeviation.played}</span>를 두었습니다.
              </span>
              <div className="spacer" />
              <button type="button" className="btn btn-ghost compact" onClick={() => dispatch({ type: 'drillRetry' })}>다시 두기</button>
              <button type="button" className="btn btn-ghost compact" onClick={() => dispatch({ type: 'drillContinue' })}>그대로 진행</button>
            </div>
          )}
          {toast && <div className="tr-msg note"><span>{toast}</span></div>}
          {state.startFen !== S.START_FEN && (
            <div className="tr-msg note small">
              <span>시작 국면</span>
              <span className="tr-fen" style={{ flex: 1 }}>{state.startFen}</span>
              {reviewLink && <Link to={reviewLink}>리뷰로 <IconArrow /></Link>}
            </div>
          )}
        </div>

        <div className="card tr-panel">
          <div className="tr-tabs" role="tablist">
            {TABS.map((t) => (
              <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} className={`tr-tab${tab === t.id ? ' active' : ''}`} onClick={() => setTab(t.id)}>
                {t.label}
                {t.id === 'coach' && state.pendingAlert && <span className="badge badge-bad">1</span>}
                {t.id === 'opening' && S.tabiyaReached(state) && <span className="badge badge-good">타비야</span>}
              </button>
            ))}
          </div>

          {state.pendingAlert && (
            <AlertBanner
              alert={state.pendingAlert}
              canTakeback={canTakeback}
              onTakeback={() => dispatch({ type: 'takeback' })}
              onKeep={() => dispatch({ type: 'dismissAlert' })}
            />
          )}

          {tab === 'moves' && <MoveList startFen={state.startFen} plies={state.plies} cursor={state.cursor} onSelect={setCursor} />}
          {tab === 'coach' && (
            <CoachPanel
              coach={state.coach}
              stats={state.stats}
              hint={hint}
              hintsShown={state.hintFen === live ? state.hintsShown : []}
              canHint={canHint}
              hintReason={hintReason}
              onHint={requestHint}
            />
          )}
          {tab === 'opening' && (
            <OpeningTab
              username={username}
              current={state.opening}
              onPick={pickOpening}
              playedCount={state.plies.length}
              drillOn={drill}
              tabiya={S.tabiyaReached(state)}
              manual={state.control === 'manual'}
              bookOn={bookOn}
              onToggleBook={setBookOn}
              book={book}
              bookError={bookError}
            />
          )}
          {tab === 'settings' && (
            <SettingsPanel
              control={state.control}
              onControl={(c) => dispatch({ type: 'setControl', control: c })}
              opponent={state.opponent}
              onOpponent={(o) => dispatch({ type: 'setOpponent', opponent: o })}
              coach={state.coach}
              onPreset={(p) => dispatch({ type: 'setPreset', preset: p })}
              onCoach={(c) => dispatch({ type: 'setCoach', coach: c })}
              username={username}
              onUsername={(n) => { setUsername(n); setUser(n); setNeedName(false); }}
              onNewGame={() => newGame(S.START_FEN)}
            />
          )}
        </div>
      </div>

      {confirmCut !== null && (
        <Dialog title="뒤의 수를 지울까요" onClose={cancelBoardMove}>
          <p className="muted">
            지금 보고 있는 국면에서 두면 뒤의 {state.plies.length - state.cursor}수가 지워집니다. 변화수는 따로 남지 않습니다.
          </p>
          <div className="tr-actions">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => { const uci = confirmCut; setConfirmCut(null); dispatch({ type: 'truncateAndMove', uci, hintLevel: hintLevelForMove() }); }}
            >
              잘라내고 두기
            </button>
            <button type="button" className="btn btn-ghost" onClick={cancelBoardMove}>취소</button>
          </div>
        </Dialog>
      )}

      {fenDialog !== null && (
        <FenDialog
          value={fenDialog}
          onChange={setFenDialog}
          onClose={() => setFenDialog(null)}
          onApply={(fen) => { setFenDialog(null); newGame(fen); }}
        />
      )}

      {pgnDialog !== null && (
        <Dialog title="PGN" onClose={() => setPgnDialog(null)}>
          <textarea className="pl-textarea" readOnly value={pgnDialog} rows={10} onFocus={(e) => e.currentTarget.select()} />
          <div className="tr-actions"><button type="button" className="btn btn-ghost" onClick={() => setPgnDialog(null)}>닫기</button></div>
        </Dialog>
      )}
    </div>
  );
}

function StartCard({ onStart, onCatalog, onFen }: { onStart: () => void; onCatalog: () => void; onFen: () => void }) {
  return (
    <div className="card tr-empty">
      <div className="h3">어디서 시작할까요</div>
      <p className="muted">초기 국면에서 한 판 두거나, 오프닝 카탈로그의 타비야에서 시작하거나, FEN을 직접 넣을 수 있습니다. 리뷰에서 넘어온 국면은 바로 열립니다.</p>
      <div className="tr-actions">
        <button type="button" className="btn btn-primary" onClick={onStart}>초기 국면</button>
        <button type="button" className="btn btn-ghost" onClick={onCatalog}>오프닝 카탈로그</button>
        <button type="button" className="btn btn-ghost" onClick={onFen}>FEN 입력</button>
      </div>
    </div>
  );
}

function FenDialog({ value, onChange, onClose, onApply }: { value: string; onChange: (v: string) => void; onClose: () => void; onApply: (fen: string) => void }) {
  const ok = S.validFen(value.trim());
  return (
    <Dialog title="국면 설정" onClose={onClose}>
      <textarea className="pl-textarea" rows={3} value={value} onChange={(e) => onChange(e.target.value)} spellCheck={false} autoFocus />
      <span className={`small ${ok ? 'faint' : ''}`} style={ok ? undefined : { color: 'var(--bad)' }}>
        {ok ? `${SIDE_LABEL[sideToMove(ok)]} 차례로 시작합니다.` : '읽을 수 없는 FEN입니다.'}
      </span>
      <div className="tr-actions">
        <button type="button" className="btn btn-primary" disabled={!ok} onClick={() => ok && onApply(ok)}>이 국면에서 시작</button>
        <button type="button" className="btn btn-ghost" onClick={() => onChange(S.START_FEN)}>초기 국면</button>
        <button type="button" className="btn btn-ghost" onClick={onClose}>취소</button>
      </div>
    </Dialog>
  );
}

function Dialog({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); onClose(); } };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="pl-veil" onClick={onClose} role="presentation">
      <div className="card pl-dialog" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={title}>
        <div className="h3">{title}</div>
        {children}
      </div>
    </div>
  );
}
