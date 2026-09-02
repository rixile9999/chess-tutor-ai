import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '../../api/client';

export interface QueryState<T> {
  data: T | null; error: string | null; status: number | null; loading: boolean; idle: boolean; reload: () => void;
}

/** Minimal fetch-on-deps hook. `fn` may return null to stay idle (e.g. no username yet). Stale responses are dropped. */
export function useQuery<T>(fn: () => Promise<T> | null, deps: unknown[]): QueryState<T> {
  const [state, setState] = useState<Omit<QueryState<T>, 'reload'>>({ data: null, error: null, status: null, loading: false, idle: true });
  const [tick, setTick] = useState(0);
  const seq = useRef(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    const id = ++seq.current;
    let p: Promise<T> | null;
    try { p = fnRef.current(); } catch (e) { p = Promise.reject(e); }
    if (!p) { setState({ data: null, error: null, status: null, loading: false, idle: true }); return; }
    setState({ data: null, error: null, status: null, loading: true, idle: false });
    p.then((data) => {
      if (seq.current === id) setState({ data, error: null, status: null, loading: false, idle: false });
    }).catch((e: unknown) => {
      if (seq.current !== id) return;
      const status = e instanceof ApiError ? e.status : null;
      const message = e instanceof Error ? e.message : String(e);
      setState({ data: null, error: message || '알 수 없는 오류', status, loading: false, idle: false });
    });
    return () => { if (seq.current === id) seq.current++; };
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { ...state, reload };
}
