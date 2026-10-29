export interface ApiResponse<T> { data: T; etag: string | null }
type Fetcher = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export class ApiFailure extends Error {
  constructor(public status: number, public code: string, message: string, public requestId = '', public details: unknown = null) {
    super(message);
    this.name = 'ApiFailure';
  }
}

export function parseApiFailure(status: number, body: unknown, fallback = '请求失败'): ApiFailure {
  const envelope = (body && typeof body === 'object' ? body : {}) as Record<string, unknown>;
  const error = (envelope.error && typeof envelope.error === 'object' ? envelope.error : envelope) as Record<string, unknown>;
  return new ApiFailure(status, String(error.code || `HTTP_${status}`), String(error.message || fallback), String(error.request_id || ''), error.field_errors || error.details);
}

export class ApiClient {
  private csrf = '';
  constructor(private fetcher: Fetcher = (...args) => fetch(...args)) {}
  setCsrf(value: string) { this.csrf = value; }

  async request<T>(path: string, method = 'GET', body?: unknown, options: { etag?: string; key?: string; signal?: AbortSignal } = {}): Promise<ApiResponse<T>> {
    const headers = new Headers({ Accept: 'application/json' });
    if (body !== undefined) headers.set('Content-Type', 'application/json');
    if (method !== 'GET') {
      if (this.csrf) headers.set('X-CSRF-Token', this.csrf);
      if (method === 'POST') headers.set('Idempotency-Key', options.key || crypto.randomUUID());
      if (options.etag) headers.set('If-Match', options.etag);
    }
    // 更新必须基于已读取版本；不以通配符绕过并发控制。
    if ((['PUT', 'PATCH'].includes(method) || (method === 'DELETE' && path !== '/auth/session')) && !options.etag) throw new ApiFailure(428, 'ETAG_REQUIRED', '缺少资源版本，请重新获取后再保存。');
    let response: Response;
    try {
      response = await this.fetcher(`/api/v1${path}`, {
        method, headers, credentials: 'same-origin',
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: options.signal || AbortSignal.timeout(20_000),
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') throw error;
      throw new ApiFailure(0, 'NETWORK_ERROR', '无法连接本机 API，或请求已超时。请检查服务，再重新获取状态。');
    }
    const raw = await response.text();
    let data: unknown = null;
    try { data = raw ? JSON.parse(raw) : null; } catch { data = { message: raw.slice(0, 600) }; }
    if (!response.ok) {
      if (response.status === 401 && !(method === 'POST' && path === '/auth/session')) window.dispatchEvent(new Event('fpga-session-expired'));
      throw parseApiFailure(response.status, data, response.statusText);
    }
    return { data: data as T, etag: response.headers.get('ETag') };
  }

  get<T>(path: string, signal?: AbortSignal) { return this.request<T>(path, 'GET', undefined, { signal }); }
  post<T>(path: string, body: unknown, key?: string, etag?: string) { return this.request<T>(path, 'POST', body, { key, etag }); }
  put<T>(path: string, body: unknown, etag: string) { return this.request<T>(path, 'PUT', body, { etag }); }
  patch<T>(path: string, body: unknown, etag: string) { return this.request<T>(path, 'PATCH', body, { etag }); }
  delete<T>(path: string, etag?: string) { return this.request<T>(path, 'DELETE', undefined, { etag }); }
}

export const api = new ApiClient();
export const projectPath = (id: string, collection = '') => `/projects/${encodeURIComponent(id)}${collection ? `/${collection}` : ''}`;
