import type { components } from './schema'

type Schemas = components['schemas']

export type Environment = Schemas['EnvironmentOut']
export type EnvironmentStatus = Schemas['EnvironmentStatus']
export type Operation = Schemas['OperationOut']
export type Template = Schemas['TemplateOut']
export type Estimate = Schemas['EstimateOut']
export type EnvironmentRequest = Schemas['EnvironmentCreate']
export type LogLine = Schemas['LogLineOut']

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message)
  }
}

// FastAPI returns {detail: "..."} for our own errors and {detail: [{msg, loc}, ...]}
// for request validation errors.
async function errorMessage(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json()
    const detail = (body as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      return detail.map((item: { msg?: string }) => item.msg ?? 'invalid value').join('; ')
    }
  } catch {
    // Not JSON: fall through to the status text.
  }
  return response.statusText || `HTTP ${response.status}`
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init.headers },
  })
  if (!response.ok) throw new ApiError(response.status, await errorMessage(response))
  return (await response.json()) as T
}

function post<T>(path: string, body?: unknown, headers?: Record<string, string>): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    body: body === undefined ? undefined : JSON.stringify(body),
    headers,
  })
}

export const api = {
  templates: () => request<Template[]>('/templates'),
  estimate: (body: EnvironmentRequest) =>
    post<Estimate>('/estimates', {
      template: body.template,
      size: body.size,
      ttl_hours: body.ttl_hours,
    }),
  environments: () => request<Environment[]>('/environments'),
  environment: (id: string) => request<Environment>(`/environments/${id}`),
  // The key makes a double submit return the first environment instead of a second one.
  create: (body: EnvironmentRequest, idempotencyKey: string) =>
    post<Environment>('/environments', body, { 'Idempotency-Key': idempotencyKey }),
  retry: (id: string) => post<Environment>(`/environments/${id}/retry`),
  destroy: (id: string) => post<Environment>(`/environments/${id}/destroy`),
  unlock: (id: string, lockId: string) =>
    post<Environment>(`/environments/${id}/unlock`, { lock_id: lockId }),
  logStreamUrl: (id: string, after: number) => `/api/environments/${id}/logs/stream?after=${after}`,
}
