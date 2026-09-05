/**
 * TextMirror 校对相关 API
 */
import request from '@/utils/request'

export interface ProofreadIssue {
  original: string
  type: string
  suggestion: string
  explanation: string
  severity: string
  chunk_index: number
}

export interface TextProofreadResponse {
  issues: ProofreadIssue[]
  total_issues: number
  chunks_count: number
  usage: Record<string, number>
  domain: string
  check_types: string[]
  record_id?: number
}

/**
 * 文本校对
 */
export function textProofreadApi(data: {
  text: string
  check_types?: string[]
  domain?: string
  config_id?: number
}): Promise<TextProofreadResponse> {
  return request.post('/proofread/text', data, { timeout: 300000 })
}

/** 审校建议反馈上报 */
export interface IssueFeedbackPayload {
  record_id?: number
  items: Array<{
    original: string
    suggestion?: string
    issue_type?: string
    action: 'accept' | 'ignore'
  }>
}

export function submitIssueFeedbackApi(data: IssueFeedbackPayload): Promise<{ saved: number }> {
  return request.post('/proofread/feedback', data)
}

/** 多模型对比：单模型校对结果 */
export interface ModelProofreadResult {
  config_id: number
  config_name: string
  model: string
  issues: (ProofreadIssue & { _accepted?: boolean; _ignored?: boolean })[]
  total_issues: number
  success: boolean
  error?: string
  elapsed_ms: number
}

/** 多模型对比响应 */
export interface ProofreadCompareResponse {
  results: ModelProofreadResult[]
  consensus_originals: string[]
  only_in: Record<string, string[]>
}

/** 多模型并发校对对比 */
export function proofreadCompareApi(data: {
  text: string
  check_types?: string[]
  domain?: string
  config_ids: number[]
}): Promise<ProofreadCompareResponse> {
  return request.post('/proofread/compare', data, { timeout: 300000 })
}
