import { describe, expect, it } from 'vitest'
import {
  createReviewPatch,
  expandReviewIssues,
  getIssuePatch,
  getReviewPatches,
  pendingDisplayRanges,
  renderReviewText,
  serializeReviewIssues,
  type ReviewIssue,
} from '../review'

function raw(overrides: Partial<ReviewIssue> = {}): ReviewIssue {
  return { original: '帐号', suggestion: '账号', type: 'typo', severity: 'error', ...overrides }
}

function accepted(source: string, issue: ReviewIssue): ReviewIssue {
  return { ...issue, _accepted: true, _patch: createReviewPatch(source, issue)! }
}

describe('expandReviewIssues', () => {
  it('按 codepoint 展开每处位置，保留报告元数据但清除原始决策', () => {
    const result = expandReviewIssues('😀帐号与帐号', [raw({
      explanation: '说明', chunk_index: 2, _accepted: true, _ignored: true,
      _patch: { start: 0, end: 1, original: '😀', replacement: 'x' },
    })])
    expect(result).toEqual([1, 4].map(start => ({
      ...raw(), explanation: '说明', chunk_index: 2, start, end: start + 2,
      _accepted: false, _ignored: false,
    })))
  })

  it('显式 start/end 仅匹配该处，不扩散到其他相同文字', () => {
    expect(expandReviewIssues('帐号与帐号', [raw({ start: 3, end: 5 })]))
      .toEqual([expect.objectContaining({ start: 3, end: 5 })])
  })

  it.each([
    { start: 2, end: 4 }, // emoji 前缀下的 UTF-16 位置，不是 codepoint
    { start: 1 },
    { end: 3 },
    { start: -1, end: 1 },
    { start: 1.5, end: 3 },
    { start: 1, end: 99 },
    { start: 3, end: 1 },
    { start: 1, end: 1 },
    { start: NaN, end: 3 },
    { start: null as unknown as number, end: 3 },
  ])('位置无效时不回退全文搜索：%j', positions => {
    const result = expandReviewIssues('😀帐号与帐号', [raw(positions)])
    expect(result).toHaveLength(1)
    expect(result[0]).toMatchObject({ start: -1, end: -1, _accepted: false })
    expect(createReviewPatch('😀帐号与帐号', result[0])).toBeNull()
  })

  it('无法定位或空 original 仍保留人工核对项', () => {
    const result = expandReviewIssues('正常', [raw(), raw({ original: '' })])
    expect(result).toHaveLength(2)
    expect(result.every(issue => issue.start === -1 && issue.end === -1)).toBe(true)
  })

  it('按 span/type/suggestion 去重，不同建议或不同类型保留', () => {
    const result = expandReviewIssues('帐号帐号', [
      raw(), raw({ explanation: '重复报告', chunk_index: 1 }), raw({ start: 0, end: 2 }),
      raw({ suggestion: '账户' }), raw({ type: 'style' }),
    ])
    expect(result).toHaveLength(6)
    expect(result.filter(issue => issue.suggestion === '账号' && issue.type === 'typo')).toHaveLength(2)
    expect(result.filter(issue => issue.suggestion === '账户')).toHaveLength(2)
    expect(result.filter(issue => issue.type === 'style')).toHaveLength(2)
  })

  it('枚举重叠 occurrence，保留冲突交由接受时校验', () => {
    expect(expandReviewIssues('aaa', [raw({ original: 'aa', suggestion: 'x' })])
      .map(issue => [issue.start, issue.end])).toEqual([[0, 2], [1, 3]])
  })
})

