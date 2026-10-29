import { useState, type FormEvent } from 'react';
import schemasJson from '../schemas.json';
import type { Data, Schema } from '../types';
import { ErrorNotice, Modal } from './ui';

const schemas = schemasJson as unknown as Record<string, Schema>;
const labels: Record<string, string> = {
  name: '名称', description: '说明', team_id: '团队 ID', parent_id: '父版本', revision_id: '工程版本',
  requirement_artifact_id: '需求工件 ID', contract_artifact_id: '契约工件 ID', acceptance_artifact_id: '验收规范工件 ID',
  mode: '运行模式', max_cost_cny: '本次费用上限（元）', display_name: '显示名称', provider_kind: '服务商类型',
  base_url: 'API 基址', model_id: '模型标识', secret_ref: '密钥引用 ID', enabled: '启用', daily_budget_cny: '每日预算（元）',
  context_limit: '上下文长度', max_output_tokens: '最大输出 Token', timeout_seconds: '超时（秒）', capabilities: '能力',
  transport: '传输方式', endpoint: '端点', kind: '类型', permissions: '申请权限', source_ref: '来源', version: '版本号',
  content_artifact_id: '内容工件 ID', content_sha256: '内容 SHA256', license: '许可证', dependencies: '依赖',
  collection_id: '知识集合', collection_ids: '知识集合 ID', query: '检索内容', top_k: '返回条数',
  embedding_profile_version_id: '嵌入模型版本 ID', embedding_dimension: '嵌入维度', acl_member_ids: '可访问成员 ID', external_connection_id: '外部连接 ID',
  capture_artifact_id: '回放采集工件 ID', board_profile_id: '板卡配置 ID',
  reason: '操作原因', target_version_id: '目标版本 ID', artifact_id: '工件 ID', approval_id: '批准 ID', preserve_history: '保留历史',
  action: '操作', target_id: '目标 ID', target_sha256: '目标 SHA256', expires_at: '有效期',
  supplier: '供应商', manufacturer: '制造商', mpn: '制造商料号', package: '封装', currency: '币种',
  price_breaks: '阶梯价', moq: '最小订购量', order_multiple: '订购倍数', stock_qty: '库存', lead_days: '交期（天）',
  observed_at: '采集时间', source_uri: '来源', data_mode: '数据模式', evidence_sha256: '证据 SHA256', reuse_policy: '复用政策',
  username: '用户名', password: '密码', global_role: '全局角色', role: '项目角色', user_id: '用户 ID',
  schema_version: 'Schema 版本', scope: '范围', daily_limit_cny: '每日上限（元）', per_run_limit_cny: '每次运行上限（元）',
};

export function resolveSchema(schema: Schema): Schema {
  if (schema.$ref) return resolveSchema(schemas[schema.$ref.split('/').pop()!] || {});
  return schema;
}

export function draftFor(schemaName: string): Data {
  const draft = (input: Schema, key = ''): unknown => {
    const schema = resolveSchema(input);
    if (schema.const !== undefined) return schema.const;
    if (schema.anyOf?.some(item => item.type === 'null')) return null;
    if (schema.enum) return schema.enum[0];
    if (schema.type === 'object') return Object.fromEntries(Object.entries(schema.properties || {}).map(([name, child]) => [name, draft(child, name)]));
    if (schema.type === 'array') return Array.from({ length: schema.minItems || 0 }, () => draft(schema.items || {}));
    if (schema.type === 'integer' || schema.type === 'number') return schema.minimum || 0;
    if (schema.type === 'boolean') return false;
    if (schema.format === 'uuid' && key === 'id') return crypto.randomUUID();
    if (key.includes('budget') || key.includes('cost') || key.includes('limit_cny')) return '0';
    return '';
  };
  return draft(schemas[schemaName] || { type: 'object' }) as Data;
}

