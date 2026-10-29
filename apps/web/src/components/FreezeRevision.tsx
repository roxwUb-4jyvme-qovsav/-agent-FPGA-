import { useState } from 'react';
import { api, projectPath } from '../api';
import { useResource } from '../hooks';
import type { Resource } from '../types';
import { freezeApprovedRevision } from '../workflow';
import { ErrorNotice, Loading, Modal } from './ui';

interface ApprovalTarget { action: string; target_sha256: string; revision_id: string }

export function FreezeRevision({ projectId, revision, close, done }: { projectId: string; revision: Resource; etag: string; close: () => void; done: () => void }) {
  const target = useResource<ApprovalTarget>(projectPath(projectId, `revisions/${revision.id}/approval-target`));
  const [reason, setReason] = useState('');
  const [failure, setFailure] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState('');
  const [approvalId, setApprovalId] = useState<string | null>(null);
  const [expiresAt] = useState(() => new Date(Date.now() + 30 * 60_000).toISOString());
  const freeze = async () => {
    if (!target.data) return;
    setBusy(true); setFailure(null);
    try {
      // 批准只绑定当前基线摘要；冻结仍带 If-Match，基线变化由后端拒绝。
      let id = approvalId;
      if (!id) {
        setStep('正在登记批准请求');
        const approval = await api.post<Resource>(projectPath(projectId, 'approvals'), { action: 'freeze', target_id: revision.id, target_sha256: target.data.target_sha256, expires_at: expiresAt, reason });
        id = approval.data.id; setApprovalId(id);
        setStep('正在提交审阅决定');
        await api.post(projectPath(projectId, `approvals/${id}/decision`), { expected_version: approval.data.version, decision: 'approve', reason }, undefined, approval.etag || approval.data.etag);
      }
      setStep('正在冻结当前版本');
      await freezeApprovedRevision(projectId, revision.id, id, target.data.target_sha256, reason);
      done(); close();
    } catch (error) { setFailure(error); setStep('操作未完成，请核对批准记录与当前版本后再操作'); }
    finally { setBusy(false); }
  };
  return <Modal title="核对工程基线并冻结" close={close}><p>目标版本：<strong className="mono">{revision.id}</strong> · v{revision.version}</p><ErrorNotice error={target.error || failure} retry={target.error ? target.reload : undefined} />{target.loading ? <Loading /> : target.data && <dl className="metadata"><dt>动作</dt><dd>批准并冻结当前版本</dd><dt>目标 SHA256</dt><dd className="mono">{target.data.target_sha256}</dd><dt>批准有效期至</dt><dd>{new Date(expiresAt).toLocaleString('zh-CN')}</dd></dl>}<label className="field"><span>审阅与冻结原因</span><textarea required value={reason} onChange={event => setReason(event.target.value)} placeholder="说明已核对的需求、契约与验收边界" /></label><p className="form-note">此按钮将提交批准决定并冻结所列基线，需要 reviewer 或 admin 权限。冻结不代表仿真通过或硬件实测。</p>{step && <p className="form-note" role="status">{step}{approvalId && ` · 批准 ${approvalId}`}</p>}<div className="form-actions"><button className="button" disabled={busy} onClick={close}>取消</button><button className="button primary" disabled={busy || !reason.trim() || !target.data || !!target.error} onClick={freeze}>{busy ? '正在处理…' : '批准此基线并冻结'}</button></div></Modal>;
}
