import { ArrowRight, ListChecks, Play, RefreshCw } from 'lucide-react';
import { useEffect, useState, type FormEvent } from 'react';
import { api, projectPath } from '../api';
import { useResource } from '../hooks';
import type { Data, Resource, ResourceList, TaskRecord } from '../types';
import { SchemaEditor, draftFor } from '../components/SchemaEditor';
import { EventPanel } from '../components/ProjectEvents';
import { dateTime, Empty, ErrorNotice, JsonView, Loading, Modal, PageHeading, shortId, Status } from '../components/ui';

export function RunsPage({ projectId }: { projectId: string | null }) {
  const base = projectId ? projectPath(projectId, 'runs') : null;
  const runs = useResource<ResourceList>(base ? `${base}?limit=100` : null);
  const revisions = useResource<ResourceList>(projectId ? projectPath(projectId, 'revisions?limit=100') : null);
  const [selected, setSelected] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [commandOpen, setCommandOpen] = useState(false);
  const [snapshotOpen, setSnapshotOpen] = useState(false);
  useEffect(() => { setSelected(null); setCreateOpen(false); }, [projectId]);
  const run = useResource<Resource>(base && selected ? `${base}/${selected}` : null);
  const tasks = useResource<ResourceList<TaskRecord>>(base && selected ? `${base}/${selected}/tasks` : null);
  const snapshot = useResource<Data>(base && selected && snapshotOpen ? `${base}/${selected}/snapshot` : null);
  const refresh = () => { runs.reload(); run.reload(); tasks.reload(); snapshot.reload(); };
  if (!base || !projectId) return <Empty title="先选择工程项目" />;
  const active = runs.data?.items.filter(item => ['QUEUED', 'RUNNING', 'WAITING_INPUT', 'LEASED', 'CANCEL_REQUESTED'].includes(item.state)).length;
  return <>
    <PageHeading eyebrow="ORCHESTRATION / RUNS" title="运行与任务" description="从输入快照到每一次执行尝试，追踪任务状态、阻断原因与独立结果。" actions={<><button className="button" onClick={refresh}><RefreshCw size={15} />刷新状态</button><button className="button primary" onClick={() => setCreateOpen(true)}><Play size={15} />发起运行</button></>} />
    <ErrorNotice error={runs.error} retry={runs.reload} />
    <div className="run-summary"><ListChecks size={18} /><span>本页 <strong>{runs.data?.items.length ?? '—'}</strong> 次运行</span><span className="divider" /><span><strong>{active ?? '—'}</strong> 个运行仍需跟进</span><small>无自动付费重试</small></div>
    <div className="runs-layout"><div className="panel run-list"><div className="table-caption"><span>运行记录</span><small>最近读取 {runs.loadedAt?.toLocaleTimeString('zh-CN') || '—'}</small></div>{runs.loading && !runs.data ? <Loading /> : runs.data?.items.length === 0 ? <Empty title="尚未发起运行" text="先准备并冻结工程版本，再发起模拟、变更或交付任务。" /> : runs.data?.items.map(item => <button className={`run-card ${selected === item.id ? 'selected' : ''}`} key={item.id} onClick={() => setSelected(item.id)}><div><span className="mono">RUN / {shortId(item.id)}</span><Status value={item.state} /></div><strong>{String(item.payload.mode || '工程运行')}</strong><small>版本 {String(item.payload.revision_id || '').slice(0, 8)} · 上限 ¥{String(item.payload.max_cost_cny ?? '未知')}</small><small>{dateTime(item.created_at)}</small></button>)}</div>
      <div className="panel run-detail">{!selected ? <Empty title="选择一条运行记录" text="任务图、问题与执行尝试会显示在这里。" /> : <>
        <div className="section-title"><div><h2>运行详情 <span className="mono muted">{shortId(selected)}</span></h2></div><div className="inline-actions"><button className="text-link" onClick={() => setSnapshotOpen(true)}>输入快照</button>{run.data && <button className="button" onClick={() => setCommandOpen(true)}>运行操作</button>}</div></div>
        <ErrorNotice error={run.error} retry={run.reload} />{run.data && <div className="run-info"><Status value={run.data.state} /><span>v{run.data.version}</span><span>更新于 {dateTime(run.data.updated_at)}</span></div>}
        <ErrorNotice error={tasks.error} retry={tasks.reload} />{tasks.loading ? <Loading /> : tasks.data?.items.length === 0 ? <Empty title="暂无任务" text="尚无调度任务，或调度条件尚未满足。请核对运行状态。" /> : <div className="task-list">{tasks.data?.items.map(task => <TaskCard key={task.task_id} projectId={projectId} runId={selected} task={task} refresh={refresh} />)}</div>}
      </>}</div></div>
    <EventPanel />
    {createOpen && <RunForm revisions={revisions.data?.items || []} error={revisions.error} close={() => setCreateOpen(false)} save={async data => { const result = await api.post<Resource>(base, data); setSelected(result.data.id); runs.reload(); }} />}
    {commandOpen && run.data && <SchemaEditor schemaName="RunsResourceCommand" title="运行操作" initial={{ ...draftFor('RunsResourceCommand'), expected_version: run.data.version }} close={() => setCommandOpen(false)} save={async data => { await api.post(`${base}/${selected}/commands`, { ...data, expected_version: run.data!.version }, undefined, run.etag || run.data!.etag); refresh(); }} />}
    {snapshotOpen && <Modal title="不可变运行输入快照" close={() => setSnapshotOpen(false)}><ErrorNotice error={snapshot.error} retry={snapshot.reload} />{snapshot.loading ? <Loading /> : <JsonView value={snapshot.data} />}</Modal>}
  </>;
}

