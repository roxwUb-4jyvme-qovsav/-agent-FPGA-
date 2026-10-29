import { Activity, BookOpen, Boxes, ChevronDown, ChevronRight, CircuitBoard, FileCheck2, FolderKanban, GitBranch, LayoutDashboard, LogOut, Menu, Network, Play, Search, Settings2, ShieldCheck, Users, Waves, Wallet, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api, ApiFailure } from './api';
import { resources } from './catalog';
import { useHashRoute, useResource } from './hooks';
import type { Project, ResourceList, Session, SetupStatus } from './types';
import { AuthScreen } from './components/AuthScreen';
import { Empty, ErrorNotice, Loading } from './components/ui';
import { DevicePage } from './pages/Device';
import { KnowledgePage } from './pages/Knowledge';
import { ProjectsPage } from './pages/Projects';
import { ResourcesPage } from './pages/Resources';
import { RunsPage } from './pages/Runs';
import { SystemPage } from './pages/System';

const navigation = [
  { title: '工程空间', items: [
    { id: 'overview', label: '工作台概览', icon: LayoutDashboard }, { id: 'projects', label: '工程项目', icon: FolderKanban },
    { id: 'revisions', label: '版本与基线', icon: GitBranch }, { id: 'runs', label: '运行与任务', icon: Play },
    { id: 'artifacts', label: '工件与源码', icon: Boxes }, { id: 'evidence', label: '验证证据', icon: FileCheck2 },
  ] },
  { title: '工程能力', items: [
    { id: 'device', label: '产品上位机', icon: Waves }, { id: 'knowledge', label: '工程知识库', icon: BookOpen },
    { id: 'quotes', label: '器件报价', icon: Wallet }, { id: 'changes', label: '变更审查', icon: GitBranch },
    { id: 'approvals', label: '批准记录', icon: ShieldCheck },
  ] },
  { title: '配置与管理', items: [
    { id: 'ecosystem', label: '模型与扩展生态', icon: Network }, { id: 'bindings', label: 'Agent 绑定', icon: Settings2 },
    { id: 'members', label: '项目成员', icon: Users }, { id: 'system', label: '系统健康', icon: Activity },
  ] },
];

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [setup, setSetup] = useState<SetupStatus | null>(null);
  const [booting, setBooting] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [boot, setBoot] = useState(0);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [route, navigate] = useHashRoute();
  const projects = useResource<ResourceList<Project>>(session ? '/projects?limit=100' : null, boot);
  useEffect(() => {
    const expired = () => { setSession(null); api.setCsrf(''); setProjectId(null); };
    window.addEventListener('fpga-session-expired', expired);
    return () => window.removeEventListener('fpga-session-expired', expired);
  }, []);
  useEffect(() => {
    let active = true;
    setBooting(true); setError(null);
    Promise.allSettled([api.get<SetupStatus>('/setup/status'), api.get<Session>('/auth/session')]).then(results => {
      if (!active) return;
      const [status, current] = results;
      if (status.status === 'fulfilled') setSetup(status.value.data);
      else setError(status.reason);
      if (current.status === 'fulfilled') { setSession(current.value.data); api.setCsrf(current.value.data.csrf_token); }
      else if (!(current.reason instanceof ApiFailure && current.reason.status === 401)) setError(current.reason);
      setBooting(false);
    });
    return () => { active = false; };
  }, [boot]);
  useEffect(() => {
    if (projects.data && !projects.data.items.some(project => project.id === projectId)) setProjectId(projects.data.items[0]?.id || null);
  }, [projects.data]);
  const authenticate = (value: Session) => { api.setCsrf(value.csrf_token); setSession(value); setError(null); setBoot(value => value + 1); };
  if (booting && !session) return <div className="boot-screen"><CircuitBoard size={35} /><h2>FPGA COLLAB</h2><Loading /></div>;
  if (!session) return <AuthScreen key={String(setup?.initialized)} initialized={setup?.initialized !== false} error={error} retry={() => setBoot(value => value + 1)} authenticated={authenticate} />;
  const current = projects.data?.items.find(project => project.id === projectId) || null;
  const select = (id: string, destination?: string) => { setProjectId(id); if (destination) navigate(destination); };
  const title = navigation.flatMap(group => group.items).find(item => item.id === route)?.label || '工程工作台';
  let content;
  if (route === 'overview' || route === 'projects') content = <ProjectsPage projects={projects.data?.items || []} loading={projects.loading} error={projects.error} teamId={setup?.team_id || ''} current={current} select={select} reload={projects.reload} overview={route === 'overview'} />;
  else if (route === 'runs') content = <RunsPage key={projectId} projectId={projectId} />;
  else if (route === 'device') content = <DevicePage key={projectId} projectId={projectId} />;
  else if (route === 'knowledge') content = <KnowledgePage key={projectId} projectId={projectId} />;
  else if (route === 'system') content = <SystemPage />;
  else if (route === 'ecosystem') content = session.global_role === 'admin' ? <EcosystemPage /> : <Empty title="需要全局管理员权限" text="模型、连接、扩展、账号与预算由全局管理员管理。项目角色不会授予全局管理权限。" />;
  else if (resources[route]) content = <ResourcesPage key={`${route}-${projectId}`} definition={resources[route]} projectId={projectId} />;
  else content = <Empty title="页面不存在" action={<button className="button" onClick={() => navigate('overview')}>返回概览</button>} />;
  return <div className="app-shell">
    {sidebarOpen && <button className="sidebar-overlay" aria-label="收起导航" onClick={() => setSidebarOpen(false)} />}
    <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`}><div className="brand"><span className="brand-icon"><CircuitBoard size={23} /></span><span>FPGA <strong>COLLAB</strong><small>协同开发平台</small></span><button className="mobile-close icon-button" aria-label="关闭导航" onClick={() => setSidebarOpen(false)}><X size={20} /></button></div><div className="workspace-label"><span className="tiny-dot" />本机工程空间<span>LOCAL</span></div><nav aria-label="主导航">{navigation.map(group => <div className="nav-group" key={group.title}><div className="nav-group-title">{group.title}</div>{group.items.map(item => <button key={item.id} className={`nav-item ${route === item.id ? 'active' : ''}`} onClick={() => { navigate(item.id); setSidebarOpen(false); }}><item.icon size={18} strokeWidth={1.7} /><span>{item.label}</span>{route === item.id && <ChevronRight size={14} />}</button>)}</div>)}</nav><div className="sidebar-bottom"><div className="local-status"><span className="tiny-dot" />本机运行 · 同源 API</div><button className="user-button" title={session.user_id} onClick={async () => { try { await api.delete('/auth/session'); api.setCsrf(''); setSession(null); setProjectId(null); } catch (reason) { setError(reason); } }}><span className="avatar">{session.global_role === 'admin' ? 'A' : 'U'}</span><span><strong>{session.global_role === 'admin' ? '平台管理员' : '工程用户'}</strong><small>{session.user_id.slice(0, 8)}</small></span><LogOut size={17} /></button></div></aside>
    <div className="main-shell"><header className="topbar"><button className="icon-button mobile-menu" aria-label="打开导航" onClick={() => setSidebarOpen(true)}><Menu size={21} /></button><div className="breadcrumbs"><span>工程空间</span><ChevronRight size={13} /><strong>{title}</strong></div><div className="topbar-right"><label className="project-select"><FolderKanban size={16} /><select aria-label="当前工程项目" value={projectId || ''} onChange={event => setProjectId(event.target.value || null)}><option value="">选择工程项目</option>{projects.data?.items.map(project => <option key={project.id} value={project.id}>{project.payload.name}</option>)}</select><ChevronDown size={13} /></label><span className="topbar-date">{new Date().toLocaleDateString('zh-CN', { month: 'long', day: 'numeric' })}</span></div></header><main className="main-content"><ErrorNotice error={error} retry={() => setBoot(value => value + 1)} />{projects.error instanceof ApiFailure && projects.error.status === 401 && <div className="error-notice"><p>会话已过期，请重新登录。</p><button className="button" onClick={() => { setSession(null); api.setCsrf(''); }}>返回登录</button></div>}{content}<footer className="page-footer"><span>FPGA COLLAB <span className="muted">/</span> 可信工程，从每一个事实开始。</span><span>本机工作台 · schema 1.0</span></footer></main></div>
  </div>;
}

function EcosystemPage() {
  const [tab, setTab] = useState('model-profiles');
  const entries = ['model-profiles', 'mcp-connections', 'skills', 'extensions', 'knowledge-connections', 'quote-connections', 'registries', 'budgets', 'users'];
  return <><div className="ecosystem-bar"><div><Network size={19} /><strong>扩展生态管理</strong></div><span><Search size={14} />配置 · 版本 · 权限 · 健康</span></div><div className="tabs ecosystem-tabs">{entries.map(key => <button key={key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}>{resources[key].label}</button>)}</div><ResourcesPage key={tab} definition={resources[tab]} projectId={null} /></>;
}
