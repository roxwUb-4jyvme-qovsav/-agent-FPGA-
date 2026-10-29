import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useResource } from './hooks';

afterEach(() => { vi.unstubAllGlobals(); });

it('当前项目事件合并刷新 GET，其他项目事件不触发请求', async () => {
  const fetcher = vi.fn(async () => new Response('{"version":1}'));
  vi.stubGlobal('fetch', fetcher);
  const { unmount } = renderHook(() => useResource('/projects/p/runs'));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
  act(() => window.dispatchEvent(new CustomEvent('fpga-project-refresh', { detail: 'other' })));
  await new Promise(resolve => setTimeout(resolve, 150));
  expect(fetcher).toHaveBeenCalledTimes(1);
  act(() => { for (let i = 0; i < 5; i++) window.dispatchEvent(new CustomEvent('fpga-project-refresh', { detail: 'p' })); });
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  unmount();
});

it('刷新发现权限已撤销时清空旧数据和旧 ETag', async () => {
  let denied = false;
  vi.stubGlobal('fetch', vi.fn(async () => denied
    ? new Response('{"error":{"code":"NOT_FOUND","message":"资源不存在"}}', { status: 404 })
    : new Response('{"private":"previously-authorized"}', { headers: { ETag: '"old-version"' } })));
  const { result, unmount } = renderHook(() => useResource('/projects/p/runs/r'));
  await waitFor(() => expect(result.current.data).not.toBeNull());
  denied = true;
  act(() => window.dispatchEvent(new CustomEvent('fpga-project-refresh', { detail: 'p' })));
  await waitFor(() => expect(result.current.error).not.toBeNull());
  expect(result.current.data).toBeNull();
  expect(result.current.etag).toBeNull();
  unmount();
});
