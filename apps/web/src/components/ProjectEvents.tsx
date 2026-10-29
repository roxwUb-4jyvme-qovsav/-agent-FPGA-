import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { subscribeProjectEvents, type ProjectEvent, type StreamState } from '../events';
import { ErrorNotice, JsonView } from './ui';

interface EventState { events: ProjectEvent[]; state: StreamState; error: unknown; reconnect(): void }
const EventsContext = createContext<EventState>({ events: [], state: 'connecting', error: null, reconnect() {} });
const labels: Record<StreamState, string> = { connecting: '正在连接', connected: '已连接', reconnecting: '连接中断，正在重连', forbidden: '项目或会话权限已失效' };

export function ProjectEventsProvider({ projectId, children }: { projectId: string | null; children: ReactNode }) {
  const [events, setEvents] = useState<ProjectEvent[]>([]);
  const [state, setState] = useState<StreamState>('connecting');
  const [error, setError] = useState<unknown>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    setEvents([]); setError(null); setState('connecting');
    if (!projectId) return;
    const controller = new AbortController();
    void subscribeProjectEvents(projectId, {
      onEvent(value) { if (!controller.signal.aborted) setEvents(previous => [value, ...previous].slice(0, 30)); },
      onState(value) { if (!controller.signal.aborted) setState(value); },
      onError(value) { if (!controller.signal.aborted) setError(value); },
      onRefresh() { if (!controller.signal.aborted) window.dispatchEvent(new CustomEvent('fpga-project-refresh', { detail: projectId })); },
    }, { signal: controller.signal });
    return () => controller.abort();
  }, [projectId, retry]);
  return <EventsContext.Provider value={{ events, state, error, reconnect: () => setRetry(value => value + 1) }}>{children}</EventsContext.Provider>;
}

export function EventPanel() {
  const { events, state, error, reconnect } = useContext(EventsContext);
  return <section className="panel events-panel"><div className="section-title"><h2>工程动态 <span className="muted">SSE</span></h2><div className="inline-actions"><small>{labels[state]}</small><button className="text-link" onClick={reconnect}>重新同步</button></div></div><ErrorNotice error={error} />{events.length ? <div className="event-log">{events.map(event => <JsonView key={event.id} value={event} />)}</div> : <p className="muted">事件连接建立后自动刷新状态。数据库记录是最终事实。</p>}</section>;
}
