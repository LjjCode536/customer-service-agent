import { useRuntime } from '../store/useRuntime'

export const API_BASE = '/api/v1'

export class ApiError extends Error {
  status: number
  detail: unknown

  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

/** Security Context headers are read at call time, so context switches apply immediately. */
export function securityHeaders(): Record<string, string> {
  const { user, tenant, role } = useRuntime.getState()
  return {
    'x-user-id': user,
    'x-tenant-id': tenant,
    'x-user-role': role,
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'
  body?: unknown
  headers?: Record<string, string>
  signal?: AbortSignal
}

function errorMessage(status: number, payload: unknown): string {
  if (payload && typeof payload === 'object' && 'detail' in payload) {
    const detail = (payload as { detail: unknown }).detail
    return typeof detail === 'string' ? detail : JSON.stringify(detail)
  }
  if (typeof payload === 'string' && payload.trim()) {
    // Non-JSON bodies (proxy HTML, gateway pages) are not useful verbatim.
    return payload.trim().startsWith('<') ? `HTTP ${status}（服务返回非 JSON 响应）` : `HTTP ${status}: ${payload.slice(0, 160)}`
  }
  return `HTTP ${status}`
}

/** Read an error body once: a non-JSON proxy/HTML page must not mask the status. */
async function responseError(response: Response): Promise<ApiError> {
  const raw = await response.text()
  let payload: unknown = raw
  if (raw) {
    try {
      payload = JSON.parse(raw)
    } catch {
      payload = raw
    }
  }
  return new ApiError(response.status, errorMessage(response.status, payload), payload)
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...securityHeaders(),
    ...options.headers,
  }
  let body: string | undefined
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(options.body)
  }

  const response = await fetch(`${API_BASE}${path}`, {
    method: options.method ?? 'GET',
    headers,
    body,
    signal: options.signal,
  })

  if (!response.ok) throw await responseError(response)

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

/** Multipart upload: the browser sets the multipart boundary, so no Content-Type. */
export async function upload<T>(path: string, formData: FormData): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: securityHeaders(),
    body: formData,
  })

  if (!response.ok) throw await responseError(response)

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) => request<T>(path, { signal }),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: 'POST', body }),
  patch: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PATCH', body }),
}

/** Authenticated file download: security headers are required, so a plain anchor cannot be used. */
export async function downloadFile(path: string, filename: string): Promise<void> {
  const response = await fetch(`${API_BASE}${path}`, { headers: securityHeaders() })
  if (!response.ok) throw await responseError(response)
  const blob = await response.blob()
  const objectUrl = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(objectUrl)
}
