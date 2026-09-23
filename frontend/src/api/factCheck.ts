/** 事实核查独立于审阅补丁；所有调用复用带鉴权的 request，不自动发起检索。 */
import request from '@/utils/request'

export type FactCheckSearchProvider = 'model' | 'tavily'

export interface FactCheckSource {
  id: string
  name: string
  domain: string
  path_prefix: string
  is_enabled: boolean
}
export interface FactCheckOptions {
  available: boolean
  unavailable_reason: string
  provider: FactCheckSearchProvider
  model_name: string
  max_claims: number
  max_text_chars: number
  daily_limit: number
  retention_days?: number
  sources: FactCheckSource[]
}
export type FactConsistencyStatus = 'match' | 'mismatch' | 'unknown' | 'not_applicable'
export interface FactConsistencyCheck { status: FactConsistencyStatus; reason: string }
/** 模型仅依据所提供正文评估，程序验证结构与保守约束，不独立验证语义。 */
export interface FactEvidenceChecks {
  subject: FactConsistencyCheck
  event_time: FactConsistencyCheck
  scope_unit: FactConsistencyCheck
}
export interface FactSearchSource {
  url: string
  title: string
  origin: 'search' | 'supplemental'
  status: 'pending' | 'fetched' | 'failed' | 'duplicate' | 'skipped'
  reason: string
  error_code: string | null
  evidence_id: string | null
}
export interface FactSearchRound {
  kind: 'initial' | 'counter' | 'followup'
  query: string
  status: 'pending' | 'searching' | 'fetching' | 'complete' | 'partial' | 'failed'
  pages_fetched: number
  error_codes: string[]
  sources?: FactSearchSource[] | null
}
export interface FactCheckEvidence {
  id: string
  title: string
  url: string
  quote: string
  published_at: string | null
  retrieved_at: string
  publisher: string
  stance: 'supports' | 'refutes' | 'context'
  // 仅为兼容缺少这些字段的历史报告；新模型判定在服务端强制要求 checks。
  checks?: FactEvidenceChecks | null
  body_sha256?: string | null
  body_hash_scope?: 'normalized_model_visible_text_utf8' | null
  body_text_length?: number | null
  quote_start?: number | null
  quote_end?: number | null
  context_before?: string | null
  context_after?: string | null
  body_text?: string | null
}
export interface FactCheckClaim {
  id: string
  original: string
  start: number
  end: number
  statement: string
  verdict: 'supported' | 'refuted' | 'insufficient' | 'conflicting'
  reason: string
  suggestion: string | null
  evidence: FactCheckEvidence[]
  checked: boolean
  search_rounds?: FactSearchRound[]
  selected?: boolean
  original_statement?: string | null
}
export interface FactCheckReport {
  claims: FactCheckClaim[]
  coverage: { extracted: number; checked: number; unverified: number; status: 'complete' | 'partial'; reason: string }
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number; search_queries: number; pages_fetched: number }
  checked_at: string
}
export type FactCheckMode = 'web' | 'trusted'
export interface FactCheckRun {
  id: number
  record_id: number | null
  title: string
  source_kind: 'text' | 'record' | 'document'
  file_id: string | null
  parent_run_id: number | null
  stage: 'extract' | 'check' | 'complete'
  depth: 'standard' | 'deep'
  confirm_claims: boolean
  max_claims: number
  mode: FactCheckMode
  provider: FactCheckSearchProvider
  status: 'PENDING' | 'RUNNING' | 'WAITING_CONFIRMATION' | 'SUCCESS' | 'FAILURE' | 'CANCELLED'
  progress: number
  message: string
  error_code: string | null
  result: FactCheckReport | null
  source_hash: string
  created_at: string
  finished_at: string | null
  source_ids: string[]
}
export interface CreateFactCheckPayload {
  record_id?: number
  text?: string
  file_id?: string
  title?: string
  confirm_claims?: boolean
  mode: FactCheckMode
  source_ids: string[]
  allow_external_search: true
  request_id: string
}
export interface FactCheckSettings {
  enabled: boolean
  model_config_id?: number | null
  provider: FactCheckSearchProvider
  /** 仅表示 Tavily 密钥已配置，不代表模型原生搜索能力。 */
  api_key_configured: boolean
  model_name: string
  /** 适配器端点支持；实际模型版本或账号仍可能在运行时拒绝。 */
  model_search_supported: boolean
  model_search_reason: string
  max_claims: number
  sources: FactCheckSource[]
}
export interface SaveFactCheckSettingsPayload {
  enabled: boolean
  model_config_id?: number | null
  provider: FactCheckSearchProvider
  api_key?: string
  max_claims: number
  sources: FactCheckSource[]
}
export interface FactCheckReview {
  id: number; run_id: number; user_id: number; claim_id: string
  decision: 'agree' | 'disagree' | 'unresolved'; note: string; request_id: string; created_at: string
}
export interface FactCheckRequestOptions { signal?: AbortSignal }

