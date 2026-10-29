import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiFailure } from './api';

export function useResource<T>(path: string | null, refresh = 0) {
  const [data, setData] = useState<T | null>(null);
  const [etag, setEtag] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const [loadedAt, setLoadedAt] = useState<Date | null>(null);
  const previousPath = useRef(path);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = (event: Event) => {
      const projectId = (event as CustomEvent<string>).detail;
      if (!path || !(path.startsWith(`/projects/${encodeURIComponent(projectId)}/`) || path === `/projects/${encodeURIComponent(projectId)}` || path.startsWith('/projects?'))) return;
      // 合并短时间内的事件通知，刷新 GET；不重发写命令。
      if (timer === undefined) timer = setTimeout(() => { timer = undefined; setRevision(value => value + 1); }, 100);
    };
    window.addEventListener('fpga-project-refresh', refresh);
    return () => { window.removeEventListener('fpga-project-refresh', refresh); clearTimeout(timer); };
  }, [path]);
  const reload = useCallback(() => setRevision(value => value + 1), []);
  useEffect(() => {
    if (previousPath.current !== path) {
      setData(null); setEtag(null); setLoadedAt(null); previousPath.current = path;
    }
    setError(null);
    if (!path) { setLoading(false); return; }
    const controller = new AbortController();
    setLoading(true);
    api.get<T>(path, controller.signal).then(result => {
      if (!controller.signal.aborted) { setData(result.data); setEtag(result.etag); setLoadedAt(new Date()); }
    }).catch(reason => {
      if (!controller.signal.aborted) {
        setError(reason);
        if (reason instanceof ApiFailure && [401, 403, 404].includes(reason.status)) {
          setData(null); setEtag(null); setLoadedAt(null);
        }
      }
    })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [path, revision, refresh]);
  return { data, etag, error, loading, loadedAt, reload, setData };
}

export function useHashRoute() {
  const current = () => location.hash.slice(1) || 'overview';
  const [route, setRoute] = useState(current);
  useEffect(() => { const update = () => setRoute(current()); window.addEventListener('hashchange', update); return () => window.removeEventListener('hashchange', update); }, []);
  return [route, (value: string) => { location.hash = value; }] as const;
}