function RunForm({ revisions, error, close, save }: { revisions: Resource[]; error: unknown; close: () => void; save: (data: Data) => Promise<void> }) {
  const [revision, setRevision] = useState('');
  const [mode, setMode] = useState('simulate');
  const [budget, setBudget] = useState('0');
  const [failure, setFailure] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => { event.preventDefault(); setBusy(true); setFailure(null); try { await save({ revision_id: revision, mode, max_cost_cny: budget }); close(); } catch (reason) { setFailure(reason); } finally { setBusy(false); } };
  return <Modal title="发起一次工程运行" close={close}><form onSubmit={submit}><ErrorNotice error={error || failure} /><label className="field"><span>工程版本 <em>*</em></span><select value={revision} required onChange={event => setRevision(event.target.value)}><option value="">选择已准备的版本</option>{revisions.map(item => <option key={item.id} value={item.id}>{shortId(item.id)} · v{item.version} · {item.state}</option>)}</select></label><label className="field"><span>任务目标</span><select value={mode} onChange={event => setMode(event.target.value)}><option value="simulate">仿真与验证</option><option value="change">变更重建</option><option value="deliver">工程交付</option></select></label><label className="field"><span>本次费用上限（元）</span><input value={budget} required inputMode="decimal" pattern="(0|[1-9][0-9]*)(\.[0-9]{1,6})?" onChange={event => setBudget(event.target.value)} /></label><p className="form-note">默认预算为 0。缺少模型、工具、审批或隔离环境时，平台会返回实际阻断原因。</p><div className="form-actions"><button type="button" className="button" onClick={close}>取消</button><button className="button primary" disabled={busy || !revisions.length}>{busy ? '正在提交…' : '创建运行'}</button></div></form></Modal>;
}

function TaskCard({ task, projectId, runId, refresh }: { task: TaskRecord; projectId: string; runId: string; refresh: () => void }) {
  const [answer, setAnswer] = useState<{ id: string; question: string } | null>(null);
  const [value, setValue] = useState('');
  const [unit, setUnit] = useState('');
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  return <article className="task-card"><div className="task-heading"><div className="task-icon"><ArrowRight size={17} /></div><div><h3>{task.envelope.agent_role}</h3><span className="mono muted">{shortId(task.task_id)} · 第 {task.envelope.attempt} 次尝试</span></div><Status value={task.state} /></div><p>{task.envelope.goal}</p>{task.waiting_reason && <div className="stale-banner">{task.waiting_reason}</div>}{task.questions.map(question => <div className="question" key={question.id}><div><strong>{question.blocking ? '待澄清 · 阻断' : '待澄清'}</strong><p>{question.question}</p>{question.answer && <small>已答：{question.answer}</small>}</div>{!question.answer && <button className="button" onClick={() => { setAnswer(question); setValue(''); setError(null); }}>回答</button>}</div>)}<details><summary>执行尝试与结果</summary><JsonView value={{ deadline: task.envelope.deadline, attempts: task.attempts, result: task.result }} /></details>{answer && <Modal title="回答任务问题" close={() => setAnswer(null)}><form onSubmit={async event => { event.preventDefault(); setBusy(true); setError(null); try { await api.post(projectPath(projectId, `runs/${runId}/tasks/${task.task_id}/answers`), { question_id: answer.id, revision_id: task.envelope.revision_id, value, unit: unit || null }); setAnswer(null); refresh(); } catch (reason) { setError(reason); } finally { setBusy(false); } }}><p>{answer.question}</p><ErrorNotice error={error} /><label className="field"><span>回答</span><textarea required value={value} onChange={event => setValue(event.target.value)} /></label><label className="field"><span>单位（可选）</span><input value={unit} onChange={event => setUnit(event.target.value)} /></label><div className="form-actions"><button type="button" className="button" onClick={() => setAnswer(null)}>取消</button><button className="button primary" disabled={busy}>提交回答</button></div></form></Modal>}</article>;
}