export function factCheckHistoryApi(params: { page: number; page_size: number; status?: string; q?: string }, options: FactCheckRequestOptions = {}): Promise<{ items: FactCheckRun[]; total: number }> {
  return request.get('/fact-check/history', { ...config(options), params })
}
export function factCheckSourceApi(id: number, options: FactCheckRequestOptions = {}): Promise<{ text: string; source_hash: string }> {
  return request.get(`/fact-check/runs/${id}/source`, config(options))
}
export function executeFactCheckApi(id: number, data: { claims: { id: string; statement: string }[]; request_id: string }): Promise<FactCheckRun> {
  return request.post(`/fact-check/runs/${id}/execute`, data, config({}))
}
export function deepenFactCheckApi(id: number, data: { claim_id: string; request_id: string; allow_external_search: true; supplemental_urls: string[] }): Promise<FactCheckRun> {
  return request.post(`/fact-check/runs/${id}/deepen`, data, config({}))
}
export function factCheckReviewsApi(id: number, options: FactCheckRequestOptions = {}): Promise<FactCheckReview[]> {
  return request.get(`/fact-check/runs/${id}/reviews`, config(options))
}
export function addFactCheckReviewApi(id: number, data: Pick<FactCheckReview, 'claim_id' | 'decision' | 'note' | 'request_id'>): Promise<FactCheckReview> {
  return request.post(`/fact-check/runs/${id}/reviews`, data, config({}))
}
export function exportFactCheckApi(id: number, format: 'html' | 'json'): Promise<Blob> {
  return request.get(`/fact-check/runs/${id}/export`, { ...config({}), params: { format }, responseType: 'blob' })
}
export function deleteFactCheckApi(id: number): Promise<void> {
  return request.delete(`/fact-check/runs/${id}`, config({}))
}
const config = (options: FactCheckRequestOptions) => ({ ...options, headers: { 'X-Silent-Error': 'true' } })

export function createFactCheckId(): string {
  // 内网 HTTP 页面也可使用 getRandomValues，而 randomUUID 只在安全上下文提供。
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

export function getFactCheckOptionsApi(options: FactCheckRequestOptions = {}): Promise<FactCheckOptions> {
  return request.get<FactCheckOptions, FactCheckOptions>('/fact-check/options', config(options))
}
export function listFactCheckRunsApi(recordId: number, options: FactCheckRequestOptions = {}): Promise<FactCheckRun[]> {
  return request.get<FactCheckRun[], FactCheckRun[]>('/fact-check/runs', { ...config(options), params: { record_id: recordId } })
}
export function createFactCheckRunApi(data: CreateFactCheckPayload, options: FactCheckRequestOptions = {}): Promise<FactCheckRun> {
  return request.post<FactCheckRun, FactCheckRun>('/fact-check/runs', {
    ...(data.record_id ? { record_id: data.record_id } : data.file_id ? { file_id: data.file_id } : { text: data.text }), title: data.title,
    confirm_claims: data.confirm_claims, mode: data.mode, source_ids: [...data.source_ids],
    allow_external_search: data.allow_external_search, request_id: data.request_id,
  }, config(options))
}
export function getFactCheckRunApi(id: number, options: FactCheckRequestOptions = {}): Promise<FactCheckRun> {
  return request.get<FactCheckRun, FactCheckRun>(`/fact-check/runs/${id}`, config(options))
}
export function cancelFactCheckRunApi(id: number, options: FactCheckRequestOptions = {}): Promise<FactCheckRun> {
  return request.post<FactCheckRun, FactCheckRun>(`/fact-check/runs/${id}/cancel`, undefined, config(options))
}
export function getFactCheckSettingsApi(options: FactCheckRequestOptions = {}): Promise<FactCheckSettings> {
  return request.get<FactCheckSettings, FactCheckSettings>('/admin/fact-check/settings', config(options))
}
export function saveFactCheckSettingsApi(data: SaveFactCheckSettingsPayload, options: FactCheckRequestOptions = {}): Promise<FactCheckSettings> {
  const apiKey = data.provider === 'tavily' ? data.api_key?.trim() : undefined
  return request.put<FactCheckSettings, FactCheckSettings>('/admin/fact-check/settings', {
    enabled: data.enabled, provider: data.provider, max_claims: data.max_claims, model_config_id: data.model_config_id ?? null,
    ...(apiKey ? { api_key: apiKey } : {}),
    sources: data.sources.map(source => ({
      id: source.id, name: source.name.trim(), domain: source.domain.trim(),
      path_prefix: source.path_prefix.trim() || '/', is_enabled: source.is_enabled,
    })),
  }, config(options))
}
