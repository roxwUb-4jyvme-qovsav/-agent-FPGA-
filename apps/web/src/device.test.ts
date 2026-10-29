import { describe, expect, it } from 'vitest';
import { encodeConfig, encodeRead, mergeCaptureChunk, waveformSegments } from './device';

describe('采集协议和波形数据', () => {
  it('16 通道上升沿配置按小端编码，通道 15 不被截断', () => {
    expect(encodeConfig({ channels: 16, triggerMode: 1, triggerChannel: 15, divider: 10, mask: 65535, depth: 4096 })).toBe('010f0a00ffff00100000');
  });
  it('拒绝通道之外的掩码、非法深度和分频', () => {
    expect(() => encodeConfig({ channels: 8, triggerMode: 0, triggerChannel: 0, divider: 10, mask: 65535, depth: 16 })).toThrow('掩码');
    expect(() => encodeConfig({ channels: 8, triggerMode: 0, triggerChannel: 0, divider: 10, mask: 255, depth: 17 })).toThrow('深度');
  });
  it('读取请求使用采集 ID 和采样偏移，不以字节偏移代替', () => {
    expect(encodeRead(7, 256, 16)).toBe('07000000000100001000');
  });
  it('分片保留缺帧与原样本，在不同 capture_id 时拒绝混合', () => {
    const values = mergeCaptureChunk([], { capture_id: 9, offset_samples: 2, count_samples: 2, sample_bytes: 2, samples_hex: '00800100' }, 9);
    expect(values).toEqual([null, null, 32768, 1]);
    expect(() => mergeCaptureChunk(values, { capture_id: 8, offset_samples: 0, count_samples: 1, sample_bytes: 1, samples_hex: 'ff' }, 9)).toThrow('旧采集');
  });
  it('像素聚合仍保留窄脉冲和缺帧，不把缺帧补零', () => {
    const result = waveformSegments([0, 1, 0, null, 1, 1], 0, 0, 6, 2);
    expect(result.some(segment => segment.high && segment.low)).toBe(true);
    expect(result.some(segment => segment.missing)).toBe(true);
  });
});
