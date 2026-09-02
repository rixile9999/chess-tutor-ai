import { Link } from 'react-router-dom';
import type { GameSummary } from '../../api/types';
import {
  OUTCOME_LABEL, OUTCOME_TONE, STATUS_LABEL, formatDate, formatTimeControl, isInProgress, moveCount,
  opponentOf, outcomeOf, statusOf, userColor,
} from './format';
import { IconArrow, IconPlay, IconSpinner } from './icons';

type Props = {
  games: GameSummary[];
  username: string | null;
  rowErrors: Record<number, string>;
  starting: number[];
  onAnalyze: (id: number) => void;
};

export function GamesTable({ games, username, rowErrors, starting, onAnalyze }: Props) {
  return (
    <div className="games-table-wrap">
      <table className="games-table">
        <thead>
          <tr>
            <th>날짜</th>
            <th>색</th>
            <th>상대</th>
            <th>결과</th>
            <th>시간</th>
            <th>오프닝</th>
            <th>분석</th>
            <th aria-label="동작" />
          </tr>
        </thead>
        <tbody>
          {games.map((g) => (
            <GameRow
              key={g.id}
              game={g}
              username={username}
              error={rowErrors[g.id]}
              starting={starting.includes(g.id)}
              onAnalyze={onAnalyze}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

type RowProps = {
  game: GameSummary;
  username: string | null;
  error: string | undefined;
  starting: boolean;
  onAnalyze: (id: number) => void;
};

function GameRow({ game: g, username, error, starting, onAnalyze }: RowProps) {
  const color = userColor(g, username);
  const outcome = outcomeOf(g, color);
  const opp = opponentOf(g, color);
  const tc = formatTimeControl(g.time_control);
  const date = formatDate(g.played_at);
  const moves = moveCount(g.ply_count);
  const status = statusOf(g);
  const inProgress = starting || isInProgress(status);

  return (
    <tr>
      <td>
        {date ? <span className="mono">{date}</span> : <span className="faint">날짜 없음</span>}
      </td>
      <td>
        {color ? (
          <span className="games-color"><span className={`games-swatch ${color}`} />{color === 'white' ? '백' : '흑'}</span>
        ) : (
          <span className="faint">?</span>
        )}
      </td>
      <td>
        {opp ? (
          <span className="games-opp">
            <span className="games-opp-name">{opp.name}</span>
            {opp.elo != null && <span className="mono muted small">{opp.elo}</span>}
          </span>
        ) : (
          <span className="games-opp">
            <span className="games-opp-name">{g.white || '?'}</span>
            {g.white_elo != null && <span className="mono muted small">{g.white_elo}</span>}
            <span className="faint small">vs</span>
            <span className="games-opp-name">{g.black || '?'}</span>
            {g.black_elo != null && <span className="mono muted small">{g.black_elo}</span>}
          </span>
        )}
      </td>
      <td>
        <span className="games-result">
          {outcome ? (
            <span className={`badge badge-${OUTCOME_TONE[outcome]} games-badge-sm`}>{OUTCOME_LABEL[outcome]}</span>
          ) : (
            <span className="mono muted">{g.result || '*'}</span>
          )}
          {moves != null && <span className="mono faint small">{moves}수</span>}
        </span>
      </td>
      <td>
        {tc ? (
          <span className="chip games-tc">{tc.category}{tc.text && <span className="mono">{tc.text}</span>}</span>
        ) : (
          <span className="faint">-</span>
        )}
      </td>
      <td>
        <span className="games-opening" title={g.opening_name ?? undefined}>
          {g.eco && <span className="mono faint small games-eco">{g.eco}</span>}
          {g.opening_name ? g.opening_name : <span className="faint">-</span>}
        </span>
      </td>
      <td>
        <StatusBadge status={status} starting={starting} />
        {error && <div className="games-rowerr" title={error}>{error}</div>}
      </td>
      <td>
        <span className="games-actions">
          {status !== 'done' && (
            <button
              type="button"
              className="btn btn-ghost games-btn-xs"
              disabled={inProgress}
              onClick={() => onAnalyze(g.id)}
              title={inProgress ? '엔진이 분석하고 있습니다' : '엔진 분석을 시작합니다'}
            >
              {inProgress ? <IconSpinner size={12} /> : <IconPlay size={11} />}
              {inProgress ? '분석 중' : status === 'failed' ? '다시 분석' : '분석'}
            </button>
          )}
          <Link
            to={`/review/${g.id}`}
            className={`btn games-btn-xs ${status === 'done' ? 'btn-primary' : 'btn-ghost'}`}
            title={status === 'done' ? '리뷰 열기' : '분석 전에도 기보를 볼 수 있습니다'}
          >
            리뷰 <IconArrow size={13} />
          </Link>
        </span>
      </td>
    </tr>
  );
}

function StatusBadge({ status, starting }: { status: ReturnType<typeof statusOf>; starting: boolean }) {
  if (starting || status === 'pending' || status === 'running') {
    return (
      <span className="badge badge-neutral games-badge-sm">
        <IconSpinner size={11} />
        {starting && status === 'none' ? STATUS_LABEL.pending : STATUS_LABEL[status === 'none' ? 'pending' : status]}
      </span>
    );
  }
  if (status === 'done') return <span className="badge badge-good games-badge-sm">{STATUS_LABEL.done}</span>;
  if (status === 'failed') return <span className="badge badge-bad games-badge-sm">{STATUS_LABEL.failed}</span>;
  return <span className="badge badge-neutral games-badge-sm games-badge-none">{STATUS_LABEL.none}</span>;
}
