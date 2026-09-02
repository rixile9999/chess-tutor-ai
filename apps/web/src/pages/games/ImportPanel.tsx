import { useState, type FormEvent } from 'react';
import { api } from '../../api/client';
import type { ImportResult } from '../../api/types';
import { countPgnGames, describeError } from './format';
import { IconAlert, IconCheck, IconClose, IconSpinner, IconUpload } from './icons';

export type ImportTab = 'pgn' | 'chesscom' | 'lichess';

type PanelState =
  | { kind: 'idle' }
  | { kind: 'busy' }
  | { kind: 'ok'; result: ImportResult; source: ImportTab }
  | { kind: 'error'; message: string };

type Props = {
  username: string | null;
  /** Called after a successful import with the username that was used (null for an anonymous PGN paste). */
  onImported: (result: ImportResult, usedUsername: string | null) => void;
  onClose: () => void;
};

const TABS: { key: ImportTab; label: string }[] = [
  { key: 'pgn', label: 'PGN 붙여넣기' },
  { key: 'chesscom', label: 'chess.com' },
  { key: 'lichess', label: 'lichess' },
];

const SOURCE_LABEL: Record<ImportTab, string> = { pgn: 'PGN', chesscom: 'chess.com', lichess: 'lichess' };

/**
 * One Korean sentence describing an import. `errors` holds the games the parser rejected and they
 * are also counted in `skipped`, so "이미 저장됨" is skipped minus errors: a rejected PGN must never
 * be reported as an already saved game.
 */
export function importSummary(result: ImportResult, source: ImportTab): string {
  const failed = result.errors.length;
  const already = Math.max(0, result.skipped - failed);
  if (result.imported > 0) {
    const tail = already > 0 ? ` ${already}판은 이미 있어 건너뛰었습니다.` : '';
    const bad = failed > 0 ? ` ${failed}판은 읽지 못했습니다.` : '';
    return `${SOURCE_LABEL[source]}에서 ${result.imported}판을 새로 저장했습니다.${tail}${bad}`;
  }
  if (failed > 0 && already > 0) return `새로 저장한 기보가 없습니다. ${already}판은 이미 있고, ${failed}판은 읽지 못했습니다.`;
  if (failed > 0) return `새로 저장한 기보가 없습니다. ${failed}판을 읽지 못했습니다.`;
  if (already > 0) return '새 기보가 없습니다. 모두 이미 저장된 기보입니다.';
  return '가져올 기보를 찾지 못했습니다.';
}

