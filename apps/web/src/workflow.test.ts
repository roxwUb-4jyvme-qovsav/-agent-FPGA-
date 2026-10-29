import { describe, expect, it } from 'vitest';
import { ApiClient } from './api';
import { freezeApprovedRevision } from './workflow';

describe('批准后的冻结并发边界', () => {
  it('批准变更版本后，重新读取基线并使用最新 ETag 与 expected_version', async () => {
    let command: Record<string, unknown> | null = null;
    let match: string | null = null;
    const client = new ApiClient(async (path, init) => {
      if (String(path).endsWith('/approval-target')) return new Response('{"target_sha256":"digest"}');
      if (init?.method === 'POST') { command = JSON.parse(String(init.body)); match = new Headers(init.headers).get('If-Match'); return new Response('{}'); }
      return new Response('{"id":"r1","version":2,"etag":"v2","state":"WAITING_APPROVAL"}', { headers: { ETag: 'v2' } });
    });
    await freezeApprovedRevision('p1', 'r1', 'a1', 'digest', '核对通过', client);
    expect(command).toMatchObject({ action: 'freeze', expected_version: 2, parameters: { approval_id: 'a1' } });
    expect(match).toBe('v2');
  });

  it('批准后的摘要若变化，拒绝冻结，不发送命令', async () => {
    let writes = 0;
    const client = new ApiClient(async (path, init) => {
      if (init?.method === 'POST') writes++;
      return String(path).endsWith('/approval-target') ? new Response('{"target_sha256":"changed"}') : new Response('{"id":"r1","version":3,"etag":"v3"}');
    });
    await expect(freezeApprovedRevision('p1', 'r1', 'a1', 'digest', '原因', client)).rejects.toThrow('摘要已变化');
    expect(writes).toBe(0);
  });
});
