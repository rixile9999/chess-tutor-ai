import { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../../api/client';
import type { PuzzleOut } from '../../api/types';
import { getUsername, setUsername } from '../../lib/user';
import { PuzzleSolver } from './PuzzleSolver';
import { Sparring } from './Sparring';
import { IconArrow, IconPlay } from './icons';
import { errorText } from './util';
import './training.css';

type Tab = 'puzzles' | 'sparring';
type Due = { status: 'idle' | 'loading' | 'ready' | 'error'; puzzles: PuzzleOut[]; error: string | null };

export default function TrainingPage() {
  const [params, setParams] = useSearchParams();
  const fenParam = params.get('fen');
  const tabParam = params.get('tab');
  const tab: Tab = tabParam === 'sparring' || (fenParam !== null && tabParam !== 'puzzles') ? 'sparring' : 'puzzles';

  const [username, setUser] = useState<string | null>(() => getUsername());
  const [due, setDue] = useState<Due>({ status: 'idle', puzzles: [], error: null });
  const [reload, setReload] = useState(0);
  const [metaEl, setMetaEl] = useState<HTMLElement | null>(null);

  useEffect(() => { setMetaEl(document.getElementById('topbar-meta')); }, []);

  useEffect(() => {
    if (!username) { setDue({ status: 'idle', puzzles: [], error: null }); return; }
    let cancelled = false;
    setDue((d) => ({ ...d, status: 'loading', error: null }));
    api.training.due(username)
      .then((list) => {
        if (cancelled) return;
        const puzzles = (Array.isArray(list) ? list : []).filter((p) => p && typeof p.id === 'number' && typeof p.fen === 'string');
        setDue({ status: 'ready', puzzles, error: null });
      })
      .catch((e) => { if (!cancelled) setDue({ status: 'error', puzzles: [], error: errorText(e) }); });
    return () => { cancelled = true; };
  }, [username, reload]);

  const setTab = (t: Tab) => {
    setParams((p) => { const n = new URLSearchParams(p); n.set('tab', t); return n; });
  };
  const onFinished = useCallback((id: number) => {
    setDue((d) => ({ ...d, puzzles: d.puzzles.filter((p) => p.id !== id) }));
  }, []);
  const submitUser = (name: string) => { setUsername(name); setUser(name); };

  const dueCount = due.status === 'ready' ? due.puzzles.length : null;

  return (
    <div className="tr-page">
      {metaEl && createPortal(
        <>
          <span style={{ color: 'var(--ink)', fontWeight: 600 }}>훈련</span>
          <span>{tab === 'puzzles' ? '내 기보 퍼즐 · 간격 반복' : 'Maia와 이어 두기'}</span>
          {dueCount !== null && <span className="chip" style={{ height: 22 }}>복습 예정 {dueCount}</span>}
        </>,
        metaEl,
      )}

      <div className="tr-head">
        <div className="tr-title">훈련</div>
        <div className="tr-sub">내 기보에서 만든 문제를 간격 반복으로 복습하고, 리뷰한 국면을 Maia와 이어 둡니다.</div>
        <div className="spacer" />
        {username && <span className="chip">{username}</span>}
      </div>

      <div className="tr-tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === 'puzzles'} className={`tr-tab${tab === 'puzzles' ? ' active' : ''}`} onClick={() => setTab('puzzles')}>
          퍼즐
          {dueCount !== null && <span className={`badge ${dueCount > 0 ? 'badge-bad' : 'badge-neutral'}`}>{dueCount}</span>}
        </button>
        <button type="button" role="tab" aria-selected={tab === 'sparring'} className={`tr-tab${tab === 'sparring' ? ' active' : ''}`} onClick={() => setTab('sparring')}>
          이어 두기
        </button>
      </div>

      {tab === 'sparring' ? (
        <Sparring key={fenParam ?? 'start'} />
      ) : !username ? (
        <UsernameGate onSubmit={submitUser} />
      ) : due.status === 'loading' || due.status === 'idle' ? (
        <LoadingCard />
      ) : due.status === 'error' ? (
        <div className="card tr-empty">
          <div className="h3">문제를 불러오지 못했습니다</div>
          <p className="muted">{due.error}</p>
          <div className="tr-actions">
            <button type="button" className="btn btn-primary" onClick={() => setReload((n) => n + 1)}>다시 시도</button>
            <button type="button" className="btn btn-ghost" onClick={() => setTab('sparring')}><IconPlay /> Maia와 이어 두기</button>
          </div>
        </div>
      ) : due.puzzles.length === 0 ? (
        <div className="card tr-empty">
          <div className="h3">오늘 복습할 문제가 없습니다</div>
          <p className="muted">리뷰 화면에서 실수한 수를 퍼즐로 저장하면 여기에 쌓입니다. 풀 때마다 간격이 늘어나고, 틀리면 다시 가까운 날짜에 돌아옵니다.</p>
          <div className="tr-actions">
            <Link to="/games" className="btn btn-primary">기보 보러 가기 <IconArrow /></Link>
            <button type="button" className="btn btn-ghost" onClick={() => setTab('sparring')}><IconPlay /> Maia와 이어 두기</button>
          </div>
        </div>
      ) : (
        <PuzzleSolver queue={due.puzzles} onFinished={onFinished} />
      )}
    </div>
  );
}

function UsernameGate({ onSubmit }: { onSubmit: (name: string) => void }) {
  const [value, setValue] = useState('');
  return (
    <form
      className="card tr-empty"
      onSubmit={(e) => { e.preventDefault(); const v = value.trim(); if (v) onSubmit(v); }}
    >
      <div className="h3">누구의 기보로 훈련할까요</div>
      <p className="muted">chess.com 또는 lichess 사용자명을 입력하면 그 기보에서 만든 문제를 불러옵니다. 아직 기보가 없다면 먼저 가져와 주세요.</p>
      <div className="tr-actions">
        <input className="tr-input" placeholder="사용자명" value={value} onChange={(e) => setValue(e.target.value)} autoFocus />
        <button type="submit" className="btn btn-primary" disabled={!value.trim()}>불러오기</button>
        <Link to="/games?import=1" className="btn btn-ghost">기보 가져오기</Link>
      </div>
    </form>
  );
}

function LoadingCard() {
  return (
    <div className="card tr-panel" aria-busy="true">
      <span className="eyebrow">내 기보 퍼즐</span>
      <div className="tr-skeleton" style={{ width: '40%' }} />
      <div className="tr-skeleton" style={{ width: '70%' }} />
      <div className="tr-skeleton" style={{ width: '55%' }} />
      <span className="small muted">복습할 문제를 불러오는 중입니다</span>
    </div>
  );
}
