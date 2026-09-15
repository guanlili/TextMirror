import { describe, expect, it } from 'vitest'
import type { ReviewIssue } from '@/composables/useProofreadReview'
import type { ReviewCompareState } from '@/api/review'
import { compareReviewVersions, renderReviewVersionText, serializeReviewDraft } from '../reviewVersions'

function issue(overrides: Partial<ReviewIssue> = {}): ReviewIssue {
  return { original: '帐号', suggestion: '账号', type: 'typo', severity: 'warning', start: 0, end: 2, _accepted: true, ...overrides }
}

const source = '帐号与帐号'

describe('compareReviewVersions', () => {
  it('原文与版本比较：只列出已采纳的原文范围', () => {
    const result = compareReviewVersions(source, [], [issue(), issue({ start: 3, end: 5, _accepted: false })])
    expect(result).toHaveLength(1)
    expect(result[0]).toMatchObject({ kind: 'added', start: 0, end: 2, original: '帐号', before: null, after: { replacement: '账号' } })
  })

  it('版本与原文比较显示撤销', () => {
    expect(compareReviewVersions(source, [issue()], [])[0]).toMatchObject({ kind: 'removed', before: { suggestion: '账号' }, after: null })
  })

  it('相同文字不同位置不能抵消，按原文位置排序', () => {
    const result = compareReviewVersions(source, [issue({ start: 3, end: 5 })], [issue()])
    expect(result.map(change => [change.kind, change.start, change.end])).toEqual([['added', 0, 2], ['removed', 3, 5]])
  })

  it('同一范围改为不同建议是改变采纳', () => {
    const result = compareReviewVersions(source, [issue()], [issue({ suggestion: '账户' })])
    expect(result).toHaveLength(1)
    expect(result[0]).toMatchObject({ kind: 'changed', before: { suggestion: '账号' }, after: { suggestion: '账户' } })
  })

  it('不同范围即使包含相同文字也分别显示新增和撤销', () => {
    const result = compareReviewVersions('帐号名称', [issue()], [issue({ original: '帐号名称', end: 4, suggestion: '账户名' })])
    expect(result.map(change => [change.kind, change.start, change.end])).toEqual([['removed', 0, 2], ['added', 0, 4]])
  })

  it('敏感词空建议表示删除，差异保留问题范围与吞标点后的补丁范围', () => {
    const deletion = issue({ original: '敏感', suggestion: '', type: 'sensitive' })
    const result = compareReviewVersions('敏感，正常', [], [deletion])
    expect(result[0]).toMatchObject({ kind: 'added', start: 0, end: 2, after: { replacement: '', patchEnd: 3 } })
    expect(renderReviewVersionText('敏感，正常', [deletion])).toBe('正常')
  })

  it('显式删除与采纳相同建议也能区分', () => {
    const deletion = issue({ _patch: { start: 0, end: 3, original: '帐号，', replacement: '' } })
    expect(compareReviewVersions('帐号，正常', [issue()], [deletion])[0]).toMatchObject({
      kind: 'changed', before: { replacement: '账号' }, after: { replacement: '', patchEnd: 3 },
    })
  })

  it('相同采纳无变化，不受报告顺序与忽略状态变化影响', () => {
    const left = [issue(), issue({ start: 3, end: 5, _accepted: false })]
    const right = [issue({ start: 3, end: 5, _accepted: false, _ignored: true }), issue()]
    expect(compareReviewVersions(source, left, right)).toEqual([])
    expect(compareReviewVersions(source, [], [])).toEqual([])
  })

  it('跨类型重复报告只形成一条采纳差异', () => {
    expect(compareReviewVersions(source, [], [issue(), issue({ type: 'style' })])).toHaveLength(1)
  })

  it('emoji 使用 Unicode codepoint 而不是 UTF-16 范围', () => {
    const text = '😀帐号👩‍💻帐号'
    const second = issue({ start: 6, end: 8, suggestion: '🪞' })
    expect(compareReviewVersions(text, [], [second])[0]).toMatchObject({ start: 6, end: 8, after: { replacement: '🪞' } })
    expect(renderReviewVersionText(text, [second])).toBe('😀帐号👩‍💻🪞')
    expect(compareReviewVersions('😀', [], [issue({ original: '😀', start: 0, end: 1, suggestion: '🪞' })])[0].end).toBe(1)
  })

  it('不猜测缺失位置或错误范围，明确拒绝不可安全对比的采纳', () => {
    expect(() => compareReviewVersions(source, [], [issue({ start: undefined, end: undefined })])).toThrow()
    expect(() => compareReviewVersions(source, [], [issue({ start: 1, end: 3 })])).toThrow()
    expect(() => compareReviewVersions(source, [], [issue(), issue({ suggestion: '账户' })])).toThrow()
  })

  it('未定位但未采纳的报告不会造成错误或改写正文', () => {
    const pending = issue({ start: undefined, end: undefined, _accepted: false })
    expect(compareReviewVersions(source, [], [pending])).toEqual([])
    expect(renderReviewVersionText(source, [pending])).toBe(source)
  })

  it('不改变传入的原文决策或 patch', () => {
    const items = [issue({ _patch: { start: 0, end: 2, original: '帐号', replacement: '账号' } })]
    const before = JSON.stringify(items)
    compareReviewVersions(source, [], items)
    renderReviewVersionText(source, items)
    expect(JSON.stringify(items)).toBe(before)
  })
})

