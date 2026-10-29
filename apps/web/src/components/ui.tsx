import { AlertCircle, ArrowUpRight, CircleDashed, LoaderCircle, RefreshCw, X } from 'lucide-react';
import { useEffect, useRef, type ReactNode } from 'react';
import { ApiFailure } from '../api';

const statusNames: Record<string, string> = {
  NOT_RUN: '未执行', PASS: '通过', FAIL: '失败', STALE: '已失效', INDETERMINATE: '无法判定',
  QUEUED: '排队中', LEASED: '已领取', RUNNING: '运行中', SUCCEEDED: '已完成', FAILED: '失败',
  WAITING_INPUT: '等待回答', CANCEL_REQUESTED: '取消待确认', CANCELLED: '已取消', LOST: '任务失联',
  IDLE: '空闲', ARMED: '等待触发', CAPTURING: '采集中', DONE: '采集完成', ABORTED: '已中止', ERROR: '错误',
  DRAFT: '草稿', FROZEN: '已冻结', RELEASED: '已发布', ACTIVE: '有效', COMPLETED: '已完成',
  ok: '正常', degraded: '降级', PENDING: '待处理', BLOCKED: '已阻断',
};

export function Status({ value }: { value?: string | null }) {
  const state = value || 'UNKNOWN';
  const upper = state.toUpperCase();
  const tone = ['PASS', 'SUCCEEDED', 'COMPLETED', 'OK', 'ACTIVE', 'FROZEN', 'RELEASED', 'DONE'].includes(upper) ? 'good'
    : ['FAIL', 'FAILED', 'ERROR', 'LOST'].includes(upper) ? 'bad'
      : ['STALE', 'INDETERMINATE', 'DEGRADED', 'BLOCKED', 'WAITING_INPUT', 'CANCEL_REQUESTED'].includes(upper) ? 'warn'
        : ['RUNNING', 'CAPTURING', 'LEASED'].includes(upper) ? 'live' : 'neutral';
  return <span className={`status ${tone}`} title={state}><i />{statusNames[state] || state}</span>;
}

export function ErrorNotice({ error, retry }: { error: unknown; retry?: () => void }) {
  if (!error) return null;
  const failure = error instanceof ApiFailure ? error : null;
  return <div className="error-notice" role="alert"><AlertCircle size={18} /><div><strong>{failure?.status === 403 ? '权限不足' : failure?.status === 412 ? '资源已更新，请重新获取后操作' : '请求未完成'}</strong><p>{error instanceof Error ? error.message : String(error)}</p>{failure && <small>{failure.code}{failure.requestId && ` · 请求 ${failure.requestId}`}</small>}{failure?.details != null && <details><summary>诊断详情</summary><JsonView value={failure.details} /></details>}</div>{retry && <button className="button subtle" onClick={retry}><RefreshCw size={14} />重新获取</button>}</div>;
}

export function Empty({ title = '暂无记录', text = '此处只显示服务端返回的真实数据。', action }: { title?: string; text?: string; action?: ReactNode }) {
  return <div className="empty"><CircleDashed size={34} strokeWidth={1.2} /><h3>{title}</h3><p>{text}</p>{action}</div>;
}
export function Loading() { return <div className="loading" role="status"><LoaderCircle className="spin" size={20} />正在获取最新状态…</div>; }
export function JsonView({ value }: { value: unknown }) { return <pre className="json-view">{JSON.stringify(value, null, 2)}</pre>; }
export function PageHeading({ eyebrow, title, description, actions }: { eyebrow?: string; title: string; description: string; actions?: ReactNode }) {
  return <div className="page-heading"><div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}<h1>{title}</h1><p>{description}</p></div><div className="heading-actions">{actions}</div></div>;
}
export function SectionTitle({ title, children }: { title: string; children?: ReactNode }) { return <div className="section-title"><h2>{title}</h2>{children}</div>; }
export function Metric({ title, value, note }: { title: string; value: ReactNode; note: string }) { return <div className="metric"><span>{title}</span><strong>{value}</strong><small>{note}</small></div>; }
export function Modal({ title, children, close }: { title: string; children: ReactNode; close: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); }, []);
  return <dialog ref={dialog} className="modal" onCancel={event => { event.preventDefault(); close(); }}><div className="modal-heading"><h2>{title}</h2><button className="icon-button" aria-label="关闭弹窗" onClick={close}><X size={20} /></button></div>{children}</dialog>;
}
export function ExternalLink({ href, children }: { href: string; children: ReactNode }) { return <a className="text-link" href={href} target="_blank" rel="noreferrer">{children}<ArrowUpRight size={14} /></a>; }
export const dateTime = (value?: string | null) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '未记录';
export const shortId = (id: string) => id.slice(0, 8);
export const resourceName = (payload: Record<string, unknown>, fallback: string) => String(payload.name || payload.display_name || payload.mpn || payload.agent_role || payload.kind || fallback);
