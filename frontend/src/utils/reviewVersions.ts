/** 基于同一原文的审阅决策对比，不做逐字符 diff，也不改变审阅状态。 */
import type { ProofreadCoverage } from '@/api/proofread'
import type { ReviewCompareState } from '@/api/review'
import type { ReviewIssue } from '@/composables/useProofreadReview'
import { getReviewPatches, renderReviewText, serializeReviewIssues } from '@/utils/review'
import { serializeCompareReview } from '@/utils/compareReview'

export interface AcceptedReviewDecision {
  /** 0-based Unicode codepoint [start, end)，保持问题的原文范围。 */
  start: number
  end: number
  original: string
  suggestion: string
  /** 实际结果区分「采纳建议」与「显式删除」。 */
  replacement: string
  patchEnd: number
}

export interface ReviewDecisionChange {
  kind: 'added' | 'removed' | 'changed'
  start: number
  end: number
  original: string
  before: AcceptedReviewDecision | null
  after: AcceptedReviewDecision | null
}

export interface ReviewDraftState {
  sourceText: string
  issues: readonly ReviewIssue[]
  coverage?: ProofreadCoverage | null
  compare?: ReviewCompareState | null
  domain: string
  depth: string
  configId?: number | null
}

/** 仅用于内存中的脏状态/请求快照比较，绝不写入 localStorage。 */
export function serializeReviewDraft(state: ReviewDraftState): string {
  const coverage = state.coverage
  return JSON.stringify({
    sourceText: state.sourceText,
    issues: serializeReviewIssues(state.issues).map(issue => ({
      ...issue,
      start: issue.start == null || issue.start < 0 ? null : issue.start,
      end: issue.end == null || issue.end < 0 ? null : issue.end,
      explanation: issue.explanation || '',
      chunk_index: issue.chunk_index ?? null,
      _patch: issue._patch?.replacement !== issue.suggestion ? issue._patch : undefined,
    })),
    coverage: coverage ? {
      status: coverage.status,
      total_chunks: coverage.total_chunks,
      completed_chunks: coverage.completed_chunks,
      failed_chunks: coverage.failed_chunks.map(chunk => ({
        chunk_index: chunk.chunk_index,
        start: chunk.start,
        end: chunk.end,
        text: chunk.text,
        error_code: chunk.error_code,
      })),
    } : null,
    compare: serializeCompareReview(state.compare),
    domain: state.domain,
    depth: state.depth,
    configId: state.configId ?? null,
  })
}

/** 复用编辑器已校验的补丁语义（包括删除紧邻标点），不另造编辑实现。 */
export function renderReviewVersionText(source: string, issues: readonly ReviewIssue[]): string {
  return renderReviewText(source, getReviewPatches(source, issues))
}

function acceptedDecisions(source: string, issues: readonly ReviewIssue[]): AcceptedReviewDecision[] {
  // 校验非法位置、伪造补丁和重叠；不能在对比里悄悄丢弃已采纳的非法项。
  const patches = new Map(getReviewPatches(source, issues).map(patch => [patch.start, patch]))
  const decisions = new Map<string, AcceptedReviewDecision>()
  for (const issue of issues) {
    if (!issue._accepted || issue._ignored) continue
    const patch = patches.get(issue.start!)!
    const decision: AcceptedReviewDecision = {
      start: issue.start!,
      end: issue.end!,
      original: issue.original,
      suggestion: issue.suggestion,
      replacement: patch.replacement,
      patchEnd: patch.end,
    }
    // 同位置同建议的多模型/多类型重复报告只代表一个决策。
    decisions.set(JSON.stringify([decision.start, decision.end, decision.suggestion, decision.replacement, decision.patchEnd]), decision)
  }
  return [...decisions.values()]
}

/**
 * 从左到右列出新增、撤销和改变的采纳。原文侧传 []。
 * 用原文范围而非 original 文本作键，相同文字不同 occurrence 不会互相抵消。
 * 合法审阅状态每个范围只有一个实际补丁；复杂度不依赖全文字符的两两比较。
 */
export function compareReviewVersions(
  source: string,
  left: readonly ReviewIssue[],
  right: readonly ReviewIssue[],
): ReviewDecisionChange[] {
  const rangeKey = (decision: AcceptedReviewDecision) => `${decision.start}:${decision.end}`
  const before = new Map(acceptedDecisions(source, left).map(decision => [rangeKey(decision), decision]))
  const after = new Map(acceptedDecisions(source, right).map(decision => [rangeKey(decision), decision]))
  const changes: ReviewDecisionChange[] = []
  for (const key of new Set([...before.keys(), ...after.keys()])) {
    const oldDecision = before.get(key) ?? null
    const newDecision = after.get(key) ?? null
    if (oldDecision && newDecision
      && oldDecision.suggestion === newDecision.suggestion
      && oldDecision.replacement === newDecision.replacement
      && oldDecision.patchEnd === newDecision.patchEnd) continue
    const decision = newDecision ?? oldDecision!
    changes.push({
      kind: !oldDecision ? 'added' : !newDecision ? 'removed' : 'changed',
      start: decision.start,
      end: decision.end,
      original: decision.original,
      before: oldDecision,
      after: newDecision,
    })
  }
  return changes.sort((a, b) => a.start - b.start || a.end - b.end)
}
