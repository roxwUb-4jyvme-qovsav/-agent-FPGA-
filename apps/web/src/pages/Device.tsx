import { CircleStop, Cpu, Play, Radio, RefreshCw } from 'lucide-react';
import { useEffect, useState, type FormEvent } from 'react';
import { api, parseApiFailure, projectPath } from '../api';
import { encodeConfig, encodeRead, mergeCaptureChunk, type CaptureConfig } from '../device';
import { useResource } from '../hooks';
import type { CaptureChunk, CommandResult, Data, Resource, ResourceList } from '../types';
import { Waveform } from '../components/Waveform';
import { Empty, ErrorNotice, JsonView, Loading, Modal, PageHeading, shortId, Status } from '../components/ui';

export function DevicePage({ projectId }: { projectId: string | null }) {
  const base = projectId ? projectPath(projectId, 'device-sessions') : null;
  const sessions = useResource<ResourceList>(base ? `${base}?limit=100` : null);
  const revisions = useResource<ResourceList>(projectId ? projectPath(projectId, 'revisions?limit=100') : null);
  const [selected, setSelected] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [config, setConfig] = useState<CaptureConfig>({ channels: 8, triggerMode: 0, triggerChannel: 0, divider: 10, mask: 255, depth: 256 });
  const [clockHz, setClockHz] = useState(100_000_000);
  const [capabilities, setCapabilities] = useState<Data | null>(null);
  const [deviceStatus, setDeviceStatus] = useState<Data | null>(null);
  const [captureId, setCaptureId] = useState(0);
  const [samples, setSamples] = useState<(number | null)[]>([]);
  const [sequence, setSequence] = useState(() => crypto.getRandomValues(new Uint16Array(1))[0]);
  const [commands, setCommands] = useState<CommandResult[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [readOffset, setReadOffset] = useState(0);
  const [readCount, setReadCount] = useState(256);
  const [configured, setConfigured] = useState<CaptureConfig | null>(null);
  const captures = useResource<{ items: Data[]; next_cursor: string | null }>(base && selected ? `${base}/${selected}/captures` : null);
  useEffect(() => { setSelected(''); }, [projectId]);
  useEffect(() => { setSamples([]); setCapabilities(null); setDeviceStatus(null); setCaptureId(0); setCommands([]); setError(null); setConfigured(null); }, [selected]);
  const selectedSession = sessions.data?.items.find(item => item.id === selected);

  const applyResult = (result: CommandResult, submittedConfig?: CaptureConfig) => {
    setCommands(previous => [result, ...previous.filter(item => item.command_id !== result.command_id)].slice(0, 20));
    if (result.error) throw parseApiFailure(0, result.error, '设备命令失败');
    if (result.state !== 'COMPLETED' || !result.reply) return;
    const { action, data } = result.reply;
    if (action === 'get_caps') {
      const channels = Number(data.channels);
      setCapabilities(data); setClockHz(Number(data.clock_hz));
      setConfig(previous => ({ ...previous, channels, mask: 2 ** channels - 1, triggerChannel: Math.min(previous.triggerChannel, channels - 1) }));
    } else if (action === 'status') { setDeviceStatus(data); setCaptureId(Number(data.capture_id)); }
    else if (action === 'arm') { setCaptureId(Number(data.capture_id)); setSamples([]); setDeviceStatus(null); }
    else if (action === 'set_config') { setConfigured(submittedConfig || null); setSamples([]); setCaptureId(0); setDeviceStatus(null); }
    else if (action === 'abort') { setSamples([]); setDeviceStatus(null); }
    else if (action === 'read_capture') {
      const chunk = data as unknown as CaptureChunk;
      setSamples(mergeCaptureChunk(samples, chunk, captureId));
      captures.reload();
    }
  };

  const send = async (action: string, payload = '') => {
    if (!base || !selected) return;
    setBusy(true); setError(null);
    try {
      const payloadHex = action === 'set_config' ? encodeConfig(config) : payload;
      const result = await api.post<CommandResult>(`${base}/${selected}/device-commands`, { action, sequence, payload_hex: payloadHex });
      setSequence(value => (value + 1) & 0xffff);
      applyResult(result.data, action === 'set_config' ? { ...config } : undefined);
    } catch (reason) { setError(reason); } finally { setBusy(false); }
  };

  if (!base || !projectId) return <Empty title="先选择工程项目" />;
  return <>
    <PageHeading eyebrow="INSTRUMENT / LOGIC ANALYZER" title="产品上位机" description="能力协商、配置、采集与数字波形。虚拟模式与真实硬件结果始终明确区分。" actions={<><button className="button" onClick={sessions.reload}><RefreshCw size={15} />刷新会话</button><button className="button primary" onClick={() => setCreateOpen(true)}><Radio size={15} />新建会话</button></>} />
    <ErrorNotice error={sessions.error} retry={sessions.reload} />
    <div className="panel device-session-bar"><Cpu size={20} /><label>采集会话<select value={selected} onChange={event => setSelected(event.target.value)}><option value="">选择会话</option>{sessions.data?.items.map(item => <option key={item.id} value={item.id}>{shortId(item.id)} · {String(item.payload.mode)} · {item.state}</option>)}</select></label>{selectedSession && <><Status value={selectedSession.state} /><span className="mode-tag">{String(selectedSession.payload.mode) === 'mock' ? '虚拟模式 · 非硬件实测' : String(selectedSession.payload.mode)}</span></>}</div>
    {selected ? <><ErrorNotice error={error} /><div className="instrument-layout"><aside className="panel capture-config"><div className="section-title"><h2>采集配置</h2><span className="version-tag">capture/1.0</span></div><div className="inline-actions"><button className="button" disabled={busy} onClick={() => send('get_caps')}>读取能力</button><button className="button" disabled={busy} onClick={() => send('status')}>读取状态</button></div>{deviceStatus && <div className="device-live-state"><Status value={String(deviceStatus.state)} /><small>采样 {String(deviceStatus.sample_count)} · 错误 {String(deviceStatus.error_code)}</small></div>}
      <label className="field"><span>设备通道</span><input readOnly value={capabilities ? `${config.channels} 路 · ${clockHz / 1e6} MHz` : '尚未读取能力'} /></label>
      <div className="form-two"><label className="field"><span>分频</span><select value={config.divider} onChange={event => setConfig({ ...config, divider: Number(event.target.value) })}><option value={10}>10 · 10 MHz</option><option value={100}>100 · 1 MHz</option></select></label><label className="field"><span>采样深度</span><select value={config.depth} onChange={event => { setConfig({ ...config, depth: Number(event.target.value) }); setReadCount(Math.min(256, Number(event.target.value))); }}>{[16, 32, 64, 128, 256, 512, 1024, 2048, 4096].map(depth => <option value={depth} key={depth}>{depth}</option>)}</select></label></div>
      <label className="field"><span>触发方式</span><select value={config.triggerMode} onChange={event => setConfig({ ...config, triggerMode: Number(event.target.value) })}><option value={0}>软件触发</option><option value={1}>指定通道上升沿</option></select></label><label className="field"><span>触发通道</span><select value={config.triggerChannel} disabled={config.triggerMode === 0} onChange={event => setConfig({ ...config, triggerChannel: Number(event.target.value) })}>{Array.from({ length: config.channels }, (_, channel) => <option value={channel} key={channel}>D{channel.toString().padStart(2, '0')}</option>)}</select></label>
      <label className="field"><span>通道掩码（十六进制）</span><input className="mono" value={config.mask.toString(16)} pattern="[0-9a-fA-F]+" onChange={event => { const value = parseInt(event.target.value, 16); setConfig({ ...config, mask: Number.isFinite(value) ? value : 0 }); }} /></label>
      <button className="button full-width" disabled={busy || !capabilities} onClick={() => send('set_config')}>提交配置</button><div className="capture-actions"><button className="button primary" disabled={busy || !capabilities} onClick={() => send('arm')}><Play size={15} />ARM 采集</button><button className="button danger" disabled={busy} onClick={() => send('abort')}><CircleStop size={15} />中止</button></div>
      <p className="form-note">提交配置和启动新采集会使旧设备缓存失效。UART：115200 / 8N1。</p>
    </aside><div className="capture-main"><Waveform samples={samples} channels={config.channels} clockHz={clockHz} divider={configured?.divider || config.divider} timingKnown={configured !== null} /><form className="panel read-controls" onSubmit={event => { event.preventDefault(); try { void send('read_capture', encodeRead(captureId, readOffset, readCount)); } catch (reason) { setError(reason); } }}><label className="field"><span>采集 ID</span><input type="number" min="1" max="4294967295" required value={captureId || ''} onChange={event => { setCaptureId(Number(event.target.value)); setSamples([]); }} /></label><label className="field"><span>起始样本</span><input type="number" min="0" max="4095" required value={readOffset} onChange={event => setReadOffset(Number(event.target.value))} /></label><label className="field"><span>读取数量</span><input type="number" min="1" max={config.channels === 16 ? 506 : 1012} required value={readCount} onChange={event => setReadCount(Number(event.target.value))} /></label><button className="button" disabled={busy}>读取分片</button></form><div className="panel command-log"><div className="section-title"><h2>命令与响应</h2><small>下一序号 {sequence}</small></div>{commands.length ? commands.map(command => <div className="command-row" key={command.command_id}><span className="mono">#{command.sequence} {command.action}</span><Status value={command.state} />{['QUEUED', 'RUNNING'].includes(command.state) && <button className="text-link" disabled={busy} onClick={async () => { setBusy(true); try { const result = await api.get<CommandResult>(`${base}/${selected}/commands/${command.command_id}`); applyResult(result.data); } catch (reason) { setError(reason); } finally { setBusy(false); } }}>获取结果</button>}<details><summary>详情</summary><JsonView value={command} /></details></div>) : <p className="muted">尚未发送设备命令。</p>}</div></div></div><section className="panel"><div className="section-title"><h2>已保存采集</h2><button className="text-link" onClick={captures.reload}>刷新</button></div><ErrorNotice error={captures.error} retry={captures.reload} />{captures.loading ? <Loading /> : captures.data?.items.length ? captures.data.items.map((capture, index) => <details key={String(capture.id || index)}><summary>采集 {String(capture.capture_id || capture.id || index + 1)}</summary><JsonView value={capture} /></details>) : <p className="muted">平台尚未返回保存记录。设备分片与持久化采集记录分别展示。</p>}</section></> : <div className="panel"><Empty title="连接你的工程信号" text="新建虚拟、RTL 仿真、回放或硬件会话，然后选择会话开始采集。未配置的设备能力会显示具体错误。" /></div>}
    {createOpen && <DeviceSessionForm revisions={revisions.data?.items || []} error={revisions.error} close={() => setCreateOpen(false)} save={async data => { const result = await api.post<Resource>(base, data); sessions.reload(); setSelected(result.data.id); }} />}
  </>;
}

function DeviceSessionForm({ revisions, error, close, save }: { revisions: Resource[]; error: unknown; close: () => void; save: (data: Data) => Promise<void> }) {
  const [revision, setRevision] = useState('');
  const [mode, setMode] = useState('mock');
  const [artifact, setArtifact] = useState('');
  const [board, setBoard] = useState('');
  const [failure, setFailure] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => { event.preventDefault(); setBusy(true); setFailure(null); try { await save({ revision_id: revision, mode, capture_artifact_id: artifact || null, board_profile_id: board || null }); close(); } catch (reason) { setFailure(reason); } finally { setBusy(false); } };
  return <Modal title="建立采集会话" close={close}><form onSubmit={submit}><ErrorNotice error={error || failure} /><label className="field"><span>工程版本</span><select required value={revision} onChange={event => setRevision(event.target.value)}><option value="">选择版本</option>{revisions.map(item => <option value={item.id} key={item.id}>{shortId(item.id)} · {item.state}</option>)}</select></label><label className="field"><span>采集模式</span><select value={mode} onChange={event => setMode(event.target.value)}><option value="mock">虚拟采集 · 非硬件实测</option><option value="rtl_simulation">RTL 仿真</option><option value="replay">工件回放</option><option value="hardware">真实硬件</option></select></label>{mode === 'replay' && <label className="field"><span>采集工件 ID</span><input required value={artifact} onChange={event => setArtifact(event.target.value)} /></label>}{mode === 'hardware' && <label className="field"><span>板卡配置 ID</span><input required value={board} onChange={event => setBoard(event.target.value)} /></label>}<p className="form-note">真实硬件与 RTL 仿真需要后端对应工具和设备。接口失败会保留错误，不切换成虚拟数据。</p><div className="form-actions"><button type="button" className="button" onClick={close}>取消</button><button className="button primary" disabled={busy || !revisions.length}>建立会话</button></div></form></Modal>;
}
