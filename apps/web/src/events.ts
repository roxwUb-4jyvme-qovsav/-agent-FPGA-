import { ApiFailure, parseApiFailure } from './api';

export interface ProjectEvent {
  schema_version: '1.0'; id: number; project_id: string; type: string;
  resource_id: string; resource_version: number; occurred_at: string; request_id: string; summary: string;
}
interface Frame { id: string; event: string; data: string }
export type StreamState = 'connecting' | 'connected' | 'reconnecting' | 'forbidden';

export class EventDecoder {
  private pending = '';
  private frame: Frame = { id: '', event: 'message', data: '' };
  private data: string[] = [];
  private size = 0;

  push(text: string): Frame[] {
    this.pending += text;
    const frames: Frame[] = [];
    while (true) {
      const end = this.pending.search(/[\r\n]/);
      if (end < 0 || (this.pending[end] === '\r' && end === this.pending.length - 1)) break;
      const line = this.pending.slice(0, end);
      const skip = this.pending.slice(end, end + 2) === '\r\n' ? 2 : 1;
      this.pending = this.pending.slice(end + skip);
      this.size += line.length;
      if (this.size > 262144) throw new Error('事件帧超过上限');
      if (line === '') {
        if (this.frame.id || this.data.length || this.frame.event !== 'message') frames.push({ ...this.frame, data: this.data.join('\n') });
        this.frame = { id: '', event: 'message', data: '' }; this.data = []; this.size = 0;
      } else if (!line.startsWith(':')) {
        const colon = line.indexOf(':');
        const name = colon < 0 ? line : line.slice(0, colon);
        const value = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '');
        if (name === 'id' && !value.includes('\0')) this.frame.id = value;
        if (name === 'event') this.frame.event = value;
        if (name === 'data') this.data.push(value);
      }
    }
    if (this.pending.length + this.size > 262144) throw new Error('事件帧超过上限');
    return frames;
  }
}

interface Callbacks {
  onEvent(value: ProjectEvent): void;
  onState(value: StreamState): void;
  onRefresh(): void;
  onError(value: unknown): void;
}
interface Options {
  signal: AbortSignal; cursor?: string; retryMs?: number;
  fetcher?: typeof fetch;
}

function validEvent(value: unknown, projectId: string, id: string): value is ProjectEvent {
  if (!value || typeof value !== 'object') return false;
  const row = value as Partial<ProjectEvent>;
  return row.schema_version === '1.0' && row.project_id === projectId && String(row.id) === id
    && Number.isSafeInteger(row.id) && Number(row.id) > 0
    && Number.isSafeInteger(row.resource_version) && Number(row.resource_version) > 0
    && ['type', 'resource_id', 'occurred_at', 'request_id', 'summary'].every(key => typeof row[key as keyof ProjectEvent] === 'string');
}

function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise(resolve => {
    if (signal.aborted) { resolve(); return; }
    const done = () => { clearTimeout(timer); signal.removeEventListener('abort', done); resolve(); };
    const timer = setTimeout(done, ms);
    signal.addEventListener('abort', done, { once: true });
  });
}

export async function subscribeProjectEvents(projectId: string, callbacks: Callbacks, options: Options): Promise<void> {
  const fetcher = options.fetcher || fetch;
  const { signal } = options;
  let cursor = options.cursor;
  let failures = 0;
  while (!signal.aborted) {
    callbacks.onState(cursor === undefined ? 'connecting' : 'reconnecting');
    try {
      const headers = new Headers({ Accept: 'text/event-stream' });
      if (cursor !== undefined) headers.set('Last-Event-ID', cursor);
      const response = await fetcher(`/api/v1/projects/${encodeURIComponent(projectId)}/events`, { credentials: 'same-origin', headers, signal });
      if (signal.aborted) { await response.body?.cancel(); return; }
      if (response.status === 410) { cursor = undefined; callbacks.onRefresh(); continue; }
      if ([401, 403, 404].includes(response.status)) {
        callbacks.onState('forbidden'); callbacks.onRefresh();
        if (response.status === 401) window.dispatchEvent(new Event('fpga-session-expired'));
        callbacks.onError(new ApiFailure(response.status, 'EVENT_ACCESS_DENIED', '事件权限已失效，请重新确认项目或登录。'));
        return;
      }
      if (!response.ok || !response.body || !response.headers.get('Content-Type')?.startsWith('text/event-stream')) throw new Error('事件服务暂不可用');
      callbacks.onState('connected'); callbacks.onError(null); callbacks.onRefresh();
      const reader = response.body.getReader();
      const decoder = new EventDecoder(), utf8 = new TextDecoder();
      let resync = false;
      try {
        while (!signal.aborted) {
          const part = await reader.read();
          if (part.done) break;
          for (const frame of decoder.push(utf8.decode(part.value, { stream: true }))) {
            if (signal.aborted) break;
            if (frame.event === 'resync') { cursor = undefined; resync = true; callbacks.onRefresh(); break; }
            if (frame.event === 'access_denied') {
              callbacks.onState('forbidden'); callbacks.onRefresh();
              callbacks.onError(parseApiFailure(403, JSON.parse(frame.data)));
              return;
            }
            if (frame.event === 'unavailable') throw new Error('事件服务暂不可用');
            if (frame.event !== 'message' || !/^\d+$/.test(frame.id) || !Number.isSafeInteger(Number(frame.id))) continue;
            if (cursor !== undefined && Number(frame.id) <= Number(cursor)) continue;
            if (frame.data) {
              const value: unknown = JSON.parse(frame.data);
              if (!validEvent(value, projectId, frame.id)) continue;
              callbacks.onEvent(value);
            }
            cursor = frame.id;
            failures = 0;
            callbacks.onRefresh();
          }
          if (resync) break;
        }
      } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
      if (resync) continue;
    } catch (reason) {
      if (signal.aborted) return;
      callbacks.onError(reason);
    }
    if (!signal.aborted) {
      callbacks.onState('reconnecting');
      await pause(Math.min((options.retryMs ?? 1000) * 2 ** Math.min(failures++, 4), 10000), signal);
    }
  }
}
