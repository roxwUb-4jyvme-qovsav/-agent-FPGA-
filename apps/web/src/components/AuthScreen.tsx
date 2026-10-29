import { ArrowRight, CircuitBoard, Fingerprint, Layers3, ShieldCheck } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { api } from '../api';
import type { Session } from '../types';
import { ErrorNotice } from './ui';

export function AuthScreen({ initialized, error, retry, authenticated }: { initialized: boolean; error: unknown; retry: () => void; authenticated: (session: Session) => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [failure, setFailure] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setFailure(null);
    if (!initialized && password.length < 12) { setFailure(new Error('管理员密码至少 12 个字符')); return; }
    if (!initialized && password !== confirmation) { setFailure(new Error('两次密码不一致')); return; }
    setBusy(true);
    try { const result = await api.post<Session>(initialized ? '/auth/session' : '/setup', { username: username.trim(), password }); authenticated(result.data); }
    catch (reason) { setFailure(reason); } finally { setBusy(false); }
  };
  return <div className="auth-shell"><div className="auth-story"><div className="brand"><span className="brand-icon"><CircuitBoard size={24} /></span><span>FPGA <strong>COLLAB</strong><small>协同开发平台</small></span></div><div className="auth-copy"><span className="hero-kicker">LOCAL ENGINEERING WORKSPACE</span><h1>工程有边界，<br />协作有依据。</h1><p>让需求、模型、工具和工程师围绕同一个可信基线协作。从第一条约束，到最后一份验证证据。</p><div className="auth-principles"><span><Layers3 size={18} />精确版本与完整来源</span><span><ShieldCheck size={18} />独立验证与真实状态</span><span><Fingerprint size={18} />本机运行与项目权限</span></div></div><div className="auth-bottom">FPGA COLLAB / 工程工作台 <span>API v1 · 同源会话</span></div></div><main className="auth-main"><div className="auth-form-wrap"><div className="eyebrow">{initialized ? 'WELCOME BACK' : 'FIRST RUN / SETUP'}</div><h2>{initialized ? '回到你的工程空间' : '建立本机管理员'}</h2><p>{initialized ? '使用平台账号登录，继续推进你的工程。' : '首次运行只初始化一次。创建管理员后，即可配置工程与团队。'}</p><ErrorNotice error={failure || error} retry={error ? retry : undefined} /><form onSubmit={submit}><label className="field"><span>{initialized ? '用户名' : '管理员用户名'}</span><input autoComplete="username" required value={username} onChange={event => setUsername(event.target.value)} placeholder="输入你的平台账号" /></label><label className="field"><span>密码</span><input type="password" autoComplete={initialized ? 'current-password' : 'new-password'} minLength={initialized ? undefined : 12} required value={password} onChange={event => setPassword(event.target.value)} placeholder={initialized ? '输入登录密码' : '至少 12 个字符'} /></label>{!initialized && <label className="field"><span>确认密码</span><input type="password" autoComplete="new-password" minLength={12} required value={confirmation} onChange={event => setConfirmation(event.target.value)} /></label>}<button className="button primary full-width" disabled={busy}>{busy ? '正在验证…' : initialized ? '登录工作台' : '初始化并进入工作台'}<ArrowRight size={17} /></button></form><p className="auth-security">会话由本机服务管理，密钥不保存在浏览器。<br />服务不可用时，请先启动 API 与数据库。</p></div></main></div>;
}
