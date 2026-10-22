import type { ApiErrorBody, Capabilities, HistorySession, ReviewItem, ReviewResult, Scenario, SessionState, SessionSummary, SupportLevel, SupportProgress, TaskVariant, TurnResult } from './types'

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public retryable = false) { super(message) }
}

function cookie(name: string) {
  return document.cookie.split('; ').find((row) => row.startsWith(`${name}=`))?.split('=').slice(1).join('=')
}

let csrfToken = ''
export function setCsrfToken(value: string) { csrfToken = value }

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = init.method?.toUpperCase() ?? 'GET'
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const csrf = decodeURIComponent(cookie('lc_csrf') ?? csrfToken)
    if (csrf) headers.set('X-CSRF-Token', csrf)
  }
  let response: Response
  try {
    response = await fetch(path, { ...init, headers, credentials: 'include' })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError(0, 'network_unavailable', '서버에 연결할 수 없어요. PC와 네트워크 연결을 확인해 주세요.', true)
  }
  if (!response.ok) {
    let body: ApiErrorBody = {}
    try { body = await response.json() as ApiErrorBody } catch { /* empty response */ }
    const detail = body.detail
    const message = typeof detail === 'string' ? detail : detail?.message
    throw new ApiError(response.status, typeof detail === 'object' ? detail.code ?? 'request_failed' : 'request_failed', message ?? `요청을 완료하지 못했어요. (${response.status})`, typeof detail === 'object' && Boolean(detail.retryable))
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export function pairingErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'invalid_pairing_code') return '코드를 잘못 입력했거나 만료·사용된 코드예요. PC에서 새 연결 코드를 발급받아 입력해 주세요.'
    if (error.code === 'pairing_rate_limited') return '연결 시도가 여러 번 실패해 잠시 제한됐어요. 최대 10분 후 새 연결 코드로 다시 시도해 주세요.'
    if (error.code === 'pairing_timeout') return '서버 응답이 늦어 연결을 중단했어요. 네트워크를 확인하고 다시 시도해 주세요. 코드가 이미 사용됐다면 새 코드를 발급받아 주세요.'
    if (error.status === 422) return 'PC에 표시된 영문·숫자 8자리 연결 코드를 확인해 주세요.'
    return error.message
  }
  return error instanceof Error ? error.message : '기기를 연결하지 못했어요. 다시 시도해 주세요.'
}

async function pairDevice(code: string) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 15_000)
  try {
    return await request<{ authenticated: true; csrf_token: string; expires_at: string }>('/api/auth/pair', { method: 'POST', body: JSON.stringify({ code }), signal: controller.signal })
  } catch (error) {
    if (controller.signal.aborted) throw new ApiError(504, 'pairing_timeout', '연결 응답이 늦어요.', true)
    throw error
  } finally { clearTimeout(timer) }
}

