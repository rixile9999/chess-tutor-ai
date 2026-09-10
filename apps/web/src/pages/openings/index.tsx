import { useCallback, useEffect, useMemo, useReducer, useRef, useState, type FormEvent } from 'react';
import { createPortal } from 'react-dom';
import { Link, useSearchParams } from 'react-router-dom';
import type { Key } from 'chessground/types';
import { api } from '../../api/client';
import type { Color, TrapLine } from '../../api/types';
import type { BoardShape } from '../../components/Board';
import { applyUci } from '../../lib/chess';
import { getUsername, setUsername } from '../../lib/user';
import { errorText, useBoardSize } from '../training/util';
import { Strip } from './Strip';
import { Heatmap, defaultPiece, mirrorPiece, pieceOptions } from './Heatmap';
import { BreakTimeline } from './BreakTimeline';
import { Candidates, type MyMoves } from './Candidates';
import { ExplainPanel } from './ExplainPanel';
import { Journal } from './Journal';
import { LineBoard, type PositionLine } from './LineBoard';
import { SetupPanel } from './SetupPanel';
import { useQuery } from './useQuery';
import { useGuide, positionKey } from './useGuide';
import { cachedNote, rememberNote, useNote } from './useNote';
import { buildTree, pathSans } from './model';
import {
  START_FEN, fenAt, initialLine, lastBookPly, lineReducer, moveLabel, onLine, pliesFrom, sansTo, seqKey,
  type CandidateSort, type ExplainDepth,
} from './line';
import './openings.css';

/** One fetch per (username, colour); the whole tree comes down and the chips prune it in the browser. */
const FETCH_DEPTH = 24;
const FETCH_MIN_GAMES = 2;
const MIN_GAMES = [2, 3, 5] as const;
const THROUGH_MOVE = 15;
const BOARD_MAX = 440;
const COLOR_LABEL: Record<Color, string> = { white: '백', black: '흑' };
const DEPTH_KEY = 'chess-tutor:openings:depth';

function storedDepth(): ExplainDepth {
  try {
    const v = localStorage.getItem(DEPTH_KEY);
    return v === 'brief' || v === 'deep' || v === 'normal' ? v : 'normal';
  } catch { return 'normal'; }
}

/** `?color=white&moves=e4,e5,Nf3` — the line is restored from the URL on the first render (§4). */
function readQuery(params: URLSearchParams): { color: Color | null; moves: string[] } {
  const raw = params.get('color');
  const moves = (params.get('moves') ?? '').split(',').map((s) => s.trim()).filter(Boolean);
  return { color: raw === 'white' || raw === 'black' ? raw : null, moves };
}

