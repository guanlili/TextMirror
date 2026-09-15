import { describe, expect, it, vi } from 'vitest'
vi.mock('@/api/proofread', () => ({ submitIssueFeedbackApi: vi.fn() }))
import type { CollaborationReport } from '@/api/collaboration'
import { collaborationCoverageLabel, collaborationFindingMap, collaborationProvenance, findCollaborationFinding } from '../collaboration'
import { expandReviewIssues, serializeReviewIssues } from '../review'
import { useProofreadReview } from '@/composables/useProofreadReview'

const original = '𠮷帐号帐号'
const finding = { original: '帐号', suggestion: '账号', start: 1, end: 3, type: 'typo', severity: 'warning', found_by: ['language', 'consistency'], review_status: 'disputed' as const, review_note: '需按上下文确认' }
const report: CollaborationReport = Object.freeze({ status: 'partial', roles: [], findings: Object.freeze([Object.freeze(finding)]), reviewed_count: 1, review_limit: 20, config_id: 2, model_name: 'model' })

describe('immutable provenance join', () => {
  it('survives edits, save serialization and restore without extending ReviewIssue', () => {
    const before = JSON.stringify(report)
    const issues = expandReviewIssues(original, report.findings)
    issues[0]._accepted = true
    const saved = serializeReviewIssues(issues)
    expect(saved[0]).not.toHaveProperty('found_by')
    const review = useProofreadReview()
    review.restore(original, saved)
    const joined = findCollaborationFinding(review.issues.value[0], collaborationFindingMap(report))!
    expect(joined).toBe(finding)
    expect(collaborationProvenance(joined, report)).toContain('语言审校 / 一致性审校 · 存在争议：需按上下文确认')
    expect(JSON.stringify(report)).toBe(before)
  })
  it.each([
    { start: 3, end: 5 }, { suggestion: '账户' }, { type: 'style' }, { original: '账户' }, { end: 4 },
  ])('does not conflate occurrences or different proposals: %j', change => {
    expect(findCollaborationFinding({ ...finding, ...change }, collaborationFindingMap(report))).toBeUndefined()
  })
  it('matches normalized unlocated findings without inventing a source for another issue', () => {
    const missing = { ...finding, start: null, end: null }
    const findings = collaborationFindingMap({ ...report, findings: [missing] })
    expect(findCollaborationFinding({ ...missing, start: -1, end: -1 }, findings)).toBe(missing)
    expect(collaborationProvenance({ ...missing, found_by: null }, report)).toContain('来源：未记录')
  })
  it('distinguishes completion, partial and running without a coverage retry', () => {
    expect(collaborationCoverageLabel(report)).toContain('不能视为全文审校完成')
    expect(collaborationCoverageLabel({ ...report, status: 'complete' })).toContain('不保证全文无误')
    expect(collaborationCoverageLabel({ ...report, status: 'running' })).toContain('进行中')
  })
})
