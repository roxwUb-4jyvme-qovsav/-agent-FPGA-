import type { ApiClient } from './api';
import type { CaptureConfig } from './device';
import type { CommandResult } from './types';

export async function submitDeviceCommand(client: ApiClient, sessionPath: string, body: { action: string; sequence: number; payload_hex: string }): Promise<CommandResult> {
  const accepted = await client.post<{ resource_id: string }>(`${sessionPath}/device-commands`, body);
  return (await client.get<CommandResult>(`${sessionPath}/commands/${encodeURIComponent(accepted.data.resource_id)}`)).data;
}

// 用户显式选择的测试输入：先低后高，覆盖上升沿触发与最大采集窗口。
export function mockStimulus(config: CaptureConfig) {
  return [
    { value: 0, cycles: config.divider * 2 },
    { value: config.mask, cycles: config.divider * (config.depth + 2) },
  ];
}
