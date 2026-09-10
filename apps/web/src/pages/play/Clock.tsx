/** m:ss, with tenths under 20 seconds so the last moves are readable. */
export function formatClock(ms: number): string {
  const left = Math.max(0, ms);
  const total = Math.floor(left / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  if (left < 20_000) return `${m}:${String(s).padStart(2, '0')}.${Math.floor((left % 1000) / 100)}`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

export const LOW_TIME_MS = 30_000;

type Props = { ms: number; label: string; running: boolean };

/** One side's clock chip. `running` marks the clock that is counting down right now. */
export function ClockChip({ ms, label, running }: Props) {
  const low = ms < LOW_TIME_MS;
  return (
    <div className={`pl-clock${running ? ' on' : ''}${low ? ' low' : ''}`}>
      <span className="pl-clock-label">{label}</span>
      <span className="pl-clock-time mono">{formatClock(ms)}</span>
    </div>
  );
}
