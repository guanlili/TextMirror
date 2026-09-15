/** 基于固定原文位置的审阅状态。页面只消费 currentText，不再通过 HTML 回调修改文本。 */
import { ref, computed, toRaw, type Ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { submitIssueFeedbackApi } from '@/api/proofread'
import {
  createReviewPatch,
  expandReviewIssues,
  getIssuePatch,
  getReviewPatches,
  isLocatedReviewIssue,
  renderReviewText,
  reviewIssueKey,
  reviewPatchesOverlap,
  sameReviewDecision,
  serializeReviewIssues,
  type ReviewIssue,
  type ReviewPatch,
} from '@/utils/review'

export type { ReviewIssue, ReviewPatch } from '@/utils/review'

/** 反馈上报所需的最小字段（兼容对比视图的问题对象）。 */
export interface FeedbackItem {
  original: string
  suggestion?: string
  type?: string
}

const MANUAL_CHECK = '该问题无法安全定位或没有可应用的建议，请人工核对'
const OVERLAP = '该修改与已接受的修改位置重叠，请先撤销冲突项'

export function useProofreadReview() {
  const sourceText = ref('')
  const issues: Ref<ReviewIssue[]> = ref([])
  const patches = computed(() => getReviewPatches(sourceText.value, issues.value))
  const currentText = computed(() => renderReviewText(sourceText.value, patches.value))
  const filterType = ref('')
  const activeIssueIndex = ref(-1)
  const recordId = ref<number | null>(null)

  const filteredIssues = computed(() => filterType.value
    ? issues.value.filter(issue => issue.type === filterType.value)
    : issues.value)
  const acceptedCount = computed(() => issues.value.filter(issue => issue._accepted).length)
  const pendingCount = computed(() => issues.value.filter(issue => !issue._accepted && !issue._ignored).length)

  // 仅为派生索引缓存；审阅决策和实际补丁全部保存在可序列化的 issues 上。
  const issueIndexMap = computed(() => {
    const map = new WeakMap<ReviewIssue, number>()
    issues.value.forEach((issue, index) => map.set(toRaw(issue), index))
    return map
  })

  function getGlobalIndex(issue: ReviewIssue): number {
    return issueIndexMap.value.get(toRaw(issue))
      ?? issues.value.findIndex(item => reviewIssueKey(item) === reviewIssueKey(issue))
  }

  function canonicalIssue(issue: ReviewIssue): ReviewIssue | undefined {
    return issues.value[getGlobalIndex(issue)]
  }

  function setDecision(issue: ReviewIssue, accepted: boolean, ignored: boolean, patch?: ReviewPatch) {
    for (const peer of issues.value) {
      if (peer !== issue && !sameReviewDecision(peer, issue)) continue
      peer._accepted = accepted
      peer._ignored = ignored
      if (patch) peer._patch = { ...patch }
      else delete peer._patch
    }
  }

  /** 替换原文和问题；原文在审阅操作期间保持不变。返回展开后的响应式问题对象。 */
  function initialize(text: string, rawIssues: readonly ReviewIssue[]): ReviewIssue[] {
    sourceText.value = text
    issues.value = expandReviewIssues(text, rawIssues)
    activeIssueIndex.value = -1
    filterType.value = ''
    return issues.value
  }

  /**
   * 补查只增加报告，保留所有已有决策。
   * 返回本次 reports 对应的 canonical 对象，可直接放入各 model.issues 共用。
   */
  function mergeIssues(rawIssues: readonly ReviewIssue[]): ReviewIssue[] {
    const merged: ReviewIssue[] = []
    for (const incoming of expandReviewIssues(sourceText.value, rawIssues)) {
      const existing = issues.value.find(issue => reviewIssueKey(issue) === reviewIssueKey(incoming))
      if (existing) {
        merged.push(existing)
        continue
      }
      const shared = issues.value.find(issue => sameReviewDecision(issue, incoming))
      if (shared) {
        incoming._accepted = shared._accepted
        incoming._ignored = shared._ignored
        if (shared._patch) incoming._patch = { ...shared._patch }
      }
      issues.value.push(incoming)
      merged.push(issues.value[issues.value.length - 1])
    }
    return merged
  }

  // 审校建议反馈上报（fire-and-forget：失败不打扰用户）。
  function reportFeedback(items: FeedbackItem[], action: 'accept' | 'ignore') {
    if (!items.length) return
    submitIssueFeedbackApi({
      record_id: recordId.value ?? undefined,
      items: items.map(issue => ({
        original: issue.original,
        suggestion: issue.suggestion || undefined,
        issue_type: issue.type || undefined,
        action,
      })),
    }).catch(() => {})
  }

  /** 不上报、不弹窗，供单条、批量和恢复共用。 */
  function applyPatch(input: ReviewIssue, patch: ReviewPatch | null): { accepted?: ReviewIssue; error?: string } {
    const issue = canonicalIssue(input)
    if (!issue) return { error: MANUAL_CHECK }
    if (issue._accepted || issue._ignored) return {}
    if (!isLocatedReviewIssue(sourceText.value, issue) || !patch) return { error: MANUAL_CHECK }
    if (patches.value.some(accepted => reviewPatchesOverlap(accepted, patch))) return { error: OVERLAP }
    setDecision(issue, true, false, patch)
    return { accepted: issue }
  }

  function acceptOne(input: ReviewIssue, deletion = false): boolean {
    const issue = canonicalIssue(input)
    const result = applyPatch(input, issue ? createReviewPatch(sourceText.value, issue, deletion) : null)
    if (result.error) ElMessage.warning(result.error)
    if (!result.accepted) return false
    reportFeedback([result.accepted], 'accept')
    return true
  }

  /** 仅接受此原文 occurrence；敏感词无建议时自动执行单处删除。 */
  function acceptIssue(issue: ReviewIssue): boolean {
    return acceptOne(issue)
  }

  function deleteIssue(issue: ReviewIssue): boolean {
    return acceptOne(issue, true)
  }

  function ignoreIssue(input: ReviewIssue): boolean {
    const issue = canonicalIssue(input)
    if (!issue || issue._accepted || issue._ignored) return false
    setDecision(issue, false, true)
    reportFeedback([issue], 'ignore')
    return true
  }

  /** 去掉该位置/建议的决策和补丁；其余补丁仍基于 immutable source 重建。 */
  function undoIssue(input: ReviewIssue): boolean {
    const issue = canonicalIssue(input)
    if (!issue || (!issue._accepted && !issue._ignored)) return false
    setDecision(issue, false, false)
    return true
  }

  function longestFirst(targets: readonly ReviewIssue[]): ReviewIssue[] {
    const length = (issue: ReviewIssue) => isLocatedReviewIssue(sourceText.value, issue) ? issue.end - issue.start : 0
    return [...targets].sort((a, b) => length(b) - length(a))
  }

  /** 用于对比分级批量；长 span 优先，返回实际新应用的补丁数量（不是报告数量）。 */
  function applyIssues(targets: readonly ReviewIssue[]): number {
    const accepted: ReviewIssue[] = []
    const errors = new Set<string>()
    for (const input of longestFirst(targets)) {
      const issue = canonicalIssue(input)
      const result = applyPatch(input, issue ? createReviewPatch(sourceText.value, issue) : null)
      if (result.accepted) accepted.push(result.accepted)
      if (result.error) errors.add(result.error)
    }
    reportFeedback(accepted, 'accept')
    if (errors.size) ElMessage.warning([...errors].join('；'))
    return accepted.length
  }

  /** 仅联动当前已报告、未忽略、original + suggestion 完全相同的 occurrences。 */
  function acceptMatching(input: ReviewIssue): number {
    const issue = canonicalIssue(input)
    if (!issue) {
      ElMessage.warning(MANUAL_CHECK)
      return 0
    }
    return applyIssues(issues.value.filter(item => !item._ignored
      && item.original === issue.original && item.suggestion === issue.suggestion))
  }

  async function handleAcceptAll(): Promise<number> {
    const originalIssues = issues.value
    const actionable = originalIssues.filter(issue => !issue._accepted && !issue._ignored
      && issue.original && (issue.suggestion || issue.type === 'sensitive'))
    if (!actionable.length) return 0
    try {
      await ElMessageBox.confirm(
        `确认接受全部 ${actionable.length} 条修改建议？`,
        '一键修改',
        { confirmButtonText: '确认', cancelButtonText: '取消', type: 'warning' },
      )
    } catch {
      return 0
    }
    if (issues.value !== originalIssues) return 0
    const count = applyIssues(actionable)
    if (count) ElMessage.success(`已接受 ${count} 条修改`)
    return count
  }

  /**
   * 只回放原文位置经过校验的状态；旧草稿无位置时降级为 pending，绝不猜测接受范围。
   * 冲突长 span 优先，其余保持 pending 并提示；恢复不重复上报反馈。
   */
  function restore(text: string, persistedIssues: readonly ReviewIssue[]): ReviewIssue[] {
    initialize(text, persistedIssues)
    const errors = new Set<string>()
    for (const saved of persistedIssues) {
      if (saved._ignored) {
        // 未定位项也允许被忽略；只恢复对应的未定位报告，不广播到全文 occurrences。
        const issue = canonicalIssue(isLocatedReviewIssue(text, saved) ? saved : { ...saved, start: -1, end: -1 })
        if (issue) setDecision(issue, false, true)
        else errors.add(MANUAL_CHECK)
      } else if (saved._accepted && !isLocatedReviewIssue(text, saved)) {
        errors.add(MANUAL_CHECK)
      }
    }
    for (const saved of longestFirst(persistedIssues.filter(issue => issue._accepted && !issue._ignored))) {
      if (!isLocatedReviewIssue(text, saved)) continue
      const result = applyPatch(saved, getIssuePatch(text, saved))
      if (result.error) errors.add(result.error)
    }
    if (errors.size) ElMessage.warning([...errors].join('；'))
    return issues.value
  }

  /** 与 sourceText.value 一起保存；恢复调用 restore(source, serializedIssues)。 */
  function serializeIssues(): ReviewIssue[] {
    return serializeReviewIssues(issues.value)
  }

  return {
    sourceText,
    currentText,
    issues,
    patches,
    filterType,
    activeIssueIndex,
    recordId,
    filteredIssues,
    acceptedCount,
    pendingCount,
    getGlobalIndex,
    reportFeedback,
    initialize,
    mergeIssues,
    restore,
    serializeIssues,
    acceptIssue,
    deleteIssue,
    ignoreIssue,
    undoIssue,
    handleAcceptAll,
    acceptMatching,
    applyIssues,
  }
}
