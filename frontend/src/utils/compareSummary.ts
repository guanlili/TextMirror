import type { ModelProofreadResult } from '@/api/proofread'
import { reviewDecisionKind, reviewIssueKey, type ReviewIssue } from '@/utils/review'

type CompareModel = Pick<ModelProofreadResult, 'config_id' | 'config_name' | 'success'> & {
  issues: readonly ReviewIssue[]
}

export interface CompareSummaryItem {
  key: string
  issue: ReviewIssue
  modelNames: string[]
  modelCount: number
  totalModels: number
  isConsensus: boolean
  located: boolean
}

function hasPosition(issue: ReviewIssue): boolean {
  return Number.isInteger(issue.start) && Number.isInteger(issue.end)
    && issue.start! >= 0 && issue.end! > issue.start!
}

/** 输入是经 expandReviewIssues 校验后的报告。跨类型合并相同修改，不混淆删除和人工核对。 */
export function compareSummaryKey(issue: ReviewIssue): string {
  if (!hasPosition(issue)) return `unlocated:${reviewIssueKey(issue)}`
  const action = reviewDecisionKind(issue)
  return JSON.stringify([issue.start, issue.end, issue.original, issue.suggestion, action])
}

/** 只派生展示，不改写原始模型报告或共享的采纳决策。失败模型也计入分母。 */
export function buildCompareSummary(models: readonly CompareModel[]): CompareSummaryItem[] {
  const totalModels = new Set(models.map(model => model.config_id)).size
  const allSucceeded = models.every(model => model.success)
  const groups = new Map<string, { issue: ReviewIssue; sources: Map<number, string> }>()
  for (const model of models) {
    if (!model.success) continue
    for (const issue of model.issues) {
      const key = compareSummaryKey(issue)
      if (!groups.has(key)) groups.set(key, { issue, sources: new Map() })
      groups.get(key)!.sources.set(model.config_id, model.config_name)
    }
  }
  const severity: Record<string, number> = { error: 3, warning: 2, info: 1 }
  return [...groups].map(([key, { issue, sources }]) => ({
    key,
    issue,
    modelNames: [...sources.values()],
    modelCount: sources.size,
    totalModels,
    located: hasPosition(issue),
    isConsensus: totalModels >= 2 && allSucceeded && sources.size === totalModels && hasPosition(issue),
  })).sort((a, b) => Number(b.isConsensus) - Number(a.isConsensus)
    || b.modelCount - a.modelCount
    || (severity[b.issue.severity] || 0) - (severity[a.issue.severity] || 0))
}

export function compareSummaryProvenance(item: CompareSummaryItem): string {
  const sources = `${item.modelNames.join('、')}（${item.modelCount}/${item.totalModels}）`
  if (!item.located) return `来源：${sources}；位置未确认，不计为共同发现`
  if (item.isConsensus) return `共同发现：${sources}`
  return `仅部分模型发现：${sources}`
}
