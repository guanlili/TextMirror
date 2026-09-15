import request from '@/utils/request'

export type FeedbackKind = 'false_positive' | 'bad_suggestion' | 'preference' | 'other' | 'missed'
export type FeedbackStatus = 'pending' | 'confirmed' | 'rejected'

export const feedbackKindLabels: Record<FeedbackKind, string> = {
  false_positive: '原文无误', bad_suggestion: '建议不合适', preference: '保留原表达', other: '其他原因', missed: '漏检上报',
}

export interface QualityFeedbackCreate {
  record_id: number
  kind: FeedbackKind
  original: string
  suggestion: string
  issue_type: string
  start: number
  end: number
  note: string
}

export interface FeedbackSample {
  text: string
  domain: 'general' | 'official' | 'legal'
  start: number
  end: number
  original: string
  expectation: 'report' | 'no_report'
  issue_type: string
  /** 精确替换目标片段；省略等于 []，空字符串表示删除。 */
  accepted_suggestions?: string[]
  rejected_suggestions?: string[]
}

export interface FeedbackModelSnapshot {
  config_id: number | null
  config_name: string
  model: string
  success: boolean | null
  coverage_status: 'complete' | 'partial' | 'unknown'
  reported: boolean | null
}

export interface QualityFeedback extends Omit<QualityFeedbackCreate, 'record_id'> {
  id: number
  record_id: number | null
  user_id: number
  revision: number
  status: FeedbackStatus
  context_text: string
  context_start: number
  domain: string
  model_snapshot: FeedbackModelSnapshot[]
  sample: FeedbackSample | null
  review_note: string
  reviewer_id: number | null
  reviewed_at: string | null
  created_at: string
}

export interface FeedbackReviewRequest {
  revision: number
  status: 'confirmed' | 'rejected'
  sample: FeedbackSample | null
  review_note: string
}

export interface FeedbackEvaluationCase {
  feedback_id: number
  revision: number
  expectation: 'report' | 'no_report'
  status: 'pass' | 'fail' | 'error' | 'not_evaluated'
  detected: boolean | null
  detection_status: 'pass' | 'fail' | 'error'
  suggestion_status: 'pass' | 'fail' | 'not_evaluated'
  suggestion_reason: 'detection_error' | 'no_report' | 'no_constraints' | 'not_detected' | 'invalid_suggestion'
    | 'incomparable_context' | 'rejected' | 'not_accepted' | 'no_accepted_golden' | 'all_accepted'
  error: string | null
  elapsed_ms: number
}

export interface FeedbackEvaluation {
  generated_at: string
  samples: { id: number; revision: number; sample: FeedbackSample }[]
  results: {
    config_id: number
    config_name: string
    model: string
    cases: FeedbackEvaluationCase[]
    report_total: number
    report_evaluated: number
    missed: number
    no_report_total: number
    no_report_evaluated: number
    false_positives: number
    errors: number
    suggestion_evaluated: number
    suggestion_passed: number
    suggestion_failed: number
    suggestion_not_evaluated: number
  }[]
}

const adminPath = '/admin/global-dict/quality-feedback'

export function submitQualityFeedbackApi(data: QualityFeedbackCreate): Promise<QualityFeedback> {
  return request.post('/proofread/quality-feedback', data)
}

export function listQualityFeedbackApi(params: { status?: FeedbackStatus; page?: number; page_size?: number }): Promise<{ items: QualityFeedback[]; total: number }> {
  return request.get(adminPath, { params })
}

export function reviewQualityFeedbackApi(id: number, data: FeedbackReviewRequest): Promise<QualityFeedback> {
  return request.put(`${adminPath}/${id}`, data)
}

export function evaluateQualityFeedbackApi(data: { feedback_ids: number[]; config_ids: number[] }): Promise<FeedbackEvaluation> {
  return request.post(`${adminPath}/evaluate`, data, { timeout: 300000 })
}
