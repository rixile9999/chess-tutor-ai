import { Link } from 'react-router-dom';
import type { MotifMiss, PhaseAccuracy, ProfileReport, RepertoireHole, StructureStat, TimeStats, TrainingSummary } from '../../api/types';
import { motifLabel } from '../../lib/labels';
import { Bar, Card, Empty, IconArrow, IconPlay, fmtCp, fmtPawns, fmtPercent, fmtSigned, toPercent } from './bits';

/** The three-row card grid from the profile artboard. Every list is optional-safe so a stub payload renders. */
export function ReportGrid({ report, busy }: { report: ProfileReport; busy: boolean }) {
  return (
    <div className={`pf-grid${busy ? ' busy' : ''}`} aria-busy={busy}>
      <div className="pf-row pf-row-1">
        <SummaryCard report={report} />
        <PhaseCard phase={report.phase_accuracy ?? null} />
      </div>
      <div className="pf-row pf-row-2">
        <StructuresCard rows={report.structures ?? []} />
        <MotifsCard rows={report.motifs_missed ?? []} />
        <TimeCard time={report.time ?? null} />
      </div>
      <div className="pf-row pf-row-3">
        <TrainingCard training={report.training ?? null} />
        <HolesCard rows={report.repertoire_holes ?? []} />
      </div>
    </div>
  );
}

function SummaryCard({ report }: { report: ProfileReport }) {
  const text = (report.summary_text ?? '').trim();
  const paras = text ? text.split(/\n+/).map((p) => p.trim()).filter(Boolean) : [];
  const analyzed = report.analyzed_games ?? 0;
  const games = report.games ?? 0;
  return (
    <Card title="튜터의 요약" sub="통계를 설명과 연결합니다">
      {paras.length > 0 ? (
        <div className="pf-summary">{paras.map((p, i) => <p key={i}>{p}</p>)}</div>
      ) : (
        <div className="pf-summary muted">
          아직 요약을 쓸 만큼 분석된 게임이 없습니다. 기보 목록에서 게임을 분석하면 아래 통계를 설명과 연결해 드립니다.
        </div>
      )}
      <div className="pf-links">
        <Link to="/review">관련 게임 보기<IconArrow /></Link>
        <span className="faint">·</span>
        <Link to="/openings">오프닝 계획 다시 읽기<IconArrow /></Link>
        <span className="pf-spacer" />
        <span className="small faint mono">분석 {analyzed} / {games}판</span>
      </div>
    </Card>
  );
}

function PhaseCard({ phase }: { phase: PhaseAccuracy | null }) {
  if (!phase) {
    return (
      <Card title="단계별 정확도" sub="100점 만점">
        <Empty>분석된 게임이 없어 정확도를 계산할 수 없습니다. <Link to="/games">기보 목록</Link>에서 분석을 시작하세요.</Empty>
      </Card>
    );
  }
  const raw = [phase.opening, phase.middlegame, phase.endgame].map((n) => (typeof n === 'number' && !Number.isNaN(n) ? n : 0));
  const scale = Math.max(...raw) <= 1 ? 100 : 1;
  const tiles: [string, number, number | null | undefined][] = [
    ['오프닝', raw[0], phase.delta_opening],
    ['미들게임', raw[1], phase.delta_middlegame],
    ['엔드게임', raw[2], phase.delta_endgame],
  ];
  return (
    <Card title="단계별 정확도" sub="100점 만점">
      <div className="pf-tiles">
        {tiles.map(([label, v, d]) => (
          <Tile key={label} label={label} value={v * scale} delta={typeof d === 'number' && !Number.isNaN(d) ? d * scale : null} />
        ))}
      </div>
    </Card>
  );
}

function Tile({ label, value, delta }: { label: string; value: number; delta: number | null }) {
  const tone = delta === null ? 'none' : delta > 0 ? 'good' : delta < 0 ? 'bad' : 'zero';
  return (
    <div className="pf-tile">
      <div className="small muted">{label}</div>
      <div className="mono pf-tile-value">{Math.round(value)}</div>
      {delta === null ? (
        <div className="mono small faint pf-tile-delta">비교 기준 없음</div>
      ) : (
        <div className={`mono small pf-tile-delta pf-delta-${tone}`}>
          {fmtSigned(delta)} <span className="faint">vs 같은 구간</span>
        </div>
      )}
    </div>
  );
}

