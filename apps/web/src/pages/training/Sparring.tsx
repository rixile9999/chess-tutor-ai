import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Chess, type Square } from 'chess.js';
import { api } from '../../api/client';
import type { Color, SparringMoveResponse } from '../../api/types';
import { Board } from '../../components/Board';
import { applyUci, legalDests, sideToMove } from '../../lib/chess';
import { IconArrow, IconFlip, IconRestart, IconUndo } from './icons';
import { SIDE_LABEL, errorText, useBoardSize } from './util';

const START = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
const RATING_MIN = 1000;
const RATING_MAX = 2200;
const SOURCE_LABEL: Record<string, string> = { maia: 'Maia', engine: '엔진', random: '무작위' };

type Ply = { san: string; uci: string; fen: string; by: 'user' | 'maia'; prob: number | null; source: SparringMoveResponse['source'] | null; probs: Record<string, number> | null };

function validFen(fen: string | null): string | null {
  if (!fen) return null;
  try { new Chess(fen); return fen; } catch { return null; }
}

function clampRating(raw: string | null): number {
  const n = Number(raw);
  if (!Number.isFinite(n) || raw === null) return 1500;
  return Math.min(RATING_MAX, Math.max(RATING_MIN, Math.round(n / 50) * 50));
}

/** Sparring picks up the fen from the URL; the page keys this component on it so a new fen starts a new game. */
export function Sparring() {
  const [params, setParams] = useSearchParams();
  const rawFen = params.get('fen');
  const startFen = useMemo(() => validFen(rawFen) ?? START, [rawFen]);
  const fenInvalid = rawFen !== null && rawFen !== '' && validFen(rawFen) === null;
  const rating = clampRating(params.get('rating'));
  const gameParam = params.get('game');
  const plyParam = params.get('ply');
  const { ref, size } = useBoardSize();

  const [userColor, setUserColor] = useState<Color>(() => sideToMove(startFen));
  const [moves, setMoves] = useState<Ply[]>([]);
  const [thinking, setThinking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const reqId = useRef(0);

  const fen = moves.length ? moves[moves.length - 1].fen : startFen;
  const turn = sideToMove(fen);
  const game = useMemo(() => {
    const c = new Chess(startFen);
    for (const m of moves) { try { c.move(m.san); } catch { break; } }
    return c;
  }, [startFen, moves]);
  const over = game.isGameOver();
  const inCheck = game.inCheck();

  const endText = useMemo(() => {
    if (!over) return null;
    if (game.isCheckmate()) {
      const winner: Color = turn === 'white' ? 'black' : 'white';
      return `체크메이트 · ${SIDE_LABEL[winner]} 승 (${winner === userColor ? '당신의 승리' : 'Maia의 승리'})`;
    }
    if (game.isStalemate()) return '스테일메이트 · 무승부';
    if (game.isInsufficientMaterial()) return '기물 부족 · 무승부';
    if (game.isThreefoldRepetition()) return '3회 동형 반복 · 무승부';
    if (game.isDrawByFiftyMoves()) return '50수 규칙 · 무승부';
    return '무승부';
  }, [over, game, turn, userColor]);

  // Maia replies whenever it is not the user's turn.
  useEffect(() => {
    if (over || turn === userColor) return;
    let cancelled = false;
    const id = ++reqId.current;
    setThinking(true);
    setError(null);
    api.maia.move(fen, rating)
      .then((res) => {
        if (cancelled || id !== reqId.current) return;
        const uci = res.uci || '';
        const viaUci = uci ? applyUci(fen, uci) : null;
        const r = viaUci ? { ...viaUci, uci } : (res.san ? playSan(fen, res.san) : null);
        if (!r) { setError(`Maia의 응답을 이 국면에 둘 수 없습니다 (${res.san || res.uci || '빈 응답'})`); return; }
        const probs = res.probs && typeof res.probs === 'object' ? res.probs : null;
        const prob = probs ? (probs[res.san] ?? probs[r.uci] ?? probs[r.san] ?? null) : null;
        setMoves((m) => [...m, { san: r.san, uci: r.uci, fen: r.fen, by: 'maia', prob, source: res.source ?? null, probs }]);
      })
      .catch((e) => { if (!cancelled) setError(errorText(e)); })
      .finally(() => { if (!cancelled) setThinking(false); });
    return () => { cancelled = true; setThinking(false); };
  }, [fen, turn, userColor, over, rating, retry]);

  const movable = useMemo(() => {
    if (over || thinking || turn !== userColor) return null;
    return { color: userColor, dests: legalDests(fen) };
  }, [fen, turn, userColor, over, thinking]);

  const onMove = useCallback((orig: string, dest: string) => {
    if (over || thinking || turn !== userColor) return;
    let promo = false;
    try { promo = game.get(orig as Square)?.type === 'p' && (dest[1] === '8' || dest[1] === '1'); } catch { /* ignore */ }
    const uci = orig + dest + (promo ? 'q' : '');
    const r = applyUci(fen, uci);
    if (!r) return;
    setError(null);
    setMoves((m) => [...m, { san: r.san, uci, fen: r.fen, by: 'user', prob: null, source: null, probs: null }]);
  }, [over, thinking, turn, userColor, game, fen]);

  const undo = () => {
    setError(null);
    setMoves((m) => {
      let out = m.slice(0, -1);
      const fenOf = (list: Ply[]) => (list.length ? list[list.length - 1].fen : startFen);
      while (out.length && sideToMove(fenOf(out)) !== userColor) out = out.slice(0, -1);
      return out;
    });
  };
  const restart = () => { setError(null); setMoves([]); };
  const setRating = (v: number) => {
    setParams((p) => { const n = new URLSearchParams(p); n.set('rating', String(v)); return n; }, { replace: true });
  };

  const lastMove = moves.length ? ([moves[moves.length - 1].uci.slice(0, 2), moves[moves.length - 1].uci.slice(2, 4)] as [string, string]) : null;
  const lastMaia = [...moves].reverse().find((m) => m.by === 'maia') ?? null;
  const candidates = useMemo(() => {
    if (!lastMaia?.probs) return [];
    return Object.entries(lastMaia.probs).filter(([, v]) => Number.isFinite(v)).sort((a, b) => b[1] - a[1]).slice(0, 4);
  }, [lastMaia]);

  const rows = useMemo(() => {
    const parts = startFen.split(' ');
    const startNo = Number(parts[5]) || 1;
    const offset = parts[1] === 'b' ? 1 : 0;
    const out: { n: number; w: (Ply & { i: number }) | null; b: (Ply & { i: number }) | null }[] = [];
    moves.forEach((m, i) => {
      const p = i + offset;
      const n = startNo + Math.floor(p / 2);
      let row = out[out.length - 1];
      if (!row || row.n !== n) { row = { n, w: null, b: null }; out.push(row); }
      if (p % 2 === 0) row.w = { ...m, i }; else row.b = { ...m, i };
    });
    return out;
  }, [moves, startFen]);

  const hasUserMove = moves.some((m) => m.by === 'user');
  const status = over ? endText
    : thinking ? `Maia ${rating}이(가) 생각하는 중`
    : turn === userColor ? `당신 차례 · ${SIDE_LABEL[userColor]}${inCheck ? ' · 체크' : ''}`
    : 'Maia 차례';

  const reviewLink = gameParam ? `/review/${gameParam}${plyParam ? `/${plyParam}` : ''}` : null;

  return (
    <div className="tr-body">
      <div className="tr-left" ref={ref}>
        <div className="tr-board">
          <Board fen={fen} orientation={userColor} size={size} movable={movable} onMove={onMove} lastMove={lastMove} />
        </div>
        <div className="tr-controls">
          <button type="button" className="btn btn-ghost compact" onClick={undo} disabled={!hasUserMove}><IconUndo /> 되돌리기</button>
          <button type="button" className="btn btn-ghost compact" onClick={restart} disabled={moves.length === 0 && !error}><IconRestart /> 새로 시작</button>
          <div className="spacer" />
          <button type="button" className="btn btn-ghost compact" onClick={() => setUserColor((c) => (c === 'white' ? 'black' : 'white'))}>
            <IconFlip /> {userColor === 'white' ? '흑으로 두기' : '백으로 두기'}
          </button>
        </div>
        {rawFen && (
          <div className="tr-msg note small">
            <span>시작 국면</span>
            <span className="tr-fen" style={{ flex: 1 }}>{fenInvalid ? `${rawFen} (읽을 수 없어 초기 국면에서 시작)` : startFen}</span>
            {reviewLink && <Link to={reviewLink}>리뷰로 <IconArrow /></Link>}
          </div>
        )}
      </div>

      <div className="card tr-panel">
        <div className="tr-panel-head">
          <span className="eyebrow">이어 두기</span>
          <span className="small muted">이 국면에서 Maia와 계속 둡니다. Maia는 같은 구간의 사람이 둘 법한 수를 고릅니다.</span>
        </div>

        <div className="tr-task">
          <div className="tr-task-title">Maia {rating}</div>
          <div className="tr-task-sub">당신은 {SIDE_LABEL[userColor]}. 상대 실력을 조절하면 다음 응수부터 반영됩니다.</div>
        </div>

        <div className="tr-field">
          <label htmlFor="tr-rating">상대 실력</label>
          <input id="tr-rating" className="tr-range" type="range" min={RATING_MIN} max={RATING_MAX} step={100} value={rating} onChange={(e) => setRating(Number(e.target.value))} />
          <span className="mono" style={{ width: 44, textAlign: 'right' }}>{rating}</span>
        </div>

        <div className={`tr-status${over ? ' over' : ''}`}>
          <span className={`dot${thinking ? ' wait' : ''}`} />
          <span style={{ fontWeight: over ? 700 : 500 }}>{status}</span>
          <div className="spacer" />
          {over && <button type="button" className="btn btn-ghost compact" onClick={restart}><IconRestart /> 다시 두기</button>}
        </div>

        {error && (
          <div className="tr-msg bad">
            <span><b>응수를 받지 못했습니다.</b> {error}</span>
            <button type="button" className="btn btn-ghost compact" onClick={() => setRetry((n) => n + 1)}>다시 요청</button>
          </div>
        )}

        {lastMaia && (
          <div className="tr-line">
            <span className="chip">Maia 응수 <span className="mv">{lastMaia.san}</span>{lastMaia.prob !== null && <span className="mono">{formatPct(lastMaia.prob)}</span>}</span>
            {lastMaia.source && <span className="badge badge-neutral">{SOURCE_LABEL[lastMaia.source] ?? lastMaia.source}</span>}
            {lastMaia.source === 'engine' && <span className="small faint">Maia 모델을 쓸 수 없어 엔진이 대신 두었습니다</span>}
          </div>
        )}

        {candidates.length > 0 && (
          <div className="tr-probs">
            <span className="small muted">Maia가 본 후보 수</span>
            {candidates.map(([mv, p]) => (
              <div key={mv} className={`tr-prob${lastMaia && (mv === lastMaia.san || mv === lastMaia.uci) ? ' played' : ''}`}>
                <span className="mv">{mv}</span>
                <div className="tr-bar"><i style={{ width: `${Math.round(Math.min(1, Math.max(0, p)) * 100)}%` }} /></div>
                <span className="mono small muted" style={{ textAlign: 'right' }}>{formatPct(p)}</span>
              </div>
            ))}
          </div>
        )}

        <div className="card tr-movelist">
          <div className="tr-movelist-head">
            <span className="eyebrow">기보</span>
            <span className="small muted">{moves.length === 0 ? '아직 둔 수가 없습니다' : `${moves.length}수`}</span>
          </div>
          {rows.map((row) => (
            <div key={row.n} className="tr-row">
              <div className="no">{row.n}.</div>
              {[row.w, row.b].map((m, j) => m ? (
                <div key={j} className={`tr-cell${m.i === moves.length - 1 ? ' sel' : ''}`}>
                  <span className="mv" style={{ fontSize: 13 }}>{m.san}</span>
                  {m.by === 'maia' && m.prob !== null && <span className="pct">{formatPct(m.prob)}</span>}
                </div>
              ) : <div key={j} />)}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function playSan(fen: string, san: string): { fen: string; san: string; uci: string } | null {
  try {
    const c = new Chess(fen);
    const m = c.move(san);
    return { fen: c.fen(), san: m.san, uci: m.from + m.to + (m.promotion ?? '') };
  } catch { return null; }
}

function formatPct(p: number): string {
  const v = p <= 1 ? p * 100 : p;
  return `${v < 10 ? v.toFixed(1) : Math.round(v)}%`;
}
