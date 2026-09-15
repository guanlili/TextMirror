import { beforeEach, describe, expect, it, vi } from 'vitest'
import { isReadonly } from 'vue'

vi.mock('element-plus', () => ({
  ElMessage: { warning: vi.fn(), success: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))
vi.mock('@/api/proofread', () => ({ submitIssueFeedbackApi: vi.fn() }))

import { ElMessage, ElMessageBox } from 'element-plus'
import { submitIssueFeedbackApi } from '@/api/proofread'
import { useProofreadReview, type ReviewIssue } from '../useProofreadReview'

function raw(overrides: Partial<ReviewIssue> = {}): ReviewIssue {
  return { original: '帐号', suggestion: '账号', type: 'typo', severity: 'error', ...overrides }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(submitIssueFeedbackApi).mockResolvedValue({ saved: 1 })
  vi.mocked(ElMessageBox.confirm).mockResolvedValue('confirm' as Awaited<ReturnType<typeof ElMessageBox.confirm>>)
})

describe('position-based review', () => {
  it('initialize 重置原文和问题，currentText 是只读 computed，保留筛选/索引/统计接口', () => {
    const review = useProofreadReview()
    const issues = review.initialize('😀帐号帐号', [raw({ _accepted: true }), raw({ type: 'style' })])
    expect(isReadonly(review.currentText)).toBe(true)
    expect(issues).toBe(review.issues.value)
    expect(issues).toHaveLength(4)
    expect(review.pendingCount.value).toBe(4)
    review.filterType.value = 'style'
    expect(review.filteredIssues.value).toEqual(issues.slice(2))
    expect(review.getGlobalIndex(issues[2])).toBe(2)
    expect(review.getGlobalIndex(raw())).toBe(-1)
    review.activeIssueIndex.value = 2
    review.acceptIssue(issues[0])
    expect(review.acceptedCount.value).toBe(2) // 同 span/suggestion 跨 type 共享决策
    expect(review.patches.value).toHaveLength(1)
    expect(review.sourceText.value).toBe('😀帐号帐号')
    review.initialize('新原文', [])
    expect(review.currentText.value).toBe('新原文')
    expect(review.patches.value).toEqual([])
    expect(review.issues.value).toEqual([])
    expect(review.activeIssueIndex.value).toBe(-1)
    expect(review.filterType.value).toBe('')
  })

  it('acceptIssue 仅修改本处，undo 不会把原本正确的 replacement 改回', () => {
    const review = useProofreadReview()
    const [first, second] = review.initialize('😀帐号、账号、帐号', [raw()])
    expect(review.acceptIssue(second)).toBe(true)
    expect(review.currentText.value).toBe('😀帐号、账号、账号')
    expect(first._accepted).toBe(false)
    expect(second._accepted).toBe(true)
    expect(review.patches.value).toEqual([{ start: 7, end: 9, original: '帐号', replacement: '账号' }])
    expect(review.undoIssue(second)).toBe(true)
    expect(review.currentText.value).toBe('😀帐号、账号、帐号')
    expect(review.patches.value).toEqual([])
  })

  it('acceptMatching 仅作用于已报告的同 original+suggestion，跳过忽略项及不同建议', () => {
    const review = useProofreadReview()
    const items = review.initialize('帐号|帐号|帐号|帐号|帐号', [
      raw({ start: 0, end: 2 }), raw({ start: 3, end: 5 }), raw({ start: 6, end: 8 }),
      raw({ start: 9, end: 11, suggestion: '账户' }),
    ])
    review.ignoreIssue(items[1])
    expect(review.acceptMatching(items[0])).toBe(2)
    expect(review.currentText.value).toBe('账号|帐号|账号|帐号|帐号')
    expect(items[1]).toMatchObject({ _ignored: true, _accepted: false })
    expect(items[3]._accepted).toBe(false)
    expect(review.acceptMatching(items[0])).toBe(0)
  })

  it('ignore 不能误撤销已接受项；撤销忽略后才可再次接受', () => {
    const review = useProofreadReview()
    const [issue] = review.initialize('帐号', [raw()])
    expect(review.ignoreIssue(issue)).toBe(true)
    expect(review.acceptIssue(issue)).toBe(false)
    expect(review.pendingCount.value).toBe(0)
    expect(review.currentText.value).toBe('帐号')
    expect(review.undoIssue(issue)).toBe(true)
    expect(review.pendingCount.value).toBe(1)
    expect(review.acceptIssue(issue)).toBe(true)
    expect(review.ignoreIssue(issue)).toBe(false)
    expect(review.currentText.value).toBe('账号')
    expect(review.acceptIssue(issue)).toBe(false)
    expect(review.undoIssue(issue)).toBe(true)
    expect(review.undoIssue(issue)).toBe(false)
    expect(submitIssueFeedbackApi).toHaveBeenCalledTimes(2)
  })

  it('未定位、错误显式坐标、无建议和未注册项都不能接受，并提示人工核对', () => {
    const review = useProofreadReview()
    const items = review.initialize('😀帐号', [
      raw({ original: '未找到' }), raw({ start: 2, end: 4 }), raw({ suggestion: '' }),
    ])
    for (const issue of items) expect(review.acceptIssue(issue)).toBe(false)
    expect(review.acceptIssue(raw({ start: 1, end: 3 }))).toBe(false)
    expect(review.acceptMatching(raw())).toBe(0)
    expect(ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('人工核对'))
    expect(items.every(issue => !issue._accepted)).toBe(true)
    expect(review.currentText.value).toBe('😀帐号')
    expect(submitIssueFeedbackApi).not.toHaveBeenCalled()
    expect(review.ignoreIssue(items[0])).toBe(true)
  })

  it('同位置不同建议不能同步；冲突失败时不置 accepted、不上报', () => {
    const review = useProofreadReview()
    const [first, conflict] = review.initialize('帐号', [raw(), raw({ suggestion: '账户' })])
    review.acceptIssue(first)
    expect(conflict._accepted).toBe(false)
    expect(review.acceptIssue(conflict)).toBe(false)
    expect(conflict._accepted).toBe(false)
    expect(ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('重叠'))
    expect(submitIssueFeedbackApi).toHaveBeenCalledTimes(1)
    review.undoIssue(first)
    expect(review.acceptIssue(conflict)).toBe(true)
    expect(review.currentText.value).toBe('账户')
    expect(first._accepted).toBe(false)
  })

  it('替换结果不会成为下一次替换的搜索目标', () => {
    const review = useProofreadReview()
    const [first, second] = review.initialize('甲乙', [
      raw({ original: '甲', suggestion: '乙' }), raw({ original: '乙', suggestion: '丙😀' }),
    ])
    review.acceptIssue(first)
    review.acceptIssue(second)
    expect(review.currentText.value).toBe('乙丙😀')
    review.undoIssue(first)
    expect(review.currentText.value).toBe('甲丙😀')
    review.undoIssue(second)
    expect(review.currentText.value).toBe('甲乙')
  })
})

describe('merge and model sharing', () => {
  it('重复报告去重，补查返回原对象且保留 accepted/ignored，不接受补查传来的 flags', () => {
    const review = useProofreadReview()
    review.initialize('帐号帐号新词', [])
    const modelA = review.mergeIssues([raw(), raw({ chunk_index: 9 })])
    review.acceptIssue(modelA[0])
    review.ignoreIssue(modelA[1])
    const modelB = review.mergeIssues([raw(), raw({ original: '新词', suggestion: '新😀词', _accepted: true })])
    expect(modelB[0]).toBe(modelA[0])
    expect(modelB[1]).toBe(modelA[1])
    expect(modelB[0]._accepted).toBe(true)
    expect(modelB[1]._ignored).toBe(true)
    expect(modelB[2]._accepted).toBe(false)
    expect(review.issues.value).toHaveLength(3)
    expect(review.currentText.value).toBe('账号帐号新词')
    expect(review.mergeIssues([])).toEqual([])
    expect(review.currentText.value).toBe('账号帐号新词')
    review.undoIssue(modelB[0])
    expect(modelA[0]._accepted).toBe(false)
    expect(review.currentText.value).toBe('帐号帐号新词')
  })

  it('相同位置/建议跨 type 共用决策，冲突建议不继承 accepted 或 ignored', () => {
    const review = useProofreadReview()
    const [first] = review.initialize('帐号', [raw()])
    review.acceptIssue(first)
    const [same, conflict] = review.mergeIssues([raw({ type: 'style' }), raw({ suggestion: '账户' })])
    expect(same._accepted).toBe(true)
    expect(conflict._accepted).toBe(false)
    expect(review.patches.value).toHaveLength(1)
    review.undoIssue(same)
    expect(first._accepted).toBe(false)
    review.ignoreIssue(same)
    expect(first._ignored).toBe(true)
    expect(conflict._ignored).toBe(false)
    const [ignoredPeer] = review.mergeIssues([raw({ type: 'grammar' })])
    expect(ignoredPeer._ignored).toBe(true)
    expect(review.acceptIssue(conflict)).toBe(true)
    expect(first._ignored).toBe(true)
    expect(review.currentText.value).toBe('账户')
  })

  it('补查定位始终基于原文，不受先前长度变化影响，也不清空已有成功状态', () => {
    const review = useProofreadReview()
    const [first] = review.initialize('😀帐号|错词', [raw({ suggestion: '新的😀账号' })])
    review.acceptIssue(first)
    const [added] = review.mergeIssues([raw({ original: '错词', suggestion: '词', start: 4, end: 6 })])
    expect(added).toMatchObject({ start: 4, end: 6 })
    review.acceptIssue(added)
    expect(review.currentText.value).toBe('😀新的😀账号|词')
    review.undoIssue(first)
    expect(review.currentText.value).toBe('😀帐号|词')
    expect(added._accepted).toBe(true)
  })
})

describe('batch and overlap handling', () => {
  it('批量长 span 优先，重叠短 span 保持 pending，数量按成功补丁去重', () => {
    const review = useProofreadReview()
    const items = review.initialize('权力机关行使权力', [
      raw({ original: '权力', suggestion: '权利' }),
      raw({ original: '权力机关', suggestion: '权力机构', type: 'style' }),
    ])
    expect(review.applyIssues([items[0], items[1], items[2], items[2]])).toBe(2)
    expect(review.currentText.value).toBe('权力机构行使权利')
    expect(items[0]._accepted).toBe(false)
    expect(items[1]._accepted).toBe(true)
    expect(items[2]._accepted).toBe(true)
    expect(review.pendingCount.value).toBe(1)
    expect(ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('重叠'))
    expect(vi.mocked(submitIssueFeedbackApi).mock.calls[0][0].items).toHaveLength(2)
    review.undoIssue(items[2])
    expect(review.acceptIssue(items[0])).toBe(true)
    expect(review.currentText.value).toBe('权利机关行使权利')
  })

  it('批量按 codepoint span 长度排序，不按 UTF-16 字符串长度；只接受指定分级 targets', () => {
    const review = useProofreadReview()
    const items = review.initialize('😀😀甲乙丙', [
      raw({ original: '😀😀', suggestion: '短', severity: 'warning' }),
      raw({ original: '😀甲乙', suggestion: '长', severity: 'error' }),
      raw({ original: '丙', suggestion: '丁', severity: 'info' }),
    ])
    expect(review.applyIssues(items.slice(0, 2))).toBe(1)
    expect(review.currentText.value).toBe('😀长丙')
    expect(items[0]._accepted).toBe(false)
    expect(items[2]._accepted).toBe(false)
  })

  it('敏感词删除计算的紧邻标点也参与冲突检测', () => {
    const review = useProofreadReview()
    const [word, punct] = review.initialize('禁词，正常', [
      raw({ original: '禁词', suggestion: '', type: 'sensitive' }),
      raw({ original: '，', suggestion: '。', type: 'punctuation' }),
    ])
    review.acceptIssue(punct)
    expect(review.deleteIssue(word)).toBe(false)
    expect(word._accepted).toBe(false)
    expect(review.currentText.value).toBe('禁词。正常')
    review.undoIssue(punct)
    expect(review.deleteIssue(word)).toBe(true)
    expect(review.acceptIssue(punct)).toBe(false)
    expect(review.currentText.value).toBe('正常')
  })

  it('handleAcceptAll 确认后汇报实际成功数，跳过 ignored 和已接受项', async () => {
    const review = useProofreadReview()
    const items = review.initialize('帐号禁词，帐号', [
      raw(), raw({ original: '帐号禁词', suggestion: '长词' }),
      raw({ original: '禁词', suggestion: '', type: 'sensitive' }),
      raw({ original: '不存在' }),
    ])
    review.ignoreIssue(items[1])
    expect(await review.handleAcceptAll()).toBe(1)
    expect(ElMessageBox.confirm).toHaveBeenCalledOnce()
    expect(ElMessage.success).toHaveBeenCalledWith('已接受 1 条修改')
    expect(review.currentText.value).toBe('长词，帐号')
    expect(review.patches.value).toHaveLength(1)
    expect(items[0]._accepted).toBe(false)
    expect(items[4]._accepted).toBe(false)
  })

  it('批量取消或无待处理问题时不改文本、不上报、不显示成功', async () => {
    const review = useProofreadReview()
    review.initialize('帐号', [raw()])
    vi.mocked(ElMessageBox.confirm).mockRejectedValueOnce('cancel')
    expect(await review.handleAcceptAll()).toBe(0)
    expect(review.currentText.value).toBe('帐号')
    expect(submitIssueFeedbackApi).not.toHaveBeenCalled()
    expect(ElMessage.success).not.toHaveBeenCalled()
    review.initialize('正常', [])
    expect(await review.handleAcceptAll()).toBe(0)
    expect(ElMessageBox.confirm).toHaveBeenCalledOnce()
  })

  it('确认框期间 initialize 新原文，不将旧批量目标套到新审阅', async () => {
    const review = useProofreadReview()
    review.initialize('帐号旧文', [raw()])
    const confirmation = review.handleAcceptAll()
    review.initialize('帐号新文', [raw()])
    expect(await confirmation).toBe(0)
    expect(review.currentText.value).toBe('帐号新文')
    expect(review.acceptedCount.value).toBe(0)
    expect(submitIssueFeedbackApi).not.toHaveBeenCalled()
  })

  it('同建议重复 targets/共享报告只计一次实际应用', () => {
    const review = useProofreadReview()
    const items = review.initialize('帐号帐号', [raw(), raw({ type: 'style' })])
    expect(review.applyIssues([...items, ...items])).toBe(2)
    expect(review.currentText.value).toBe('账号账号')
    expect(review.patches.value).toHaveLength(2)
    expect(review.applyIssues(items)).toBe(0)
    expect(submitIssueFeedbackApi).toHaveBeenCalledOnce()
  })
})

describe('deletion and arbitrary-order undo', () => {
  it('敏感词多处带标点只删当前处，可撤销后对已报告同类全文删除', () => {
    const review = useProofreadReview()
    const source = '😀禁词，甲禁词！乙禁词。'
    const items = review.initialize(source, [raw({ original: '禁词', suggestion: '', type: 'sensitive' })])
    expect(review.deleteIssue(items[1])).toBe(true)
    expect(review.currentText.value).toBe('😀禁词，甲乙禁词。')
    expect(items[1]._patch).toEqual({ start: 5, end: 8, original: '禁词！', replacement: '' })
    expect(items[0]._accepted).toBe(false)
    expect(items[2]._accepted).toBe(false)
    review.undoIssue(items[1])
    expect(review.currentText.value).toBe(source)
    expect(review.acceptMatching(items[0])).toBe(3)
    expect(review.currentText.value).toBe('😀甲乙')
    review.undoIssue(items[1])
    expect(review.currentText.value).toBe('😀甲禁词！乙')
    review.undoIssue(items[0])
    expect(review.currentText.value).toBe('😀禁词，甲禁词！乙')
    review.undoIssue(items[2])
    expect(review.currentText.value).toBe(source)
  })

  it.each([[0, 1, 2], [0, 2, 1], [1, 0, 2], [1, 2, 0], [2, 0, 1], [2, 1, 0]])(
    '不同顺序 undo %j %j %j 均保留其余补丁，处理 emoji/长度变化/删除', (a, b, c) => {
      const review = useProofreadReview()
      const source = '😀帐号|禁词，|😀|账号'
      const items = review.initialize(source, [
        raw({ start: 1, end: 3, suggestion: '长😀账号' }),
        raw({ original: '禁词', suggestion: '', type: 'sensitive' }),
        raw({ original: '😀', suggestion: '短', start: 8, end: 9 }),
      ])
      expect(review.applyIssues(items)).toBe(3)
      const remaining = new Set([0, 1, 2])
      for (const index of [a, b, c]) {
        review.undoIssue(items[index])
        remaining.delete(index)
        expect(review.currentText.value).toBe(
          '😀' + (remaining.has(0) ? '长😀账号' : '帐号')
          + '|' + (remaining.has(1) ? '' : '禁词，')
          + '|' + (remaining.has(2) ? '短' : '😀') + '|账号',
        )
        expect(review.sourceText.value).toBe(source)
        expect(review.patches.value).toHaveLength(remaining.size)
      }
      expect(review.currentText.value).toBe(source)
    },
  )
})

describe('restore and feedback', () => {
  it('JSON snapshot 恢复真实删除/替换/忽略状态，无隐藏 Map，恢复后任意顺序撤销', () => {
    const review = useProofreadReview()
    const source = '😀帐号|禁词！|帐号'
    const [first, last, sensitive] = review.initialize(source, [
      raw({ suggestion: '新😀账号' }),
      raw({ original: '禁词', suggestion: '替代词', type: 'sensitive' }),
    ])
    review.acceptIssue(first)
    review.ignoreIssue(last)
    review.deleteIssue(sensitive)
    const serialized = JSON.parse(JSON.stringify(review.serializeIssues())) as ReviewIssue[]
    const other = useProofreadReview()
    vi.mocked(submitIssueFeedbackApi).mockClear()
    other.restore(source, serialized)
    expect(other.currentText.value).toBe('😀新😀账号||帐号')
    expect(other.patches.value).toEqual(review.patches.value)
    expect(other.issues.value[1]._ignored).toBe(true)
    expect(submitIssueFeedbackApi).not.toHaveBeenCalled()
    other.undoIssue(other.issues.value[0])
    expect(other.currentText.value).toBe('😀帐号||帐号')
    other.undoIssue(other.issues.value[2])
    expect(other.currentText.value).toBe(source)
    expect(review.currentText.value).toBe('😀新😀账号||帐号')
  })

  it('仅坐标和 flags 的合法普通建议/敏感词也能恢复；忽略仍保留', () => {
    const review = useProofreadReview()
    review.restore('帐号禁词，帐号', [
      raw({ start: 0, end: 2, _accepted: true }),
      raw({ original: '禁词', suggestion: '', type: 'sensitive', start: 2, end: 4, _accepted: true }),
      raw({ start: 5, end: 7, _ignored: true }),
    ])
    expect(review.currentText.value).toBe('账号帐号')
    expect(review.patches.value).toHaveLength(2)
    expect(review.issues.value[2]._ignored).toBe(true)
    expect(review.issues.value[1]._patch).toEqual({ start: 2, end: 5, original: '禁词，', replacement: '' })
    expect(ElMessage.warning).not.toHaveBeenCalled()
    expect(submitIssueFeedbackApi).not.toHaveBeenCalled()
  })

  it('恢复时不将无坐标旧 accepted 广播到全文；错误坐标/伪造 patch 留待人工核对', () => {
    const review = useProofreadReview()
    review.restore('😀帐号帐号', [
      raw({ _accepted: true }),
      raw({ start: 2, end: 4, _accepted: true }),
      raw({ start: 1, end: 3, suggestion: '账户', _accepted: true,
        _patch: { start: 3, end: 5, original: '帐号', replacement: '篡改' } }),
    ])
    expect(review.currentText.value).toBe('😀帐号帐号')
    expect(review.acceptedCount.value).toBe(0)
    expect(review.patches.value).toEqual([])
    expect(ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('人工核对'))
  })

  it('未定位报告的合法忽略状态也可 round-trip，但不将无坐标忽略广播到全文', () => {
    const review = useProofreadReview()
    const [unlocated] = review.initialize('帐号帐号', [raw({ original: '不存在' })])
    review.ignoreIssue(unlocated)
    const saved = JSON.parse(JSON.stringify(review.serializeIssues())) as ReviewIssue[]
    review.restore('帐号帐号', saved)
    expect(review.issues.value[0]).toMatchObject({ start: -1, end: -1, _ignored: true, _accepted: false })
    expect(review.pendingCount.value).toBe(0)
    expect(ElMessage.warning).not.toHaveBeenCalled()
    review.restore('帐号帐号', [raw({ _ignored: true })])
    expect(review.issues.value.every(issue => !issue._ignored)).toBe(true)
    expect(review.currentText.value).toBe('帐号帐号')
    expect(ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('人工核对'))
  })

  it('恢复冲突长 span 优先，不同建议不被静默接受，同 span/suggestion 共享决策', () => {
    const review = useProofreadReview()
    const items = review.restore('权力机关', [
      raw({ original: '权力', suggestion: '权利', start: 0, end: 2, _accepted: true }),
      raw({ original: '权力机关', suggestion: '权力机构', start: 0, end: 4, _accepted: true }),
      raw({ original: '权力机关', suggestion: '权力机构', type: 'style', start: 0, end: 4 }),
      raw({ original: '权力机关', suggestion: '其他机构', start: 0, end: 4, _accepted: true }),
    ])
    expect(review.currentText.value).toBe('权力机构')
    expect(items.map(issue => issue._accepted)).toEqual([false, true, true, false])
    expect(review.patches.value).toHaveLength(1)
    expect(ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('重叠'))
    expect(submitIssueFeedbackApi).not.toHaveBeenCalled()
  })

  it('保留 reportFeedback 和 recordId，上报只有实际成功的接受/忽略；失败不影响审阅', async () => {
    const review = useProofreadReview()
    review.recordId.value = 42
    const [first, second] = review.initialize('帐号帐号', [raw()])
    vi.mocked(submitIssueFeedbackApi).mockRejectedValueOnce(new Error('offline'))
    expect(review.acceptIssue(first)).toBe(true)
    expect(submitIssueFeedbackApi).toHaveBeenNthCalledWith(1, {
      record_id: 42, items: [{ original: '帐号', suggestion: '账号', issue_type: 'typo', action: 'accept' }],
    })
    review.ignoreIssue(second)
    expect(submitIssueFeedbackApi).toHaveBeenNthCalledWith(2, {
      record_id: 42, items: [{ original: '帐号', suggestion: '账号', issue_type: 'typo', action: 'ignore' }],
    })
    review.reportFeedback([], 'accept')
    review.undoIssue(first)
    await Promise.resolve()
    expect(review.currentText.value).toBe('帐号帐号')
    expect(submitIssueFeedbackApi).toHaveBeenCalledTimes(2)
  })
})
