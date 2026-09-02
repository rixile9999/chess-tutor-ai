import { useEffect, useState, type FormEvent } from 'react';
import { createPortal } from 'react-dom';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ApiError, api } from '../../api/client';
import type { ProfileReport } from '../../api/types';
import { getUsername, setUsername } from '../../lib/user';
import { ReportGrid } from './report';
import { IconAlert, IconImport, IconRefresh, IconUser, fmtDate, platformLabel } from './bits';
import './profile.css';

const DAY_OPTIONS = [30, 60, 90] as const;
type Days = (typeof DAY_OPTIONS)[number];
const DEFAULT_DAYS: Days = 60;

type Load =
  | { status: 'idle' | 'loading'; report: ProfileReport | null }
  | { status: 'done'; report: ProfileReport }
  | { status: 'error'; report: null; message: string; httpStatus: number | null };

export default function ProfilePage() {
  const params = useParams();
  const routeUser = params.username?.trim();
  const username = routeUser || getUsername() || null;
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();

  const daysParam = Number(searchParams.get('days'));
  const days: Days = (DAY_OPTIONS as readonly number[]).includes(daysParam) ? (daysParam as Days) : DEFAULT_DAYS;
  const setDays = (d: Days) => {
    setSearchParams((prev) => { const next = new URLSearchParams(prev); next.set('days', String(d)); return next; }, { replace: true });
  };

  const [load, setLoad] = useState<Load>({ status: 'idle', report: null });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!username) return;
    let alive = true;
    setLoad((prev) => ({ status: 'loading', report: prev.report }));
    api.profile.report(username, { days })
      .then((report) => { if (alive) setLoad({ status: 'done', report }); })
      .catch((e: unknown) => {
        if (!alive) return;
        const httpStatus = e instanceof ApiError ? e.status : null;
        const message = e instanceof Error ? e.message : String(e);
        setLoad({ status: 'error', report: null, message, httpStatus });
      });
    return () => { alive = false; };
  }, [username, days, attempt]);

  // The shell leaves a slot in the top bar for page metadata; fill it once the DOM exists.
  const [metaEl, setMetaEl] = useState<HTMLElement | null>(null);
  useEffect(() => { setMetaEl(document.getElementById('topbar-meta')); }, []);

  if (!username) {
    return (
      <div className="pf-page">
        {metaEl && createPortal(<span className="pf-meta-strong">프로필</span>, metaEl)}
        <PageHead days={days} onDays={setDays} />
        <UsernameGate onSubmit={(name) => { setUsername(name); navigate(`/profile/${encodeURIComponent(name)}`); }} />
      </div>
    );
  }

  const report = load.report;
  const busy = load.status === 'loading';
  const games = report?.games ?? 0;

  return (
    <div className="pf-page">
      {metaEl && createPortal(
        <>
          <span className="pf-meta-strong">프로필</span>
          {report && (
            <span>
              {platformLabel(report.platform) ? `${platformLabel(report.platform)} 연동 · ` : ''}
              {games.toLocaleString('ko-KR')}판 가져옴 · 분석 {(report.analyzed_games ?? 0).toLocaleString('ko-KR')}판
            </span>
          )}
        </>,
        metaEl,
      )}
      <PageHead days={days} onDays={setDays} username={username} report={report} busy={busy} />

      {load.status === 'error' ? (
        <ErrorState username={username} message={load.message} httpStatus={load.httpStatus} onRetry={() => setAttempt((n) => n + 1)} />
      ) : !report ? (
        <Skeleton />
      ) : games === 0 && !busy ? (
        <EmptyState username={username} days={days} onWiden={days < 90 ? () => setDays(90) : null} />
      ) : (
        <ReportGrid report={report} busy={busy} />
      )}
      {busy && <div className="pf-status faint small" role="status">리포트를 불러오는 중입니다</div>}
    </div>
  );
}

function PageHead({ days, onDays, username, report, busy }: {
  days: Days; onDays: (d: Days) => void; username?: string; report?: ProfileReport | null; busy?: boolean;
}) {
  const parts: string[] = [];
  if (username) parts.push(username);
  parts.push(`최근 ${days}일`);
  if (report) {
    const from = fmtDate(report.window_from);
    const to = fmtDate(report.window_to);
    if (from && to) parts.push(`${from} ~ ${to.startsWith(from.slice(0, 5)) ? to.slice(5) : to}`);
    else if (from) parts.push(`${from} 이후`);
    const platform = platformLabel(report.platform);
    if (platform) parts.push(platform);
    parts.push(`${(report.games ?? 0).toLocaleString('ko-KR')}판`);
  }
  const rapid = report?.rating_rapid ?? null;
  const blitz = report?.rating_blitz ?? null;
  return (
    <div className="pf-head">
      <h2 className="pf-title">약점 리포트</h2>
      <div className="muted pf-window">{parts.join(' · ')}</div>
      <div className="pf-head-right">
        <div className="pf-seg" role="group" aria-label="기간">
          {DAY_OPTIONS.map((d) => (
            <button key={d} type="button" className={`pf-seg-btn${d === days ? ' active' : ''}`} aria-pressed={d === days} disabled={busy} onClick={() => onDays(d)}>
              {d}일
            </button>
          ))}
        </div>
        {rapid !== null && <span className="chip">래피드 <span className="mono">{rapid}</span></span>}
        {blitz !== null && <span className="chip">블리츠 <span className="mono">{blitz}</span></span>}
      </div>
    </div>
  );
}

