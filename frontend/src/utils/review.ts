/** 所有位置均为原文的 0-based Unicode codepoint [start, end)，不是 UTF-16 索引。 */
export interface ReviewPatch {
  start: number
  end: number
  original: string
  replacement: string
}

export interface ReviewIssue {
  original: string
  suggestion: string
  type: string
  severity: string
  explanation?: string
  chunk_index?: number
  /** raw issue 可以没有位置；展开后无法定位的位置统一为 -1。 */
  start?: number | null
  end?: number | null
  _accepted?: boolean
  _ignored?: boolean
  /** 实际应用的原文补丁（删除时包含紧邻标点）；可直接 JSON 序列化。 */
  _patch?: ReviewPatch
}

type LocatedIssue = ReviewIssue & { start: number; end: number }
const TRAILING_PUNCT = '，。！？；、,'

function matchesSpan(chars: string[], start: number | null | undefined, end: number | null | undefined, original: string): boolean {
  return Number.isInteger(start) && Number.isInteger(end)
    && start! >= 0 && end! > start! && end! <= chars.length
    && !!original && chars.slice(start!, end!).join('') === original
}

export function isLocatedReviewIssue(source: string, issue: ReviewIssue): issue is LocatedIssue {
  return matchesSpan(Array.from(source), issue.start, issue.end, issue.original)
}

/** 报告去重仍区分 type；同位置不同建议绝不合并。 */
export function reviewIssueKey(issue: ReviewIssue): string {
  return JSON.stringify([issue.start, issue.end, issue.original, issue.type, issue.suggestion])
}

/** 跨模型/跨 type 共享决策，但绝不跨位置或跨建议。 */
export function sameReviewDecision(a: ReviewIssue, b: ReviewIssue): boolean {
  return Number.isInteger(a.start) && Number.isInteger(a.end) && a.start! >= 0 && a.end! > a.start!
    && a.start === b.start && a.end === b.end
    && a.original === b.original && a.suggestion === b.suggestion
}

/**
 * 明确给出任一位置字段时，只校验该位置，不回退到全文搜索。
 * 无位置时枚举所有 occurrence（含相互重叠的匹配）；无法定位仍保留供人工核对。
 * raw reports 的审阅 flags 不可信，初始化/补查一律不直接采纳。
 */
export function expandReviewIssues(source: string, rawIssues: readonly ReviewIssue[]): ReviewIssue[] {
  const chars = Array.from(source)
  const result: ReviewIssue[] = []
  const seen = new Set<string>()
  for (const raw of rawIssues) {
    const spans: Array<[number, number]> = []
    if (raw.start !== undefined || raw.end !== undefined) {
      if (matchesSpan(chars, raw.start, raw.end, raw.original)) spans.push([raw.start!, raw.end!])
    } else if (raw.original) {
      const word = Array.from(raw.original)
      for (let start = 0; start + word.length <= chars.length; start++) {
        if (word.every((char, offset) => chars[start + offset] === char)) {
          spans.push([start, start + word.length])
        }
      }
    }
    if (!spans.length) spans.push([-1, -1])
    for (const [start, end] of spans) {
      const issue: ReviewIssue = {
        original: raw.original,
        suggestion: raw.suggestion,
        explanation: raw.explanation,
        severity: raw.severity,
        type: raw.type,
        chunk_index: raw.chunk_index,
        start,
        end,
        _accepted: false,
        _ignored: false,
      }
      const key = reviewIssueKey(issue)
      if (seen.has(key)) continue
      seen.add(key)
      result.push(issue)
    }
  }
  return result
}

/** 默认替换建议；无建议的敏感词或显式 deleteIssue 删除当前词及一个紧邻的后置标点。 */
export function createReviewPatch(source: string, issue: ReviewIssue, deletion = false): ReviewPatch | null {
  if (!isLocatedReviewIssue(source, issue)) return null
  const chars = Array.from(source)
  let end = issue.end
  const shouldDelete = deletion || (issue.type === 'sensitive' && !issue.suggestion)
  if (!shouldDelete && !issue.suggestion) return null
  if (shouldDelete && chars[end] && TRAILING_PUNCT.includes(chars[end])) end++
  return {
    start: issue.start,
    end,
    original: chars.slice(issue.start, end).join(''),
    replacement: shouldDelete ? '' : issue.suggestion,
  }
}

function samePatch(a: ReviewPatch, b: ReviewPatch): boolean {
  return a.start === b.start && a.end === b.end && a.original === b.original && a.replacement === b.replacement
}

export function reviewPatchesOverlap(a: Pick<ReviewPatch, 'start' | 'end'>, b: Pick<ReviewPatch, 'start' | 'end'>): boolean {
  return a.start < b.end && b.start < a.end
}

