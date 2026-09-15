import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ModelProofreadResult, ProofreadCoverage } from '@/api/proofread'
import type { ReviewIssue } from '@/utils/review'

vi.mock('element-plus', () => ({
  ElMessage: { warning: vi.fn(), success: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))
vi.mock('@/api/proofread', () => ({ submitIssueFeedbackApi: vi.fn() }))

import { submitIssueFeedbackApi } from '@/api/proofread'
import { useProofreadReview } from '@/composables/useProofreadReview'
import { compareCoverageLabel, modelReviewIssues, restoreCompareReview, serializeCompareReview } from '../compareReview'

const source = '😀帐号与帐号'
function issue(overrides: Partial<ReviewIssue> = {}): ReviewIssue {
  return { original: '帐号', suggestion: '账号', type: 'typo', severity: 'warning', explanation: '模型甲的说明', start: 1, end: 3, ...overrides }
}
function coverage(): ProofreadCoverage {
  return {
    status: 'partial', total_chunks: 2, completed_chunks: 1,
    failed_chunks: [{ chunk_index: 1, start: 4, end: 6, text: '帐号', error_code: 'timeout' }],
  }
}
function model(overrides: Partial<ModelProofreadResult> = {}): ModelProofreadResult {
  return {
    config_id: 11, config_name: '模型甲', model: 'model-a', domain: 'legal', depth: 'deep',
    success: true, error: null, elapsed_ms: 123, total_issues: 1, issues: [issue()], coverage: coverage(), ...overrides,
  }
}
function reports() {
  return { results: [
    model({ issues: [issue(), issue({ start: 4, end: 6 })] }),
    model({ config_id: 22, config_name: '模型乙', model: 'model-b', issues: [
      issue({ severity: 'error', explanation: '模型乙的独立说明' }),
      issue({ start: 4, end: 6, severity: 'info', explanation: '模型乙的第二处说明' }),
    ] }),
  ] }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(submitIssueFeedbackApi).mockResolvedValue({ saved: 1 })
})

describe('serializeCompareReview', () => {
  it('只保留模型元数据与报告白名单，清除嵌套决策、运行时 patch 和派生统计', () => {
    const input = {
      record_id: 7, consensus_originals: ['帐号'], only_in: { '11': ['帐号'] },
      results: [{ ...model(), runtime: '不持久化', issues: [{
        ...issue({ chunk_index: 0, _accepted: true, _ignored: true,
          _patch: { start: 1, end: 3, original: '帐号', replacement: '账号' } }),
        runtime: '不持久化',
      }] }],
    }
    const before = structuredClone(input)
    expect(serializeCompareReview(input)).toEqual({ results: [{
      config_id: 11, config_name: '模型甲', model: 'model-a', domain: 'legal', depth: 'deep',
      success: true, error: null, elapsed_ms: 123, coverage: coverage(),
      issues: [{
        original: '帐号', suggestion: '账号', type: 'typo', severity: 'warning', explanation: '模型甲的说明',
        chunk_index: 0, start: 1, end: 3, _accepted: false, _ignored: false,
      }],
    }] })
    expect(input).toEqual(before)
  })

  it.each([undefined, null, -1])('未定位坐标 %s 统一为 null，不影响有效的零起始位置', position => {
    const snapshot = serializeCompareReview({ results: [model({
      domain: undefined, depth: '', error: undefined, coverage: undefined,
      issues: [issue({ start: position, end: position, explanation: undefined }), issue({ start: 0, end: 2 })],
    })] })!
    expect(snapshot.results[0]).toMatchObject({ domain: 'general', depth: 'standard', error: null, coverage: null })
    expect(snapshot.results[0].issues[0]).toMatchObject({ start: null, end: null, explanation: '', _accepted: false, _ignored: false })
    expect(snapshot.results[0].issues[1]).toMatchObject({ start: 0, end: 2 })
  })

  it('普通模式缺失 compare 和显式 null 均返回 null', () => {
    expect(serializeCompareReview()).toBeNull()
    expect(serializeCompareReview(null)).toBeNull()
  })

  it('issues、coverage 和失败段均是独立快照，不修改输入或共享模型对象', () => {
    const shared = model()
    const input = { results: [shared, { ...shared, config_id: 22 }] }
    const before = structuredClone(input)
    const snapshot = serializeCompareReview(input)!
    snapshot.results[0].issues[0].explanation = '只修改快照'
    snapshot.results[0].coverage!.failed_chunks[0].error_code = 'retry'
    expect(input).toEqual(before)
    expect(snapshot.results[1].issues[0].explanation).toBe('模型甲的说明')
    expect(snapshot.results[1].coverage!.failed_chunks[0].error_code).toBe('timeout')
  })
})

describe('restoreCompareReview', () => {
  it('恢复逐模型说明、严重度、coverage 与失败状态，重新计算数量，不重放嵌套 flags', () => {
    const snapshot = reports()
    snapshot.results[0].issues[0]._accepted = true
    snapshot.results[1].issues[0]._ignored = true
    snapshot.results.push(model({ config_id: 33, success: false, error: 'timeout', issues: [], total_issues: 99, coverage: null }))
    const before = structuredClone(snapshot)
    const restored = restoreCompareReview(source, snapshot)
    expect(restored.results.map(result => result.total_issues)).toEqual([2, 2, 0])
    expect(restored.results[0].issues[0]).toMatchObject({ start: 1, end: 3, severity: 'warning', explanation: '模型甲的说明' })
    expect(restored.results[1].issues[0]).toMatchObject({ start: 1, end: 3, severity: 'error', explanation: '模型乙的独立说明' })
    expect(restored.results[2]).toMatchObject({ config_id: 33, success: false, error: 'timeout', coverage: null })
    expect(restored.results.flatMap(result => result.issues).every(item => !item._accepted && !item._ignored)).toBe(true)
    expect(restored.consensus_originals).toEqual([])
    expect(restored.only_in).toEqual({})
    restored.results[0].issues[0].explanation = '本地编辑'
    restored.results[0].coverage!.failed_chunks[0].error_code = 'retry'
    expect(restored.results[1].issues[0].explanation).toBe('模型乙的独立说明')
    expect(restored.results[1].coverage!.failed_chunks[0].error_code).toBe('timeout')
    expect(snapshot).toEqual(before)
  })

  it('同一输入恢复两次不共享 issues/coverage；未定位报告不猜测为重复词的任一处', () => {
    const snapshot = { results: [model({ issues: [issue({ start: null, end: null, _accepted: true })] })] }
    const first = restoreCompareReview(source, snapshot)
    const second = restoreCompareReview(source, snapshot)
    expect(first.results[0].issues).toEqual([expect.objectContaining({ start: -1, end: -1, _accepted: false })])
    first.results[0].issues[0]._ignored = true
    first.results[0].coverage!.failed_chunks.splice(0)
    expect(second.results[0].issues[0]._ignored).toBe(false)
    expect(second.results[0].coverage!.failed_chunks).toHaveLength(1)
  })
})

describe('modelReviewIssues', () => {
  it('顶层是唯一决策源，投影 flags/patch 但保留逐模型说明、严重度和 chunk', () => {
    const nested = [issue({ _ignored: true, chunk_index: 8 }), issue({ start: 4, end: 6, _accepted: true }), issue({ suggestion: '账户', _accepted: true })]
    const decisions = [issue({ _accepted: true, explanation: '综合说明', severity: 'error',
      _patch: { start: 1, end: 3, original: '帐号', replacement: '账号' } })]
    const before = structuredClone({ nested, decisions })
    const projected = modelReviewIssues(nested, decisions)
    expect(projected[0]).toMatchObject({ _accepted: true, _ignored: false, _patch: decisions[0]._patch, explanation: '模型甲的说明', severity: 'warning', chunk_index: 8 })
    expect(projected.slice(1)).toEqual([
      expect.objectContaining({ _accepted: false, _ignored: false, _patch: undefined }),
      expect.objectContaining({ _accepted: false, _ignored: false, _patch: undefined }),
    ])
    expect(projected[0]).not.toBe(nested[0])
    expect(projected[0]).not.toBe(decisions[0])
    expect({ nested, decisions }).toEqual(before)
  })

  it.each(['typo', 'style'])('从模型明细采纳、撤销与忽略跨模型同步（乙报告类型 %s），重复词不串位置', type => {
    const snapshot = reports()
    snapshot.results[1].issues.forEach(item => { item.type = type })
    const restored = restoreCompareReview(source, snapshot)
    const review = useProofreadReview()
    review.initialize(source, restored.results.flatMap(result => result.issues))
    const views = () => restored.results.map(result => modelReviewIssues(result.issues, review.issues.value))
    expect(review.acceptIssue(views()[0][0])).toBe(true)
    expect(views().map(items => items.map(item => item._accepted))).toEqual([[true, false], [true, false]])
    expect(review.currentText.value).toBe('😀账号与帐号')
    expect(review.patches.value).toHaveLength(1)
    expect(views()[1][0]).toMatchObject({ severity: 'error', explanation: '模型乙的独立说明' })
    expect(review.undoIssue(views()[1][0])).toBe(true)
    expect(views().flat().every(item => !item._accepted && !item._patch)).toBe(true)
    expect(review.currentText.value).toBe(source)
    expect(review.ignoreIssue(views()[1][1])).toBe(true)
    expect(views().map(items => items.map(item => item._ignored))).toEqual([[false, true], [false, true]])
    expect(review.undoIssue(views()[0][1])).toBe(true)
    expect(views().flat().every(item => !item._ignored)).toBe(true)
    expect(restored.results.flatMap(result => result.issues).every(item => !item._accepted && !item._ignored)).toBe(true)
  })

  it('补查新增模型归属后保留该模型说明并继承顶层决策，序列化再恢复仍可跨模型撤销', () => {
    const restored = restoreCompareReview(source, reports())
    const review = useProofreadReview()
    review.initialize(source, restored.results[0].issues)
    review.acceptIssue(review.issues.value[0])
    review.ignoreIssue(review.issues.value[1])
    review.mergeIssues(restored.results[1].issues)
    const snapshot = serializeCompareReview(restored)!
    const savedDecisions = JSON.parse(JSON.stringify(review.serializeIssues())) as ReviewIssue[]
    const other = useProofreadReview()
    other.restore(source, savedDecisions)
    const reopened = restoreCompareReview(source, snapshot)
    const modelB = () => modelReviewIssues(reopened.results[1].issues, other.issues.value)
    expect(modelB()[0]).toMatchObject({ _accepted: true, severity: 'error', explanation: '模型乙的独立说明' })
    expect(modelB()[1]).toMatchObject({ _ignored: true, severity: 'info', explanation: '模型乙的第二处说明' })
    expect(other.currentText.value).toBe('😀账号与帐号')
    expect(other.undoIssue(modelB()[0])).toBe(true)
    expect(modelReviewIssues(reopened.results[0].issues, other.issues.value)[0]._accepted).toBe(false)
    expect(other.currentText.value).toBe(source)
    expect(review.currentText.value).toBe('😀账号与帐号')
  })
})

describe('compareCoverageLabel', () => {
  it('区分完整、失败、部分完成和历史未记录 coverage，不能宣称所有模型已完成', () => {
    const complete: ProofreadCoverage = { status: 'complete', total_chunks: 1, completed_chunks: 1, failed_chunks: [] }
    expect(compareCoverageLabel({ results: [model({ coverage: complete }), model({ coverage: complete })] })).toBe('所有模型已完成')
    expect(compareCoverageLabel({ results: [model({ coverage: complete }), model()] })).toBe('部分模型未完成')
    expect(compareCoverageLabel({ results: [model({ coverage: complete }), model({ success: false, coverage: null })] })).toBe('部分模型未完成')
    expect(compareCoverageLabel({ results: [model({ coverage: complete }), model({ coverage: null })] })).toContain('无法确认全文完成')
  })
})
