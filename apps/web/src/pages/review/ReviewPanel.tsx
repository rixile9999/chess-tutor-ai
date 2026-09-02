import type { Color, GameAnalysis, GameDetail, MoveReviewOut } from '../../api/types';
import { ExplanationPanel } from './ExplanationPanel';
import { FeaturesPanel } from './FeaturesPanel';
import { StrategyPanel } from './StrategyPanel';
import type { Preview } from './shared';

export type Tab = 'move' | 'plan' | 'features';
const TABS: { key: Tab; label: string }[] = [
  { key: 'move', label: '이 수의 설명' }, { key: 'plan', label: '전략과 계획' }, { key: 'features', label: '국면 특징' },
];
const STATUS_COPY: Record<GameAnalysis['status'], string> = {
  none: '분석을 시작하는 중', pending: '분석 대기 중', running: '엔진이 수를 살펴보는 중', done: '분석 완료', failed: '분석 실패',
};

type Props = {
  game: GameDetail; analysis: GameAnalysis | null; analysisWorking: boolean; analysisError: string | null;
  ply: number; review: MoveReviewOut | null; loading: boolean; error: string | null; retry: () => void;
  tab: Tab; onTab: (t: Tab) => void; preview: Preview | null; onPreview: (p: Preview | null) => void;
  rating: number | undefined; boardFen: string; onSavePuzzle: () => void;
};

function Skeleton() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }} aria-hidden>
      <div className="rv-skel" style={{ height: 30, width: 320 }} />
      <div className="rv-skel" style={{ height: 16, width: '92%' }} />
      <div className="rv-skel" style={{ height: 16, width: '78%' }} />
      <div className="rv-skel" style={{ height: 64, width: '100%', marginTop: 8 }} />
      <div className="rv-skel" style={{ height: 16, width: '60%' }} />
      <div className="rv-skel" style={{ height: 16, width: '70%' }} />
    </div>
  );
}

/** Right-hand card: tabs plus the pending / loading / error / content states for the selected ply. */
export function ReviewPanel(p: Props) {
  const { game, analysis, analysisWorking, analysisError, ply, review, loading, error, retry, tab, onTab } = p;
  const userColor: Color | null = game.user_color;
  const strategy = review?.strategy ?? null;
  const analyzed = analysis?.moves?.length ?? 0;
  const total = game.moves.length || 1;

  let body: React.ReactNode;
  if (ply === 0) {
    body = (
      <div className="rv-empty">
        <div className="h3">시작 국면</div>
        <div>오른쪽 화살표나 기보의 수를 눌러 한 수씩 따라가 보세요. 실수한 수에는 반박 수순과 대신 두었어야 할 수가 함께 나옵니다.</div>
        {analysisWorking && <div className="small muted">{STATUS_COPY[analysis?.status ?? 'none']}입니다. 끝나면 수마다 분류 배지가 붙습니다.</div>}
      </div>
    );
  } else if (analysisWorking) {
    body = (
      <div className="rv-empty" style={{ gap: 12 }}>
        <div className="h3">{STATUS_COPY[analysis?.status ?? 'none']}</div>
        <div>엔진이 게임 전체를 먼저 훑어야 이 수의 설명을 만들 수 있습니다. 보통 1분 안팎이 걸리고, 그동안 보드와 기보는 자유롭게 볼 수 있습니다.</div>
        <div className="rv-progress"><i style={{ width: `${Math.round(Math.min(1, analyzed / total) * 100)}%` }} /></div>
        <div className="small muted">{analyzed > 0 ? `${analyzed} / ${game.moves.length} 수 분석` : '2초마다 상태를 확인합니다'}</div>
        <Skeleton />
      </div>
    );
  } else if (loading) {
    body = <Skeleton />;
  } else if (error) {
    body = (
      <div className="rv-error">
        <div><b>이 수의 설명을 불러오지 못했습니다.</b> {error}</div>
        {analysisError && <div className="small muted">엔진 분석: {analysisError}</div>}
        {analysis?.status === 'failed' && analysis.error && <div className="small muted">엔진 분석 실패: {analysis.error}</div>}
        <div><button type="button" className="btn btn-ghost" onClick={retry}>다시 시도</button></div>
      </div>
    );
  } else if (!review) {
    body = <div className="rv-empty">설명이 아직 없습니다.</div>;
  } else if (tab === 'move') {
    body = <ExplanationPanel review={review} ply={ply} rating={p.rating} boardFen={p.boardFen} preview={p.preview} onPreview={p.onPreview} onSavePuzzle={p.onSavePuzzle} />;
  } else if (tab === 'plan') {
    body = strategy
      ? <StrategyPanel review={review} strategy={strategy} ply={ply} userColor={userColor} rating={p.rating} boardFen={p.boardFen} preview={p.preview} onPreview={p.onPreview} onSavePuzzle={p.onSavePuzzle} />
      : <div className="rv-empty">이 국면에는 전략 정보가 없습니다. 오프닝을 벗어난 중반 국면에서 구조와 계획이 정리됩니다.</div>;
  } else {
    body = <FeaturesPanel features={strategy?.features ?? []} />;
  }

  return (
    <div className="card rv-panel">
      <div className="rv-tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.key} type="button" role="tab" aria-selected={tab === t.key} className={`rv-tab${tab === t.key ? ' active' : ''}`} onClick={() => onTab(t.key)}>{t.label}</button>
        ))}
      </div>
      {body}
    </div>
  );
}
