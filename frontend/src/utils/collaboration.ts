import type { CollaborationFinding, CollaborationReport, CollaborationRoleStatus } from '@/api/collaboration'
import type { ReviewIssue } from '@/utils/review'

export const collaborationRoleLabels = { rules: '规则检查', language: '语言审校', consistency: '一致性审校', reviewer: '复核' }
export const collaborationStatusLabels: Record<CollaborationRoleStatus, string> = {
  pending: '等待执行', running: '执行中', success: '已完成', failed: '失败', skipped: '已跳过', cancelled: '已取消',
}
export const collaborationStatusTypes = { pending: 'info', running: 'primary', success: 'success', failed: 'danger', skipped: 'warning', cancelled: 'warning' } as const
export const collaborationVerdictLabels = { not_reviewed: '未复核', confirmed: '复核确认', disputed: '存在争议' }
export function collaborationCoverageLabel(report: CollaborationReport): string {
  return report.status === 'complete' ? '协作流程已完成（不保证全文无误）' : report.status === 'partial' ? '协作仅部分完成，不能视为全文审校完成' : '协作尚在进行中'
}
function findingKey(issue: ReviewIssue) {
  const position = (value: number | null | undefined) => value == null || value < 0 ? null : value
  return JSON.stringify([position(issue.start), position(issue.end), issue.original, issue.type, issue.suggestion])
}
/** 按原文位置和建议连接，不能只按 original，也不能把来源塞进可编辑 ReviewIssue。 */
export function collaborationFindingMap(report: CollaborationReport | null) {
  return new Map(report?.findings.map(finding => [findingKey(finding), finding]) ?? [])
}
export function findCollaborationFinding(issue: ReviewIssue, findings: ReadonlyMap<string, CollaborationFinding>) {
  return findings.get(findingKey(issue))
}
export function collaborationProvenance(finding: CollaborationFinding, report: CollaborationReport): string {
  const sources = finding.found_by?.length ? finding.found_by : finding.source ? [finding.source] : []
  const names = sources.map(id => report.roles.find(role => role.id === id)?.name || collaborationRoleLabels[id as keyof typeof collaborationRoleLabels] || id)
  return `来源：${names.join(' / ') || '未记录'} · ${collaborationVerdictLabels[finding.review_status]}${finding.review_note ? `：${finding.review_note}` : ''}`
}
