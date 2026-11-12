export interface Member {
  email: string
  workspace_id: string
  workspace_name: string
}

export interface DocumentRow {
  id: string
  filename: string
  pages: number
  status: 'queued' | 'processing' | 'ready' | 'failed'
  progress: number
  error: string | null
  created_at: string
}

export interface UploadSettings {
  max_bytes: number
  max_pages: number
  embedding_provider: string | null
}

export interface Provider {
  name: string
  label: string
  ready: boolean
}

export interface Citation {
  number: number
  document_id: string
  source: string
  page: number
  quote: string
  verified: boolean
  preview_url: string | null
}

export interface Answer {
  status: 'answered' | 'not_found' | 'unverified' | 'invalid_output'
  text: string
  partial: boolean
  clarify?: boolean
  searched_for: string
  reason: string
  citations: Citation[]
  passages: { document_id: string; source: string; page: number; text: string }[]
  judgement: { verdict: 'supported' | 'partial' | 'unsupported' | 'error'; judge: string; problems: string[] } | null
}

export interface UsageRow {
  day: string
  channel: string
  channel_label?: string
  questions: number
  answered: number
  not_found: number
  blocked: number
}

/** Short codes the API sends with some errors, next to the friendly message in `detail`. */
export type ErrorCode = 'rate_limited' | 'provider_unavailable' | 'misconfigured' | 'too_many_requests'
  | 'request_too_large' | 'workspace_full' | 'uploads_pending' | 'server_busy' | 'pdf_busy'

export class ApiError extends Error {
  constructor(public status: number, message: string, public code: ErrorCode | null = null) {
    super(message)
  }
}

const errorCodes = new Set<string>([
  'rate_limited', 'provider_unavailable', 'misconfigured', 'too_many_requests',
  'request_too_large', 'workspace_full', 'uploads_pending', 'server_busy', 'pdf_busy',
])

const prefix = '/api/v1'

async function request(path: string, options: RequestInit = {}): Promise<Response> {
  const headers = new Headers(options.headers)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (options.method && options.method !== 'GET') {
    const csrf = document.cookie.split('; ').find((value) => value.startsWith('__Host-sourced_csrf='))
    if (csrf) headers.set('X-CSRF-Token', csrf.slice('__Host-sourced_csrf='.length))
  }
  const response = await fetch(prefix + path, { ...options, headers, credentials: 'same-origin' })
  if (!response.ok) {
    if (response.status === 401 && path !== '/auth/login' && path !== '/auth/signup') {
      window.dispatchEvent(new Event('sourced:session-expired'))
    }
    const body = await response.json().catch(() => null)
    const detail = body?.detail
    const fieldError = Array.isArray(detail) && typeof detail[0]?.msg === 'string' ? detail[0].msg : ''
    const code = typeof body?.code === 'string' && errorCodes.has(body.code) ? body.code as ErrorCode : null
    throw new ApiError(response.status, typeof detail === 'string' ? detail : fieldError || 'Something went wrong. Try again.', code)
  }
  return response
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await request(path, options)
  return response.status === 204 ? (undefined as T) : response.json()
}

export function post<T>(path: string, data?: object): Promise<T> {
  return api<T>(path, { method: 'POST', body: data && JSON.stringify(data) })
}

export function documentPath(id: string) {
  return '/documents/' + encodeURIComponent(id)
}

export function citationPaths(citation: Citation) {
  const { document_id, page } = citation
  if (!/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(document_id) || !Number.isSafeInteger(page) || page < 1) {
    return null
  }
  const path = documentPath(document_id)
  return {
    preview: prefix + path + '/pages/' + page + '.png',
    highlight: path + '/pages/' + page + '/highlight',
    pdf: prefix + path + '/pdf#page=' + page,
  }
}

export async function highlightedPage(path: string, quote: string, signal: AbortSignal) {
  const response = await request(path, { method: 'POST', body: JSON.stringify({ quote }), signal })
  if (response.headers.get('Content-Type')?.split(';')[0] !== 'image/png') throw new Error('Invalid preview.')
  return response.blob()
}

export function errorMessage(error: unknown) {
  return error instanceof ApiError ? error.message : 'Could not reach Sourced. Try again.'
}