describe('renderReviewVersionText', () => {
  it.each(['，', '。', '！', '？', '；', '、', ','])('删除一个紧邻标点 %s', punctuation => {
    const deletion = issue({ original: '敏感', suggestion: '', type: 'sensitive' })
    expect(renderReviewVersionText(`敏感${punctuation}后文`, [deletion])).toBe('后文')
  })

  it('不会吞掉前置标点、空白后的标点或第二个标点', () => {
    const deletion = issue({ original: '敏感', suggestion: '', type: 'sensitive' })
    expect(renderReviewVersionText('，敏感  后文', [{ ...deletion, start: 1, end: 3 }])).toBe('，  后文')
    expect(renderReviewVersionText('敏感，，后文', [deletion])).toBe('，后文')
  })

  it('返回纯文本，HTML 字符不变成执行内容', () => {
    expect(renderReviewVersionText(source, [issue({ suggestion: '<img src=x onerror=alert(1)>' })]))
      .toBe('<img src=x onerror=alert(1)>与帐号')
  })
})

describe('serializeReviewDraft', () => {
  const state = { sourceText: source, issues: [issue({ _accepted: false })], domain: 'general', depth: 'standard' }

  it('稳定规范化缺失 flags、可选配置与 coverage', () => {
    expect(serializeReviewDraft(state)).toBe(serializeReviewDraft({
      ...state, configId: null, coverage: null, issues: [issue({ _accepted: undefined, _ignored: false })],
    }))
  })

  it('服务端去除运行时补丁及空值规范化不造成虚假未保存', () => {
    const local = [issue({ _patch: { start: 0, end: 2, original: '帐号', replacement: '账号' } }), issue({ start: -1, end: -1, _accepted: false })]
    const remote = [issue({ explanation: '' }), issue({ start: null, end: null, _accepted: false, explanation: '' })]
    expect(serializeReviewDraft({ ...state, issues: local })).toBe(serializeReviewDraft({ ...state, issues: remote }))
  })

  it('保存期间继续采纳或改变未审范围不会与捕获快照相等', () => {
    const capture = serializeReviewDraft(state)
    expect(serializeReviewDraft({ ...state, issues: [issue()] })).not.toBe(capture)
    expect(serializeReviewDraft({
      ...state,
      coverage: { status: 'partial', total_chunks: 1, completed_chunks: 0, failed_chunks: [{ chunk_index: 0, start: 0, end: 5, text: source, error_code: 'timeout' }] },
    })).not.toBe(capture)
    expect(serializeReviewDraft({ ...state, depth: 'deep' })).not.toBe(capture)
  })

  function compare(): ReviewCompareState {
    return { results: [1, 2].map(id => ({
      config_id: id, config_name: `模型${id}`, model: `model-${id}`, domain: 'general', depth: 'standard',
      success: true, error: null, elapsed_ms: 100,
      issues: id === 1 ? [issue({ _accepted: false })] : [],
      coverage: { status: 'partial', total_chunks: 1, completed_chunks: 0,
        failed_chunks: [{ chunk_index: 0, start: 0, end: 5, text: source, error_code: 'timeout' }] },
    })) }
  }

  it('指纹包含逐模型快照，嵌套 flags/patch 不代表第二份决策且不修改输入', () => {
    const models = compare()
    models.results[0].issues[0]._accepted = true
    models.results[0].issues[0]._ignored = true
    models.results[0].issues[0]._patch = { start: 0, end: 2, original: '帐号', replacement: '账号' }
    const before = structuredClone(models)
    const capture = serializeReviewDraft({ ...state, compare: models })
    const serialized = JSON.parse(capture)
    expect(serialized.compare.results).toHaveLength(2)
    expect(serialized.compare.results[0].issues[0]).toMatchObject({ _accepted: false, _ignored: false })
    expect(serialized.compare.results[0].issues[0]).not.toHaveProperty('_patch')
    expect(capture).toBe(serializeReviewDraft({ ...state, compare: compare() }))
    expect(models).toEqual(before)
    expect(serializeReviewDraft({ ...state, compare: models, issues: [issue()] })).not.toBe(capture)
  })

  it('只有逐模型 coverage、失败原因或问题归属改变也标记 dirty，顶层问题不变', () => {
    const models = compare()
    const capture = serializeReviewDraft({ ...state, compare: models })
    const completed = structuredClone(models)
    completed.results[1].coverage = { status: 'complete', total_chunks: 1, completed_chunks: 1, failed_chunks: [] }
    expect(serializeReviewDraft({ ...state, compare: completed })).not.toBe(capture)
    const failedAgain = structuredClone(models)
    failedAgain.results[1].coverage!.failed_chunks[0].error_code = 'retry-failed'
    expect(serializeReviewDraft({ ...state, compare: failedAgain })).not.toBe(capture)
    const reassigned = structuredClone(models)
    reassigned.results[1].issues = reassigned.results[0].issues
    reassigned.results[0].issues = []
    expect(serializeReviewDraft({ ...state, compare: reassigned })).not.toBe(capture)
    const newConsensus = structuredClone(models)
    newConsensus.results[1].issues = structuredClone(newConsensus.results[0].issues)
    expect(serializeReviewDraft({ ...state, compare: newConsensus })).not.toBe(capture)
  })

  it('模型报告说明或严重度变化仍需保存，即使顶层汇总完全相同', () => {
    const models = compare()
    const capture = serializeReviewDraft({ ...state, compare: models })
    models.results[0].issues[0].explanation = '补查后的说明'
    expect(serializeReviewDraft({ ...state, compare: models })).not.toBe(capture)
    const changedSeverity = compare()
    changedSeverity.results[0].issues[0].severity = 'error'
    expect(serializeReviewDraft({ ...state, compare: changedSeverity })).not.toBe(capture)
  })

  it('compare 的 null/undefined 与嵌套可选字段服务端回读不造成假 dirty', () => {
    expect(serializeReviewDraft({ ...state, compare: undefined })).toBe(serializeReviewDraft({ ...state, compare: null }))
    const local = compare()
    local.results[0].issues = [issue({ start: -1, end: -1, _accepted: undefined, _ignored: undefined })]
    const remote = structuredClone(local)
    remote.results[0].issues = [JSON.parse(JSON.stringify({
      ...issue({ start: null, end: null, _accepted: false, _ignored: false }), explanation: null, chunk_index: null,
    }))]
    expect(serializeReviewDraft({ ...state, compare: local })).toBe(serializeReviewDraft({ ...state, compare: remote }))
    const reopened = JSON.parse(serializeReviewDraft({ ...state, compare: local }))
    expect(serializeReviewDraft(reopened)).toBe(serializeReviewDraft({ ...state, compare: local }))
  })
})
