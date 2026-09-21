/* eslint-disable vue/one-component-per-file -- 仅用于无 DOM 的真实 SFC renderer 测试。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as Vue from 'vue'
import { createRenderer, defineComponent, h, nextTick, type Component, type VNode } from 'vue'
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'
import { ModuleKind, ScriptTarget, transpileModule } from 'typescript'
import type { FeedbackEvaluation, FeedbackSample, FeedbackStatus, QualityFeedback } from '@/api/qualityFeedback'
import type { AvailableModel } from '@/api/polish'

vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')
  const stub = (name: string) => defineComponent({
    name, inheritAttrs: false,
    setup(_props, { attrs, slots }) { return () => h(name, attrs, slots.default?.()) },
  })
  return {
    ElAlert: stub('ElAlert'), ElButton: stub('ElButton'), ElCheckbox: stub('ElCheckbox'),
    ElEmpty: stub('ElEmpty'), ElForm: stub('ElForm'), ElFormItem: stub('ElFormItem'),
    ElInput: stub('ElInput'), ElOption: stub('ElOption'), ElPagination: stub('ElPagination'),
    ElSelect: stub('ElSelect'), ElTag: stub('ElTag'), ElMessageBox: { confirm: vi.fn() },
  }
})
// 不实例化真实请求/路由/用户 store，不访问任何凭据或 localStorage。
vi.mock('@/utils/request', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }))
vi.mock('@/api/qualityFeedback', async importOriginal => ({
  ...await importOriginal<typeof import('@/api/qualityFeedback')>(),
  listQualityFeedbackApi: vi.fn(), reviewQualityFeedbackApi: vi.fn(), evaluateQualityFeedbackApi: vi.fn(),
}))
vi.mock('@/api/polish', () => ({ getAvailableModelsApi: vi.fn() }))

import * as ElementPlus from 'element-plus'
import { ElMessageBox } from 'element-plus'
import * as QualityApi from '@/api/qualityFeedback'
import { listQualityFeedbackApi, reviewQualityFeedbackApi, evaluateQualityFeedbackApi } from '@/api/qualityFeedback'
import * as PolishApi from '@/api/polish'
import { getAvailableModelsApi } from '@/api/polish'
import * as ReviewApi from '@/api/review'
import * as ProofreadUtils from '@/utils/proofread'
import * as QualityUtils from '@/utils/qualityFeedback'
import { feedbackSampleError, feedbackSnapshotLabel, feedbackTargetParts, findFeedbackTargets, initialFeedbackSample, serializeFeedbackReport } from '@/utils/qualityFeedback'
import componentSource from './QualityFeedbackAdmin.vue?raw'
import globalDictSource from '@/views/admin/global-dict/index.vue?raw'

const { descriptor } = parse(componentSource)
const script = compileScript(descriptor, { id: 'quality-feedback-test' })
const template = compileTemplate({
  source: descriptor.template!.content, filename: 'QualityFeedbackAdmin.vue', id: 'quality-feedback-test',
  compilerOptions: { bindingMetadata: script.bindings, expressionPlugins: ['typescript'] },
})
if (template.errors.length) throw new Error(String(template.errors[0]))
const compiled = transpileModule(`${script.content}\n${template.code}`, {
  compilerOptions: { module: ModuleKind.CommonJS, target: ScriptTarget.ES2020 },
}).outputText
const modules: Record<string, unknown> = {
  vue: Vue, 'element-plus': ElementPlus, '@/api/qualityFeedback': QualityApi, '@/api/polish': PolishApi,
  '@/api/review': ReviewApi, '@/utils/proofread': ProofreadUtils, '@/utils/qualityFeedback': QualityUtils,
}
const compiledModule = { exports: {} as { default: Component; render: () => VNode } }
new Function('require', 'module', 'exports', compiled)((id: string) => {
  if (!(id in modules)) throw new Error(`Unexpected dependency: ${id}`)
  return modules[id]
}, compiledModule, compiledModule.exports)
const QualityFeedbackAdmin = Object.assign(compiledModule.exports.default, { render: compiledModule.exports.render })

interface HostNode {
  kind: string
  text: string
  props: Record<string, unknown>
  parent: HostNode | null
  children: HostNode[]
}
const node = (kind: string, text = ''): HostNode => ({ kind, text, props: {}, parent: null, children: [] })
function remove(child: HostNode) {
  if (!child.parent) return
  const index = child.parent.children.indexOf(child)
  if (index >= 0) child.parent.children.splice(index, 1)
  child.parent = null
}
function insert(child: HostNode, parent: HostNode, anchor: HostNode | null = null) {
  if (child.parent) remove(child)
  const index = anchor ? parent.children.indexOf(anchor) : -1
  parent.children.splice(index < 0 ? parent.children.length : index, 0, child)
  child.parent = parent
}
const renderer = createRenderer<HostNode, HostNode>({
  createElement: kind => node(kind), createText: text => node('text', text), createComment: text => node('comment', text),
  setText: (target, text) => { target.text = text },
  setElementText: (target, text) => { target.text = text; target.children = [] },
  patchProp: (target, key, _previous, value) => { target.props[key] = value },
  parentNode: target => target.parent,
  nextSibling: target => target.parent?.children[target.parent.children.indexOf(target) + 1] ?? null,
  insert, remove,
  insertStaticContent(content, parent, anchor) {
    const target = node('static', content)
    insert(target, parent, anchor)
    return [target, target]
  },
})
function textContent(target: HostNode): string {
  return `${target.text}${target.props.description ?? ''}${target.children.map(textContent).join('')}`
}
const allNodes = (target: HostNode): HostNode[] => [target, ...target.children.flatMap(allNodes)]
function findControl(root: HostNode, label: string) {
  const found = allNodes(root).find(target => target.props['aria-label'] === label)
  if (!found) throw new Error(`Control not found: ${label}`)
  return found
}

const context = '😀帐号与帐号。'
function feedback(overrides: Partial<QualityFeedback> = {}): QualityFeedback {
  return {
    id: 1, record_id: 42, user_id: 7, revision: 2, kind: 'false_positive', original: '帐号', suggestion: '账号',
    issue_type: 'typo', start: 104, end: 106, context_start: 100, context_text: context,
    domain: 'legal', note: '保留原表达', status: 'pending', model_snapshot: [], sample: null,
    review_note: '', reviewer_id: null, reviewed_at: null, created_at: '2026-05-01T01:00:00Z', ...overrides,
  }
}
function confirmed(id = 1): QualityFeedback {
  const row = feedback({ id, status: 'confirmed' })
  row.sample = initialFeedbackSample(row)
  return row
}
const availableModels: AvailableModel[] = [
  { id: 11, name: '模型甲', model: 'model-a' }, { id: 22, name: '模型乙', model: 'model-b', is_active: true },
  { id: 33, name: '模型丙', model: 'model-c' }, { id: 44, name: '模型丁', model: 'model-d' },
  { id: 55, name: '模型戊', model: 'model-e' },
]
function report(): FeedbackEvaluation {
  return {
    generated_at: '2026-05-01T02:00:00Z', samples: [1, 2, 3].map(id => ({ id, revision: id + 2, sample: { ...confirmed(id).sample!, expectation: id === 2 ? 'report' : 'no_report' } })),
    results: [{
      config_id: 22, config_name: '模型乙', model: 'model-b', report_total: 1, report_evaluated: 1, missed: 1,
      no_report_total: 2, no_report_evaluated: 1, false_positives: 0, errors: 1,
      suggestion_evaluated: 0, suggestion_passed: 0, suggestion_failed: 0, suggestion_not_evaluated: 3,
      cases: [
        { feedback_id: 1, revision: 3, expectation: 'no_report', status: 'pass', detected: false, detection_status: 'pass', suggestion_status: 'not_evaluated', suggestion_reason: 'no_report', error: null, elapsed_ms: 200 },
        { feedback_id: 2, revision: 4, expectation: 'report', status: 'fail', detected: false, detection_status: 'fail', suggestion_status: 'not_evaluated', suggestion_reason: 'no_constraints', error: null, elapsed_ms: 210 },
        { feedback_id: 3, revision: 5, expectation: 'no_report', status: 'error', detected: null, detection_status: 'error', suggestion_status: 'not_evaluated', suggestion_reason: 'detection_error', error: '审校仅部分完成', elapsed_ms: 220 },
      ],
    }, {
      config_id: 11, config_name: '模型甲', model: 'model-a', report_total: 1, report_evaluated: 0, missed: 0,
      no_report_total: 2, no_report_evaluated: 0, false_positives: 0, errors: 3,
      suggestion_evaluated: 0, suggestion_passed: 0, suggestion_failed: 0, suggestion_not_evaluated: 3,
      cases: [1, 2, 3].map(id => ({ feedback_id: id, revision: id + 2, expectation: id === 2 ? 'report' : 'no_report', status: 'error', detected: null, detection_status: 'error', suggestion_status: 'not_evaluated', suggestion_reason: 'detection_error', error: '调用超时', elapsed_ms: 300000 })),
    }],
  }
}
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}
interface AdminState {
  status: FeedbackStatus
  page: number
  total: number
  items: QualityFeedback[]
  loading: boolean
  listError: string
  active: QualityFeedback | null
  draft: Required<FeedbackSample>
  sample: FeedbackSample
  targetStart: number | null
  positions: { start: number; end: number; label: string }[]
  sampleLength: number
  validationError: string
  reviewNote: string
  expectationConfirmed: boolean
  reviewError: string
  reviewNotice: string
  conflict: boolean
  saving: boolean
  canConfirm: boolean
  selectedIds: number[]
  selectedModels: number[]
  models: AvailableModel[]
  modelsLoading: boolean
  modelsError: string
  canRun: boolean
  runBusy: boolean
  runError: string
  evaluation: FeedbackEvaluation | null
  downloadError: string
  changeStatus: (value: FeedbackStatus) => void
  changePage: (value: number) => void
  loadList: () => Promise<void>
  refreshList: () => Promise<void>
  loadModels: () => Promise<void>
  openReview: (row: QualityFeedback) => void
  closeReview: () => void
  submitReview: (value: 'confirmed' | 'rejected') => Promise<void>
  toggleSelection: (row: QualityFeedback, checked: boolean) => void
  runEvaluation: () => Promise<void>
  downloadReport: () => void
}
const cleanups: Array<() => void> = []
function mount() {
  let workspace!: VNode
  const app = renderer.createApp(defineComponent({ setup: () => () => (workspace = h(QualityFeedbackAdmin)) }))
  const root = node('root')
  app.mount(root)
  const state = (workspace.component as unknown as { setupState: AdminState }).setupState
  let mounted = true
  const unmount = () => { if (mounted) app.unmount(); mounted = false }
  cleanups.push(unmount)
  return { state, root, unmount }
}
async function flush() { for (let i = 0; i < 5; i++) await nextTick() }
async function mountConfirmed(rows = [confirmed()]) {
  const view = mount()
  await flush()
  vi.mocked(listQualityFeedbackApi).mockResolvedValue({ items: rows, total: rows.length })
  view.state.changeStatus('confirmed')
  await flush()
  return view
}
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(listQualityFeedbackApi).mockResolvedValue({ items: [], total: 0 })
  vi.mocked(getAvailableModelsApi).mockResolvedValue({ models: availableModels })
  vi.mocked(ElMessageBox.confirm).mockResolvedValue('confirm' as Awaited<ReturnType<typeof ElMessageBox.confirm>>)
  vi.mocked(reviewQualityFeedbackApi).mockImplementation(async (id, data) => feedback({ id, ...data, revision: data.revision + 1 }))
  vi.mocked(evaluateQualityFeedbackApi).mockResolvedValue(report())
})
afterEach(() => {
  cleanups.splice(0).forEach(cleanup => cleanup())
  vi.unstubAllGlobals()
})

describe('质量反馈目标纯函数', () => {
  it('以 Unicode 定位重复和重叠片段，保留原始文本', () => {
    expect(findFeedbackTargets(context, '帐号').map(({ start, end }) => ({ start, end }))).toEqual([{ start: 1, end: 3 }, { start: 4, end: 6 }])
    expect(findFeedbackTargets('😀😀😀', '😀😀').map(match => match.start)).toEqual([0, 1])
    expect(findFeedbackTargets(context, '')).toEqual([])
    expect(findFeedbackTargets(context, '不存在')).toEqual([])
    expect(feedbackTargetParts(context, 4, 6, '帐号')).toEqual({ before: '😀帐号与', target: '帐号', after: '。' })
    expect(feedbackTargetParts(context, 5, 7, '帐号').target).toBe('')
  })
  it('按反馈原因预填，坏建议只预填禁止项，已保存样例独立复制', () => {
    expect(initialFeedbackSample(feedback())).toMatchObject({ text: context, start: 4, end: 6, domain: 'legal', expectation: 'no_report' })
    expect(initialFeedbackSample(feedback({ kind: 'missed', domain: 'official' }))).toMatchObject({ expectation: 'report', domain: 'official' })
    expect(initialFeedbackSample(feedback({ domain: 'custom', issue_type: 'custom' }))).toMatchObject({ domain: 'general', issue_type: '' })
    expect(initialFeedbackSample(feedback({ kind: 'bad_suggestion' }))).toMatchObject({ expectation: 'report', accepted_suggestions: [], rejected_suggestions: ['账号'] })
    expect(initialFeedbackSample(feedback({ kind: 'bad_suggestion', suggestion: '' })).rejected_suggestions).toEqual([''])
    const row = confirmed()
    const sample = initialFeedbackSample(row)
    sample.text = '已改'
    sample.accepted_suggestions.push('人工认可')
    sample.rejected_suggestions.push('人工禁止')
    expect(row.sample!.text).toBe(context)
    expect(row.sample!.accepted_suggestions).toEqual([])
    expect(row.sample!.rejected_suggestions).toEqual([])
    delete row.sample!.accepted_suggestions
    delete row.sample!.rejected_suggestions
    row.kind = 'bad_suggestion'
    expect(initialFeedbackSample(row)).toMatchObject({ accepted_suggestions: [], rejected_suggestions: [] })
  })
  it('替换约束精确、限长限量、去重互斥，no_report 不允许附带', () => {
    const sample = initialFeedbackSample(feedback({ kind: 'missed' }))
    expect(feedbackSampleError({ ...sample, accepted_suggestions: ['', ' 认可\n', '\u{20000}'.repeat(500)] })).toBe('')
    expect(feedbackSampleError({ ...sample, accepted_suggestions: ['\u{20000}'.repeat(501)] })).toContain('500')
    expect(feedbackSampleError({ ...sample, rejected_suggestions: Array.from({ length: 11 }, (_, i) => String(i)) })).toContain('10')
    expect(feedbackSampleError({ ...sample, accepted_suggestions: ['同文', '同文'] })).toContain('重复')
    expect(feedbackSampleError({ ...sample, rejected_suggestions: ['', ''] })).toContain('重复')
    expect(feedbackSampleError({ ...sample, accepted_suggestions: ['同文'], rejected_suggestions: ['同文'] })).toContain('不能相同')
    expect(feedbackSampleError({ ...sample, expectation: 'no_report', accepted_suggestions: [''] })).toContain('不能附带')
  })
  it('目标匹配、领域、预期与枚举有效，4000 个 Unicode 字符而不是 UTF-16 长度', () => {
    const sample = initialFeedbackSample(feedback())
    expect(feedbackSampleError(sample)).toBe('')
    expect(feedbackSampleError({ ...sample, text: '😀'.repeat(4000), original: '😀', start: 3999, end: 4000 })).toBe('')
    expect(feedbackSampleError({ ...sample, text: '😀'.repeat(4001) })).toContain('4000')
    expect(feedbackSampleError({ ...sample, start: -1 })).toContain('有效目标')
    expect(feedbackSampleError({ ...sample, original: '' })).toContain('目标原文')
    expect(feedbackSampleError({ ...sample, issue_type: '' })).toBe('')
    expect(feedbackSampleError({ ...sample, issue_type: 'made-up' })).toContain('已有问题类型')
  })
  it.each([
    [false, 'complete', '调用失败'], [null, 'complete', '调用状态未知'],
    [true, 'partial', '部分完成'], [true, 'unknown', '完成状态未知'],
  ] as const)('失败或未完成 success=%s coverage=%s 不被当成无漏检', (success, coverage_status, label) => {
    const text = feedbackSnapshotLabel({ config_id: 1, config_name: '模型', model: 'm', success, coverage_status, reported: false })
    expect(text).toContain(label)
    expect(text).toContain('不可判定')
    expect(text).not.toContain('未报告目标')
  })
  it('报告 JSON 原样保留样例 revision 和错误，不转换为通过率', () => {
    const evaluation = report()
    const exported = JSON.parse(serializeFeedbackReport(evaluation))
    expect(exported.samples).toEqual(evaluation.samples)
    expect(exported.results).toEqual(evaluation.results)
    expect(exported.scope).toBe('confirmed_target_only')
    expect(exported.notice).toContain('不代表全文精确率')
  })
})

describe('QualityFeedbackAdmin 真实 SFC', () => {
  it('入口复用现有编辑权限，打开挂载、关闭卸载，不引入新权限', () => {
    expect(globalDictSource).toMatch(/userStore\.hasPermission\([^)]*admin:global_dict:edit[^)]*\)/)
    expect(globalDictSource).toMatch(/<el-drawer(?=[^>]*v-if="canManageQualityFeedback")(?=[^>]*v-model="showQualityFeedback")[^>]*>/)
    expect(globalDictSource).toMatch(/<QualityFeedbackAdmin(?=[^>]*v-if="showQualityFeedback")[^>]*\/>/)
  })
  it('首次 pending 加载/空态，没有自动提交、模型调用或用户身份披露', async () => {
    const { state, root } = mount()
    expect(state.loading).toBe(true)
    expect(textContent(root)).toContain('正在加载反馈')
    await flush()
    expect(listQualityFeedbackApi).toHaveBeenCalledWith({ status: 'pending', page: 1, page_size: 20 })
    expect(textContent(root)).toContain('暂无待确认的质量反馈')
    expect(textContent(root)).toContain('收集反馈不等于模型真错')
    expect(state.total).toBe(0)
    expect(state.canRun).toBe(false)
    expect(reviewQualityFeedbackApi).not.toHaveBeenCalled()
    expect(evaluateQualityFeedbackApi).not.toHaveBeenCalled()
    expect(componentSource).not.toContain('user_id')
    expect(componentSource).not.toContain('v-html')
  })
  it('初次失败明确 detail 且不假装为空，可重试获得准确 total', async () => {
    vi.mocked(listQualityFeedbackApi).mockRejectedValueOnce({ response: { data: { detail: '暂无读取权限' } } })
    const { state, root } = mount()
    await flush()
    expect(state.listError).toBe('暂无读取权限')
    expect(textContent(root)).not.toContain('暂无待确认的质量反馈')
    vi.mocked(listQualityFeedbackApi).mockResolvedValueOnce({ items: [feedback()], total: 43 })
    await state.loadList()
    await flush()
    expect(state.listError).toBe('')
    expect(state.total).toBe(43)
    expect(findControl(root, '反馈分页').props.total).toBe(43)
  })
  it('切换状态/页码忽略旧列表成功与失败；始终 page_size20', async () => {
    const first = deferred<{ items: QualityFeedback[]; total: number }>()
    const second = deferred<{ items: QualityFeedback[]; total: number }>()
    vi.mocked(listQualityFeedbackApi).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const { state } = mount()
    state.changeStatus('confirmed')
    vi.mocked(listQualityFeedbackApi).mockResolvedValueOnce({ items: [confirmed(21)], total: 61 })
    state.changePage(2)
    await flush()
    first.resolve({ items: [feedback()], total: 999 })
    second.reject(new Error('旧请求错误'))
    await flush()
    expect(state.items.map(item => item.id)).toEqual([21])
    expect(state.total).toBe(61)
    expect(state.page).toBe(2)
    expect(state.listError).toBe('')
    expect(listQualityFeedbackApi).toHaveBeenLastCalledWith({ status: 'confirmed', page: 2, page_size: 20 })
  })
  it('最后一页条目移走时使用真实 total 回到有效页', async () => {
    const { state } = mount()
    await flush()
    vi.mocked(listQualityFeedbackApi).mockResolvedValue({ items: [], total: 20 })
    state.changePage(2)
    await flush()
    expect(state.page).toBe(1)
    expect(listQualityFeedbackApi).toHaveBeenLastCalledWith({ status: 'pending', page: 1, page_size: 20 })
  })
  it('确认必须有有效目标及管理员显式核对；编辑不触发查询或调用模型', async () => {
    const row = feedback()
    const { state, root } = mount()
    await flush()
    state.openReview(row)
    await flush()
    expect(state.sample).toMatchObject({ text: context, start: 4, end: 6, original: '帐号' })
    expect(state.canConfirm).toBe(false)
    await state.submitReview('confirmed')
    expect(reviewQualityFeedbackApi).not.toHaveBeenCalled()
    state.expectationConfirmed = true
    expect(state.canConfirm).toBe(true)
    state.draft.text = '已经脱敏但没有目标'
    expect(state.expectationConfirmed).toBe(false)
    state.expectationConfirmed = true
    expect(state.canConfirm).toBe(false)
    await state.submitReview('confirmed')
    await flush()
    expect(textContent(root)).toContain('找不到目标原文')
    expect(listQualityFeedbackApi).toHaveBeenCalledTimes(1)
    expect(reviewQualityFeedbackApi).not.toHaveBeenCalled()
    expect(evaluateQualityFeedbackApi).not.toHaveBeenCalled()
    expect(row.context_text).toBe(context)
  })
  it('修改文本重新定位所有重复词，只允许下拉选定处提交 Unicode 坐标', async () => {
    const { state, root } = mount()
    await flush()
    state.openReview(feedback({ start: 101, end: 103 }))
    state.draft.text = '😀帐号，😀帐号'
    expect(state.positions.map(target => target.start)).toEqual([1, 5])
    expect(state.targetStart).toBeNull()
    state.expectationConfirmed = true
    await state.submitReview('confirmed')
    expect(reviewQualityFeedbackApi).not.toHaveBeenCalled()
    await flush()
    const selector = findControl(root, '目标出现位置')
    ;(selector.props['onUpdate:modelValue'] as (value: number) => void)(5)
    state.expectationConfirmed = true
    state.reviewNote = '已核对脱敏与第二处目标'
    await state.submitReview('confirmed')
    expect(reviewQualityFeedbackApi).toHaveBeenCalledWith(1, {
      revision: 2, status: 'confirmed', review_note: '已核对脱敏与第二处目标',
      sample: { text: '😀帐号，😀帐号', start: 5, end: 7, original: '帐号', domain: 'legal', expectation: 'no_report', issue_type: 'typo', accepted_suggestions: [], rejected_suggestions: [] },
    })
  })
  it('坏建议预填禁止项且必须确认；录入认可文本后重置勾选，原样保存', async () => {
    const row = feedback({ kind: 'bad_suggestion' })
    const { state, root } = mount()
    await flush()
    state.openReview(row)
    await flush()
    expect(state.draft.expectation).toBe('report')
    expect(state.draft.rejected_suggestions).toEqual(['账号'])
    expect(findControl(root, '禁止替换 1').props.modelValue).toBe('账号')
    expect(textContent(root)).toContain('请人工确认')
    expect(textContent(root)).toContain('纳入样例不等于质量已修复')
    expect(textContent(root)).toContain('未提供认可改法')
    expect(state.canConfirm).toBe(false)
    state.expectationConfirmed = true
    ;(findControl(root, '添加认可替换').props.onClick as () => void)()
    expect(state.expectationConfirmed).toBe(false)
    await flush()
    const input = findControl(root, '认可替换 1')
    ;(input.props['onUpdate:modelValue'] as (value: string) => void)(' 用户账号\n')
    state.expectationConfirmed = true
    await state.submitReview('confirmed')
    expect(reviewQualityFeedbackApi).toHaveBeenCalledWith(1, expect.objectContaining({
      sample: expect.objectContaining({ expectation: 'report', accepted_suggestions: [' 用户账号\n'], rejected_suggestions: ['账号'] }),
    }))
    expect(state.expectationConfirmed).toBe(false)
    expect(row.sample).toBeNull()
    expect(evaluateQualityFeedbackApi).not.toHaveBeenCalled()
  })
  it('约束冲突阻止保存，条目移除/增改均需重新确认；no_report 自动排除约束', async () => {
    const { state, root } = mount()
    await flush()
    state.openReview(feedback({ kind: 'bad_suggestion' }))
    state.draft.accepted_suggestions.push('账号')
    state.expectationConfirmed = true
    expect(state.validationError).toContain('不能相同')
    expect(state.canConfirm).toBe(false)
    await state.submitReview('confirmed')
    expect(reviewQualityFeedbackApi).not.toHaveBeenCalled()
    await flush()
    ;(findControl(root, '移除认可替换 1').props.onClick as () => void)()
    expect(state.expectationConfirmed).toBe(false)
    state.draft.accepted_suggestions.push('') // 空条目是人工认可删除，不是空数组。
    state.expectationConfirmed = true
    state.draft.rejected_suggestions[0] = '另一个坏建议'
    expect(state.expectationConfirmed).toBe(false)
    state.draft.expectation = 'no_report'
    expect(state.sample.accepted_suggestions).toEqual([])
    expect(state.sample.rejected_suggestions).toEqual([])
    state.draft.expectation = 'report'
    expect(state.sample.accepted_suggestions).toEqual([''])
    expect(state.sample.rejected_suggestions).toEqual(['另一个坏建议'])
    state.draft.expectation = 'no_report'
    state.expectationConfirmed = true
    await state.submitReview('confirmed')
    expect(reviewQualityFeedbackApi).toHaveBeenCalledWith(1, expect.objectContaining({
      sample: expect.objectContaining({ expectation: 'no_report', accepted_suggestions: [], rejected_suggestions: [] }),
    }))
  })
  it('已有 confirmed 样例独立编辑再确认，并可按更新 revision 撤回 rejected/null', async () => {
    const row = confirmed()
    const { state } = await mountConfirmed([row])
    state.openReview(row)
    state.draft.original = '😀'
    state.draft.domain = 'official'
    state.draft.issue_type = ''
    state.draft.expectation = 'report'
    state.expectationConfirmed = true
    await state.submitReview('confirmed')
    expect(row.sample!.original).toBe('帐号')
    expect(reviewQualityFeedbackApi).toHaveBeenLastCalledWith(1, expect.objectContaining({ revision: 2, sample: expect.objectContaining({ original: '😀', start: 0, end: 1, expectation: 'report', issue_type: '' }) }))
    expect(state.active!.revision).toBe(3)
    expect(state.expectationConfirmed).toBe(false)
    await state.submitReview('rejected')
    expect(reviewQualityFeedbackApi).toHaveBeenLastCalledWith(1, { revision: 3, status: 'rejected', sample: null, review_note: '' })
  })
  it('409 保留编辑与旧 revision，不重试/不自动读，取消重读仍保留', async () => {
    vi.mocked(reviewQualityFeedbackApi).mockRejectedValueOnce({ response: { status: 409, data: { detail: 'revision 已更新' } } })
    const { state } = mount()
    await flush()
    state.openReview(feedback())
    state.draft.text = '😀脱敏帐号'
    state.reviewNote = '我的审核'
    state.expectationConfirmed = true
    await state.submitReview('confirmed')
    expect(state.conflict).toBe(true)
    expect(state.reviewError).toContain('409')
    expect(state.reviewError).toContain('revision 已更新')
    expect(state.active!.revision).toBe(2)
    expect(state.draft.text).toBe('😀脱敏帐号')
    await state.submitReview('confirmed')
    await state.submitReview('rejected')
    expect(reviewQualityFeedbackApi).toHaveBeenCalledTimes(1)
    expect(listQualityFeedbackApi).toHaveBeenCalledTimes(1)
    vi.mocked(ElMessageBox.confirm).mockRejectedValueOnce('cancel')
    await state.refreshList()
    expect(state.conflict).toBe(true)
    expect(state.reviewNote).toBe('我的审核')
    const latest = feedback({ revision: 8 })
    vi.mocked(listQualityFeedbackApi).mockResolvedValueOnce({ items: [latest], total: 1 })
    await state.refreshList()
    state.openReview(state.items[0])
    expect(state.active!.revision).toBe(8)
    expect(state.canConfirm).toBe(false)
    state.expectationConfirmed = true
    await state.submitReview('confirmed')
    expect(reviewQualityFeedbackApi).toHaveBeenLastCalledWith(1, expect.objectContaining({ revision: 8 }))
  })
  it('普通提交失败展示结构化 detail，保留本地样例并允许显式重试', async () => {
    vi.mocked(reviewQualityFeedbackApi).mockRejectedValueOnce({ response: { status: 422, data: { detail: [{ msg: '样例不合法' }] } } })
    const { state, root } = mount()
    await flush()
    state.openReview(feedback())
    state.expectationConfirmed = true
    await state.submitReview('confirmed')
    await flush()
    expect(textContent(root)).toContain('样例不合法')
    expect(state.canConfirm).toBe(true)
    expect(state.active!.revision).toBe(2)
    await state.submitReview('confirmed')
    expect(reviewQualityFeedbackApi).toHaveBeenCalledTimes(2)
  })
  it('逐模型失败/partial/unknown 明示不可判定，用户内容仅作文本', async () => {
    const row = feedback({
      note: '<img src=x onerror=alert(1)>',
      model_snapshot: [
        { config_id: 1, config_name: '失败模型', model: 'm1', success: false, reported: false, coverage_status: 'complete' },
        { config_id: 2, config_name: '未完成模型', model: 'm2', success: true, reported: false, coverage_status: 'partial' },
        { config_id: 3, config_name: '旧模型', model: 'm3', success: true, reported: null, coverage_status: 'unknown' },
      ],
    })
    vi.mocked(listQualityFeedbackApi).mockResolvedValueOnce({ items: [row], total: 1 })
    const { root } = mount()
    await flush()
    expect(textContent(root)).toContain(row.note)
    expect(textContent(root)).toContain('调用失败 · 目标结果不可判定')
    expect(textContent(root)).toContain('部分完成 · 目标结果不可判定')
    expect(textContent(root)).toContain('完成状态未知 · 目标结果不可判定')
    expect(allNodes(root).some(target => target.kind === 'img' || 'innerHTML' in target.props)).toBe(false)
  })
  it('仅 confirmed 且有样例可选 1–10 条，模型默认当前+下一可用，严格 1–4 个', async () => {
    const rows = Array.from({ length: 11 }, (_, index) => confirmed(index + 1))
    const { state } = await mountConfirmed(rows)
    expect(state.selectedModels).toEqual([22, 11])
    expect(state.canRun).toBe(false)
    state.toggleSelection(feedback({ id: 100 }), true)
    state.toggleSelection(feedback({ id: 101, status: 'rejected' }), true)
    state.toggleSelection(feedback({ id: 102, status: 'confirmed', sample: null }), true)
    expect(state.selectedIds).toEqual([])
    rows.forEach(row => state.toggleSelection(row, true))
    expect(state.selectedIds).toHaveLength(10)
    expect(state.canRun).toBe(true)
    state.selectedModels = []
    expect(state.canRun).toBe(false)
    state.selectedModels = [11, 22, 33, 44, 55]
    expect(state.canRun).toBe(false)
    state.selectedModels = [999]
    await state.runEvaluation()
    expect(evaluateQualityFeedbackApi).not.toHaveBeenCalled()
    state.selectedModels = [11]
    state.selectedIds = [999]
    expect(state.canRun).toBe(false)
    state.selectedIds = [1]
    state.changeStatus('pending')
    expect(state.canRun).toBe(false)
    expect(state.selectedIds).toEqual([])
  })
  it('可用模型加载错误可重试，空列表禁运行', async () => {
    vi.mocked(getAvailableModelsApi).mockRejectedValueOnce(new Error('模型列表离线'))
    const { state, root } = await mountConfirmed()
    expect(textContent(root)).toContain('模型列表离线')
    expect(state.canRun).toBe(false)
    vi.mocked(getAvailableModelsApi).mockResolvedValueOnce({ models: [] })
    await state.loadModels()
    await flush()
    expect(textContent(root)).toContain('暂无可用模型')
    expect(state.modelsError).toBe('')
    expect(state.selectedModels).toEqual([])
  })
  it('费用/样例发送确认取消不请求；等待确认阶段也防重复点击', async () => {
    const confirmation = deferred<Awaited<ReturnType<typeof ElMessageBox.confirm>>>()
    vi.mocked(ElMessageBox.confirm).mockReturnValueOnce(confirmation.promise)
    const { state } = await mountConfirmed()
    state.toggleSelection(state.items[0], true)
    const running = state.runEvaluation()
    await state.runEvaluation()
    expect(ElMessageBox.confirm).toHaveBeenCalledTimes(1)
    expect(vi.mocked(ElMessageBox.confirm).mock.calls[0][0]).toContain('正常模型调用费用，但不消耗用户审校次数')
    expect(vi.mocked(ElMessageBox.confirm).mock.calls[0][0]).toContain('管理员已确认、脱敏并保存的样例')
    expect(state.runBusy).toBe(true)
    confirmation.reject('cancel')
    await running
    expect(evaluateQualityFeedbackApi).not.toHaveBeenCalled()
    expect(state.runBusy).toBe(false)
    expect(state.runError).toBe('')
  })
  it('运行中防重入并锁定筛选/模型选择，捕获 ids，不发未保存编辑', async () => {
    const pending = deferred<FeedbackEvaluation>()
    vi.mocked(evaluateQualityFeedbackApi).mockReturnValueOnce(pending.promise)
    const { state } = await mountConfirmed()
    state.toggleSelection(state.items[0], true)
    const running = state.runEvaluation()
    await flush()
    await state.runEvaluation()
    state.changeStatus('pending')
    state.changePage(2)
    expect(state.status).toBe('confirmed')
    expect(state.page).toBe(1)
    expect(evaluateQualityFeedbackApi).toHaveBeenCalledTimes(1)
    expect(evaluateQualityFeedbackApi).toHaveBeenCalledWith({ feedback_ids: [1], config_ids: [22, 11] })
    pending.resolve(report())
    await running
    expect(state.evaluation).toEqual(report())
    expect(state.runBusy).toBe(false)
  })
  it('运行失败显示 API detail，不能假装有评测报告', async () => {
    vi.mocked(evaluateQualityFeedbackApi).mockRejectedValueOnce({ response: { data: { detail: '样例已撤回，请重读' } } })
    const { state, root } = await mountConfirmed()
    state.toggleSelection(state.items[0], true)
    await state.runEvaluation()
    await flush()
    expect(textContent(root)).toContain('样例已撤回，请重读')
    expect(state.evaluation).toBeNull()
    expect(state.canRun).toBe(true)
  })
  it('失败不能计入通过率，指标用有效分母，病例 revision 和本地 JSON 可复现', async () => {
    const { state, root } = await mountConfirmed([confirmed(1), confirmed(2), confirmed(3)])
    state.items.forEach(row => state.toggleSelection(row, true))
    await state.runEvaluation()
    await flush()
    const content = textContent(root)
    expect(content).toContain('目标误报0 / 1')
    expect(content).toContain('目标漏检1 / 1')
    expect(content).toContain('目标误报0 / 0')
    expect(content).toContain('失败或未完成3')
    expect(content).toContain('revision 5')
    expect(content).toContain('失败或未完成 / error')
    expect(content).toContain('审校仅部分完成')
    expect(content).toContain('不代表全文精确率')
    expect(content).toContain('LLM 调用结果可能波动')
    const createObjectURL = vi.fn(() => 'blob:quality')
    const revokeObjectURL = vi.fn()
    const link = { href: '', download: '', click: vi.fn() }
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })
    vi.stubGlobal('document', { createElement: vi.fn(() => link) })
    state.downloadReport()
    const blob = (createObjectURL.mock.calls[0] as unknown as [Blob])[0]
    expect(blob.type).toContain('application/json')
    const exported = JSON.parse(await blob.text())
    expect(exported.samples).toEqual(report().samples)
    expect(exported.results[0].cases[2].status).toBe('error')
    expect(exported.results[1].no_report_evaluated).toBe(0)
    expect(link.download).toMatch(/^quality-feedback-.*\.json$/)
    expect(link.click).toHaveBeenCalledTimes(1)
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:quality')
  })
  it('同例检出通过而建议失败分开显示，未评估不伪装通过，下载保留 golden', async () => {
    const evaluation = report()
    const result = evaluation.results[0]
    Object.assign(result, { missed: 0, report_total: 3, report_evaluated: 3, no_report_total: 0, no_report_evaluated: 0,
      errors: 0, suggestion_evaluated: 2, suggestion_passed: 1, suggestion_failed: 1, suggestion_not_evaluated: 1 })
    const statuses = ['fail', 'pass', 'not_evaluated'] as const
    const reasons = ['rejected', 'all_accepted', 'incomparable_context'] as const
    result.cases.forEach((entry, index) => Object.assign(entry, {
      expectation: 'report', detected: true, detection_status: 'pass', status: statuses[index],
      suggestion_status: statuses[index], suggestion_reason: reasons[index], error: null,
    }))
    evaluation.samples.forEach(entry => Object.assign(entry.sample, {
      expectation: 'report', accepted_suggestions: ['人工正确改法'], rejected_suggestions: ['已知坏建议'],
    }))
    vi.mocked(evaluateQualityFeedbackApi).mockResolvedValueOnce(evaluation)
    const { state, root } = await mountConfirmed()
    state.toggleSelection(state.items[0], true)
    await state.runEvaluation()
    await flush()
    const content = textContent(root)
    expect(content).toContain('目标漏检0 / 3')
    expect(content).toContain('建议通过1 / 2')
    expect(content).toContain('建议不符1 / 2')
    expect(content).toContain('建议未评估1')
    expect(content).toContain('检出：通过 / pass')
    expect(content).toContain('建议：未通过 / fail')
    expect(content).toContain('建议：未评估 / not_evaluated')
    expect(content).toContain('无法可靠比较')
    expect(content).toContain('人工正确改法')
    expect(content).toContain('已知坏建议')
    const exported = JSON.parse(serializeFeedbackReport(state.evaluation!))
    expect(exported.samples).toEqual(evaluation.samples)
    expect(exported.results).toEqual(evaluation.results)
  })
  it('关闭后旧评测响应不得回填重新打开的面板', async () => {
    const pending = deferred<FeedbackEvaluation>()
    vi.mocked(evaluateQualityFeedbackApi).mockReturnValueOnce(pending.promise)
    const first = await mountConfirmed()
    first.state.toggleSelection(first.state.items[0], true)
    const running = first.state.runEvaluation()
    await flush()
    first.unmount()
    const second = mount()
    await flush()
    pending.resolve(report())
    await running
    expect(first.state.evaluation).toBeNull()
    expect(second.state.evaluation).toBeNull()
  })
  it('费用确认期间关闭面板，随后同意也不发模型请求', async () => {
    const confirmation = deferred<Awaited<ReturnType<typeof ElMessageBox.confirm>>>()
    vi.mocked(ElMessageBox.confirm).mockReturnValueOnce(confirmation.promise)
    const { state, unmount } = await mountConfirmed()
    state.toggleSelection(state.items[0], true)
    const running = state.runEvaluation()
    unmount()
    confirmation.resolve('confirm' as Awaited<ReturnType<typeof ElMessageBox.confirm>>)
    await running
    expect(evaluateQualityFeedbackApi).not.toHaveBeenCalled()
  })
  it('保存期间禁切记录，关闭后迟到保存不刷新或改写新面板', async () => {
    const pending = deferred<QualityFeedback>()
    vi.mocked(reviewQualityFeedbackApi).mockReturnValueOnce(pending.promise)
    const first = mount()
    await flush()
    first.state.openReview(feedback())
    first.state.expectationConfirmed = true
    const saving = first.state.submitReview('confirmed')
    first.state.openReview(feedback({ id: 2 }))
    expect(first.state.active!.id).toBe(1)
    first.unmount()
    const second = mount()
    await flush()
    second.state.openReview(feedback({ id: 2 }))
    pending.resolve(confirmed())
    await saving
    expect(second.state.active!.id).toBe(2)
    expect(second.state.active!.revision).toBe(2)
    expect(listQualityFeedbackApi).toHaveBeenCalledTimes(2)
  })
  it('切换记录后旧重读确认失效，不清空新编辑', async () => {
    const confirmation = deferred<Awaited<ReturnType<typeof ElMessageBox.confirm>>>()
    vi.mocked(ElMessageBox.confirm).mockReturnValueOnce(confirmation.promise)
    const { state } = mount()
    await flush()
    state.openReview(feedback())
    const refreshing = state.refreshList()
    state.openReview(feedback({ id: 2 }))
    state.reviewNote = '第二条的本地备注'
    confirmation.resolve('confirm' as Awaited<ReturnType<typeof ElMessageBox.confirm>>)
    await refreshing
    expect(state.active!.id).toBe(2)
    expect(state.reviewNote).toBe('第二条的本地备注')
    expect(listQualityFeedbackApi).toHaveBeenCalledTimes(1)
  })
})
