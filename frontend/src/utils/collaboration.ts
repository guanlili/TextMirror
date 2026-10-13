import type { CollaborationFinding, CollaborationReport, CollaborationRoleStatus } from '@/api/collaboration'
import type { ReviewIssue } from '@/utils/review'

export const collaborationRoleLabels = { rules: '规则检查', language: '文字表达', consistency: '全文一致性', reviewer: '疑点复核' }
const legacyRoleLabels: Record<string, string> = { 语言审校: '文字表达', 一致性审校: '全文一致性', 争议复核: '疑点复核', 复核: '疑点复核' }
/** 只转换显示名称，保留报告里的角色 ID、旧名称和来源不变。 */
export function collaborationRoleLabel(id: string, fallback = id): string {
  return Object.prototype.hasOwnProperty.call(collaborationRoleLabels, id)
    ? collaborationRoleLabels[id as keyof typeof collaborationRoleLabels]
    : Object.prototype.hasOwnProperty.call(legacyRoleLabels, id) ? legacyRoleLabels[id] : fallback
}
export function collaborationDisplayMessage(message: string): string {
  return message.replace(/协作审校|语言审校|一致性审校|争议复核/g, label => label === '协作审校' ? '分工审校' : collaborationRoleLabel(label))
}
export const collaborationStatusLabels: Record<CollaborationRoleStatus, string> = {
  pending: '等待执行', running: '执行中', success: '已完成', failed: '失败', skipped: '已跳过', cancelled: '已取消',
}
export const collaborationStatusTypes = { pending: 'info', running: 'primary', success: 'success', failed: 'danger', skipped: 'warning', cancelled: 'warning' } as const
export const collaborationVerdictLabels = { not_reviewed: '未复核', confirmed: '复核确认', disputed: '存在疑点' }
export function collaborationCoverageLabel(report: CollaborationReport): string {
  return report.status === 'complete' ? '流程已完成（不保证全文无误）' : report.status === 'partial' ? '流程部分完成，不能视为全文审校完成' : '检查进行中'
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
  const names = sources.map(id => {
    const role = report.roles.find(role => role.id === id || role.name === id)
    return collaborationRoleLabel(role?.id ?? id, role?.name || id)
  })
  const reviewInterrupted = report.roles.some(role => role.id === 'reviewer' && ['failed', 'cancelled'].includes(role.status))
  const verdict = finding.review_status === 'not_reviewed' && reviewInterrupted ? '复核未完成（未复核）' : collaborationVerdictLabels[finding.review_status]
  return `来源：${[...new Set(names)].join(' / ') || '未记录'} · ${verdict}${finding.review_note ? `：${finding.review_note}` : ''}`
}
