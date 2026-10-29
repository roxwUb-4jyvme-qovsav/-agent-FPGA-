import { ApiClient, api, projectPath } from './api';
import type { Resource } from './types';

export async function freezeApprovedRevision(projectId: string, revisionId: string, approvalId: string, digest: string, reason: string, client: ApiClient = api): Promise<void> {
  const path = projectPath(projectId, `revisions/${revisionId}`);
  // 批准请求会推进版本状态，必须重新读取。内容摘要变更则重新审阅，不能沿用批准。
  const current = await client.get<Resource>(path);
  const target = await client.get<{ target_sha256: string }>(`${path}/approval-target`);
  if (target.data.target_sha256 !== digest) throw new Error('工程基线摘要已变化，请重新核对并申请批准');
  await client.post(`${path}/commands`, {
    action: 'freeze', expected_version: current.data.version,
    parameters: { target_version_id: null, artifact_id: null, approval_id: approvalId, reason, preserve_history: true },
  }, undefined, current.etag || current.data.etag);
}
