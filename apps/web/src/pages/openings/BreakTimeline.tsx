import type { BreakTiming, Color } from '../../api/types';

const W = 232, H = 36, BAR_TOP = 28, BAR_MAX = 26;

type Props = { rows: BreakTiming[]; color: Color };

/** One histogram row per pawn break: master distribution as bars, my average as a solid line, the master median dashed. */
export function BreakTimeline({ rows, color }: Props) {
  if (!rows.length) return <div className="small faint">기록된 폰 브레이크가 없습니다.</div>;
  return (
    <div className="op-hist-list">
      {rows.map((r, i) => <BreakRow key={`${r.label}-${i}`} row={r} mine={r.side === color} />)}
    </div>
  );
}

function BreakRow({ row, mine }: { row: BreakTiming; mine: boolean }) {
  const bins = (row.histogram ?? []).map((b) => (typeof b === 'number' && b > 0 ? b : 0));
  const from = row.from_move ?? 10;
  const to = row.to_move ?? from + Math.max(0, bins.length - 1);
  const n = bins.length;
  const max = Math.max(0, ...bins);
  const bw = n > 0 ? W / n : W;
  const span = Math.max(1, to - from);
  const xOf = (m: number) => {
    const t = Math.max(0, Math.min(1, (m - from) / span));
    return n > 0 ? t * (W - bw) + bw / 2 - 1 : t * W;
  };
  const colour = mine ? 'var(--good)' : 'var(--bad)';
  return (
    <div className="op-hist">
      <div>
        <div className="mv" style={{ fontSize: 13 }}>{row.label}</div>
        <div className="small faint">{row.side === 'white' ? '백' : '흑'}</div>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} style={{ display: 'block' }}>
        {n > 0 && max > 0 ? bins.map((b, i) => {
          const h = (b / max) * BAR_MAX;
          return (
            <rect key={i} x={(i * bw).toFixed(1)} y={(BAR_TOP - h).toFixed(1)} width={Math.max(1, bw - 2).toFixed(1)} height={h.toFixed(1)} rx={2} fill={colour} opacity={0.45 + (b / max) * 0.5}>
              <title>{`${from + i}수 · ${b}판`}</title>
            </rect>
          );
        }) : (
          <text x={W / 2} y={18} textAnchor="middle" fontSize={9} fill="var(--ink-3)" fontFamily="var(--font-mono)">분포 없음</text>
        )}
        {typeof row.master_median === 'number' && (
          <line x1={xOf(row.master_median)} y1={0} x2={xOf(row.master_median)} y2={30} stroke="var(--ink-3)" strokeWidth={1.5} strokeDasharray="2 2">
            <title>{`마스터 중앙값 ${row.master_median.toFixed(1)}수`}</title>
          </line>
        )}
        {typeof row.my_avg === 'number' && (
          <line x1={xOf(row.my_avg)} y1={0} x2={xOf(row.my_avg)} y2={30} stroke="var(--ink)" strokeWidth={2}>
            <title>{`내 평균 ${row.my_avg.toFixed(1)}수`}</title>
          </line>
        )}
        <text x={0} y={35.5} fontFamily="var(--font-mono)" fontSize={8.5} fill="var(--ink-3)">{from}수</text>
        <text x={W} y={35.5} textAnchor="end" fontFamily="var(--font-mono)" fontSize={8.5} fill="var(--ink-3)">{to}수</text>
      </svg>
    </div>
  );
}