function StructuresCard({ rows }: { rows: StructureStat[] }) {
  const list = rows.filter(Boolean);
  const maxLoss = Math.max(150, ...list.map((r) => Math.abs(r.avg_loss_cp ?? 0)));
  return (
    <Card title="구조별 성적" sub="판수 · 승률 · 판당 평균 손실(센티폰)">
      {list.length === 0 ? (
        <Empty>폰 구조가 판별된 게임이 아직 없습니다. 게임을 분석하면 구조별로 묶어 드립니다.</Empty>
      ) : (
        <div className="pf-table-wrap">
          <table className="pf-table" aria-label="구조별 성적">
            <tbody>
              {list.map((r, i) => {
                const win = toPercent(r.win_rate);
                const loss = Math.abs(r.avg_loss_cp ?? 0);
                return (
                  <tr key={r.key || `${r.name}-${i}`}>
                    <td className="pf-td-name">{r.name || r.key || '구조 미상'}</td>
                    <td className="mono muted pf-td-num">{r.games ?? 0}판</td>
                    <td className={`mono pf-td-win${win < 45 ? ' pf-bad-text' : ''}`}>{fmtPercent(r.win_rate)}</td>
                    <td className="pf-td-loss">
                      <div className="pf-loss">
                        <div className="pf-loss-track" aria-hidden="true">
                          <div className={`pf-loss-bar${loss > 120 ? ' pf-loss-bar-bad' : ''}`} style={{ width: `${Math.round((loss / maxLoss) * 100)}%` }} />
                        </div>
                        <span className="mono small muted">{fmtCp(loss)}</span>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function MotifsCard({ rows }: { rows: MotifMiss[] }) {
  const sorted = rows
    .filter((m) => m && typeof m.count === 'number' && m.count > 0)
    .sort((a, b) => b.count - a.count)
    .slice(0, 8);
  const max = sorted[0]?.count ?? 0;
  return (
    <Card title="놓친 전술 모티프" sub="리뷰에서 태그된 횟수">
      {sorted.length === 0 ? (
        <Empty>리뷰에서 태그된 모티프가 아직 없습니다. <Link to="/review">게임을 리뷰</Link>하면 채워집니다.</Empty>
      ) : (
        <div className="pf-bars">
          {sorted.map((m, i) => (
            <Bar key={m.kind} label={motifLabel(m.kind)} value={m.count} max={max} right={`${m.count}회`} tone={i === 0 ? 'bad' : 'ink'} />
          ))}
        </div>
      )}
    </Card>
  );
}

function TimeCard({ time }: { time: TimeStats | null }) {
  if (!time) {
    return (
      <Card title="시간 관리" sub="남은 시간과 실수의 관계">
        <Empty>시계 정보가 있는 분석 게임이 없습니다. chess.com에서 가져온 기보에는 수마다 남은 시간이 담겨 있습니다.</Empty>
      </Card>
    );
  }
  const under = toPercent(time.blunder_rate_under_30s);
  const over = toPercent(time.blunder_rate_over_30s);
  const base = toPercent(time.baseline ?? 0.09);
  const max = Math.max(under, over, base, 1) * 1.15;
  const worse = under > base;
  const moves = time.moves_under_30s ?? 0;
  return (
    <Card title="시간 관리" sub="남은 시간과 실수의 관계">
      <div className="pf-time-text">
        30초 미만에 둔 수의 블런더율이 <b className={worse ? 'pf-bad-text' : ''}>{fmtPercent(time.blunder_rate_under_30s)}</b>입니다.
        {' '}같은 구간 기준 {fmtPercent(time.baseline ?? 0.09)}.
        {moves > 0 && <> 30초 미만으로 둔 수는 <span className="mono">{moves}</span>개였습니다.</>}
      </div>
      <div className="pf-bars">
        <Bar label="30초 미만" value={under} max={max} right={fmtPercent(time.blunder_rate_under_30s)} tone={worse ? 'bad' : 'ink'} marker={base} />
        <Bar label="30초 이상" value={over} max={max} right={fmtPercent(time.blunder_rate_over_30s)} tone="ink" marker={base} />
        <Bar label="구간 기준" value={base} max={max} right={fmtPercent(time.baseline ?? 0.09)} tone="faint" />
      </div>
    </Card>
  );
}

function TrainingCard({ training }: { training: TrainingSummary | null }) {
  const due = training?.due_puzzles ?? 0;
  const sets = (training?.motif_sets ?? []).filter((s) => s && s.kind);
  const studies = (training?.studies ?? []).filter((s) => typeof s === 'string' && s.trim());
  const primary = sets[0];
  const others = sets.slice(1).map((s) => motifLabel(s.kind));
  return (
    <Card title="오늘의 훈련" sub="내 기보에서 만든 문제 · 간격 반복">
      <div className="pf-train">
        <div className="pf-train-tile">
          <div className="small muted">복습 예정</div>
          <div className="pf-train-title">내 기보 퍼즐 <span className="mono">{due}</span>개</div>
          <div className="small muted">{due > 0 ? '내 게임의 실수 장면에서 만든 문제' : '오늘 복습할 문제가 없습니다'}</div>
          <Link to="/training" className={`btn ${due > 0 ? 'btn-primary' : 'btn-ghost'} pf-train-btn`}><IconPlay />시작</Link>
        </div>
        <div className="pf-train-tile">
          <div className="small muted">모티프 세트</div>
          {primary ? (
            <div className="pf-train-title">{motifLabel(primary.kind)} <span className="mono">{primary.count ?? 0}</span>문제</div>
          ) : (
            <div className="pf-train-title muted">준비된 세트 없음</div>
          )}
          <div className="small muted">{others.length > 0 ? `그 외 ${others.join(', ')}` : '놓친 모티프 기준으로 선별'}</div>
          <Link to={primary ? `/training?motif=${encodeURIComponent(primary.kind)}` : '/training'} className="btn btn-ghost pf-train-btn">시작</Link>
        </div>
        <div className="pf-train-tile">
          <div className="small muted">구조 스터디</div>
          {studies[0] ? (
            <div className="pf-train-title">{studies[0]}</div>
          ) : (
            <div className="pf-train-title muted">준비된 스터디 없음</div>
          )}
          <div className="small muted">{studies.length > 1 ? `그 외 ${studies.length - 1}개` : '내 게임 국면과 마스터 국면을 나란히'}</div>
          <Link to="/training" className="btn btn-ghost pf-train-btn">열기</Link>
        </div>
      </div>
    </Card>
  );
}

function HolesCard({ rows }: { rows: RepertoireHole[] }) {
  const list = rows.filter((h) => h && h.label);
  return (
    <Card title="레퍼토리 구멍" sub="책에서 벗어난 지점과 그 뒤의 손실">
      {list.length === 0 ? (
        <Empty>레퍼토리 구멍이 아직 잡히지 않았습니다. 같은 오프닝을 여러 판 두면 책에서 벗어나는 지점이 드러납니다.</Empty>
      ) : (
        <div className="pf-holes">
          {list.map((h, i) => {
            const win = toPercent(h.win_rate);
            return (
              <div className="pf-hole" key={`${h.label}-${i}`}>
                <span className="mv">{h.label}</span>
                <span className="muted">{h.games ?? 0}판 · 책 이탈 {fmtPercent(h.deviation_rate)}</span>
                <span className={`mono pf-hole-win${win < 45 ? ' pf-bad-text' : ''}`}>승률 {fmtPercent(h.win_rate)}</span>
                <span className="pf-spacer" />
                <span className="mono muted">평균 {fmtPawns(h.avg_loss_cp)}</span>
                <Link to="/openings" className="pf-hole-link">오프닝 지도에서 보기<IconArrow /></Link>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}
