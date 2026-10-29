import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AuthScreen } from './AuthScreen';

describe('登录与首次初始化', () => {
  it('初始化显示密码要求，确认不一致时不发送请求', async () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
    render(<AuthScreen initialized={false} error={null} retry={() => {}} authenticated={() => {}} />);
    fireEvent.change(screen.getByLabelText('管理员用户名'), { target: { value: 'owner' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'long-password-123' } });
    fireEvent.change(screen.getByLabelText('确认密码'), { target: { value: 'different-password' } });
    fireEvent.submit(screen.getByRole('button', { name: '初始化并进入工作台' }).closest('form')!);
    expect(await screen.findByText('两次密码不一致')).toBeInTheDocument();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('登录失败保留明确错误，不进入工作台', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"code":"INVALID_LOGIN","message":"账号或密码错误"}', { status: 401 })));
    const authenticated = vi.fn();
    render(<AuthScreen initialized error={null} retry={() => {}} authenticated={authenticated} />);
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'engineer' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'invalid' } });
    fireEvent.submit(screen.getByRole('button', { name: '登录工作台' }).closest('form')!);
    await waitFor(() => expect(screen.getByText('账号或密码错误')).toBeInTheDocument());
    expect(authenticated).not.toHaveBeenCalled();
  });
});
