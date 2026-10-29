import { expect, it } from 'vitest';
import { EventDecoder, subscribeProjectEvents } from './events';

const pid = '11111111-1111-4111-8111-111111111111';
const event = (id: number) => ({ schema_version: '1.0', id, project_id: pid, type: 'runs.updated', resource_id: '22222222-2222-4222-8222-222222222222', resource_version: 2, occurred_at: '2026-10-29T08:00:00Z', request_id: '33333333-3333-4333-8333-333333333333', summary: 'runs.updated' });
const wire = (id: number) => `id: ${id}\ndata: ${JSON.stringify(event(id))}\n\n`;
const stream = (value: string) => new Response(new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode(value)); controller.close(); } }), { headers: { 'Content-Type': 'text/event-stream' } });

it('解析跨块 CRLF、多行 data、注释和 id-only 检查点', () => {
  const decoder = new EventDecoder();
  expect(decoder.push(': heartbeat\r')).toEqual([]);
  expect(decoder.push('\n\r\nid: 3\r\ndata: {\r\ndata: "value": 1}\r')).toEqual([]);
  expect(decoder.push('\n\r\nid: 4\n\n')).toEqual([
    { id: '3', event: 'message', data: '{\n"value": 1}' },
    { id: '4', event: 'message', data: '' },
  ]);
});

it('重连携带最后游标，重复事件仅消费一次', async () => {
  const controller = new AbortController();
  const seen: number[] = [], headers: (string | null)[] = [];
  let calls = 0;
  await subscribeProjectEvents(pid, {
    onEvent(value) { seen.push(value.id); if (value.id === 2) controller.abort(); },
    onState() {}, onRefresh() {}, onError() {},
  }, { signal: controller.signal, retryMs: 0, fetcher: async (_url, init) => {
    headers.push(new Headers(init?.headers).get('Last-Event-ID'));
    return stream(calls++ === 0 ? wire(1) : wire(1) + wire(2));
  } });
  expect(seen).toEqual([1, 2]);
  expect(headers).toEqual([null, '1']);
});

it('410 后刷新状态并清除过期游标，拒绝跨项目的事件', async () => {
  const controller = new AbortController();
  const headers: (string | null)[] = [], seen: number[] = [];
  let calls = 0, refreshed = 0;
  await subscribeProjectEvents(pid, {
    onEvent(value) { seen.push(value.id); controller.abort(); },
    onState() {}, onRefresh() { refreshed++; }, onError() {},
  }, { signal: controller.signal, cursor: '1', retryMs: 0, fetcher: async (_url, init) => {
    headers.push(new Headers(init?.headers).get('Last-Event-ID'));
    return calls++ === 0 ? new Response('', { status: 410 })
      : stream(`id: 2\ndata: ${JSON.stringify({ ...event(2), project_id: 'other-project' })}\n\n` + wire(3));
  } });
  expect(headers).toEqual(['1', null]);
  expect(refreshed).toBeGreaterThan(0);
  expect(seen).toEqual([3]);
});

it('权限失效停止重连，不继续消费资源事件', async () => {
  const controller = new AbortController();
  let calls = 0, state = '';
  await subscribeProjectEvents(pid, {
    onEvent() { throw new Error('不应交付事件'); }, onState(value) { state = value; }, onRefresh() {}, onError() {},
  }, { signal: controller.signal, retryMs: 0, fetcher: async () => { calls++; return new Response('', { status: 404 }); } });
  expect(calls).toBe(1);
  expect(state).toBe('forbidden');
});

it('已取消的旧订阅随后返回401不影响当前登录会话', async () => {
  const controller = new AbortController();
  let expired = 0;
  const listener = () => { expired++; };
  window.addEventListener('fpga-session-expired', listener);
  try {
    await subscribeProjectEvents(pid, {
      onEvent() {}, onState() {}, onRefresh() {}, onError() {},
    }, { signal: controller.signal, fetcher: async () => {
      controller.abort();
      return new Response('', { status: 401 });
    } });
    expect(expired).toBe(0);
  } finally { window.removeEventListener('fpga-session-expired', listener); }
});
