import type { ModelProofreadResult, ProofreadCompareResponse } from '@/api/proofread'
import type { ReviewCompareState } from '@/api/review'
import { expandReviewIssues, reviewIssueKey, serializeReviewIssues, type ReviewIssue } from '@/utils/review'

type CompareReports = { results: readonly Omit<ModelProofreadResult, 'total_issues'>[] }

export function serializeCompareReview(compare?: CompareReports | null): ReviewCompareState | null {
  if (!compare) return null
  return {
    results: compare.results.map(result => ({
      config_id: result.config_id,
      config_name: result.config_name,
      model: result.model,
      domain: result.domain || 'general',
      depth: result.depth || 'standard',
      success: result.success,
      error: result.error ?? null,
      elapsed_ms: result.elapsed_ms,
      issues: serializeReviewIssues(result.issues).map(issue => ({
        original: issue.original,
        suggestion: issue.suggestion,
        type: issue.type,
        severity: issue.severity,
        explanation: issue.explanation || '',
        chunk_index: issue.chunk_index ?? undefined,
        start: issue.start == null || issue.start < 0 ? null : issue.start,
        end: issue.end == null || issue.end < 0 ? null : issue.end,
        _accepted: false,
        _ignored: false,
      })),
      coverage: result.coverage ? JSON.parse(JSON.stringify(result.coverage)) : null,
    })),
  }
}

export function restoreCompareReview(source: string, snapshot: CompareReports): ProofreadCompareResponse {
  const reports = serializeCompareReview(snapshot)!
  const results = reports.results.map(result => {
    const issues = expandReviewIssues(source, result.issues)
    return { ...result, issues, total_issues: issues.length }
  })
  return { results, consensus_originals: [], only_in: {} }
}

export function modelReviewIssues(reports: readonly ReviewIssue[], decisions: readonly ReviewIssue[]): ReviewIssue[] {
  const pool = new Map(decisions.map(issue => [reviewIssueKey(issue), issue]))
  return reports.map(report => {
    const decision = pool.get(reviewIssueKey(report))
    return { ...report, _accepted: !!decision?._accepted, _ignored: !!decision?._ignored, _patch: decision?._patch }
  })
}

export function compareCoverageLabel(compare: CompareReports): string {
  if (compare.results.some(result => !result.success || result.coverage?.status === 'partial')) return '部分模型未完成'
  if (compare.results.some(result => !result.coverage)) return '部分模型未记录覆盖范围，无法确认全文完成'
  return '所有模型已完成'
}
