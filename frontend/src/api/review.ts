/** 审阅草稿、显式版本和导出；不创建记录、不触发校对。 */
import request from '@/utils/request'
import type { ProofreadCoverage } from '@/api/proofread'
import type { CollaborationReport } from '@/api/collaboration'
import type { ReviewIssue } from '@/composables/useProofreadReview'
import { serializeCompareReview } from '@/utils/compareReview'

export interface ReviewCompareModel {
  config_id: number
  config_name: string
  model: string
  domain: string
  depth: string
  success: boolean
  error: string | null
  elapsed_ms: number
  issues: ReviewIssue[]
  coverage: ProofreadCoverage | null
}

export interface ReviewCompareState {
  results: ReviewCompareModel[]
}

export interface ReviewSnapshot {
  issues: ReviewIssue[]
  coverage?: ProofreadCoverage | null
  compare?: ReviewCompareState | null
  modified_text: string
}

export interface ReviewVersion extends ReviewSnapshot {
  id: string
  label: string
  created_at: string
}

export interface ReviewResponse extends ReviewSnapshot {
  record_id: number
  revision: number
  original_text: string
  source_file_id?: string | null
  source_filename?: string | null
  domain: string
  depth: string
  config_id?: number | null
  versions: ReviewVersion[]
  readonly collaboration?: CollaborationReport | null
}

/** restore 只回放本地审阅决策；接收方同时恢复 coverage，不自动写入服务端。 */
export interface ReviewRestorePayload {
  issues: ReviewIssue[]
  coverage: ProofreadCoverage | null
  compare?: ReviewCompareState | null
}

export interface SaveReviewPayload {
  revision: number
  issues: ReviewIssue[]
  coverage?: ProofreadCoverage | null
  compare?: ReviewCompareState | null
  depth?: string
  config_id?: number | null
}

export interface CreateReviewVersionPayload extends SaveReviewPayload {
  label?: string
}

export interface ReviewRequestOptions {
  signal?: AbortSignal
}

export interface ExportReviewPayload {
  revision: number
  version_id?: string
  format: 'docx' | 'txt'
}

interface ReviewError {
  message?: string
  response?: { status?: number; data?: unknown }
}

function detailText(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map(item => {
    if (item && typeof item === 'object' && 'msg' in item) return detailText(item.msg)
    return detailText(item)
  }).filter(Boolean).join('；')
  return ''
}

export function getReviewErrorDetail(error: unknown): string {
  const err = error as ReviewError | null
  const data = err?.response?.data
  const detail = data && typeof data === 'object' && 'detail' in data ? detailText(data.detail) : ''
  return detail || err?.message || '请求失败，请重试'
}

/** 保留原错误、HTTP 状态和拒绝语义，让调用方能明确处理 409/422。 */
async function reviewRequest<T>(promise: Promise<T>): Promise<T> {
  try {
    return await promise
  } catch (error) {
    const err = error as ReviewError | null
    const data = err?.response?.data
    if (typeof Blob !== 'undefined' && data instanceof Blob && err?.response) {
      try {
        err.response.data = JSON.parse(await data.text())
        err.message = getReviewErrorDetail(err)
      } catch {
        // 非 JSON 的错误响应仍抛出原错误，不把失败当成可下载的文件。
      }
    }
    throw error
  }
}

// 页面/组件显示错误（尤其 Blob detail），避免请求拦截器先弹出无意义的通用错误。
const silentHeaders = { 'X-Silent-Error': 'true' }

export function getReviewApi(recordId: number, options: ReviewRequestOptions = {}): Promise<ReviewResponse> {
  return reviewRequest(request.get<ReviewResponse, ReviewResponse>(`/history/${recordId}/review`, {
    ...options, headers: silentHeaders,
  }))
}

function wireIssues(issues: readonly ReviewIssue[]) {
  return issues.map(issue => ({
    original: issue.original,
    suggestion: issue.suggestion,
    type: issue.type,
    severity: issue.severity,
    explanation: issue.explanation,
    chunk_index: issue.chunk_index,
    start: issue.start == null || issue.start < 0 ? null : issue.start,
    end: issue.end == null || issue.end < 0 ? null : issue.end,
    _accepted: issue._accepted,
    _ignored: issue._ignored,
  }))
}

function wireReview(data: SaveReviewPayload) {
  return {
    revision: data.revision, issues: wireIssues(data.issues),
    ...('coverage' in data ? { coverage: data.coverage } : {}),
    ...('compare' in data ? { compare: serializeCompareReview(data.compare) } : {}),
    ...('depth' in data ? { depth: data.depth } : {}),
    ...('config_id' in data ? { config_id: data.config_id } : {}),
  }
}

export function saveReviewApi(recordId: number, data: SaveReviewPayload, options: ReviewRequestOptions = {}): Promise<ReviewResponse> {
  return reviewRequest(request.put<ReviewResponse, ReviewResponse>(`/history/${recordId}/review`, wireReview(data), {
    ...options, headers: silentHeaders,
  }))
}

export function createReviewVersionApi(recordId: number, data: CreateReviewVersionPayload, options: ReviewRequestOptions = {}): Promise<ReviewResponse> {
  return reviewRequest(request.post<ReviewResponse, ReviewResponse>(`/history/${recordId}/versions`, { ...wireReview(data), ...('label' in data ? { label: data.label } : {}) }, {
    ...options, headers: silentHeaders,
  }))
}

export function exportReviewApi(recordId: number, data: ExportReviewPayload, options: ReviewRequestOptions = {}): Promise<Blob> {
  return reviewRequest(request.post<Blob, Blob>(`/history/${recordId}/export`, data, {
    ...options, responseType: 'blob', headers: silentHeaders,
  }))
}

export function exportDocumentReviewApi(fileId: string, data: { issues: ReviewIssue[]; format: 'docx' | 'txt' }, options: ReviewRequestOptions = {}): Promise<Blob> {
  return reviewRequest(request.post<Blob, Blob>(`/document/${encodeURIComponent(fileId)}/export`, { ...data, issues: wireIssues(data.issues) }, {
    ...options, responseType: 'blob', headers: silentHeaders,
  }))
}