export default function OpeningsPage() {
  const [params] = useSearchParams();
  const boot = useRef<{ color: Color | null; moves: string[] } | null>(null);
  if (!boot.current) boot.current = readQuery(params);

  const [username, setUser] = useState<string | null>(() => getUsername());
  const [editingUser, setEditingUser] = useState(false);
  const [color, setColor] = useState<Color>(boot.current.color ?? 'white');
  const [minGames, setMinGames] = useState<number>(FETCH_MIN_GAMES);
  const [piece, setPiece] = useState<string>(() => defaultPiece(boot.current?.color ?? 'white'));
  const [orientation, setOrientation] = useState<Color>(boot.current.color ?? 'white');
  const [line, dispatch] = useReducer(lineReducer, undefined, () => initialLine(START_FEN, storedDepth()));
  const [hover, setHover] = useState<string | null>(null);
  const [marked, setMarked] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);

  const mapQ = useQuery(
    () => (username ? api.openings.map(username, color, FETCH_DEPTH, FETCH_MIN_GAMES) : null),
    [username, color],
  );
  const heatQ = useQuery(() => (username ? api.openings.heatmap(username, color, piece, THROUGH_MOVE) : null), [username, color, piece]);
  const breaksQ = useQuery(() => (username ? api.openings.breaks(username, color) : null), [username, color]);

  const map = mapQ.data;
  const tree = useMemo(() => buildTree(map, minGames), [map, minGames]);

  const cursorFen = fenAt(line, line.cursor);
  const cursorKey = positionKey(cursorFen);
  const guideQ = useGuide(cursorFen, color, true);
  const guide = guideQ.data;

  // ---------- the line ----------
  const annotate = useCallback((sans: string[]) => {
    if (!sans.length) return;
    dispatch({ type: 'annotating', sans });
    // The whole line goes up every time: the server needs the move order to see transpositions,
    // and it caps its own engine calls (ANNOTATE_ENGINE_CAP).
    api.openings.annotate({ start_fen: START_FEN, moves_san: sans, color, engine: 'off_book', naturalness: true })
      .then((res) => dispatch({ type: 'annotated', sans, annotations: res.annotations ?? [] }))
      .catch((e: unknown) => dispatch({ type: 'annotateFailed', sans, message: errorText(e) }));
  }, [color]);

  const play = useCallback((uci: string) => {
    const r = applyUci(fenAt(line, line.cursor), uci);
    if (!r) return;
    dispatch({ type: 'play', uci });
    annotate([...sansTo(line, line.cursor), r.san]);
  }, [line, annotate]);

  const jumpTo = useCallback((sans: string[]) => {
    dispatch({ type: 'jumpTo', sans });
    annotate(pliesFrom(START_FEN, sans).map((p) => p.san));
  }, [annotate]);

  // First load only: restore ?moves= with one batch annotation.
  const restored = useRef(false);
  useEffect(() => {
    if (restored.current) return;
    restored.current = true;
    if (boot.current?.moves.length) jumpTo(boot.current.moves);
  }, [jumpTo]);

  // Shareable URL without a navigation (the page state is the line, not the route).
  useEffect(() => {
    const q = new URLSearchParams({ color });
    const sans = sansTo(line, line.cursor);
    if (sans.length) q.set('moves', sans.join(','));
    try { window.history.replaceState(null, '', `${window.location.pathname}?${q}`); } catch { /* ignore */ }
  }, [color, line.plies, line.cursor]);

  useEffect(() => { try { localStorage.setItem(DEPTH_KEY, line.depth); } catch { /* ignore */ } }, [line.depth]);

  // ---------- the focused move and its deep note ----------
  const focusPly = Math.min(line.focusPly, line.plies.length);
  const focused = focusPly > 0 ? line.plies[focusPly - 1] : null;
  const focusFenBefore = focusPly > 0 ? fenAt(line, focusPly - 1) : null;
  const noteQ = useNote(focusFenBefore, focused?.san ?? null);

  const generate = useCallback((regenerate: boolean) => {
    if (!focused || !focusFenBefore) return;
    setGenerating(true);
    setGenError(null);
    api.openings.makeNote({ fen: focusFenBefore, san: focused.san, username, regenerate })
      .then((note) => { rememberNote(focusFenBefore, focused.san, note); noteQ.reload(); })
      .catch((e: unknown) => setGenError(errorText(e)))
      .finally(() => setGenerating(false));
  }, [focused, focusFenBefore, username, noteQ]);

  const onTrap = useCallback((trap: TrapLine, index: number, step: number) => {
    const plies = pliesFrom(fenAt(line, focusPly), trap.line_san.slice(0, step));
    if (!plies.length) return;
    const last = plies[plies.length - 1];
    dispatch({
      type: 'preview',
      preview: {
        key: `${index}:${step}`,
        fen: last.fen,
        title: `${trap.title} · ${plies.map((p, i) => moveLabel(focusPly + i + 1, p.san)).join(' ')}`,
        lastMove: [last.uci.slice(0, 2), last.uci.slice(2, 4)],
      },
    });
  }, [line, focusPly]);

  // ---------- board wiring ----------
  const { ref: sideRef, size } = useBoardSize(BOARD_MAX);
  const boardFen = line.preview ? line.preview.fen : cursorFen;
  const lastMove = useMemo<[string, string] | null>(() => {
    if (line.preview) return line.preview.lastMove;
    const p = line.cursor > 0 ? line.plies[line.cursor - 1] : null;
    return p && p.uci.length >= 4 ? [p.uci.slice(0, 2), p.uci.slice(2, 4)] : null;
  }, [line.preview, line.cursor, line.plies]);
  const shapes = useMemo<BoardShape[]>(() => {
    const out: BoardShape[] = [];
    if (hover && hover.length >= 4) out.push({ orig: hover.slice(0, 2) as Key, dest: hover.slice(2, 4) as Key, brush: 'blue' });
    if (marked) out.push({ orig: marked as Key, brush: 'yellow' });
    return out;
  }, [hover, marked]);

  // ---------- my record overlaid on this position (지도 DAG) ----------
  const myMoves = useMemo<MyMoves>(() => {
    const out: MyMoves = new Map();
    for (const e of map?.edges ?? []) {
      if (e.source === cursorKey && (e.games ?? 0) > 0) out.set(e.target, { games: e.games, score: e.score });
    }
    return out;
  }, [map, cursorKey]);
  const myNode = useMemo(() => map?.nodes?.find((n) => n.id === cursorKey) ?? null, [map, cursorKey]);

  const position: PositionLine = {
    text: line.plies.slice(0, line.cursor).map((p) => p.label).join(' '),
    turn: cursorFen.split(' ')[1] === 'b' ? 'black' : 'white',
    name: guide?.name ?? null,
    eco: guide?.eco ?? null,
    inBook: guide?.in_book ?? true,
    record: myNode && (myNode.games ?? 0) > 0 ? { games: myNode.games, score: myNode.score } : null,
    merges: (tree.others.get(cursorKey) ?? 0) + 1,
  };

  // Journal summaries: a stored note wins over the one-line annotation (§9.3). An entry left behind by
  // a rewind still points at its own line, so its position is replayed from its `seq`.
  const noteSummaries = useMemo(() => {
    const out = new Map<string, string>();
    for (const e of line.journal) {
      if (e.kind !== 'move' || !e.seq.length) continue;
      const key = seqKey(e.seq);
      if (out.has(key)) continue;
      const before = onLine(line, e) ? fenAt(line, e.ply - 1) : fenAfterSans(e.seq.slice(0, -1));
      const note = before ? cachedNote(before, e.seq[e.seq.length - 1]) : null;
      if (note) out.set(key, note.summary);
    }
    return out;
  }, [line, noteQ.data]);

  const bookPly = lastBookPly(line);
  const pathIds = useMemo(
    () => new Set([tree.root?.id ?? '', ...line.plies.slice(0, line.cursor).map((p) => positionKey(p.fen))].filter(Boolean)),
    [tree, line.plies, line.cursor],
  );

  // Raising the threshold or switching colour never touches the line; only the strip is redrawn.
  const changeColor = (c: Color) => {
    if (c === color) return;
    setColor(c);
    setOrientation(c);
    setPiece((p) => mirrorPiece(p, c));
  };

  // First load: if the user has no games as the default colour, show the other colour instead of an empty map.
  const [autoSwitched, setAutoSwitched] = useState(false);
  useEffect(() => {
    if (autoSwitched || mapQ.status !== 404) return;
    setAutoSwitched(true);
    changeColor(color === 'white' ? 'black' : 'white');
  }, [mapQ.status, autoSwitched, color]);

  const saveUser = (name: string) => {
    const v = name.trim();
    if (!v) return;
    setUsername(v);
    setUser(v);
    setEditingUser(false);
  };

  const total = map?.total_games ?? 0;
  // The API answers 404 when the user has no game with this colour, which is an empty state, not an error.
  const noGames = mapQ.status === 404 || (!!map && (total === 0 || (map.nodes ?? []).length === 0));
  const rootNode = useMemo(() => map?.nodes?.find((n) => n.id === map.root) ?? null, [map]);
  const rootName = rootNode?.name?.trim() || null;
  const subtitle = username
    ? [COLOR_LABEL[color], rootName, map ? `내 ${total}판 위에 마스터 DB를 겹침` : '내 기보 위에 마스터 DB를 겹침'].filter(Boolean).join(' · ')
    : '사용자명을 입력하면 내 기보 위에 마스터 DB를 겹쳐 보여줍니다';
  const crumbLabel = focused?.label ?? guide?.name ?? '';
  const urlHint = `/openings?color=${color}${line.cursor ? `&moves=${sansTo(line, line.cursor).join(',')}` : ''}`;

  const [metaEl, setMetaEl] = useState<HTMLElement | null>(null);
  useEffect(() => { setMetaEl(document.getElementById('topbar-meta')); }, []);

  return (
    <div className="op-page">
      {metaEl && createPortal(
        <>
          <span style={{ color: 'var(--ink)', fontWeight: 600 }}>오프닝</span>
          <span>레퍼토리 · {COLOR_LABEL[color]}{crumbLabel ? ` ${crumbLabel}` : ''}</span>
        </>,
        metaEl,
      )}

      <div className="op-head">
        <div className="op-title">오프닝 지도</div>
        <span className="op-sub">{subtitle}</span>
        <div className="spacer" />
        <div className="op-controls">
          {username && !editingUser ? (
            <span className="chip op-user" title="사용자명">
              <span className="op-dot" />
              <span className="mono">{username}</span>
              <button type="button" className="op-icon-btn" title="사용자명 변경" onClick={() => setEditingUser(true)}><IconPencil /></button>
            </span>
          ) : null}
          <div className="op-seg" role="group" aria-label="색">
            {(['white', 'black'] as Color[]).map((c) => (
              <button key={c} type="button" className={c === color ? 'on' : ''} onClick={() => changeColor(c)}>{COLOR_LABEL[c]}</button>
            ))}
          </div>
          <span className="small muted">최소 판수</span>
          <div className="op-min-games" role="group" aria-label="최소 판수">
            {MIN_GAMES.map((m) => (
              <button key={m} type="button" className={`chip op-chip-btn${minGames === m ? ' on' : ''}`} onClick={() => setMinGames(m)}>{m}</button>
            ))}
          </div>
        </div>
      </div>

      {(!username || editingUser) && (
        <UsernameCard initial={username ?? ''} onSave={saveUser} onCancel={username ? () => setEditingUser(false) : undefined} />
      )}

      <div className="card op-card">
        <div className="op-card-head" style={{ alignItems: 'baseline' }}>
          <span className="h3">레퍼토리 개요</span>
          <span className="small muted">폭은 판수, 색은 내 승률. 칸을 누르면 보드가 그 수순으로 점프하고 수순 전체가 일지에 붙습니다</span>
          <div className="op-grow" />
          {map && !noGames && <span className="small faint mono">{(map.nodes ?? []).length}노드 · {(map.edges ?? []).length}가지</span>}
        </div>

        {!username ? (
          <div className="op-state">
            <div className="op-state-title">사용자명이 필요합니다</div>
            <div>위 입력란에 chess.com 또는 lichess 사용자명을 넣어 주세요.</div>
          </div>
        ) : mapQ.loading ? (
          <div className="op-state"><div className="op-spinner" /><div>오프닝 지도를 만드는 중</div></div>
        ) : noGames ? (
          <div className="op-state">
            <div className="op-state-title">{COLOR_LABEL[color]}으로 둔 기보가 아직 없습니다</div>
            <div>기보를 가져오면 {COLOR_LABEL[color]} 레퍼토리 지도를 그립니다. 다른 색으로 두었다면 위에서 색을 바꿔 보세요.</div>
            <Link to="/games?import=1" className="btn btn-primary" style={{ height: 32 }}>기보 가져오기</Link>
          </div>
        ) : mapQ.error ? (
          <ErrorState message={mapQ.error} status={mapQ.status} onRetry={mapQ.reload} />
        ) : !tree.root ? (
          <div className="op-state">
            <div>표시할 가지가 없습니다.</div>
            <div className="small faint">최소 판수를 낮춰 보세요.</div>
          </div>
        ) : (
          <Strip tree={tree} focusId={cursorKey} pathIds={pathIds} onFocus={(n) => jumpTo(pathSans(tree, n))} />
        )}

        <div className="op-legend">
          <span className="item"><span className="op-sw-scale" />색 = 내 승률 (빨강 낮음 · 회색 50% · 파랑 높음)</span>
          <span className="item"><span className="op-sw-tab" />타비야</span>
          <span className="item"><span className="op-sw-dev" />책 이탈</span>
          <span className="item">합류 = 다른 수순으로도 도달</span>
          <span className="item"><span className="op-sw-dash" />점선 = 마스터 DB에만 있는 수</span>
        </div>
      </div>

      <div className="card op-card">
        <div className="op-card-head">
          <span className="h3">국면 탐색</span>
          <span className="small muted">보드에서 직접 두거나 후보를 누르세요. 책에 없는 수도 둘 수 있습니다</span>
          <div className="op-grow" />
          <span className="small faint mono">{urlHint}</span>
        </div>

        <div className="op-line-explore">
          <div className="op-line-side" ref={sideRef}>
            <LineBoard
              fen={boardFen}
              orientation={orientation}
              size={size}
              canPlay={!line.preview}
              onPlay={play}
              lastMove={lastMove}
              shapes={shapes}
              preview={line.preview}
              onClearPreview={() => dispatch({ type: 'preview', preview: null })}
              cursor={line.cursor}
              plyCount={line.plies.length}
              onGoto={(c) => dispatch({ type: 'goto', cursor: c })}
              onFlip={() => setOrientation((o) => (o === 'white' ? 'black' : 'white'))}
              onReset={() => dispatch({ type: 'reset' })}
              position={position}
              playHref={`/play?fen=${encodeURIComponent(cursorFen)}&color=${color}`}
            />
            <SetupPanel
              setups={guide?.setups ?? []}
              side={position.turn}
              loading={guideQ.loading}
              marked={marked}
              onMark={setMarked}
            />
          </div>

          <ExplainPanel
            ply={focusPly}
            label={focused?.label ?? null}
            annotation={focused?.annotation ?? null}
            name={guide?.name ?? null}
            eco={guide?.eco ?? null}
            note={noteQ.data}
            loading={noteQ.loading}
            error={noteQ.error}
            onRetry={noteQ.reload}
            depth={line.depth}
            onDepth={(d) => dispatch({ type: 'setDepth', depth: d })}
            generating={generating}
            genError={genError}
            onGenerate={generate}
            onTrap={onTrap}
            previewKey={line.preview?.key ?? null}
          />
        </div>

        <div className="op-cand-area">
          <Candidates
            candidates={guide?.candidates ?? []}
            mine={myMoves}
            sort={line.sort}
            onSort={(s: CandidateSort) => dispatch({ type: 'setSort', sort: s })}
            orientation={orientation}
            onPlay={play}
            onHover={setHover}
            loading={guideQ.loading}
            error={guideQ.error}
            onRetry={guideQ.reload}
            onBackToBook={() => dispatch({ type: 'goto', cursor: bookPly })}
            backToBookLabel={bookPly ? line.plies[bookPly - 1].label : '시작 국면'}
          />
        </div>
      </div>

      <div className="card op-card">
        <div className="op-card-head">
          <span className="h3">해설 일지</span>
          <span className="small muted">
            둔 수의 시간순 색인. 항목을 누르면 보드가 그 국면으로 가고 옆 패널이 그 수의 해설로 바뀝니다. 되돌아가 다른 수를 두어도 이전 항목은 지워지지 않습니다
          </span>
        </div>
        <Journal
          journal={line.journal}
          plies={line.plies}
          focusPly={focusPly}
          onGoto={(ply) => dispatch({ type: 'goto', cursor: ply })}
          onRetry={annotate}
          noteSummary={(seq) => noteSummaries.get(seqKey(seq)) ?? null}
        />
      </div>

      <div className="op-below">
        <div className="card op-card">
          <div className="op-card-head">
            <span className="h3">기물 목적지</span>
            <span className="small muted">{heatQ.data?.through_move ?? THROUGH_MOVE}수까지 이 기물이 놓인 칸</span>
            <div className="op-grow" />
            <select className="op-select" value={piece} onChange={(e) => setPiece(e.target.value)} aria-label="기물 선택">
              {pieceOptions(color).map((o) => <option key={o.code} value={o.code}>{o.label}</option>)}
            </select>
          </div>
          {!username ? (
            <div className="op-state compact">사용자명을 입력하면 표시됩니다.</div>
          ) : heatQ.loading ? (
            <div className="op-state compact"><div className="op-spinner" /></div>
          ) : heatQ.status === 404 ? (
            <div className="op-state compact">{COLOR_LABEL[color]}으로 둔 기보가 아직 없습니다.</div>
          ) : heatQ.error ? (
            <ErrorState compact message={heatQ.error} status={heatQ.status} onRetry={heatQ.reload} />
          ) : (
            <Heatmap data={heatQ.data} color={color} />
          )}
        </div>

        <div className="card op-card">
          <div className="op-card-head">
            <span className="h3">폰 브레이크 시점</span>
            <span className="small muted">{COLOR_LABEL[color]} · 마스터 분포</span>
            <div className="op-grow" />
            <span className="op-keys">
              <span className="op-key"><span className="op-key-mine" />내 평균</span>
              <span className="op-key"><span className="op-key-master" />마스터 중앙값</span>
            </span>
          </div>
          {!username ? (
            <div className="op-state compact">사용자명을 입력하면 표시됩니다.</div>
          ) : breaksQ.loading ? (
            <div className="op-state compact"><div className="op-spinner" /></div>
          ) : breaksQ.status === 404 ? (
            <div className="op-state compact">{COLOR_LABEL[color]}으로 둔 기보가 아직 없습니다.</div>
          ) : breaksQ.error ? (
            <ErrorState compact message={breaksQ.error} status={breaksQ.status} onRetry={breaksQ.reload} />
          ) : (
            <BreakTimeline rows={breaksQ.data ?? []} color={color} />
          )}
        </div>
      </div>
    </div>
  );
}

