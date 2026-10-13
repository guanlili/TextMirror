import { describe, expect, it, vi } from 'vitest'
vi.mock('@/api/proofread', () => ({ submitIssueFeedbackApi: vi.fn() }))
import type { CollaborationReport } from '@/api/collaboration'
import { collaborationCoverageLabel, collaborationDisplayMessage, collaborationFindingMap, collaborationProvenance, collaborationRoleLabel, findCollaborationFinding } from '../collaboration'
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
    expect(collaborationProvenance(joined, report)).toContain('文字表达 / 全文一致性 · 存在疑点：需按上下文确认')
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
  it.each([
    ['rules', '规则检查'], ['language', '文字表达'], ['consistency', '全文一致性'], ['reviewer', '疑点复核'],
    ['语言审校', '文字表达'], ['一致性审校', '全文一致性'], ['争议复核', '疑点复核'], ['复核', '疑点复核'],
    ['自定义检查', '自定义检查'], ['toString', 'toString'],
  ])('maps the display role %s to %s without needing a report', (id, label) => {
    expect(collaborationRoleLabel(id)).toBe(label)
    expect(collaborationProvenance({ ...finding, found_by: [id] }, report)).toContain(`来源：${label} ·`)
  })
  it.each([['语言审校', '一致性审校'], ['language', 'consistency']])('normalizes backend legacy names and IDs before role.name, leaving storage untouched: %s, %s', (language, consistency) => {
    const data: CollaborationReport = Object.freeze({ ...report, roles: Object.freeze([
      Object.freeze({ id: 'language' as const, name: '语言审校', status: 'success' as const, message: '', issue_count: 1, elapsed_ms: 1, usage: {} }),
      Object.freeze({ id: 'consistency' as const, name: '一致性审校', status: 'success' as const, message: '', issue_count: 1, elapsed_ms: 1, usage: {} }),
    ]), findings: Object.freeze([Object.freeze({ ...finding, found_by: Object.freeze([language, consistency]) })]) })
    const before = JSON.stringify(data)
    expect(collaborationProvenance(data.findings[0], data)).toBe('来源：文字表达 / 全文一致性 · 存在疑点：需按上下文确认')
    expect(JSON.stringify(data)).toBe(before)
  })
  it('deduplicates display aliases, preserves unknown sources and falls back to source', () => {
    expect(collaborationProvenance({ ...finding, found_by: ['language', '语言审校', 'dict_scan'] }, report)).toContain('来源：文字表达 / dict_scan ·')
    expect(collaborationProvenance({ ...finding, found_by: [], source: '一致性审校' }, report)).toContain('来源：全文一致性 ·')
  })
  it.each(['failed', 'cancelled'] as const)('shows interrupted review for %s, but never overwrites an existing verdict', status => {
    const data: CollaborationReport = { ...report, roles: [{ id: 'reviewer', name: '争议复核', status, message: '复核中断', issue_count: 0, elapsed_ms: 1, usage: {} }] }
    expect(collaborationProvenance({ ...finding, review_status: 'not_reviewed', review_note: '需人工核对' }, data)).toContain('复核未完成（未复核）：需人工核对')
    expect(collaborationProvenance({ ...finding, review_status: 'confirmed' }, data)).toContain('复核确认')
    expect(collaborationProvenance(finding, data)).toContain('存在疑点')
  })
  it('does not turn unreviewed items into confirmed ones even when the flow is complete', () => {
    expect(collaborationProvenance({ ...finding, review_status: 'not_reviewed', review_note: '' }, { ...report, status: 'complete' })).toBe('来源：文字表达 / 全文一致性 · 未复核')
  })
  it('updates only display terminology in progress messages, preserving failure and budget semantics', () => {
    expect(collaborationDisplayMessage('协作审校部分完成；语言审校失败，一致性审校已完成，争议复核未完成；超出20条复核预算')).toBe('分工审校部分完成；文字表达失败，全文一致性已完成，疑点复核未完成；超出20条复核预算')
  })
})
