import type { FeedbackEvaluation, FeedbackModelSnapshot, FeedbackSample, QualityFeedback } from '@/api/qualityFeedback'
import type { AvailableModel } from '@/api/polish'

export const feedbackIssueTypes = ['typo', 'grammar', 'punctuation', 'style', 'sensitive', 'logic'] as const
export const evaluationScopeNotice = '仅评测管理员确认的目标范围，不代表全文精确率；检出与建议分别计数。建议仅按人工认可/禁止替换后的上下文精确匹配，不额外调用 LLM 判官；无约束、未检出或无法比较时为未评估，不代表建议通过。每次 LLM 调用结果可能波动。'

/** 与服务端一致，以 Unicode code point 而非 UTF-16 code unit 定位，包含重叠出现。 */
export function findFeedbackTargets(text: string, original: string): { start: number; end: number; label: string }[] {
  const chars = Array.from(text)
  const target = Array.from(original)
  if (!original.trim() || target.length > chars.length) return []
  const matches = []
  for (let start = 0; start <= chars.length - target.length; start++) {
    if (!target.every((char, index) => chars[start + index] === char)) continue
    const end = start + target.length
    const before = chars.slice(Math.max(0, start - 12), start).join('')
    const after = chars.slice(end, end + 12).join('')
    matches.push({ start, end, label: `第 ${matches.length + 1} 处 · ${before}【${original}】${after}` })
  }
  return matches
}

export function feedbackTargetParts(text: string, start: number, end: number, original: string) {
  const chars = Array.from(text)
  const valid = Number.isInteger(start) && Number.isInteger(end) && start >= 0 && end > start
    && end <= chars.length && chars.slice(start, end).join('') === original
  return valid
    ? { before: chars.slice(0, start).join(''), target: original, after: chars.slice(end).join('') }
    : { before: text, target: '', after: '' }
}

export function feedbackSampleError(sample: FeedbackSample): string {
  const length = Array.from(sample.text).length
  if (!sample.text.trim() || length > 4000) return '脱敏样例须为 1–4000 个 Unicode 字符。'
  if (!sample.original.trim()) return '请填写目标原文。'
  if (!feedbackTargetParts(sample.text, sample.start, sample.end, sample.original).target) {
    return '请在样例中选择有效目标；目标原文必须与所选片段完全一致。'
  }
  if (!['general', 'official', 'legal'].includes(sample.domain)) return '请选择样例领域。'
  if (!['report', 'no_report'].includes(sample.expectation)) return '请确认目标预期。'
  if (sample.issue_type !== '' && !feedbackIssueTypes.some(type => type === sample.issue_type)) return '请选择已有问题类型或任意类型。'
  const accepted = sample.accepted_suggestions ?? []
  const rejected = sample.rejected_suggestions ?? []
  for (const values of [accepted, rejected]) {
    if (values.length > 10 || values.some(value => Array.from(value).length > 500)) return '每组替换最多 10 条，每条最多 500 个 Unicode 字符。'
    if (new Set(values).size !== values.length) return '同组替换不可重复。'
  }
  if (accepted.some(value => rejected.includes(value))) return '认可与禁止替换不能相同。'
  if (sample.expectation === 'no_report' && (accepted.length || rejected.length)) return '不应报告的样例不能附带替换约束。'
  return ''
}

/** 已确认反馈沿用独立样例；数组也独立复制，不把旧样例自动升级为建议 golden。 */
export function initialFeedbackSample(feedback: QualityFeedback): Required<FeedbackSample> {
  if (feedback.sample) return {
    ...feedback.sample,
    accepted_suggestions: [...(feedback.sample.accepted_suggestions ?? [])],
    rejected_suggestions: [...(feedback.sample.rejected_suggestions ?? [])],
  }
  return {
    text: feedback.context_text,
    domain: feedback.domain === 'legal' || feedback.domain === 'official' ? feedback.domain : 'general',
    start: feedback.start - feedback.context_start,
    end: feedback.end - feedback.context_start,
    original: feedback.original,
    expectation: feedback.kind === 'missed' || feedback.kind === 'bad_suggestion' ? 'report' : 'no_report',
    issue_type: feedbackIssueTypes.some(type => type === feedback.issue_type) ? feedback.issue_type : '',
    accepted_suggestions: [],
    // 仅预填用户声称不合适的建议，仍须管理员确认；空字符串也是有效删除建议。
    rejected_suggestions: feedback.kind === 'bad_suggestion' ? [feedback.suggestion] : [],
  }
}

export function feedbackSnapshotLabel(snapshot: FeedbackModelSnapshot): string {
  if (snapshot.success === false) return '调用失败 · 目标结果不可判定'
  if (snapshot.success !== true) return '调用状态未知 · 目标结果不可判定'
  if (snapshot.coverage_status === 'partial') return '部分完成 · 目标结果不可判定'
  if (snapshot.coverage_status !== 'complete') return '完成状态未知 · 目标结果不可判定'
  if (snapshot.reported === null) return '审校完成 · 目标报告状态未知'
  return snapshot.reported ? '审校完成 · 已报告目标' : '审校完成 · 未报告目标（不等于漏检）'
}

export function defaultFeedbackModels(models: AvailableModel[]): number[] {
  const current = models.find(model => model.is_active)
  return [...new Set([...(current ? [current.id] : []), ...models.map(model => model.id)])].slice(0, 2)
}

export function serializeFeedbackReport(evaluation: FeedbackEvaluation): string {
  return JSON.stringify({ ...evaluation, scope: 'confirmed_target_only', notice: evaluationScopeNotice }, null, 2)
}