export function SchemaEditor({ schemaName, initial, title, close, save }: { schemaName: string; initial?: Data; title: string; close: () => void; save: (data: Data) => Promise<void> }) {
  const [values, setValues] = useState<Data>(initial || draftFor(schemaName));
  const [raw, setRaw] = useState(JSON.stringify(values, null, 2));
  const [advanced, setAdvanced] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const schema = resolveSchema(schemas[schemaName] || { type: 'object' });
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError(null); setBusy(true);
    try { const data = advanced ? JSON.parse(raw) : values; if (!data || Array.isArray(data) || typeof data !== 'object') throw new Error('请求必须是 JSON 对象'); await save(data); close(); }
    catch (reason) { setError(reason); } finally { setBusy(false); }
  };
  return <Modal title={title} close={close}><form onSubmit={submit}><div className="editor-toolbar"><span>契约：{schemaName}</span><button type="button" className="text-link" onClick={() => { if (!advanced) setRaw(JSON.stringify(values, null, 2)); else { try { setValues(JSON.parse(raw)); } catch { setError(new Error('请先修正 JSON')); return; } } setAdvanced(!advanced); }}>{advanced ? '切换字段表单' : '高级 JSON 编辑'}</button></div><ErrorNotice error={error} />{advanced ? <label className="field"><span>请求 JSON</span><textarea className="code-input large" value={raw} onChange={event => setRaw(event.target.value)} spellCheck={false} rows={18} /></label> : <div className="schema-fields">{Object.entries(schema.properties || {}).map(([name, input]) => {
    const resolved = resolveSchema(input);
    const nullable = resolved.anyOf?.some(item => item.type === 'null');
    const field = nullable ? resolveSchema(resolved.anyOf!.find(item => item.type !== 'null')!) : resolved;
    const value = values[name];
    const required = schema.required?.includes(name) && !nullable;
    const change = (next: unknown) => setValues(current => ({ ...current, [name]: next }));
    const label = <span>{labels[name] || name}{required && <em>*</em>}<small>{name}</small></span>;
    if (field.type === 'array' || field.type === 'object' || field.oneOf || field.anyOf) return <JsonField key={name} label={label} value={value} change={change} />;
    if (field.const !== undefined) return <label className="field" key={name}>{label}<input value={String(field.const)} readOnly /></label>;
    if (field.type === 'boolean') return <label className="field checkbox-field" key={name}><input type="checkbox" checked={value === true} onChange={event => change(event.target.checked)} />{label}</label>;
    if (field.enum) return <label className="field" key={name}>{label}<select value={value == null ? '' : String(value)} onChange={event => change(event.target.value || null)}>{nullable && <option value="">未指定</option>}{field.enum.map(option => <option key={String(option)} value={String(option)}>{String(option)}</option>)}</select></label>;
    return <label className={`field ${name === 'description' ? 'wide' : ''}`} key={name}>{label}<input required={required} type={name === 'password' ? 'password' : field.type === 'integer' || field.type === 'number' ? 'number' : 'text'} min={field.minimum} max={field.maximum} minLength={field.minLength} value={value == null ? '' : String(value)} placeholder={nullable ? '留空表示未指定' : field.format === 'uuid' ? '输入真实资源 UUID' : undefined} readOnly={busy} onChange={event => change(event.target.value === '' && nullable ? null : field.type === 'integer' || field.type === 'number' ? Number(event.target.value) : event.target.value)} /></label>;
  })}</div>}<p className="form-note">保存由服务端校验。配置登记不会自动执行工具或产生验收通过结论。</p><div className="form-actions"><button type="button" className="button" onClick={close} disabled={busy}>取消</button><button className="button primary" disabled={busy}>{busy ? '正在提交…' : '保存到平台'}</button></div></form></Modal>;
}

function JsonField({ label, value, change }: { label: React.ReactNode; value: unknown; change: (value: unknown) => void }) {
  const [text, setText] = useState(JSON.stringify(value, null, 2));
  const [invalid, setInvalid] = useState(false);
  return <label className="field wide">{label}<textarea className="code-input" rows={3} value={text} aria-invalid={invalid} onChange={event => { setText(event.target.value); try { change(JSON.parse(event.target.value)); setInvalid(false); } catch { setInvalid(true); } }} required onInvalid={() => setInvalid(true)} />{invalid && <small className="field-error">JSON 格式无效，请修正后保存</small>}<input className="json-validity" aria-hidden="true" tabIndex={-1} value={invalid ? '' : 'valid'} required readOnly /></label>;
}