/** 校验持久化补丁只能是此 occurrence 的建议替换或删除，不能重放任意文本改写。 */
export function getIssuePatch(source: string, issue: ReviewIssue): ReviewPatch | null {
  const normal = createReviewPatch(source, issue)
  if (issue._patch === undefined) return normal
  if (!issue._patch) return null
  if (normal && samePatch(issue._patch, normal)) return normal
  const deletion = createReviewPatch(source, issue, true)
  return deletion && samePatch(issue._patch, deletion) ? deletion : null
}

/** 从可序列化 issue 状态派生补丁；共享决策只产生一个补丁，非法/重叠状态明确报错。 */
export function getReviewPatches(source: string, issues: readonly ReviewIssue[]): ReviewPatch[] {
  const applied: Array<{ issue: ReviewIssue; patch: ReviewPatch }> = []
  for (const issue of issues) {
    if (!issue._accepted || issue._ignored) continue
    const patch = getIssuePatch(source, issue)
    if (!patch) throw new Error('审阅补丁与原文不匹配，请人工核对')
    const shared = applied.find(entry => sameReviewDecision(entry.issue, issue))
    if (shared && samePatch(shared.patch, patch)) continue
    if (applied.some(entry => reviewPatchesOverlap(entry.patch, patch))) {
      throw new Error('审阅补丁位置重叠，请先撤销冲突项')
    }
    applied.push({ issue, patch })
  }
  return applied.map(entry => entry.patch).sort((a, b) => a.start - b.start)
}

/** 只从 immutable source 重建文本；不搜索当前文本、不反向替换建议。 */
export function renderReviewText(source: string, patches: readonly ReviewPatch[]): string {
  const chars = Array.from(source)
  const parts: string[] = []
  let cursor = 0
  for (const patch of [...patches].sort((a, b) => a.start - b.start)) {
    if (!matchesSpan(chars, patch.start, patch.end, patch.original) || patch.start < cursor) {
      throw new Error('审阅补丁位置无效或重叠')
    }
    parts.push(chars.slice(cursor, patch.start).join(''), patch.replacement)
    cursor = patch.end
  }
  parts.push(chars.slice(cursor).join(''))
  return parts.join('')
}

export interface PendingDisplayRange {
  issue: ReviewIssue
  /** issues 数组内的全局索引。 */
  index: number
  /** currentText 内的 codepoint 位置（渲染 HTML 前需按 codepoint 切片）。 */
  start: number
  end: number
}

/** 已被接受的补丁覆盖的 pending issue 不再高亮，避免把冲突建议标到另一段文字上。 */
export function pendingDisplayRanges(
  source: string,
  issues: readonly ReviewIssue[],
  patches: readonly ReviewPatch[] = getReviewPatches(source, issues),
): PendingDisplayRange[] {
  const ranges: PendingDisplayRange[] = []
  issues.forEach((issue, index) => {
    if (issue._accepted || issue._ignored || !isLocatedReviewIssue(source, issue)) return
    if (patches.some(patch => reviewPatchesOverlap(patch, issue))) return
    const delta = patches.filter(patch => patch.end <= issue.start)
      .reduce((sum, patch) => sum + Array.from(patch.replacement).length - (patch.end - patch.start), 0)
    ranges.push({ issue, index, start: issue.start + delta, end: issue.end + delta })
  })
  return ranges
}

/** 返回独立的 JSON-safe 快照；无需保存任何 Map 或运行时撤销历史。 */
export function serializeReviewIssues(issues: readonly ReviewIssue[]): ReviewIssue[] {
  return issues.map(issue => ({
    original: issue.original,
    suggestion: issue.suggestion,
    explanation: issue.explanation,
    severity: issue.severity,
    type: issue.type,
    chunk_index: issue.chunk_index,
    start: issue.start,
    end: issue.end,
    _accepted: !!issue._accepted,
    _ignored: !!issue._ignored,
    ...(issue._patch ? { _patch: { ...issue._patch } } : {}),
  }))
}

export function reviewStateFingerprint(issues: readonly ReviewIssue[], coverage?: {
  status: string; total_chunks: number; completed_chunks: number
  failed_chunks: { chunk_index: number; start: number; end: number; text: string }[]
} | null): string {
  return JSON.stringify([
    issues.map(i => [i.start ?? -1, i.end ?? -1, i.original, i.suggestion, i.type,
      i.explanation || '', i.severity, !!i._accepted, !!i._ignored]),
    coverage ? [coverage.status, coverage.total_chunks, coverage.completed_chunks,
      coverage.failed_chunks.map(c => [c.chunk_index, c.start, c.end, c.text])] : null,
  ])
}
