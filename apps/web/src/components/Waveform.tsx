import { useEffect, useRef, useState } from 'react';
import { waveformSegments } from '../device';
import { Empty } from './ui';

export function Waveform({ samples, channels, clockHz, divider, timingKnown }: { samples: (number | null)[]; channels: number; clockHz: number; divider: number; timingKnown: boolean }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const wrapper = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState(0);
  const [width, setWidth] = useState(800);
  const count = Math.max(1, Math.ceil(samples.length / zoom));
  const start = Math.min(offset, Math.max(0, samples.length - count));
  useEffect(() => {
    if (!wrapper.current) return;
    const observer = new ResizeObserver(entries => setWidth(Math.floor(entries[0].contentRect.width)));
    observer.observe(wrapper.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const element = canvas.current;
    if (!element || !samples.length) return;
    const ratio = window.devicePixelRatio || 1;
    const height = 42 + channels * 38;
    element.width = width * ratio; element.height = height * ratio;
    element.style.width = `${width}px`; element.style.height = `${height}px`;
    const context = element.getContext('2d');
    if (!context) return;
    context.scale(ratio, ratio);
    context.fillStyle = '#131e1a'; context.fillRect(0, 0, width, height);
    const left = 68; const plotWidth = Math.max(1, width - left - 20);
    context.font = '11px "Cascadia Code", monospace';
    for (let tick = 0; tick <= 8; tick++) {
      const x = left + tick * plotWidth / 8;
      context.strokeStyle = '#2c3932'; context.beginPath(); context.moveTo(x, 32); context.lineTo(x, height); context.stroke();
      context.fillStyle = '#85988d';
      const microseconds = (start + count * tick / 8) * divider / clockHz * 1e6;
      context.fillText(timingKnown ? `${microseconds.toFixed(2)}µs` : `#${Math.floor(start + count * tick / 8)}`, Math.min(x, width - 64), 19);
    }
    for (let channel = 0; channel < channels; channel++) {
      const top = 45 + channel * 38;
      const low = top + 21;
      context.fillStyle = '#b5c7bc'; context.fillText(`D${channel.toString().padStart(2, '0')}`, 17, top + 13);
      const segments = waveformSegments(samples, channel, start, count, plotWidth);
      for (const segment of segments) {
        const x = left + segment.x;
        if (segment.missing) { context.fillStyle = '#a4534e55'; context.fillRect(x, top - 3, 1, 29); }
        context.strokeStyle = channel % 3 === 0 ? '#f0b971' : channel % 3 === 1 ? '#83c5ac' : '#99b9e9';
        context.beginPath();
        if (segment.high) { context.moveTo(x, top); context.lineTo(x + 1, top); }
        if (segment.low) { context.moveTo(x, low); context.lineTo(x + 1, low); }
        if (segment.high && segment.low) { context.moveTo(x, top); context.lineTo(x, low); }
        const previous = segments[segment.x - 1];
        if (previous && !previous.missing && !segment.missing && (previous.high !== segment.high || previous.low !== segment.low)) { context.moveTo(x, top); context.lineTo(x, low); }
        context.stroke();
      }
    }
  }, [samples, channels, clockHz, divider, timingKnown, start, count, width]);
  const missing = samples.filter(value => value === null).length;
  return <div className="waveform" ref={wrapper}><div className="waveform-toolbar"><div><strong>数字波形</strong><span>{samples.length ? `${samples.length} 个位置 · 缺帧 ${missing}` : '等待真实采集分片'}</span></div><label>缩放<select value={zoom} onChange={event => setZoom(Number(event.target.value))}>{[1, 2, 4, 8, 16].map(value => <option value={value} key={value}>{value}×</option>)}</select></label></div>{samples.length ? <><canvas ref={canvas} aria-label={`${channels} 通道数字波形，${samples.length} 个采样位置，${missing} 个缺帧位置`} role="img" /><label className="waveform-pan"><span>采样偏移 {start}</span><input aria-label="波形平移" type="range" min="0" max={Math.max(0, samples.length - count)} value={start} onChange={event => setOffset(Number(event.target.value))} /></label><p className="waveform-note">{timingKnown ? `相对时间 t[n] = n × ${divider} / ${clockHz} s` : '未取得本次配置，仅显示采样序号'} · 红色区间表示数据缺失 · 无绝对测量时间</p></> : <Empty title="尚无采集数据" text="连接会话后读取能力、提交配置并启动采集。此处只绘制设备接口实际返回的样本。" />}</div>;
}
