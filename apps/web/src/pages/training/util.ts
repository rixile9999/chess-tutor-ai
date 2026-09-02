import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../../api/client';

export function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 404) return '서버에 아직 이 기능이 없습니다 (404)';
    return `${e.message} (${e.status})`;
  }
  if (e instanceof Error) return e.message || '알 수 없는 오류';
  return '알 수 없는 오류';
}

export function formatSeconds(s: number): string {
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
}

export const SIDE_LABEL = { white: '백', black: '흑' } as const;

/** Board size that follows the width of the column it sits in (capped at `max`). */
export function useBoardSize(max = 520) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState(max);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => setSize(Math.max(240, Math.min(max, Math.floor(el.clientWidth))));
    update();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [max]);
  return { ref, size };
}
