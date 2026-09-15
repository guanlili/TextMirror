/**
 * TextMirror 校对历史 API
 */
import request from '@/utils/request'
import type { ReviewIssue } from '@/utils/review'

export interface HistoryReviewMetadata {
  mode: 'single' | 'compare' | 'collaboration' | null
  coverage_status: 'complete' | 'partial' | 'unknown'
  review_summary: {
    total: number
    accepted: number
    ignored: number
    pending: number
    failed_models: number
  }
}

export interface HistoryItem extends HistoryReviewMetadata {
  id: number
  type: string
  domain: string
  total_issues: number
  text_preview: string
  source_filename?: string
  token_usage?: Record<string, number>
  created_at?: string
}

export interface HistoryListResponse {
  items: HistoryItem[]
  total: number
  page: number
  page_size: number
}

export interface HistoryDetail extends HistoryReviewMetadata {
  issues: ReviewIssue[]
  id: number
  type: string
  domain: string
  original_text: string
  modified_text?: string
  check_types?: string
  result?: {
    issues?: Array<{
      original: string
      type: string
      suggestion: string
      explanation: string
      severity: string
    }>
    versions?: Array<{ label: string; content: string }>
    [key: string]: unknown
  }
  total_issues: number
  source_filename?: string
  token_usage?: Record<string, number>
  created_at?: string
}

/** 今日使用量与配额 */
export interface TodayUsage {
  used_today: number
  daily_quota: number | null  // null = 不限
}

/** 获取今日使用量与配额 */
export function getTodayUsageApi(): Promise<TodayUsage> {
  return request.get('/history/usage')
}

/** 获取历史列表 */
export function listHistoryApi(params?: {
  page?: number
  page_size?: number
  type?: string
  domain?: string
}): Promise<HistoryListResponse> {
  return request.get('/history', { params })
}

/** 获取历史详情 */
export function getHistoryDetailApi(id: number): Promise<HistoryDetail> {
  return request.get(`/history/${id}`)
}

/** 删除历史记录 */
export function deleteHistoryApi(id: number): Promise<void> {
  return request.delete(`/history/${id}`)
}
