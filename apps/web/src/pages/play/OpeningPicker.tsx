import { useEffect, useMemo, useState } from 'react';
import { api } from '../../api/client';
import type { BookMoves, Color, OpeningCard } from '../../api/types';
import { MiniBoard } from '../../components/MiniBoard';
import { errorText } from '../training/util';
import type { OpeningState } from './state';

type PickerProps = {
  username: string | null;
  current: OpeningState | null;
  onPick: (card: OpeningCard, color: Color, drill: boolean) => void;
};

function recordText(card: OpeningCard): string | null {
  const r = card.record;
  if (!r) return null;
  const parts: string[] = [];
  if (r.games > 0) parts.push(`내 기록 ${r.games}판${r.score !== null ? ` ${Math.round(r.score * 100)}%` : ''}`);
  if (r.practice_games > 0) parts.push(`연습 ${r.practice_games}판${r.practice_score !== null ? ` ${Math.round(r.practice_score * 100)}%` : ''}`);
  return parts.length ? parts.join(' · ') : null;
}

/** 카탈로그 그리드 (4.4). Every card's line and tabiya FEN come from the server, not from this file. */
export function OpeningPicker({ username, current, onPick }: PickerProps) {
  const [cards, setCards] = useState<OpeningCard[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setCards(null);
    api.play.openings(username)
      .then((list) => { if (!cancelled) setCards(Array.isArray(list) ? list.filter((c) => c && c.id && c.tabiya_fen) : []); })
      .catch((e) => { if (!cancelled) setError(errorText(e)); });
    return () => { cancelled = true; };
  }, [username, retry]);

  const families = useMemo(() => {
    const out: { key: string; label: string; items: OpeningCard[] }[] = [];
    for (const c of cards ?? []) {
      let f = out.find((x) => x.key === c.family);
      if (!f) { f = { key: c.family, label: c.family_label || c.family, items: [] }; out.push(f); }
      f.items.push(c);
    }
    return out;
  }, [cards]);

  if (error) {
    return (
      <div className="tr-msg bad">
        <span><b>오프닝 카탈로그를 불러오지 못했습니다.</b> {error}</span>
        <button type="button" className="btn btn-ghost compact" onClick={() => setRetry((n) => n + 1)}>다시 시도</button>
      </div>
    );
  }
  if (!cards) {
    return (
      <div aria-busy="true" className="pl-tabbody">
        <div className="tr-skeleton" style={{ width: '40%' }} />
        <div className="tr-skeleton" style={{ width: '65%' }} />
      </div>
    );
  }
  if (!cards.length) return <p className="small faint">아직 카탈로그가 비어 있습니다.</p>;

  return (
    <div className="pl-families">
      {families.map((f) => (
        <section key={f.key} className="pl-family">
          <div className="tr-section-head"><span className="eyebrow">{f.label}</span><span className="small faint">{f.items.length}개</span></div>
          <div className="pl-grid">
            {f.items.map((c) => {
              const rec = recordText(c);
              return (
                <div key={c.id} className={`card pl-op${current?.id === c.id ? ' on' : ''}`}>
                  <MiniBoard fen={c.tabiya_fen} size={84} />
                  <div className="pl-op-meta">
                    <div className="pl-op-name">{c.name}</div>
                    <div className="tr-line">
                      {c.eco && <span className="badge badge-neutral">{c.eco}</span>}
                      {c.structure?.name && <span className="chip">{c.structure.name}</span>}
                    </div>
                    <div className="small faint">{rec ?? '기록 없음'}</div>
                    <div className="tr-actions">
                      {(c.sides?.length ? c.sides : (['white', 'black'] as Color[])).map((side) => (
                        <button key={side} type="button" className="btn btn-ghost compact" onClick={() => onPick(c, side, false)}>
                          {side === 'white' ? '백으로' : '흑으로'}
                        </button>
                      ))}
                      <button type="button" className="btn btn-ghost compact" onClick={() => onPick(c, (c.sides?.[0] ?? 'white'), true)}>수순 드릴</button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}

type TabProps = PickerProps & {
  playedCount: number;
  drillOn: boolean;
  tabiya: boolean;
  manual: boolean;
  bookOn: boolean;
  onToggleBook: (on: boolean) => void;
  book: BookMoves | null;
  bookError: string | null;
};

/** 오프닝 tab: the opening this game is practising, the 책 따라가기 toggle, then the catalogue. */
export function OpeningTab({ playedCount, drillOn, tabiya, manual, bookOn, onToggleBook, book, bookError, ...picker }: TabProps) {
  const current = picker.current;
  return (
    <div className="pl-tabbody">
      {current ? (
        <div className="tr-task">
          <div className="tr-task-title">{current.name}</div>
          <div className="tr-line">
            {current.drill && <span className="badge badge-neutral">수순 드릴</span>}
            {tabiya && <span className="badge badge-good">타비야 도달</span>}
            {!current.drill && <span className="badge badge-neutral">타비야 대국</span>}
            <span className="small muted">
              {current.drill ? `책 ${Math.min(playedCount, current.line.length)}/${current.line.length}수` : '타비야에서 시작했습니다'}
            </span>
          </div>
          {current.line.length > 0 && (
            <div className="tr-line">
              {current.line.map((san, i) => (
                <span key={`${san}-${i}`} className={`tr-mv${i < playedCount ? '' : ' dim'}`}>{san}</span>
              ))}
            </div>
          )}
          {drillOn && <p className="small faint" style={{ margin: 0 }}>AI는 이 수순대로 응수합니다. 책에서 벗어나면 책 수를 알려 드립니다.</p>}
        </div>
      ) : (
        <p className="small muted" style={{ margin: 0 }}>지금 게임은 오프닝 연습이 아닙니다. 아래에서 하나 골라 시작할 수 있습니다.</p>
      )}

      <div className="tr-section">
        <label className="pl-check">
          <input type="checkbox" checked={bookOn} disabled={!manual} onChange={(e) => onToggleBook(e.target.checked)} />
          <span>책 따라가기 {manual ? '' : '(수동 모드에서만)'}</span>
        </label>
        {bookError && <span className="small faint">{bookError}</span>}
        {bookOn && book && (
          book.moves.length ? (
            <div className="tr-line">
              {book.moves.slice(0, 8).map((m) => (
                <span key={m.uci} className="chip"><span className="mv">{m.san}</span> {m.name}</span>
              ))}
            </div>
          ) : <span className="small faint">이 국면 다음 수는 책에 없습니다.</span>
        )}
      </div>

      <div className="tr-section">
        <OpeningPicker {...picker} />
      </div>
    </div>
  );
}
