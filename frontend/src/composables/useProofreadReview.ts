/**
 * 校对审阅共享逻辑（TextProofread.vue / DocumentProofread.vue 共用）
 *
 * 持有问题列表、修订文本等响应式状态，提供接受/忽略/删除/撤销/一键接受与反馈上报。
 * 组件间的细微差异（HTML 同步、撤销锚点越界策略）通过 options 参数化。
 */
import { ref, computed, type Ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { submitIssueFeedbackApi } from '@/api/proofread'
import { computeSensitiveDeletion } from '@/utils/proofread'

/** 带审阅状态的校对问题 */
export interface ReviewIssue {
  original: string
  type: string
  suggestion: string
  explanation: string
  severity: string
  chunk_index?: number
  _accepted?: boolean
  _ignored?: boolean
  /** 删除该词操作的实际删除内容（含标点），供撤销恢复 */
  _deletedText?: string
  /** 删除时的词首位置，撤销按此插回 */
  _undoAnchor?: number
}

/** 反馈上报所需的最小字段（兼容对比视图的问题对象） */
export interface FeedbackItem {
  original: string
  suggestion?: string
  type?: string
}

export interface ProofreadReviewOptions {
  /**
   * 撤销删除时锚点越界的策略：
   * true = 钳位到文末插入（文档校对）；false/缺省 = 越界则不恢复（文本校对）
   */
  clampUndoAnchor?: boolean
  /** 接受建议替换后的额外同步（如文档校对的 HTML 视图） */
  onAcceptReplace?: (original: string, suggestion: string) => void
  /** 删除敏感词后的额外同步 */
  onDeleteWord?: (target: string) => void
  /** 撤销建议替换后的额外同步 */
  onUndoReplace?: (suggestion: string, original: string) => void
}

export function useProofreadReview(options: ProofreadReviewOptions = {}) {
  const issues: Ref<ReviewIssue[]> = ref([])
  const currentText = ref('')
  const filterType = ref('')
  const activeIssueIndex = ref(-1)
  const recordId = ref<number | null>(null)

  // 筛选后的问题列表
  const filteredIssues = computed(() => {
    if (!filterType.value) return issues.value
    return issues.value.filter(i => i.type === filterType.value)
  })

  const acceptedCount = computed(() => issues.value.filter(i => i._accepted).length)
  const pendingCount = computed(() => issues.value.filter(i => !i._accepted && !i._ignored).length)

  // 获取问题在全局列表中的索引（用于联动高亮）
  function getGlobalIndex(issue: ReviewIssue): number {
    return issues.value.indexOf(issue)
  }

  // 审校建议反馈上报（fire-and-forget：失败不打扰用户）
  function reportFeedback(items: FeedbackItem[], action: 'accept' | 'ignore') {
    if (!items.length) return
    submitIssueFeedbackApi({
      record_id: recordId.value ?? undefined,
      items: items.map(i => ({
        original: i.original,
        suggestion: i.suggestion || undefined,
        issue_type: i.type || undefined,
        action,
      })),
    }).catch(() => {})
  }

  // 接受单条修改
  function acceptIssue(issue: ReviewIssue) {
    if (issue.original && issue.suggestion) {
      currentText.value = currentText.value.replace(issue.original, issue.suggestion)
      options.onAcceptReplace?.(issue.original, issue.suggestion)
    }
    issue._accepted = true
    reportFeedback([issue], 'accept')
  }

  // 忽略
  function ignoreIssue(issue: ReviewIssue) {
    issue._ignored = true
    reportFeedback([issue], 'ignore')
  }

  // 删除敏感词（违禁词的自动修复 = 删除，连同紧邻标点避免悬空标点）
  function deleteIssue(issue: ReviewIssue) {
    const del = computeSensitiveDeletion(currentText.value, issue.original)
    if (!del) return
    currentText.value = currentText.value.replace(del.target, '')
    options.onDeleteWord?.(del.target)
    issue._accepted = true
    // 记录删除内容与词首位置，撤销时按锚点插回
    issue._deletedText = del.target
    issue._undoAnchor = del.anchor
    reportFeedback([issue], 'accept')
  }

  // 撤销
  function undoIssue(issue: ReviewIssue) {
    if (issue._accepted && issue.original && issue.suggestion) {
      currentText.value = currentText.value.replace(issue.suggestion, issue.original)
      options.onUndoReplace?.(issue.suggestion, issue.original)
    } else if (issue._accepted && issue._deletedText !== undefined) {
      // 删除类撤销：把删掉的词（含标点）插回原位——按删除时的锚点定位
      const deleted = issue._deletedText
      if (options.clampUndoAnchor) {
        const anchor = Math.min(issue._undoAnchor ?? 0, currentText.value.length)
        currentText.value = currentText.value.slice(0, anchor) + deleted + currentText.value.slice(anchor)
      } else {
        const anchor = issue._undoAnchor
        if (anchor !== undefined && anchor >= 0 && anchor <= currentText.value.length) {
          currentText.value = currentText.value.slice(0, anchor) + deleted + currentText.value.slice(anchor)
        }
      }
      issue._deletedText = undefined
      issue._undoAnchor = undefined
    }
    issue._accepted = false
    issue._ignored = false
  }

  // 一键修改全部（与单条操作语义一致：有建议的替换 + 敏感词删除）
  async function handleAcceptAll() {
    const actionable = issues.value.filter(i =>
      !i._accepted && !i._ignored && i.original && (i.suggestion || i.type === 'sensitive')
    )
    try {
      await ElMessageBox.confirm(
        `确认接受全部 ${actionable.length} 条修改建议？`,
        '一键修改',
        { confirmButtonText: '确认', cancelButtonText: '取消', type: 'warning' }
      )
      const accepted: ReviewIssue[] = []
      for (const issue of actionable) {
        if (issue.suggestion) {
          currentText.value = currentText.value.replace(issue.original, issue.suggestion)
          options.onAcceptReplace?.(issue.original, issue.suggestion)
        } else {
          // 敏感词：删除（含紧邻标点），记录撤销锚点
          const del = computeSensitiveDeletion(currentText.value, issue.original)
          if (!del) continue
          currentText.value = currentText.value.replace(del.target, '')
          options.onDeleteWord?.(del.target)
          issue._deletedText = del.target
          issue._undoAnchor = del.anchor
        }
        issue._accepted = true
        accepted.push(issue)
      }
      reportFeedback(accepted, 'accept')
      ElMessage.success('已接受所有修改')
    } catch {
      // 取消
    }
  }

  return {
    issues,
    currentText,
    filterType,
    activeIssueIndex,
    recordId,
    filteredIssues,
    acceptedCount,
    pendingCount,
    getGlobalIndex,
    reportFeedback,
    acceptIssue,
    ignoreIssue,
    deleteIssue,
    undoIssue,
    handleAcceptAll,
  }
}
