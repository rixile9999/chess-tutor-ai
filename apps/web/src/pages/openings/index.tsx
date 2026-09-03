import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import type { Color } from '../../api/types';
import { getUsername, setUsername } from '../../lib/user';
import { Explorer } from './Explorer';
import { Strip } from './Strip';
import { Heatmap, defaultPiece, mirrorPiece, pieceOptions } from './Heatmap';
import { BreakTimeline } from './BreakTimeline';
import { useQuery } from './useQuery';
import { buildTree, pathTo } from './model';
import { plainLabel } from './colors';
import './openings.css';

/** One fetch per (username, colour); the whole tree comes down and the chips prune it in the browser. */
const FETCH_DEPTH = 24;
const FETCH_MIN_GAMES = 2;
const MIN_GAMES = [2, 3, 5] as const;
const THROUGH_MOVE = 15;
const COLOR_LABEL: Record<Color, string> = { white: '백', black: '흑' };

export default function OpeningsPage() {
  const [username, setUser] = useState<string | null>(() => getUsername());
  const [editingUser, setEditingUser] = useState(false);
  const [color, setColor] = useState<Color>('white');
  const [minGames, setMinGames] = useState<number>(FETCH_MIN_GAMES);
  const [piece, setPiece] = useState<string>(() => defaultPiece('white'));
  const [focusId, setFocusId] = useState<string | null>(null);

  const mapQ = useQuery(
    () => (username ? api.openings.map(username, color, FETCH_DEPTH, FETCH_MIN_GAMES) : null),
    [username, color],
  );
  const heatQ = useQuery(() => (username ? api.openings.heatmap(username, color, piece, THROUGH_MOVE) : null), [username, color, piece]);
  const breaksQ = useQuery(() => (username ? api.openings.breaks(username, color) : null), [username, color]);

  const map = mapQ.data;
  const tree = useMemo(() => buildTree(map, minGames), [map, minGames]);

  // Raising the threshold can prune the focus away; fall back along the path it used to be on.
  const oldPath = useRef<string[]>([]);
  const focus = useMemo(() => {
    if (!tree.root) return null;
    if (focusId) {
      const hit = tree.byId.get(focusId);
      if (hit) return hit;
      for (let i = oldPath.current.length - 1; i >= 0; i--) {
        const up = tree.byId.get(oldPath.current[i]);
        if (up) return up;
      }
    }
    return tree.root;
  }, [tree, focusId]);
  useEffect(() => { oldPath.current = pathTo(tree, focus).map((n) => n.id); }, [tree, focus]);
  useEffect(() => { setFocusId(null); oldPath.current = []; }, [username, color, map]);

  const pathIds = useMemo(() => new Set(pathTo(tree, focus).map((n) => n.id)), [tree, focus]);

  // First load: if the user has no games as the default colour, show the other colour instead of an empty map.
  const [autoSwitched, setAutoSwitched] = useState(false);
  useEffect(() => {
    if (autoSwitched || mapQ.status !== 404) return;
    setAutoSwitched(true);
    const other: Color = color === 'white' ? 'black' : 'white';
    setColor(other);
    setPiece((p) => mirrorPiece(p, other));
  }, [mapQ.status, autoSwitched, color]);

  const changeColor = (c: Color) => {
    if (c === color) return;
    setColor(c);
    setPiece((p) => mirrorPiece(p, c));
  };
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
  const crumbLabel = focus ? plainLabel(focus.label) : '';

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
          <span className="small muted">폭은 판수, 색은 내 승률. 칸을 누르면 아래 탐색기가 그 국면으로 이동합니다</span>
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
        ) : !map ? (
          <div className="op-state"><div className="op-spinner" /></div>
        ) : !focus ? (
          <div className="op-state">
            <div>표시할 가지가 없습니다.</div>
            <div className="small faint">최소 판수를 낮춰 보세요.</div>
          </div>
        ) : (
          <>
            <Strip tree={tree} focusId={focus.id} pathIds={pathIds} onFocus={(n) => setFocusId(n.id)} />
            <Explorer tree={tree} focus={focus} color={color} onFocus={(n) => setFocusId(n.id)} />
          </>
        )}

        <div className="op-legend">
          <span className="item"><span className="op-sw-scale" />색 = 내 승률 (빨강 낮음 · 회색 50% · 파랑 높음)</span>
          <span className="item"><span className="op-sw-tab" />타비야</span>
          <span className="item"><span className="op-sw-dev" />책 이탈</span>
          <span className="item">합류 = 다른 수순으로도 도달</span>
          <span className="item"><span className="op-sw-dash" />점선 = 마스터 DB에만 있는 수</span>
        </div>
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
