import { describe, expect, it } from 'vitest'
import type { ReviewIssue } from '../review'
import { buildCompareSummary, compareSummaryProvenance } from '../compareSummary'
import { modelReviewIssues, restoreCompareReview, serializeCompareReview } from '../compareReview'

const issue = (overrides: Partial<ReviewIssue> = {}): ReviewIssue => ({
  original: '帐号', suggestion: '账号', type: 'typo', severity: 'warning', start: 1, end: 3, ...overrides,
})
const model = (id: number, issues = [issue()], success = true) => ({
  config_id: id, config_name: `模型${id}`, model: `model-${id}`, elapsed_ms: 10, success, issues,
})

describe('多模型统一建议清单', () => {
  it('所有参与模型均成功且报告相同位置及修改时才算共同发现', () => {
    const [item] = buildCompareSummary([model(1), model(2), model(3)])
    expect(item).toMatchObject({ isConsensus: true, modelCount: 3, totalModels: 3 })
    expect(compareSummaryProvenance(item)).toBe('共同发现：模型1、模型2、模型3（3/3）')
  })

  it.each([true, false])('2/3 是仅部分发现，无论第三个模型是否成功（%s）', success => {
    const [item] = buildCompareSummary([model(1), model(2), model(3, [], success)])
    expect(item).toMatchObject({ isConsensus: false, modelCount: 2, totalModels: 3 })
    expect(compareSummaryProvenance(item)).toBe('仅部分模型发现：模型1、模型2（2/3）')
  })

  it('失败模型附带的报告不作为成功的共同发现', () => {
    const [item] = buildCompareSummary([model(1), model(2, [issue()], false)])
    expect(item).toMatchObject({ isConsensus: false, modelCount: 1, totalModels: 2 })
  })

  it('同一模型重复报告或重复配置不增加票数，也不能形成共同发现', () => {
    const [item] = buildCompareSummary([model(1, [issue(), issue()]), model(1)])
    expect(item).toMatchObject({ modelCount: 1, totalModels: 1, isConsensus: false })
  })

  it('重复词不同位置及同位置不同建议保持独立', () => {
    const items = buildCompareSummary([
      model(1),
      model(2, [issue({ start: 4, end: 6 }), issue({ suggestion: '账户' })]),
    ])
    expect(items).toHaveLength(3)
    expect(items.every(item => !item.isConsensus && item.modelCount === 1)).toBe(true)
  })

  it('相同修改跨类型合并但不丢弃各模型独立说明和严重度', () => {
    const reports = [model(1), model(2, [issue({ type: 'style', severity: 'error', explanation: '独立说明' })])]
    const before = structuredClone(reports)
    const [item] = buildCompareSummary(reports)
    expect(item).toMatchObject({ modelCount: 2, isConsensus: true })
    expect(reports).toEqual(before)
    expect(item.issue).toBe(reports[0].issues[0])
  })

  it('未定位报告即使所有模型均有报告，也不宣称相同位置共同发现', () => {
    const unknown = issue({ start: -1, end: -1 })
    const [item] = buildCompareSummary([model(1, [unknown]), model(2, [unknown])])
    expect(item.isConsensus).toBe(false)
    expect(compareSummaryProvenance(item)).toContain('位置未确认，不计为共同发现')
  })

  it('人工核对与敏感词删除不合并，也不把缺失建议变为删除', () => {
    const manual = issue({ suggestion: '', type: 'consistency' })
    const items = buildCompareSummary([model(1, [manual]), model(2, [issue({ suggestion: '', type: 'sensitive' })])])
    expect(items).toHaveLength(2)
    expect(items[0].issue).toBe(manual)
    expect(items[0].issue.suggestion).toBe('')
    expect(items.every(item => !item.isConsensus)).toBe(true)
  })

  it('补查及序列化恢复后来源与顶层决策仍然保留', () => {
    const reports = { results: [model(1), model(2, []), model(3, [], false)] }
    const decision = issue({ _accepted: true, _patch: { start: 1, end: 3, original: '帐号', replacement: '账号' } })
    reports.results[1].issues.push(issue({ explanation: '补查新发现' }))
    const saved = JSON.parse(JSON.stringify({ compare: serializeCompareReview(reports), issues: [decision] }))
    const restored = restoreCompareReview('😀帐号', saved.compare)
    const projected = restored.results.map(result => ({ ...result, issues: modelReviewIssues(result.issues, saved.issues) }))
    const [item] = buildCompareSummary(projected)
    expect(item).toMatchObject({ modelCount: 2, totalModels: 3, isConsensus: false, issue: { _accepted: true, _patch: decision._patch } })
    expect(compareSummaryProvenance(item)).toBe('仅部分模型发现：模型1、模型2（2/3）')
    expect(projected[1].issues[0].explanation).toBe('补查新发现')
    expect(restored.results[2].success).toBe(false)
  })

  it('空结果返回空清单', () => {
    expect(buildCompareSummary([])).toEqual([])
    expect(buildCompareSummary([model(1, [], false), model(2, [], false)])).toEqual([])
  })
})
