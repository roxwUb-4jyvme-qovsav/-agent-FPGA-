import { CheckCircle2, Database, Monitor, RefreshCw, Server, ShieldCheck, Terminal } from 'lucide-react';
import { useResource } from '../hooks';
import type { Data, Health } from '../types';
import { Empty, ErrorNotice, JsonView, Loading, PageHeading, Status } from '../components/ui';

export function SystemPage() {
  const health = useResource<Health>('/health');
  const system = useResource<Data>('/system/status');
  const checks = health.data ? [
    { label: '数据库', ready: health.data.database_ready, icon: Database, description: '持久化与事务连接' },
    { label: '任务调度器', ready: health.data.scheduler_ready, icon: Server, description: '任务租约与 Worker' },
    { label: 'EDA 工具链', ready: health.data.eda_ready, icon: Terminal, description: 'RTL 仿真与综合环境' },
    { label: 'CAD 工具链', ready: health.data.cad_ready, icon: Monitor, description: '原理图与网表验证' },
  ] : [];
  return <>
    <PageHeading eyebrow="LOCAL SYSTEM / OBSERVABILITY" title="系统健康与实现状态" description="以服务探测和开发记录为依据。工具未登记、未安装或未运行的部分保持可见。" actions={<button className="button" onClick={() => { health.reload(); system.reload(); }}><RefreshCw size={15} />重新检测</button>} />
    <ErrorNotice error={health.error} retry={health.reload} />
    {health.loading ? <Loading /> : health.data && <><div className="system-banner"><ShieldCheck size={28} /><div><strong>本机 API · {health.data.api_version}</strong><p>服务与工程工具分别检测；连接正常不代表工程验收通过。</p></div><Status value={health.data.status} /></div><div className="health-grid">{checks.map(check => <div className="panel health-card" key={check.label}><check.icon size={23} /><h3>{check.label}</h3><p>{check.description}</p><span className={`status ${check.ready ? 'good' : 'warn'}`}><i />{check.ready ? '服务报告就绪' : '未就绪'}</span></div>)}</div></>}
    <section className="panel"><div className="section-title"><h2><CheckCircle2 size={17} />开发诊断</h2><span className="muted">/system/status</span></div><ErrorNotice error={system.error} retry={system.reload} />{system.loading ? <Loading /> : system.data ? <SystemStatus value={system.data} /> : !system.error && <Empty title="尚无诊断数据" />}</section>
    <div className="system-footnote"><strong>真实验收边界</strong><p>前端构建、虚拟采集和单元测试均不能替代 PostgreSQL 并发验收、独立工具验证或硬件实测。未执行项目应记录为 NOT_RUN。</p></div>
  </>;
}

function SystemStatus({ value }: { value: Data }) {
  return <>{Object.entries(value).map(([key, content]) => <details className="diagnostic-section" open={typeof content !== 'object' || key === 'tools'} key={key}><summary>{key}</summary>{content && typeof content === 'object' ? <JsonView value={content} /> : <p>{String(content)}</p>}</details>)}</>;
}
