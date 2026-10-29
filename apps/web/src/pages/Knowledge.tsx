import { Search } from 'lucide-react';
import { useState } from 'react';
import { api, projectPath } from '../api';
import { resources } from '../catalog';
import { useResource } from '../hooks';
import type { Data, ResourceList } from '../types';
import { Empty, ErrorNotice, JsonView, PageHeading } from '../components/ui';
import { ResourcesPage } from './Resources';

export function KnowledgePage({ projectId }: { projectId: string | null }) {
  const [tab, setTab] = useState('search');
  return <><div className="tabs">{[['search', '知识检索'], ['collections', '知识集合'], ['documents', '来源文档']].map(([id, title]) => <button className={tab === id ? 'active' : ''} key={id} onClick={() => setTab(id)}>{title}</button>)}</div>{tab === 'search' ? <KnowledgeSearch key={projectId} projectId={projectId} /> : <ResourcesPage definition={resources[tab]} projectId={projectId} />}</>;
}

function KnowledgeSearch({ projectId }: { projectId: string | null }) {
  const collections = useResource<ResourceList>(projectId ? projectPath(projectId, 'collections?limit=100') : null);
  const [selected, setSelected] = useState<string[]>([]);
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<{ hits: Data[]; partial: boolean; warnings: string[] } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  if (!projectId) return <Empty title="先选择工程项目" />;
  return <><PageHeading eyebrow="KNOWLEDGE / TRACEABLE SOURCES" title="让每一条判断有据可查" description="检索只在选定集合与可访问来源中进行，保留引用位置、内容哈希与授权信息。" /><form className="panel search-panel" onSubmit={async event => { event.preventDefault(); setBusy(true); setError(null); setResult(null); try { const response = await api.post<{ hits: Data[]; partial: boolean; warnings: string[] }>(projectPath(projectId, 'knowledge/search'), { collection_ids: selected, query, top_k: 10, document_version_ids: [] }); setResult(response.data); } catch (reason) { setError(reason); } finally { setBusy(false); } }}><label className="field"><span>在工程资料中检索</span><div className="search-input"><Search size={19} /><input required value={query} onChange={event => setQuery(event.target.value)} placeholder="例如：采样深度和 UART 校验方式的约束" /><button className="button primary" disabled={busy || !selected.length}>{busy ? '检索中…' : '检索来源'}</button></div></label><fieldset className="collection-picker"><legend>选择知识集合（至少一个）</legend><ErrorNotice error={collections.error} retry={collections.reload} />{collections.data?.items.map(item => <label key={item.id}><input type="checkbox" checked={selected.includes(item.id)} onChange={event => setSelected(previous => event.target.checked ? [...previous, item.id] : previous.filter(id => id !== item.id))} />{String(item.payload.name)}</label>)}{collections.data?.items.length === 0 && <span className="muted">尚无集合，请在“知识集合”中创建。</span>}</fieldset></form><ErrorNotice error={error} />{result ? <><div className="section-title"><h2>检索结果 · {result.hits.length}</h2><small>{result.partial ? '部分返回' : '本次检索已返回'}</small></div>{result.warnings.map((warning, index) => <div className="stale-banner" key={index}>{warning}</div>)}{result.hits.length ? <div className="search-results">{result.hits.map((hit, index) => <article className="panel" key={index}><div className="eyebrow">来源 {index + 1} · {String(hit.locator || '')}</div><p className="source-text">{String(hit.text || '')}</p><details><summary>来源与证据元数据</summary><JsonView value={hit} /></details></article>)}</div> : <Empty title="没有匹配来源" text="可调整检索内容，或检查文档是否已完成解析。" />}</> : <Empty title="从一个工程问题开始" text="检索结果会显示原文、定位与来源证据。" />}</>;
}
