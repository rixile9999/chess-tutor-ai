import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../../api/client';
import type { AnalysisStatus, GameSummary, ImportResult } from '../../api/types';
import { getUsername, setUsername } from '../../lib/user';
import { ImportPanel } from './ImportPanel';
import { GamesTable } from './GamesTable';
import { dedupeGames, describeError, isInProgress, sortGames, statusOf } from './format';
import { IconAlert, IconClose, IconRefresh, IconSpinner, IconUpload, IconUser } from './icons';
import './games.css';

const PAGE = 50;
const POLL_MS = 2000;
/** Consecutive polling failures before a game is shown as failed (the job may not exist yet right after start). */
const POLL_MAX_ERRORS = 5;

export default function GamesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [username, setUser] = useState<string | null>(() => getUsername());
  const [editingUser, setEditingUser] = useState(false);
  const [importOpen, setImportOpen] = useState(() => searchParams.get('import') === '1');

  const [games, setGames] = useState<GameSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [rowErrors, setRowErrors] = useState<Record<number, string>>({});
  const [starting, setStarting] = useState<number[]>([]);
  const errCount = useRef(new Map<number, number>());
  const reqSeq = useRef(0);

  // The topbar's "기보 가져오기" link navigates to /games?import=1 even when this page is already mounted.
  useEffect(() => {
    if (searchParams.get('import') === '1') setImportOpen(true);
  }, [searchParams]);

  const load = useCallback(async (offset: number) => {
    const seq = ++reqSeq.current;
    if (offset === 0) { setLoading(true); setListError(null); } else { setLoadingMore(true); }
    try {
      const rows = await api.games.list({ user: username ?? undefined, limit: PAGE, offset });
      if (seq !== reqSeq.current) return;
      const list = Array.isArray(rows) ? rows : [];
      setGames((prev) => sortGames(offset === 0 ? list : dedupeGames([...prev, ...list])));
      setHasMore(list.length >= PAGE);
    } catch (e) {
      if (seq !== reqSeq.current) return;
      if (offset === 0) setGames([]);
      setListError(describeError(e));
    } finally {
      if (seq === reqSeq.current) { setLoading(false); setLoadingMore(false); }
    }
  }, [username]);

  useEffect(() => { void load(0); }, [load]);

  const patchStatus = useCallback((id: number, status: AnalysisStatus) => {
    setGames((prev) => {
      const i = prev.findIndex((g) => g.id === id);
      if (i < 0 || prev[i].analysis_status === status) return prev;
      const next = prev.slice();
      next[i] = { ...prev[i], analysis_status: status };
      return next;
    });
  }, []);

  const setRowError = useCallback((id: number, message: string | null) => {
    setRowErrors((prev) => {
      if (message === null) {
        if (!(id in prev)) return prev;
        const next = { ...prev };
        delete next[id];
        return next;
      }
      return prev[id] === message ? prev : { ...prev, [id]: message };
    });
  }, []);

  // Poll every game that is pending/running until it is done or failed.
  const pollKey = useMemo(
    () => games.filter((g) => isInProgress(statusOf(g))).map((g) => g.id).sort((a, b) => a - b).join(','),
    [games],
  );
  useEffect(() => {
    if (!pollKey) return;
    const ids = pollKey.split(',').map(Number);
    const inflight = new Set<number>();
    let cancelled = false;
    const tick = () => {
      for (const id of ids) {
        if (inflight.has(id)) continue;
        inflight.add(id);
        api.analysis.get(id)
          .then((a) => {
            if (cancelled) return;
            errCount.current.delete(id);
            const st: AnalysisStatus = a?.status ?? 'running';
            if (st === 'done') { patchStatus(id, 'done'); setRowError(id, null); }
            else if (st === 'failed') { patchStatus(id, 'failed'); setRowError(id, a?.error || '분석에 실패했습니다'); }
            else if (st === 'pending' || st === 'running') patchStatus(id, st);
            // 'none' after a start request: the job has not been registered yet, keep polling.
          })
          .catch((e) => {
            if (cancelled) return;
            const n = (errCount.current.get(id) ?? 0) + 1;
            errCount.current.set(id, n);
            if (n >= POLL_MAX_ERRORS) {
              errCount.current.delete(id);
              patchStatus(id, 'failed');
              setRowError(id, `상태를 확인하지 못했습니다 · ${describeError(e)}`);
            }
          })
          .finally(() => { inflight.delete(id); });
      }
    };
    const timer = setInterval(tick, POLL_MS);
    return () => { cancelled = true; clearInterval(timer); };
  }, [pollKey, patchStatus, setRowError]);

  async function analyze(id: number) {
    if (starting.includes(id)) return;
    setStarting((prev) => [...prev, id]);
    setRowError(id, null);
    try {
      const a = await api.analysis.start(id);
      const st: AnalysisStatus = a?.status && a.status !== 'none' ? a.status : 'pending';
      patchStatus(id, st);
      if (st === 'failed') setRowError(id, a?.error || '분석에 실패했습니다');
    } catch (e) {
      patchStatus(id, 'failed');
      setRowError(id, describeError(e));
    } finally {
      setStarting((prev) => prev.filter((x) => x !== id));
    }
  }

  function commitUsername(name: string) {
    const n = name.trim();
    if (!n) return;
    setUsername(n);
    setEditingUser(false);
    if (n !== username) setUser(n); // a changed username reloads the list through `load`
  }

  function handleImported(_result: ImportResult, usedUsername: string | null) {
    const n = usedUsername?.trim() ?? '';
    if (n && n !== username) commitUsername(n);
    else void load(0);
  }

  function closeImport() {
    setImportOpen(false);
    if (searchParams.has('import')) {
      const next = new URLSearchParams(searchParams);
      next.delete('import');
      setSearchParams(next, { replace: true });
    }
  }

  const countText = loading && games.length === 0 ? '불러오는 중' : `${games.length}${hasMore ? '+' : ''}판`;

  return (
    <div className="games-page">
      <header className="games-head">
        <h1 className="games-title">기보</h1>
        <span className="games-sub muted">{username ? `${username}의 기보` : '모든 기보'} · {countText}</span>
        <div className="spacer" />
        <UserChip
          key={`${username ?? ''}:${editingUser ? 'edit' : 'view'}`}
          username={username}
          editing={editingUser}
          onEdit={() => setEditingUser(true)}
          onCancel={() => setEditingUser(false)}
          onSave={commitUsername}
        />
        {importOpen ? (
          <button type="button" className="btn btn-ghost games-btn-sm" onClick={closeImport}><IconClose /> 가져오기 닫기</button>
        ) : (
          <button type="button" className="btn btn-primary games-btn-sm" onClick={() => setImportOpen(true)}><IconUpload /> 기보 가져오기</button>
        )}
      </header>

      {importOpen && <ImportPanel username={username} onImported={handleImported} onClose={closeImport} />}

      <section className="card games-list" aria-label="기보 목록">
        <div className="games-list-head">
          <span className="h3">기보 목록</span>
          <span className="small muted">최근 순 · 분석이 끝난 기보는 리뷰에서 수마다 설명을 읽을 수 있습니다</span>
          <div className="spacer" />
          <button type="button" className="btn btn-ghost games-btn-sm" onClick={() => void load(0)} disabled={loading}>
            {loading ? <IconSpinner /> : <IconRefresh />} 새로고침
          </button>
        </div>

        {loading && games.length === 0 ? (
          <Skeleton />
        ) : listError ? (
          <div className="games-error" role="alert">
            <IconAlert className="games-bad" />
            <div className="games-error-text">
              <b>기보 목록을 불러오지 못했습니다</b>
              <span className="muted">{listError}</span>
            </div>
            <div className="spacer" />
            <button type="button" className="btn btn-ghost games-btn-sm" onClick={() => void load(0)}>다시 시도</button>
          </div>
        ) : games.length === 0 ? (
          <EmptyState username={username} onImport={() => setImportOpen(true)} onSetUser={() => setEditingUser(true)} />
        ) : (
          <>
            <GamesTable games={games} username={username} rowErrors={rowErrors} starting={starting} onAnalyze={(id) => void analyze(id)} />
            {hasMore && (
              <div className="games-more">
                <button type="button" className="btn btn-ghost games-btn-sm" disabled={loadingMore} onClick={() => void load(games.length)}>
                  {loadingMore ? <IconSpinner /> : null} 더 보기
                </button>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}

type UserChipProps = {
  username: string | null;
  editing: boolean;
  onEdit: () => void;
  onCancel: () => void;
  onSave: (name: string) => void;
};

function UserChip({ username, editing, onEdit, onCancel, onSave }: UserChipProps) {
  const [draft, setDraft] = useState(username ?? '');
  if (username && !editing) {
    return (
      <button type="button" className="chip games-user" onClick={onEdit} title="사용자 이름 변경">
        <IconUser />
        {username}
      </button>
    );
  }
  const submit = (e: FormEvent) => { e.preventDefault(); onSave(draft); };
  return (
    <form className="games-userform" onSubmit={submit}>
      <input
        className="games-input"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder="chess.com 사용자 이름"
        aria-label="사용자 이름"
        autoComplete="off"
        autoFocus={editing}
      />
      <button type="submit" className="btn btn-primary games-btn-sm" disabled={!draft.trim()}>저장</button>
      {username && <button type="button" className="btn btn-ghost games-btn-sm" onClick={onCancel}>취소</button>}
    </form>
  );
}

function EmptyState({ username, onImport, onSetUser }: { username: string | null; onImport: () => void; onSetUser: () => void }) {
  return (
    <div className="games-empty">
      <div className="games-empty-board" aria-hidden>
        {Array.from({ length: 16 }, (_, i) => <span key={i} className={(Math.floor(i / 4) + i) % 2 === 0 ? 'light' : 'dark'} />)}
      </div>
      <div className="games-empty-title">{username ? `${username}의 기보가 아직 없습니다` : '아직 가져온 기보가 없습니다'}</div>
      <p>
        {username
          ? 'chess.com이나 lichess에서 최근 기보를 가져오거나, PGN을 직접 붙여넣으세요. 가져온 뒤 분석을 시작하면 수마다 설명이 붙은 리뷰를 읽을 수 있습니다.'
          : '사용자 이름을 저장하면 그 이름으로 둔 기보만 모아 보여줍니다. 먼저 PGN을 붙여넣거나 chess.com 계정에서 가져와 보세요.'}
      </p>
      <div className="games-empty-actions">
        <button type="button" className="btn btn-primary" onClick={onImport}><IconUpload /> 기보 가져오기</button>
        {!username && <button type="button" className="btn btn-ghost" onClick={onSetUser}><IconUser /> 사용자 이름 저장</button>}
      </div>
    </div>
  );
}

function Skeleton() {
  return (
    <div className="games-skel" aria-hidden>
      {Array.from({ length: 5 }, (_, i) => (
        <div key={i} className="games-skel-row">
          <span /><span /><span /><span /><span /><span /><span /><span />
        </div>
      ))}
    </div>
  );
}
