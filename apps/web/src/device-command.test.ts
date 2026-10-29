import { expect, it } from 'vitest';
import { ApiClient } from './api';
import { submitDeviceCommand, mockStimulus } from './device-command';

it('提交返回 ActionResult 后查询命令资源，不把受理当成最终回复', async () => {
  const paths: string[] = [];
  const client = new ApiClient(async (url, init) => {
    paths.push(String(url));
    return new Response(JSON.stringify(init?.method === 'POST'
      ? { resource_id: 'command-1', state: 'COMPLETED' }
      : { command_id: 'command-1', action: 'arm', state: 'COMPLETED', reply: { action: 'arm', data: { capture_id: 7 } } }));
  });
  const result = await submitDeviceCommand(client, '/projects/p/device-sessions/s', { action: 'arm', sequence: 1, payload_hex: '' });
  expect(result.reply?.data.capture_id).toBe(7);
  expect(paths).toEqual(['/api/v1/projects/p/device-sessions/s/device-commands', '/api/v1/projects/p/device-sessions/s/commands/command-1']);
});

it('演示刺激明确包含低到高边沿且总周期覆盖最大深度，不生成设备回复', () => {
  const segments = mockStimulus({ channels: 16, triggerMode: 1, triggerChannel: 15, divider: 100, mask: 65535, depth: 4096 });
  expect(segments[0].value).toBe(0);
  expect(segments[1].value & 0x8000).toBe(0x8000);
  expect(segments.reduce((n, s) => n + s.cycles, 0)).toBeLessThanOrEqual(500000);
  expect(segments.reduce((n, s) => n + s.cycles, 0)).toBeGreaterThan(409600);
});
