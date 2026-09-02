import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import type { Color, OpeningNode } from '../../api/types';
import { getUsername, setUsername } from '../../lib/user';
import { OpeningDag } from './OpeningDag';
import { Heatmap, defaultPiece, mirrorPiece, pieceOptions } from './Heatmap';
import { BreakTimeline } from './BreakTimeline';
import { useQuery } from './useQuery';
import { norm01, pct } from './layout';
import './openings.css';

const DEPTHS = [8, 12, 16] as const;
const THROUGH_MOVE = 15;
const COLOR_LABEL: Record<Color, string> = { white: '백', black: '흑' };

export default function OpeningsPage() {
  const [username, setUser] = useState<string | null>(() => getUsername());
  const [editingUser, setEditingUser] = useState(false);
  const [color, setColor] = useState<Color>('white');
  const [depth, setDepth] = useState<number>(12);
  const [minGames, setMinGames] = useState(1);
  const [piece, setPiece] = useState<string>(() => defaultPiece('white'));
  const [selected, setSelected] = useState<OpeningNode | null>(null);

  const mapQ = useQuery(() => (username ? api.openings.map(username, color, depth) : null), [username, color, depth]);
  const heatQ = useQuery(() => (username ? api.openings.heatmap(username, color, piece, THROUGH_MOVE) : null), [username, color, piece]);
  const breaksQ = useQuery(() => (username ? api.openings.breaks(username, color) : null), [username, color]);

  useEffect(() => { setSelected(null); }, [username, color, depth, minGames]);

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

  const map = mapQ.data;
  const total = map?.total_games ?? 0;
  const noGames = !!map && (total === 0 || (map.nodes ?? []).length === 0);
  const rootNode = useMemo(() => map?.nodes?.find((n) => n.id === map.root) ?? null, [map]);
  const rootName = rootNode?.name?.trim() || null;
  const subtitle = username
    ? [COLOR_LABEL[color], rootName, map ? `내 ${total}판 위에 마스터 DB를 겹침` : '내 기보 위에 마스터 DB를 겹침'].filter(Boolean).join(' · ')
    : '사용자명을 입력하면 내 기보 위에 마스터 DB를 겹쳐 보여줍니다';

  const [metaEl, setMetaEl] = useState<HTMLElement | null>(null);
  useEffect(() => { setMetaEl(document.getElementById('topbar-meta')); }, []);

  return (
    <div className="op-page">
      {metaEl && createPortal(
        <>
          <span style={{ color: 'var(--ink)', fontWeight: 600 }}>오프닝</span>
          <span>레퍼토리 · {COLOR_LABEL[color]}{rootNode?.label ? ` ${rootNode.label}` : ''}</span>
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
          {DEPTHS.map((d) => (
            <button key={d} type="button" className={`chip op-chip-btn${depth === d ? ' on' : ''}`} onClick={() => setDepth(d)}>깊이 {d}플라이</button>
          ))}
          <label className="chip op-chip-input">
            최소 판수
            <input
              type="number"
              min={0}
              max={999}
              value={minGames}
              onChange={(e) => setMinGames(Math.max(0, Math.min(999, Number(e.target.value) || 0)))}
            />
          </label>
        </div>
      </div>

      {(!username || editingUser) && (
        <UsernameCard initial={username ?? ''} onSave={saveUser} onCancel={username ? () => setEditingUser(false) : undefined} />
      )}

      <div className="op-grid">
        <div className="card op-card">
          <div className="op-card-head" style={{ alignItems: 'baseline' }}>
            <span className="h3">가지와 합류</span>
            <span className="small muted">같은 구조로 합쳐지는 수순은 하나의 타비야 노드로 병합</span>
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
          ) : mapQ.error ? (
            <ErrorState message={mapQ.error} status={mapQ.status} onRetry={mapQ.reload} />
          ) : map && noGames ? (
            <div className="op-state">
              <div className="op-state-title">{COLOR_LABEL[color]}으로 둔 기보가 아직 없습니다</div>
              <div>기보를 가져오면 {COLOR_LABEL[color]} 레퍼토리 지도를 그립니다.</div>
              <Link to="/games?import=1" className="btn btn-primary" style={{ height: 32 }}>기보 가져오기</Link>
            </div>
          ) : map ? (
            <>
              <OpeningDag map={map} color={color} minGames={minGames} selectedId={selected?.id ?? null} onSelect={setSelected} />
              <NodeDetail node={selected} />
            </>
          ) : (
            <div className="op-state"><div className="op-spinner" /></div>
          )}

          <div className="op-legend">
            <span className="item"><span className="op-sw-line" />굵기 = 내 게임 수</span>
            <span className="item"><span className="op-sw" style={{ background: 'var(--bad)' }} />내 승률 낮음</span>
            <span className="item"><span className="op-sw" style={{ background: '#9b9187' }} />50%</span>
            <span className="item"><span className="op-sw" style={{ background: 'var(--good)' }} />내 승률 높음</span>
            <span className="item"><span className="op-sw-dash" />마스터 DB에만 있는 가지</span>
            <span className="item"><span className="op-sw-tab" />타비야 (전이 병합)</span>
            <span className="item"><span className="op-sw-dev" />책 이탈</span>
          </div>
        </div>

        <div className="op-side">
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
            ) : breaksQ.error ? (
              <ErrorState compact message={breaksQ.error} status={breaksQ.status} onRetry={breaksQ.reload} />
            ) : (
              <BreakTimeline rows={breaksQ.data ?? []} color={color} />
            )}
          </div>
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

function NodeDetail({ node }: { node: OpeningNode | null }) {
  if (!node) return <div className="op-detail faint">노드를 클릭하면 세부 기록을 보여줍니다. 휠로 확대, 드래그로 이동.</div>;
  const s = norm01(node.score);
  const tone = s === null ? 'badge-neutral' : s >= 0.55 ? 'badge-good' : s <= 0.45 ? 'badge-bad' : 'badge-neutral';
  return (
    <div className="op-detail">
      <span className="mv">{node.label || node.san || '?'}</span>
      {node.name && <span>{node.name}</span>}
      {node.eco && <span className="mono faint">{node.eco}</span>}
      <span className="mono">{node.master_only ? '마스터 DB 전용' : `${node.games ?? 0}판`}</span>
      {!node.master_only && (
        <span className="mono muted">승 {node.wins ?? 0} · 무 {node.draws ?? 0} · 패 {node.losses ?? 0}</span>
      )}
      {!node.master_only && <span className={`badge ${tone}`} style={{ height: 20, fontSize: 11 }}>승률 {pct(s)}</span>}
      {node.is_tabiya && <span className="badge badge-neutral" style={{ height: 20, fontSize: 11 }}>타비야</span>}
      {node.is_deviation && <span className="badge badge-bad" style={{ height: 20, fontSize: 11 }}>책 이탈</span>}
    </div>
  );
}

const I = { width: 12, height: 12, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2.2, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const };
function IconPencil() { return <svg {...I}><path d="M4 20h4l10.5-10.5a2.1 2.1 0 0 0-3-3L5 17z" /><path d="M13.5 6.5l3 3" /></svg>; }
function IconWarn() { return <svg {...I} width={14} height={14}><path d="M12 3L2 21h20z" /><path d="M12 10v5M12 18h.01" /></svg>; }