/** FEN after a SAN list from the start position; null when the list does not replay. */
function fenAfterSans(sans: string[]): string | null {
  if (!sans.length) return START_FEN;
  const plies = pliesFrom(START_FEN, sans);
  return plies.length === sans.length ? plies[plies.length - 1].fen : null;
}

function UsernameCard({ initial, onSave, onCancel }: { initial: string; onSave: (name: string) => void; onCancel?: () => void }) {
  const [value, setValue] = useState(initial);
  const submit = (e: FormEvent) => { e.preventDefault(); onSave(value); };
  return (
    <form className="card op-card" onSubmit={submit} style={{ gap: 8 }}>
      <div className="op-card-head">
        <span className="h3">누구의 레퍼토리인가요?</span>
        <span className="small muted">기보를 가져올 때 쓴 chess.com 또는 lichess 사용자명</span>
      </div>
      <div className="op-user-form">
        <input className="op-input mono" value={value} onChange={(e) => setValue(e.target.value)} placeholder="사용자명" autoFocus spellCheck={false} />
        <button type="submit" className="btn btn-primary" style={{ height: 32 }} disabled={!value.trim()}>이 사용자로 보기</button>
        {onCancel && <button type="button" className="btn btn-ghost" style={{ height: 32 }} onClick={onCancel}>취소</button>}
        <Link to="/games?import=1" className="small" style={{ marginLeft: 4 }}>아직 기보가 없다면 가져오기</Link>
      </div>
    </form>
  );
}

function ErrorState({ message, status, onRetry, compact }: { message: string; status: number | null; onRetry: () => void; compact?: boolean }) {
  const hint = status === 404 ? '아직 준비되지 않은 기능이거나 사용자를 찾지 못했습니다.' : status && status >= 500 ? '서버 오류입니다.' : null;
  return (
    <div className={`op-state${compact ? ' compact' : ''}`}>
      <div className="op-error">
        <IconWarn />
        <span>불러오지 못했습니다{status ? ` (${status})` : ''}: {message}</span>
      </div>
      {hint && <div className="small faint">{hint}</div>}
      <button type="button" className="btn btn-ghost" style={{ height: 30 }} onClick={onRetry}>다시 시도</button>
    </div>
  );
}

const I = { width: 12, height: 12, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2.2, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const };
function IconPencil() { return <svg {...I}><path d="M4 20h4l10.5-10.5a2.1 2.1 0 0 0-3-3L5 17z" /><path d="M13.5 6.5l3 3" /></svg>; }
function IconWarn() { return <svg {...I} width={14} height={14}><path d="M12 3L2 21h20z" /><path d="M12 10v5M12 18h.01" /></svg>; }
