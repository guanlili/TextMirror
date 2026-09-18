/**
 * TextMirror 文档校对相关 API
 */
import request from '@/utils/request'
import type { ProofreadIssue, ProofreadCoverage } from './proofread'

export interface DocumentUploadResponse {
  file_id: string
  filename: string
  file_size: number
  file_ext: string
  text_length: number
  text_preview: string
}

export interface DocumentProofreadResponse {
  file_id: string
  filename: string
  issues: ProofreadIssue[]
  total_issues: number
  chunks_count: number
  usage: Record<string, number>
  domain: string
  record_id?: number
  corrected_download_url?: string
  depth?: string
  config_id?: number | null
  coverage?: ProofreadCoverage | null
}

/**
 * 上传文档
 */
export function uploadDocumentApi(
  file: File,
  signal?: AbortSignal,
): Promise<DocumentUploadResponse> {
  const formData = new FormData()
  formData.append('file', file)
  return request.post('/document/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    signal,
    timeout: 60000,
  })
}

/**
 * 文档校对
 */
export function documentProofreadApi(data: {
  file_id: string
  check_types?: string[]
  domain?: string
  config_id?: number
  depth?: string
}): Promise<DocumentProofreadResponse> {
  return request.post('/document/proofread', data)
}

/**
 * 获取文档提取的全文（会话恢复用）
 */
export function fetchExtractedTextApi(fileId: string): Promise<{ file_id: string; extracted_text: string; extracted_html: string }> {
  return request.get(`/document/${fileId}/extracted-text`)
}

/**
 * 导出修订文本为 Word
 */
export function exportRevisedTextApi(
  fileId: string,
  data: { text: string; filename: string },
): Promise<Blob> {
  return request.post(`/document/${fileId}/export-revised-text`, data, { responseType: 'blob' })
}

export interface ReportIssueItem {
  type: string
  severity: string
  original: string
  suggestion: string
  explanation?: string
  context?: string
  status: string
}

export interface ReportCoverage {
  total_chunks: number
  completed_chunks: number
  failed_chunks: { start: number; end: number; error_code?: string }[]
}

/**
 * 导出问题报告为 Word
 */
export function exportReportApi(
  fileId: string,
  data: {
    filename: string
    status: string
    total_issues: number
    accepted_count: number
    ignored_count: number
    pending_count: number
    coverage?: ReportCoverage | null
    issues: ReportIssueItem[]
  },
): Promise<Blob> {
  return request.post(`/document/${fileId}/export-report`, data, { responseType: 'blob' })
}
