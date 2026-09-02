const S = { width: 15, height: 15, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const };

export function IconBulb() {
  return <svg {...S}><path d="M9 18h6M10 21h4M8.5 14a6 6 0 1 1 7 0c-.8.6-1.2 1.4-1.3 2.5h-4.4c-.1-1.1-.5-1.9-1.3-2.5z" /></svg>;
}
export function IconClock() {
  return <svg {...S}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>;
}
export function IconCheck() {
  return <svg {...S} strokeWidth={2.2}><circle cx="12" cy="12" r="9" /><path d="M8 12.5l2.8 2.8L16.5 9.5" /></svg>;
}
export function IconArrow() {
  return <svg {...S} width={13} height={13} strokeWidth={2.2}><path d="M5 12h14M13 6l6 6-6 6" /></svg>;
}
export function IconUndo() {
  return <svg {...S}><path d="M9 14l-4-4 4-4" /><path d="M5 10h9a5 5 0 0 1 0 10h-3" /></svg>;
}
export function IconRestart() {
  return <svg {...S}><path d="M4 9a8 8 0 0 1 14-3l2 2M20 4v4h-4" /><path d="M20 15a8 8 0 0 1-14 3l-2-2M4 20v-4h4" /></svg>;
}
export function IconFlip() {
  return <svg {...S}><path d="M7 4v16M7 20l-3-3M7 20l3-3M17 20V4M17 4l-3 3M17 4l3 3" /></svg>;
}
export function IconPlay() {
  return <svg width={13} height={13} viewBox="0 0 24 24" fill="currentColor"><path d="M6 4l14 8-14 8z" /></svg>;
}
export function IconSkip() {
  return <svg {...S}><path d="M5 5l9 7-9 7z" /><path d="M18 5v14" /></svg>;
}