function clampInt(value: string | number, min: number, max: number, fallback: number): number {
  const n = Math.trunc(Number(value));
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

export function ImportPanel({ username, onImported, onClose }: Props) {
  const [tab, setTab] = useState<ImportTab>('pgn');
  const [state, setState] = useState<PanelState>({ kind: 'idle' });

  const [pgn, setPgn] = useState('');
  const [pgnUser, setPgnUser] = useState(username ?? '');
  const [ccUser, setCcUser] = useState(username ?? '');
  const [months, setMonths] = useState('3');
  const [liUser, setLiUser] = useState(username ?? '');
  const [maxGames, setMaxGames] = useState('100');

  const busy = state.kind === 'busy';
  const pgnCount = countPgnGames(pgn);

  async function run(source: ImportTab, usedUsername: string | null, fn: () => Promise<ImportResult>) {
    setState({ kind: 'busy' });
    try {
      const raw = await fn();
      const result: ImportResult = {
        imported: raw?.imported ?? 0,
        skipped: raw?.skipped ?? 0,
        game_ids: Array.isArray(raw?.game_ids) ? raw.game_ids : [],
        user_id: raw?.user_id ?? null,
        errors: Array.isArray(raw?.errors) ? raw.errors.filter((m): m is string => typeof m === 'string') : [],
      };
      setState({ kind: 'ok', result, source });
      onImported(result, usedUsername);
    } catch (e) {
      setState({ kind: 'error', message: describeError(e) });
    }
  }

  function submitPgn(e: FormEvent) {
    e.preventDefault();
    const text = pgn.trim();
    if (!text || busy) return;
    const user = pgnUser.trim() || null;
    void run('pgn', user, () => api.games.importPgn(text, user ?? undefined));
  }

  function submitChesscom(e: FormEvent) {
    e.preventDefault();
    const user = ccUser.trim();
    if (!user || busy) return;
    const m = clampInt(months, 1, 12, 3);
    setMonths(String(m));
    void run('chesscom', user, () => api.games.importChesscom(user, m));
  }

  function submitLichess(e: FormEvent) {
    e.preventDefault();
    const user = liUser.trim();
    if (!user || busy) return;
    const n = clampInt(maxGames, 1, 1000, 100);
    setMaxGames(String(n));
    void run('lichess', user, () => api.games.importLichess(user, n));
  }

  function selectTab(next: ImportTab) {
    setTab(next);
    if (state.kind === 'error') setState({ kind: 'idle' });
  }

  const submitLabel = busy ? (
    <><IconSpinner /> 가져오는 중</>
  ) : (
    <><IconUpload size={14} /> 가져오기</>
  );

  return (
    <section className="card games-import" aria-label="기보 가져오기">
      <div className="games-import-head">
        <div className="games-tabs" role="tablist" aria-label="가져오기 방식">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={tab === t.key}
              className="games-tab"
              onClick={() => selectTab(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <button type="button" className="games-iconbtn" onClick={onClose} aria-label="가져오기 패널 닫기" title="닫기">
          <IconClose />
        </button>
      </div>

      {tab === 'pgn' && (
        <form className="games-form" onSubmit={submitPgn} role="tabpanel">
          <label className="games-field">
            <span className="eyebrow">PGN</span>
            <textarea
              className="games-textarea"
              value={pgn}
              onChange={(e) => setPgn(e.target.value)}
              placeholder={'[Event "Live Chess"]\n[White "..."]\n[Black "..."]\n\n1. e4 c5 2. Nf3 d6 ...'}
              spellCheck={false}
              disabled={busy}
            />
          </label>
          <div className="games-row">
            <label className="games-field games-field-user">
              <span className="eyebrow">내 사용자 이름 (선택)</span>
              <input
                className="games-input"
                value={pgnUser}
                onChange={(e) => setPgnUser(e.target.value)}
                placeholder="기보 속 White 또는 Black 이름"
                disabled={busy}
              />
            </label>
            <span className="games-hint">
              {pgnCount > 0 ? <><b className="mono">{pgnCount}</b>판 감지 · </> : null}
              여러 판을 한 번에 붙여넣을 수 있습니다. 사용자 이름을 적으면 그 이름이 둔 쪽을 내 색으로 기록합니다.
            </span>
            <div className="spacer" />
            <button type="submit" className="btn btn-primary" disabled={busy || pgnCount === 0}>{submitLabel}</button>
          </div>
        </form>
      )}

      {tab === 'chesscom' && (
        <form className="games-form" onSubmit={submitChesscom} role="tabpanel" noValidate>
          <div className="games-row">
            <label className="games-field games-field-user">
              <span className="eyebrow">chess.com 사용자 이름</span>
              <input
                className="games-input"
                value={ccUser}
                onChange={(e) => setCcUser(e.target.value)}
                placeholder="예: hikaru"
                autoComplete="off"
                disabled={busy}
              />
            </label>
            <label className="games-field">
              <span className="eyebrow">기간</span>
              <span className="games-inputgroup">
                <input
                  className="games-input games-input-num"
                  type="number"
                  min={1}
                  max={12}
                  step={1}
                  inputMode="numeric"
                  value={months}
                  onChange={(e) => setMonths(e.target.value)}
                  disabled={busy}
                />
                <span className="games-suffix">개월</span>
              </span>
            </label>
            <div className="spacer" />
            <button type="submit" className="btn btn-primary" disabled={busy || !ccUser.trim()}>{submitLabel}</button>
          </div>
          <div className="games-hint">최근 1~12개월의 월별 아카이브를 가져옵니다. 이미 저장된 기보는 건너뜁니다. 가져오면 이 이름이 현재 사용자로 저장됩니다.</div>
        </form>
      )}

      {tab === 'lichess' && (
        <form className="games-form" onSubmit={submitLichess} role="tabpanel" noValidate>
          <div className="games-row">
            <label className="games-field games-field-user">
              <span className="eyebrow">lichess 사용자 이름</span>
              <input
                className="games-input"
                value={liUser}
                onChange={(e) => setLiUser(e.target.value)}
                placeholder="예: DrNykterstein"
                autoComplete="off"
                disabled={busy}
              />
            </label>
            <label className="games-field">
              <span className="eyebrow">최대 판수</span>
              <span className="games-inputgroup">
                <input
                  className="games-input games-input-num"
                  type="number"
                  min={1}
                  max={1000}
                  step={1}
                  inputMode="numeric"
                  value={maxGames}
                  onChange={(e) => setMaxGames(e.target.value)}
                  disabled={busy}
                />
                <span className="games-suffix">판</span>
              </span>
            </label>
            <div className="spacer" />
            <button type="submit" className="btn btn-primary" disabled={busy || !liUser.trim()}>{submitLabel}</button>
          </div>
          <div className="games-hint">최근 기보부터 최대 1,000판까지 가져옵니다. 이미 저장된 기보는 건너뜁니다.</div>
        </form>
      )}

      {state.kind === 'ok' && (
        <div className="games-import-result" role="status">
          {state.result.errors.length > 0 ? <IconAlert className="games-bad" /> : <IconCheck className="games-ok" />}
          <span className="badge badge-good">가져옴 {state.result.imported}</span>
          <span className="badge badge-neutral">건너뜀 {state.result.skipped}</span>
          {state.result.errors.length > 0 && (
            <span className="badge badge-bad">읽지 못함 {state.result.errors.length}</span>
          )}
          <span className="muted">{importSummary(state.result, state.source)}</span>
          {state.result.errors.length > 0 && (
            <ul className="games-import-errors">
              {state.result.errors.map((message, i) => <li key={`${i}-${message}`}>{message}</li>)}
            </ul>
          )}
        </div>
      )}
      {state.kind === 'error' && (
        <div className="games-import-result bad" role="alert">
          <IconAlert />
          <span><b>가져오지 못했습니다</b> · {state.message}</span>
        </div>
      )}
    </section>
  );
}
