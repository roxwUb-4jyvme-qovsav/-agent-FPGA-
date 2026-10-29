import { describe, expect, it, vi } from 'vitest';
import { ApiClient, ApiFailure } from './api';

describe('同源 API 客户端', () => {
  it('POST 同时发送会话、CSRF 与幂等键，保留服务端 ETag', async () => {
    let observed: RequestInit | undefined;
    const client = new ApiClient(async (_url, init) => {
      observed = init;
      return new Response('{"id":"created"}', { status: 201, headers: { ETag: '"v1"' } });
    });
    client.setCsrf('csrf-token');
    const result = await client.post('/projects', { name: '真实项目' }, 'operation-1');
    const headers = new Headers(observed?.headers);
    expect(observed?.credentials).toBe('same-origin');
    expect(headers.get('X-CSRF-Token')).toBe('csrf-token');
    expect(headers.get('Idempotency-Key')).toBe('operation-1');
    expect(result.etag).toBe('"v1"');
  });

  it('拒绝无 ETag 的覆盖写入，防止旧界面覆盖新版本', async () => {
    const fetcher = vi.fn();
    const client = new ApiClient(fetcher);
    await expect(client.put('/admin/skills/1', {}, '')).rejects.toThrow('重新获取');
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('完整保留错误码、请求编号与详情，绝不把失败响应当成功', async () => {
    const client = new ApiClient(async () => new Response(JSON.stringify({ code: 'TOOL_UNAVAILABLE', message: '未安装仿真器', request_id: 'req-1', details: { tool: 'iverilog' } }), { status: 503 }));
    try { await client.get('/health'); throw new Error('应失败'); }
    catch (error) {
      expect(error).toBeInstanceOf(ApiFailure);
      expect(error).toMatchObject({ status: 503, code: 'TOOL_UNAVAILABLE', requestId: 'req-1' });
      expect((error as Error).message).toContain('未安装仿真器');
    }
  });

  it('网络断开返回明确离线错误，写请求不自动重试', async () => {
    let calls = 0;
    const client = new ApiClient(async () => { calls++; throw new TypeError('Failed to fetch'); });
    await expect(client.post('/projects', {})).rejects.toMatchObject({ code: 'NETWORK_ERROR' });
    expect(calls).toBe(1);
  });

  it('删除资源同样要求 ETag，但退出会话允许不提供资源 ETag', async () => {
    const client = new ApiClient(async () => new Response(null, { status: 204 }));
    await expect(client.delete('/admin/skills/1')).rejects.toMatchObject({ code: 'ETAG_REQUIRED' });
    await expect(client.delete('/auth/session')).resolves.toMatchObject({ data: null });
  });

  it('按冻结契约读取嵌套 ApiError，包括字段错误', async () => {
    const client = new ApiClient(async () => new Response(JSON.stringify({ error: { code: 'VALIDATION_ERROR', message: '字段不合法', request_id: 'req-2', retryable: false, field_errors: [{ path: 'name', message: '不能为空' }] } }), { status: 422 }));
    await expect(client.post('/projects', {})).rejects.toMatchObject({ code: 'VALIDATION_ERROR', requestId: 'req-2', details: [{ path: 'name', message: '不能为空' }] });
  });
});
