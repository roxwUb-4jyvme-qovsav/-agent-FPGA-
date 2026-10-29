import type { CaptureChunk } from './types';

export interface CaptureConfig { channels: number; triggerMode: number; triggerChannel: number; divider: number; mask: number; depth: number }
const hex = (bytes: Uint8Array) => Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');

export function encodeConfig(config: CaptureConfig): string {
  if (![8, 16].includes(config.channels) || ![10, 100].includes(config.divider)) throw new Error('通道或分频无效');
  if (!Number.isInteger(config.mask) || config.mask < 1 || config.mask >= 2 ** config.channels) throw new Error('通道掩码超出设备范围');
  if (!Number.isInteger(config.depth) || config.depth < 16 || config.depth > 4096 || (config.depth & (config.depth - 1)) !== 0) throw new Error('深度必须是 16–4096 的 2 次幂');
  if (![0, 1].includes(config.triggerMode) || !Number.isInteger(config.triggerChannel) || config.triggerChannel < 0 || config.triggerChannel >= config.channels) throw new Error('触发通道无效');
  const bytes = new Uint8Array(10);
  const view = new DataView(bytes.buffer);
  view.setUint8(0, config.triggerMode); view.setUint8(1, config.triggerChannel);
  view.setUint16(2, config.divider, true); view.setUint16(4, config.mask, true); view.setUint32(6, config.depth, true);
  return hex(bytes);
}

export function encodeRead(captureId: number, offset: number, count: number): string {
  if (!Number.isInteger(captureId) || captureId < 1 || captureId > 0xffffffff || !Number.isInteger(offset) || offset < 0 || offset > 4095 || !Number.isInteger(count) || count < 1 || count > 1012 || offset + count > 4096) throw new Error('采集编号或读取范围无效');
  const bytes = new Uint8Array(10); const view = new DataView(bytes.buffer);
  view.setUint32(0, captureId, true); view.setUint32(4, offset, true); view.setUint16(8, count, true);
  return hex(bytes);
}

export function mergeCaptureChunk(previous: (number | null)[], chunk: CaptureChunk, captureId: number): (number | null)[] {
  if (chunk.capture_id !== captureId) throw new Error('旧采集数据已拒绝，请读取当前采集');
  if (![1, 2].includes(chunk.sample_bytes) || chunk.samples_hex.length !== chunk.count_samples * chunk.sample_bytes * 2 || !/^([0-9a-f]{2})+$/i.test(chunk.samples_hex) || chunk.offset_samples + chunk.count_samples > 4096) throw new Error('采集分片长度或编码错误，数据未绘制');
  const next = Array.from({ length: Math.max(previous.length, chunk.offset_samples + chunk.count_samples) }, (_, i) => previous[i] ?? null);
  for (let i = 0; i < chunk.count_samples; i++) {
    let value = 0;
    for (let byte = 0; byte < chunk.sample_bytes; byte++) value |= parseInt(chunk.samples_hex.slice((i * chunk.sample_bytes + byte) * 2, (i * chunk.sample_bytes + byte + 1) * 2), 16) << (8 * byte);
    next[chunk.offset_samples + i] = value;
  }
  return next;
}

export function waveformSegments(samples: (number | null)[], channel: number, start: number, count: number, pixels: number) {
  const segments: { x: number; width: number; high: boolean; low: boolean; missing: boolean }[] = [];
  // 每像素聚合所有样本的高低状态，保留窄脉冲；缺帧单独标记，不生成虚假零值。
  for (let x = 0; x < pixels; x++) {
    const from = start + Math.floor(x * count / pixels);
    const to = start + Math.max(Math.floor((x + 1) * count / pixels), Math.floor(x * count / pixels) + 1);
    let high = false, low = false, missing = false;
    for (let i = from; i < to && i < start + count; i++) {
      const value = samples[i];
      if (value === null || value === undefined) missing = true;
      else if ((value >> channel) & 1) high = true;
      else low = true;
    }
    segments.push({ x, width: 1, high, low, missing });
  }
  return segments;
}