describe('review patches and rendering', () => {
  it('替换完全基于原文位置，不修改原本正确的建议文字，支持 emoji 长度变化', () => {
    const source = '😀帐号|账号|帐号'
    const issues = expandReviewIssues(source, [raw({ suggestion: '✅账号😀' })])
    const first = accepted(source, issues[0])
    const second = accepted(source, issues[1])
    expect(first._patch).toEqual({ start: 1, end: 3, original: '帐号', replacement: '✅账号😀' })
    expect(renderReviewText(source, getReviewPatches(source, [first, second])))
      .toBe('😀✅账号😀|账号|✅账号😀')
    expect(renderReviewText(source, getReviewPatches(source, [issues[0], second])))
      .toBe('😀帐号|账号|✅账号😀')
    expect(renderReviewText(source, [])).toBe(source)
  })

  it('敏感词只删除本处和一个紧邻后置标点，不影响别处或跨过空格', () => {
    const source = '😀禁词，甲禁词！乙禁词 丙禁词'
    const issues = expandReviewIssues(source, [raw({ original: '禁词', suggestion: '', type: 'sensitive' })])
    expect(issues.map(issue => createReviewPatch(source, issue))).toEqual([
      { start: 1, end: 4, original: '禁词，', replacement: '' },
      { start: 5, end: 8, original: '禁词！', replacement: '' },
      { start: 9, end: 11, original: '禁词', replacement: '' },
      { start: 13, end: 15, original: '禁词', replacement: '' },
    ])
    expect(renderReviewText(source, [createReviewPatch(source, issues[1])!]))
      .toBe('😀禁词，甲乙禁词 丙禁词')
  })

  it('显式删除覆盖非空建议，序列化真实补丁仍可准确回放', () => {
    const source = '禁词，正常'
    const [issue] = expandReviewIssues(source, [raw({ original: '禁词', suggestion: '替代词', type: 'sensitive' })])
    issue._accepted = true
    issue._patch = createReviewPatch(source, issue, true)!
    const snapshot = serializeReviewIssues([issue])
    expect(snapshot[0]._patch).not.toBe(issue._patch)
    const restored = JSON.parse(JSON.stringify(snapshot)) as ReviewIssue[]
    expect(renderReviewText(source, getReviewPatches(source, restored))).toBe('正常')
  })

  it('非敏感无建议项不可自动应用', () => {
    expect(createReviewPatch('帐号', raw({ start: 0, end: 2, suggestion: '' }))).toBeNull()
  })

  it('同 span/suggestion 不同 type 共享一份 patch，不同建议即使都删除也冲突', () => {
    const source = '帐号'
    const issues = expandReviewIssues(source, [raw(), raw({ type: 'style' })])
    expect(getReviewPatches(source, issues.map(issue => accepted(source, issue)))).toHaveLength(1)
    const conflicting = [raw(), raw({ suggestion: '账户' })].map(issue => ({
      ...issue, start: 0, end: 2, _accepted: true,
      _patch: createReviewPatch(source, { ...issue, start: 0, end: 2 }, true)!,
    }))
    expect(() => getReviewPatches(source, conflicting)).toThrow('重叠')
  })

  it('拒绝伪造补丁及越界/重叠补丁，不静默显示非法 accepted 状态', () => {
    const source = '帐号帐号'
    const [issue] = expandReviewIssues(source, [raw()])
    const forged = { ...accepted(source, issue), _patch: { start: 2, end: 4, original: '帐号', replacement: '盗改' } }
    expect(getIssuePatch(source, forged)).toBeNull()
    expect(() => getReviewPatches(source, [forged])).toThrow('原文不匹配')
    const patch = createReviewPatch(source, issue)!
    expect(() => renderReviewText(source, [patch, patch])).toThrow('重叠')
    expect(() => renderReviewText(source, [{ ...patch, start: -1 }])).toThrow('无效')
  })
})

describe('pendingDisplayRanges', () => {
  it('按 codepoint 映射长度变化后的 pending 位置，忽略 accepted、ignored 和冲突项', () => {
    const source = '😀帐号，禁词！帐号末'
    const issues = expandReviewIssues(source, [
      raw({ suggestion: '新😀账号' }),
      raw({ original: '禁词', suggestion: '', type: 'sensitive' }),
      raw({ start: 1, end: 3, suggestion: '账户' }),
      raw({ original: '末', suggestion: '尾' }),
      raw({ original: '不存在' }),
    ])
    issues[0] = accepted(source, issues[0])
    issues[2] = accepted(source, issues[2])
    issues[4]._ignored = true
    const patches = getReviewPatches(source, issues)
    const current = renderReviewText(source, patches)
    expect(current).toBe('😀新😀账号，帐号末')
    const ranges = pendingDisplayRanges(source, issues)
    expect(ranges).toEqual([{ issue: issues[1], index: 1, start: 6, end: 8 }])
    expect(Array.from(current).slice(ranges[0].start, ranges[0].end).join('')).toBe('帐号')
  })

  it('相邻 patch 不算重叠，恢复后坐标仍指向原文 occurrence', () => {
    const source = '帐号帐号'
    const issues = expandReviewIssues(source, [raw({ suggestion: '😀' })])
    issues[0] = accepted(source, issues[0])
    expect(pendingDisplayRanges(source, issues)[0]).toMatchObject({ index: 1, start: 1, end: 3 })
    issues[0]._accepted = false
    expect(pendingDisplayRanges(source, issues).map(({ start, end }) => [start, end])).toEqual([[0, 2], [2, 4]])
  })
})