function UsernameGate({ onSubmit }: { onSubmit: (name: string) => void }) {
  const [value, setValue] = useState('');
  const trimmed = value.trim();
  const submit = (e: FormEvent) => { e.preventDefault(); if (trimmed) onSubmit(trimmed); };
  return (
    <form className="card pf-state" onSubmit={submit}>
      <div className="pf-state-icon"><IconUser /></div>
      <h3 className="h3">누구의 리포트를 볼까요?</h3>
      <p className="muted">chess.com 또는 Lichess 아이디를 입력하면 가져온 기보를 바탕으로 약점 리포트를 만듭니다. 아이디는 이 브라우저에 기억됩니다.</p>
      <div className="pf-gate-row">
        <input className="pf-input" value={value} onChange={(e) => setValue(e.target.value)} placeholder="chess.com 아이디" aria-label="사용자 아이디" autoFocus autoComplete="username" spellCheck={false} />
        <button type="submit" className="btn btn-primary" disabled={!trimmed}>리포트 보기</button>
      </div>
      <p className="small faint">아직 기보가 없다면 <Link to="/games?import=1">기보 가져오기</Link>부터 시작하세요.</p>
    </form>
  );
}

function ErrorState({ username, message, httpStatus, onRetry }: { username: string; message: string; httpStatus: number | null; onRetry: () => void }) {
  const notFound = httpStatus === 404;
  return (
    <div className="card pf-state" role="alert">
      <div className="pf-state-icon"><IconAlert /></div>
      <h3 className="h3">{notFound ? '사용자 기록을 찾을 수 없습니다' : '리포트를 불러오지 못했습니다'}</h3>
      <p className="muted">
        {notFound
          ? `${username} 이름으로 가져온 기보가 없습니다. 아이디 철자를 확인하거나 기보를 먼저 가져오세요.`
          : httpStatus === null
            ? '서버에 연결할 수 없습니다. 백엔드가 실행 중인지 확인한 뒤 다시 시도하세요.'
            : '서버가 리포트를 만들지 못했습니다. 잠시 후 다시 시도하세요.'}
      </p>
      {message && <div className="mono small faint pf-error-detail">{httpStatus !== null ? `HTTP ${httpStatus} · ` : ''}{message}</div>}
      <div className="pf-state-actions">
        <button type="button" className="btn btn-primary" onClick={onRetry}><IconRefresh />다시 시도</button>
        <Link to="/games?import=1" className="btn btn-ghost"><IconImport />기보 가져오기</Link>
        <Link to="/profile" className="btn btn-ghost" onClick={() => setUsername('')}>다른 아이디</Link>
      </div>
    </div>
  );
}

function EmptyState({ username, days, onWiden }: { username: string; days: Days; onWiden: (() => void) | null }) {
  return (
    <div className="card pf-state">
      <div className="pf-state-icon"><IconImport /></div>
      <h3 className="h3">아직 분석할 기보가 없습니다</h3>
      <p className="muted">
        {username}의 최근 {days}일 기보가 없습니다. chess.com이나 Lichess에서 기보를 가져오면 단계별 정확도, 구조별 성적, 놓친 전술, 시간 관리까지 한 화면에 정리해 드립니다.
      </p>
      <div className="pf-state-actions">
        <Link to="/games?import=1" className="btn btn-primary"><IconImport />기보 가져오기</Link>
        {onWiden && <button type="button" className="btn btn-ghost" onClick={onWiden}>90일로 넓혀 보기</button>}
      </div>
    </div>
  );
}

function Skeleton() {
  return (
    <div className="pf-grid pf-skeleton" aria-hidden="true">
      <div className="pf-row pf-row-1"><div className="card" /><div className="card" /></div>
      <div className="pf-row pf-row-2"><div className="card" /><div className="card" /><div className="card" /></div>
      <div className="pf-row pf-row-3"><div className="card" /><div className="card" /></div>
    </div>
  );
}