const id = () => crypto.randomUUID()
export function buildAudioForm(blob: Blob, question: SessionState['question'], retry: boolean) {
  const form = new FormData()
  const extension = blob.type.includes('mp4') ? 'm4a' : 'webm'
  form.append('file', blob, `answer.${extension}`)
  form.append('client_attempt_id', id())
  if (!retry) {
    if (question?.intent_id) form.append('intent_id', question.intent_id)
    form.append('task_variant', question?.task_variant ?? 'initial')
  }
  return form
}
export function buildReviewAudioForm(blob: Blob) {
  const form = new FormData()
  form.append('file', blob, blob.type.includes('mp4') ? 'review.m4a' : 'review.webm')
  form.append('client_attempt_id', id())
  return form
}
export const api = {
  capabilities: () => request<Capabilities>('/api/capabilities'),
  pair: pairDevice,
  logout: () => request<{ ok: true }>('/api/auth/logout', { method: 'POST' }),
  scenarios: () => request<{ items: Scenario[] }>('/api/scenarios'),
  createSession: (scenarioId: string, signal?: AbortSignal) => request<SessionState>('/api/sessions', { method: 'POST', body: JSON.stringify({ scenario_id: scenarioId, idempotency_key: id() }), signal }),
  session: (sessionId: string, signal?: AbortSignal) => request<SessionState>(`/api/sessions/${sessionId}`, { signal }),
  submitText: (sessionId: string, text: string, question: SessionState['question'], signal?: AbortSignal) => request<TurnResult>(`/api/sessions/${sessionId}/turns`, { method: 'POST', body: JSON.stringify({ text, client_attempt_id: id(), intent_id: question?.intent_id, task_variant: question?.task_variant ?? 'initial' }), signal }),
  submitAudio: (sessionId: string, blob: Blob, question: SessionState['question'], signal?: AbortSignal, retry = false) => {
    const form = buildAudioForm(blob, question, retry)
    return request<TurnResult>(`/api/sessions/${sessionId}/audio`, { method: 'POST', body: form, signal })
  },
  editTurn: (turnId: string, text: string, signal?: AbortSignal) => request<TurnResult>(`/api/turns/${turnId}`, { method: 'PATCH', body: JSON.stringify({ text, client_attempt_id: id() }), signal }),
  markTranscriptSuspect: (turnId: string, signal?: AbortSignal) => request<TurnResult>(`/api/turns/${turnId}/transcript-suspect`, { method: 'POST', body: JSON.stringify({ client_attempt_id: id() }), signal }),
  exposeAlternative: (turnId: string) => request<{ ok: true; assistance_level?: string }>(`/api/turns/${turnId}/alternative-exposure`, { method: 'POST', body: JSON.stringify({ client_attempt_id: id() }) }),
  hint: (turnId: string, signal?: AbortSignal) => request<{ hint: { level: 'H1'|'H2'|'H3'; content_type: string; content: string }; session: SessionState }>(`/api/turns/${turnId}/hints`, { method: 'POST', body: JSON.stringify({ client_attempt_id: id() }), signal }),
  retry: (turnId: string, text: string, signal?: AbortSignal) => request<TurnResult>(`/api/turns/${turnId}/retry`, { method: 'POST', body: JSON.stringify({ text, client_attempt_id: id() }), signal }),
  continueTurn: (turnId: string, signal?: AbortSignal) => request<SessionState | { session: SessionState }>(`/api/turns/${turnId}/continue`, { method: 'POST', body: JSON.stringify({ client_attempt_id: id() }), signal }),
  setSupport: (intentId: string, level: SupportLevel) => request<{ support_progress: SupportProgress }>(`/api/intents/${intentId}/support`, { method: 'POST', body: JSON.stringify({ level, client_attempt_id: id() }) }),
  pause: (sessionId: string, signal?: AbortSignal) => request<SessionState>(`/api/sessions/${sessionId}/pause`, { method: 'POST', body: JSON.stringify({ client_attempt_id: id() }), signal }),
  heartbeat: (sessionId: string, signal?: AbortSignal) => request<{ ok:true; expires_at:string; client_attempt_id:string }>(`/api/sessions/${sessionId}/heartbeat`, { method: 'POST', body: JSON.stringify({ client_attempt_id: id() }), signal }),
  skip: (sessionId: string, signal?: AbortSignal) => request<SessionState | { session: SessionState }>(`/api/sessions/${sessionId}/skip`, { method: 'POST', body: JSON.stringify({ client_attempt_id: id() }), signal }),
  resume: (sessionId: string, signal?: AbortSignal) => request<SessionState>(`/api/sessions/${sessionId}/resume`, { method: 'POST', body: JSON.stringify({ client_attempt_id: id() }), signal }),
  end: (sessionId: string, signal?: AbortSignal) => request<{ session: SessionState; summary: SessionSummary }>(`/api/sessions/${sessionId}/end`, { method: 'POST', body: JSON.stringify({ client_attempt_id: id() }), signal }),
  reviews: () => request<{ items: ReviewItem[] }>('/api/reviews/due?limit=3'),
  reviewHint: (reviewId: string) => request<{ hint: { level: 'H1'|'H2'|'H3'; content_type: string; content: string } }>(`/api/reviews/${reviewId}/hints`, { method: 'POST', body: JSON.stringify({ client_attempt_id: id() }) }),
  reviewAttempt: (reviewId: string, text: string, taskVariant: 'review'|'transfer' = 'transfer') => request<ReviewResult>(`/api/reviews/${reviewId}/attempts`, { method: 'POST', body: JSON.stringify({ text, client_attempt_id: id(), task_variant: taskVariant satisfies TaskVariant }) }),
  reviewAudio: (reviewId: string, blob: Blob, signal?: AbortSignal) => request<ReviewResult>(`/api/reviews/${reviewId}/audio`, { method: 'POST', body: buildReviewAudioForm(blob), signal }),
  exposeReviewAlternative: (reviewId: string) => request<{ ok:true }>(`/api/reviews/${reviewId}/alternative-exposure`, { method:'POST', body:JSON.stringify({ client_attempt_id:id() }) }),
  history: () => request<{ sessions: HistorySession[] }>('/api/history'),
  exportData: () => request<Record<string, unknown>>('/api/export'),
  importPreview: (data: unknown) => request<{ preview_id: string; counts: Record<string, number>; warnings: string[]; expires_at: string }>('/api/import/preview', { method: 'POST', body: JSON.stringify(data) }),
  importApply: (previewId: string) => request<{ counts: Record<string, number> }>('/api/import/apply', { method: 'POST', body: JSON.stringify({ preview_id: previewId, client_attempt_id: id() }) }),
  deleteSession: (sessionId: string, preserve = false) => request<{ deleted: true }>(`/api/sessions/${sessionId}?preserve_edited_expressions=${preserve}`, { method: 'DELETE', headers: { 'X-Confirm-Delete': 'session' } }),
  deleteAll: () => request<{ deleted: true; client_cache_action: 'clear' }>('/api/profile/data', { method: 'DELETE', headers: { 'X-Confirm-Delete': 'all-learning-data' } }),
}
