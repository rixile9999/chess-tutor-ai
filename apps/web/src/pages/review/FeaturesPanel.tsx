import type { FeatureDiffRow } from '../../api/types';

type Props = { features: FeatureDiffRow[]; aLabel?: string; bLabel?: string };

function deltaText(d: number | null): string {
  if (d === null || d === undefined) return '';
  const v = Number.isInteger(d) ? String(d) : d.toFixed(1);
  return d > 0 ? `+${v}` : v;
}

/** 국면 특징 tab: the position's feature rows as a plain table. */
export function FeaturesPanel({ features, aLabel = 'A', bLabel = 'B' }: Props) {
  if (!features.length) {
    return <p className="rv-lead-sub" style={{ marginTop: -4 }}>이 국면의 특징 정보가 아직 없습니다.</p>;
  }
  return (
    <div className="rv-section" style={{ gap: 8 }}>
      <div className="rv-section-head">
        <span className="h3">국면 특징</span>
        <span className="small muted">폰 구조, 기물 활동, 킹 안전 같은 항목을 이 국면에서 양쪽 비교</span>
      </div>
      <table className="rv-table">
        <thead>
          <tr><th>항목</th><th className="col-a">{aLabel}</th><th className="col-b">{bLabel}</th><th className="col-d">차이</th></tr>
        </thead>
        <tbody>
          {features.map((r, i) => (
            <tr key={`${r.feature}-${i}`}>
              <td>{r.feature}</td>
              <td className="col-a">{r.a}</td>
              <td className="col-b">{r.b}</td>
              <td className="col-d mono" style={{ color: r.delta == null ? 'var(--ink-3)' : r.delta > 0 ? 'var(--good)' : r.delta < 0 ? 'var(--bad)' : 'var(--ink-2)' }}>{deltaText(r.delta)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="small faint" style={{ margin: 0 }}>차이는 양수일수록 {aLabel} 쪽에 유리합니다.</p>
    </div>
  );
}
