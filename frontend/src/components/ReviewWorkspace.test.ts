/* eslint-disable vue/one-component-per-file -- 测试桩与 renderer 宿主均只用于此组件的测试。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as Vue from 'vue'
import { createRenderer, defineComponent, h, nextTick, reactive, type Component, type VNode } from 'vue'
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'
import { ModuleKind, ScriptTarget, transpileModule } from 'typescript'
import type { ProofreadCoverage } from '@/api/proofread'
import type { ReviewIssue } from '@/composables/useProofreadReview'
import type { ReviewCompareState, ReviewResponse, ReviewRestorePayload, ReviewVersion } from '@/api/review'

vi.mock('element-plus', async () => {
  const { defineComponent, h } = await import('vue')
  const stub = (name: string) => defineComponent({
    name,
    inheritAttrs: false,
    setup(_props, { attrs, slots }) {
      return () => name === 'ElDrawer' && !attrs.modelValue ? null : h(name, attrs, slots.default?.())
    },
  })
  return {
    ElAlert: stub('ElAlert'), ElButton: stub('ElButton'), ElDrawer: stub('ElDrawer'),
    ElInput: stub('ElInput'), ElOption: stub('ElOption'), ElSelect: stub('ElSelect'), ElTag: stub('ElTag'),
    ElMessageBox: { confirm: vi.fn() },
  }
})
vi.mock('@/api/review', () => ({
  getReviewApi: vi.fn(), saveReviewApi: vi.fn(), createReviewVersionApi: vi.fn(),
  getReviewErrorDetail: (error: { message?: string; response?: { data?: { detail?: string } } }) => error?.response?.data?.detail || error?.message || '请求失败',
}))

import { ElMessageBox } from 'element-plus'
import * as ElementPlus from 'element-plus'
import * as ReviewApi from '@/api/review'
import * as ReviewUtils from '@/utils/review'
import * as ReviewVersions from '@/utils/reviewVersions'
import * as CompareReview from '@/utils/compareReview'
import { createReviewVersionApi, getReviewApi, saveReviewApi } from '@/api/review'
import componentSource from './ReviewWorkspace.vue?raw'

// Node 环境默认把 .vue 编译为 SSR；这里用已安装的编译器编译真实客户端模板，
// 不引入 jsdom、不写配置，也不触发 auto-import 插件生成其他人的文件。
const { descriptor } = parse(componentSource)
const script = compileScript(descriptor, { id: 'workspace-test' })
const template = compileTemplate({
  source: descriptor.template!.content,
  filename: 'ReviewWorkspace.vue',
  id: 'workspace-test',
  compilerOptions: { bindingMetadata: script.bindings, expressionPlugins: ['typescript'] },
})
if (template.errors.length) throw new Error(String(template.errors[0]))
const compiled = transpileModule(`${script.content}\n${template.code}`, {
  compilerOptions: { module: ModuleKind.CommonJS, target: ScriptTarget.ES2020 },
}).outputText
const modules: Record<string, unknown> = {
  vue: Vue, 'element-plus': ElementPlus, '@/api/review': ReviewApi,
  '@/utils/review': ReviewUtils, '@/utils/reviewVersions': ReviewVersions,
  '@/utils/compareReview': CompareReview,
}
const compiledModule = { exports: {} as { default: Component; render: () => VNode } }
new Function('require', 'module', 'exports', compiled)((id: string) => {
  if (!(id in modules)) throw new Error(`Unexpected component dependency: ${id}`)
  return modules[id]
}, compiledModule, compiledModule.exports)
const ReviewWorkspace = Object.assign(compiledModule.exports.default, { render: compiledModule.exports.render })

/** 无浏览器依赖的 Vue renderer：运行真实 SFC 生命周期和响应式逻辑。 */
interface HostNode {
  kind: string
  text: string
  props: Record<string, unknown>
  parent: HostNode | null
  children: HostNode[]
}
const node = (kind: string, text = ''): HostNode => ({ kind, text, props: {}, parent: null, children: [] })
function insert(child: HostNode, parent: HostNode, anchor: HostNode | null = null) {
  if (child.parent) remove(child)
  const index = anchor ? parent.children.indexOf(anchor) : -1
  parent.children.splice(index < 0 ? parent.children.length : index, 0, child)
  child.parent = parent
}
function remove(child: HostNode) {
  if (!child.parent) return
  const index = child.parent.children.indexOf(child)
  if (index >= 0) child.parent.children.splice(index, 1)
  child.parent = null
}
const renderer = createRenderer<HostNode, HostNode>({
  createElement: kind => node(kind),
  createText: text => node('text', text),
  createComment: text => node('comment', text),
  setText: (target, text) => { target.text = text },
  setElementText: (target, text) => { target.text = text; target.children = [] },
  patchProp: (target, key, _previous, value) => { target.props[key] = value },
  parentNode: target => target.parent,
  nextSibling: target => target.parent?.children[target.parent.children.indexOf(target) + 1] ?? null,
  insert,
  remove,
  insertStaticContent(content, parent, anchor) {
    const target = node('static', content)
    insert(target, parent, anchor)
    return [target, target]
  },
})
function textContent(target: HostNode): string {
  return `${target.text}${target.props.title ?? ''}${target.children.map(textContent).join('')}`
}

const source = '帐号与帐号'
const partial: ProofreadCoverage = {
  status: 'partial', total_chunks: 1, completed_chunks: 0,
  failed_chunks: [{ chunk_index: 0, start: 0, end: 5, text: source, error_code: 'timeout' }],
}
function issue(accepted = false): ReviewIssue {
  return { original: '帐号', suggestion: '账号', type: 'typo', severity: 'warning', start: 0, end: 2, _accepted: accepted, _ignored: false }
}
function response(overrides: Partial<ReviewResponse> = {}): ReviewResponse {
  return {
    record_id: 1, revision: 0, original_text: source, domain: 'general', depth: 'standard', config_id: null,
    issues: [issue()], coverage: null, modified_text: source, versions: [], ...overrides,
  }
}
function version(overrides: Partial<ReviewVersion> = {}): ReviewVersion {
  return { id: 'v1', label: '版本一', created_at: '2026-01-01T00:00:00Z', issues: [issue(true)], coverage: partial, modified_text: '账号与帐号', ...overrides }
}
function compare(): ReviewCompareState {
  return { results: [1, 2].map(id => ({
    config_id: id, config_name: `模型${id}`, model: `model-${id}`, domain: 'general', depth: 'standard',
    success: true, error: null, elapsed_ms: 100,
    issues: [{ ...issue(), explanation: `模型${id}的说明`, severity: id === 1 ? 'warning' : 'error' }],
    coverage: JSON.parse(JSON.stringify(partial)),
  })) }
}
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}
interface WorkspaceProps {
  recordId: number | null
  sourceText: string
  issues: ReviewIssue[]
  coverage: ProofreadCoverage | null
  compare?: ReviewCompareState | null
  domain: string
  depth: string
  configId: number | null
  savedReview: ReviewResponse | null
}
interface WorkspaceState {
  dirty: boolean
  canSave: boolean
  busy: string | null
  needsLoad: boolean
  mustRefresh: boolean
  conflict: boolean
  error: string
  notice: string
  statusText: string
  remote: ReviewResponse | null
  versionLabel: string
  versionLimitReached: boolean
  drawerOpen: boolean
  leftChoice: string
  rightChoice: string
  panes: Record<'left' | 'right', { text: string }>
  comparison: { changes: unknown[]; error?: string }
  save: (kind: 'draft' | 'version') => Promise<void>
  loadRemote: () => Promise<void>
  loadSavedDraft: () => Promise<void>
  restoreVersion: (snapshot: ReviewVersion) => Promise<void>
  cancelRequest: () => void
}
const cleanups: Array<() => void> = []
function mount(overrides: Partial<WorkspaceProps> = {}) {
  const props = reactive<WorkspaceProps>(JSON.parse(JSON.stringify({
    recordId: 1, sourceText: source, issues: [issue()], coverage: null,
    domain: 'general', depth: 'standard', configId: null, savedReview: response(), ...overrides,
  })))
  const saved = vi.fn((value: ReviewResponse) => { props.savedReview = value })
  const restore = vi.fn((value: ReviewRestorePayload) => {
    props.issues = value.issues
    props.coverage = value.coverage
    props.compare = value.compare ?? null
  })
  let workspace!: VNode
  const app = renderer.createApp(defineComponent({
    setup: () => () => (workspace = h(ReviewWorkspace, { ...props, onSaved: saved, onRestore: restore })),
  }))
  const root = node('root')
  app.mount(root)
  const state = (workspace.component as unknown as { setupState: WorkspaceState }).setupState
  let mounted = true
  const unmount = () => {
    if (mounted) app.unmount()
    mounted = false
  }
  cleanups.push(unmount)
  return { props, state, saved, restore, root, unmount }
}
async function flush() {
  for (let i = 0; i < 4; i++) await nextTick()
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(ElMessageBox.confirm).mockResolvedValue('confirm' as Awaited<ReturnType<typeof ElMessageBox.confirm>>)
  vi.mocked(getReviewApi).mockResolvedValue(response())
})
afterEach(() => {
  cleanups.splice(0).forEach(cleanup => cleanup())
  vi.unstubAllGlobals()
})

describe('ReviewWorkspace', () => {
  it('游客显示需登录保存，不请求服务端、不触碰 localStorage', async () => {
    const storage = { setItem: vi.fn(), getItem: vi.fn() }
    vi.stubGlobal('localStorage', storage)
    const { state, root } = mount({ recordId: null, savedReview: null })
    expect(textContent(root)).toContain('需登录保存')
    expect(state.canSave).toBe(false)
    await state.save('draft')
    await state.save('version')
    expect(getReviewApi).not.toHaveBeenCalled()
    expect(saveReviewApi).not.toHaveBeenCalled()
    expect(createReviewVersionApi).not.toHaveBeenCalled()
    expect(storage.getItem).not.toHaveBeenCalled()
    expect(storage.setItem).not.toHaveBeenCalled()
  })

  it('首次 GET revision=0 后允许手动保存，不自动保存', async () => {
    const { state } = mount({ savedReview: null })
    expect(state.busy).toBe('load')
    expect(state.canSave).toBe(false)
    await flush()
    expect(getReviewApi).toHaveBeenCalledWith(1, expect.objectContaining({ signal: expect.any(AbortSignal) }))
    expect(state.canSave).toBe(true)
    expect(state.dirty).toBe(false)
    expect(saveReviewApi).not.toHaveBeenCalled()
  })

  it('首次发现已有草稿不静默覆盖，明确加载时携带 coverage', async () => {
    vi.mocked(getReviewApi).mockResolvedValue(response({ revision: 3, issues: [issue(true)], coverage: partial }))
    const { state, props, restore } = mount({ savedReview: null })
    await flush()
    expect(state.needsLoad).toBe(true)
    expect(state.canSave).toBe(false)
    expect(props.issues[0]._accepted).toBe(false)
    expect(restore).not.toHaveBeenCalled()
    await state.save('draft')
    expect(saveReviewApi).not.toHaveBeenCalled()
    await state.loadSavedDraft()
    await flush()
    expect(restore).toHaveBeenCalledWith({ issues: [issue(true)], coverage: partial, compare: null })
    expect(props.savedReview?.revision).toBe(3)
    expect(state.canSave).toBe(true)
    expect(state.dirty).toBe(false)
  })

  it('用户取消明确加载时保持原决策和禁止覆盖状态', async () => {
    vi.mocked(getReviewApi).mockResolvedValue(response({ revision: 2 }))
    vi.mocked(ElMessageBox.confirm).mockRejectedValue('cancel')
    const { state, restore } = mount({ savedReview: null })
    await flush()
    await state.loadSavedDraft()
    expect(restore).not.toHaveBeenCalled()
    expect(state.needsLoad).toBe(true)
    expect(state.canSave).toBe(false)
  })

  it('保存捕获 snapshot，保存期间的新采纳和父级 saved 回填仍显示未保存', async () => {
    const pending = deferred<ReviewResponse>()
    vi.mocked(saveReviewApi).mockReturnValue(pending.promise)
    const { state, props, saved } = mount({ coverage: partial })
    const saving = state.save('draft')
    expect(state.busy).toBe('draft')
    expect(state.canSave).toBe(false)
    props.issues[0]._accepted = true
    props.coverage!.failed_chunks[0].error_code = 'retry-failed'
    await flush()
    const sent = vi.mocked(saveReviewApi).mock.calls[0][1]
    expect(sent.issues[0]._accepted).toBe(false)
    expect(sent.coverage?.failed_chunks[0].error_code).toBe('timeout')
    pending.resolve(response({ revision: 1, coverage: partial }))
    await saving
    await flush()
    expect(saved).toHaveBeenCalledTimes(1)
    expect(state.dirty).toBe(true)
    expect(state.statusText).toBe('有未保存改动')
    expect(props.issues[0]._accepted).toBe(true)
  })

  it('成功保存无后续改动时变为已保存，第二次请求使用已确认 revision', async () => {
    vi.mocked(saveReviewApi).mockResolvedValue(response({ revision: 2, issues: [issue(true)] }))
    const { props, state } = mount()
    props.issues[0]._accepted = true
    await flush()
    expect(state.dirty).toBe(true)
    await state.save('draft')
    await flush()
    expect(state.dirty).toBe(false)
    await state.save('draft')
    expect(vi.mocked(saveReviewApi).mock.calls[1][1].revision).toBe(2)
  })

  it('409 显示冲突，不自动获取最新 revision；明确重读和加载后才能保存', async () => {
    vi.mocked(saveReviewApi).mockRejectedValue({ response: { status: 409, data: { detail: '并发冲突' } } })
    const { props, state, saved } = mount()
    props.issues[0]._accepted = true
    await flush()
    await state.save('draft')
    expect(state.conflict).toBe(true)
    expect(state.error).toContain('409')
    expect(state.remote?.revision).toBe(0)
    expect(getReviewApi).not.toHaveBeenCalled()
    expect(saved).not.toHaveBeenCalled()
    await state.save('draft')
    expect(saveReviewApi).toHaveBeenCalledTimes(1)
    vi.mocked(getReviewApi).mockResolvedValue(response({ revision: 8 }))
    await state.loadRemote()
    expect(state.canSave).toBe(false)
    expect(props.issues[0]._accepted).toBe(true)
    await state.loadSavedDraft()
    await flush()
    expect(state.conflict).toBe(false)
    expect(state.canSave).toBe(true)
  })

  it('普通保存失败保留脏状态，可按原 revision 重试', async () => {
    vi.mocked(saveReviewApi).mockRejectedValue(new Error('离线'))
    const { state } = mount({ issues: [issue(true)] })
    await state.save('draft')
    expect(state.error).toContain('离线')
    expect(state.dirty).toBe(true)
    expect(state.canSave).toBe(true)
    expect(state.remote?.revision).toBe(0)
  })

  it('保存版本可不填名称，携带 coverage；达到 20 个仅禁止新版本', async () => {
    vi.mocked(createReviewVersionApi).mockResolvedValue(response({ revision: 1, versions: [version()] }))
    const { state } = mount({ coverage: partial })
    state.versionLabel = '   '
    await state.save('version')
    expect(createReviewVersionApi).toHaveBeenCalledWith(1, expect.objectContaining({ revision: 0, coverage: partial }), expect.anything())
    expect(vi.mocked(createReviewVersionApi).mock.calls[0][1]).not.toHaveProperty('label')
    const full = mount({ savedReview: response({ versions: Array.from({ length: 20 }, (_, i) => version({ id: `v${i}` })) }) })
    expect(full.state.versionLimitReached).toBe(true)
    expect(full.state.canSave).toBe(true)
    await full.state.save('version')
    expect(createReviewVersionApi).toHaveBeenCalledTimes(1)
  })

  it('版本 422 错误明确显示，不发 saved 或丢失本地修改', async () => {
    vi.mocked(createReviewVersionApi).mockRejectedValue({ response: { status: 422, data: { detail: '版本达到上限' } } })
    const { state, saved } = mount({ issues: [issue(true)] })
    await state.save('version')
    expect(state.error).toContain('版本达到上限')
    expect(state.dirty).toBe(true)
    expect(saved).not.toHaveBeenCalled()
  })

  it('恢复历史版本只 emit issues/coverage，不直接覆盖服务端，并提示需保存', async () => {
    const historical = version()
    const { state, restore, props } = mount({ savedReview: response({ versions: [historical] }) })
    await state.restoreVersion(historical)
    await flush()
    expect(restore).toHaveBeenCalledWith({ issues: [issue(true)], coverage: partial, compare: null })
    expect(state.notice).toContain('尚未保存')
    expect(state.dirty).toBe(true)
    expect(saveReviewApi).not.toHaveBeenCalled()
    expect(createReviewVersionApi).not.toHaveBeenCalled()
    props.issues[0]._accepted = false
    expect(historical.issues[0]._accepted).toBe(true)
  })

  it('左右支持原文/当前/历史，历史全文优先 modified_text，纯插值不产生 HTML 元素', async () => {
    const historical = version({ label: '<b>版本</b>', modified_text: '<img src=x onerror=alert(1)>' })
    const { state, root } = mount({ issues: [issue(true)], savedReview: response({ versions: [historical] }) })
    expect(state.leftChoice).toBe('original')
    expect(state.rightChoice).toBe('current')
    expect(state.panes.left.text).toBe(source)
    expect(state.panes.right.text).toBe('账号与帐号')
    expect(state.comparison.changes).toHaveLength(1)
    state.drawerOpen = true
    state.leftChoice = 'current'
    state.rightChoice = 'version:v1'
    await flush()
    expect(state.panes.right.text).toBe(historical.modified_text)
    expect(state.comparison.changes).toEqual([])
    expect(textContent(root)).toContain(historical.modified_text)
    const allNodes = (target: HostNode): HostNode[] => [target, ...target.children.flatMap(allNodes)]
    expect(allNodes(root).some(target => target.kind === 'img')).toBe(false)
    expect(allNodes(root).some(target => 'innerHTML' in target.props)).toBe(false)
  })

  it('切换 record 会中止旧请求；旧保存响应不能回填新文档', async () => {
    const pending = deferred<ReviewResponse>()
    vi.mocked(saveReviewApi).mockReturnValue(pending.promise)
    const { state, props, saved } = mount()
    const saving = state.save('draft')
    const signal = vi.mocked(saveReviewApi).mock.calls[0][2]?.signal
    props.recordId = 2
    props.sourceText = '另一篇'
    props.issues = []
    props.savedReview = response({ record_id: 2, original_text: '另一篇', issues: [] })
    await flush()
    expect(signal?.aborted).toBe(true)
    pending.resolve(response({ revision: 1 }))
    await saving
    expect(saved).not.toHaveBeenCalled()
    expect(state.remote?.record_id).toBe(2)
    expect(state.remote?.revision).toBe(0)
  })

  it('旧 GET 即使不响应 AbortSignal 也不能回填新文档', async () => {
    const pending = deferred<ReviewResponse>()
    vi.mocked(getReviewApi).mockReturnValueOnce(pending.promise)
    const { state, props, restore } = mount({ savedReview: null })
    props.recordId = 2
    props.savedReview = response({ record_id: 2, revision: 2 })
    await flush()
    pending.resolve(response({ revision: 7 }))
    await flush()
    expect(state.remote?.record_id).toBe(2)
    expect(state.remote?.revision).toBe(2)
    expect(restore).not.toHaveBeenCalled()
  })

  it('取消保存后忽略迟到响应，必须重新读取才能继续保存', async () => {
    const pending = deferred<ReviewResponse>()
    vi.mocked(saveReviewApi).mockReturnValue(pending.promise)
    const { state, saved } = mount({ issues: [issue(true)] })
    const saving = state.save('draft')
    state.cancelRequest()
    pending.resolve(response({ revision: 1 }))
    await saving
    expect(state.mustRefresh).toBe(true)
    expect(state.canSave).toBe(false)
    expect(state.dirty).toBe(true)
    expect(state.notice).toContain('服务端可能已收到')
    expect(saved).not.toHaveBeenCalled()
  })

  it('确认恢复期间切换 record，不回放到错误文档', async () => {
    const confirmation = deferred<Awaited<ReturnType<typeof ElMessageBox.confirm>>>()
    vi.mocked(ElMessageBox.confirm).mockReturnValue(confirmation.promise)
    const { state, props, restore } = mount({ savedReview: response({ versions: [version()] }) })
    const restoring = state.restoreVersion(version())
    props.recordId = null
    await flush()
    confirmation.resolve('confirm' as Awaited<ReturnType<typeof ElMessageBox.confirm>>)
    await restoring
    expect(restore).not.toHaveBeenCalled()
  })

  it('父页导出保存成功同步 revision，但不把请求后的本地操作标成已保存', async () => {
    const { state, props } = mount({ issues: [issue(true)] })
    props.savedReview = response({ revision: 5 })
    await flush()
    expect(state.needsLoad).toBe(false)
    expect(state.canSave).toBe(true)
    expect(state.remote?.revision).toBe(5)
    expect(state.dirty).toBe(true)
    expect(props.issues[0]._accepted).toBe(true)
  })

  it('取消初次读取后忽略迟到草稿，不解锁保存', async () => {
    const pending = deferred<ReviewResponse>()
    vi.mocked(getReviewApi).mockReturnValueOnce(pending.promise)
    const { state, restore } = mount({ savedReview: null })
    state.cancelRequest()
    pending.resolve(response({ revision: 5 }))
    await flush()
    expect(state.remote).toBeNull()
    expect(state.canSave).toBe(false)
    expect(state.mustRefresh).toBe(true)
    expect(restore).not.toHaveBeenCalled()
  })

  it('卸载组件时中止保存，迟到结果不发 saved', async () => {
    const pending = deferred<ReviewResponse>()
    vi.mocked(saveReviewApi).mockReturnValue(pending.promise)
    const { state, saved, unmount } = mount()
    const saving = state.save('draft')
    const signal = vi.mocked(saveReviewApi).mock.calls[0][2]?.signal
    unmount()
    expect(signal?.aborted).toBe(true)
    pending.resolve(response({ revision: 2 }))
    await saving
    expect(saved).not.toHaveBeenCalled()
  })

  it('GET 错误或原文不匹配时不允许保存', async () => {
    vi.mocked(getReviewApi).mockRejectedValueOnce(new Error('离线'))
    const first = mount({ savedReview: null })
    await flush()
    expect(first.state.error).toContain('离线')
    expect(first.state.canSave).toBe(false)
    vi.mocked(getReviewApi).mockResolvedValueOnce(response({ original_text: '不是当前原文' }))
    await first.state.loadRemote()
    expect(first.state.error).toContain('原文与当前文档不一致')
    expect(first.state.canSave).toBe(false)
  })
})

describe('ReviewWorkspace 多模型草稿', () => {
  it('展示逐模型未完成状态，不能用顶层无 coverage 或空问题误报全部完成', async () => {
    const models = compare()
    const { state, props, root } = mount({ compare: models, savedReview: response({ compare: models }) })
    expect(state.dirty).toBe(false)
    expect(textContent(root)).toContain('多模型审阅')
    expect(textContent(root)).toContain('2 个模型')
    expect(textContent(root)).toContain('部分模型未完成')
    props.compare!.results.forEach(result => { result.coverage = null })
    await flush()
    expect(textContent(root)).toContain('无法确认全文完成')
    expect(textContent(root)).not.toContain('所有模型已完成')
  })

  it.each([
    ['draft', 'coverage'], ['draft', 'ownership'], ['version', 'coverage'], ['version', 'ownership'],
  ] as const)('%s 捕获独立 compare 快照；请求期间只有模型 %s 变化仍 dirty', async (kind, change) => {
    const api = kind === 'draft' ? saveReviewApi : createReviewVersionApi
    const pending = deferred<ReviewResponse>()
    vi.mocked(api).mockReturnValueOnce(pending.promise)
    const models = compare()
    const baseline = response({ issues: [issue(true)], compare: models })
    const { state, props, saved } = mount({ issues: baseline.issues, compare: models, savedReview: baseline })
    expect(state.dirty).toBe(false)
    state.versionLabel = '  多模型版本  '
    const saving = state.save(kind)
    const sent = vi.mocked(api).mock.calls[0][1]
    const captured = JSON.parse(JSON.stringify(sent))
    expect(sent.compare).toEqual(models)
    expect(sent.compare).not.toBe(props.compare)
    expect(sent.compare!.results[0].issues[0]).not.toBe(props.compare!.results[0].issues[0])
    expect(sent.compare!.results[0].coverage!.failed_chunks[0]).not.toBe(props.compare!.results[0].coverage!.failed_chunks[0])
    expect(sent.issues[0]._accepted).toBe(true)
    if (kind === 'version') expect(sent).toHaveProperty('label', '多模型版本')
    if (change === 'coverage') {
      props.compare!.results[1].coverage!.status = 'complete'
      props.compare!.results[1].coverage!.completed_chunks = 1
      props.compare!.results[1].coverage!.failed_chunks.splice(0)
    } else {
      // 顶层问题和正文完全相同，仅模型乙不再报告该问题。
      props.compare!.results[1].issues.splice(0)
    }
    await flush()
    expect(sent).toEqual(captured)
    expect(props.issues).toEqual(baseline.issues)
    expect(props.coverage).toBeNull()
    pending.resolve(response({ revision: 1, issues: captured.issues, compare: captured.compare,
      versions: kind === 'version' ? [version({ compare: captured.compare })] : [] }))
    await saving
    await flush()
    expect(saved).toHaveBeenCalledTimes(1)
    expect(state.remote?.compare).toEqual(models)
    expect(state.dirty).toBe(true)
    expect(state.statusText).toBe('有未保存改动')
    expect(state.canSave).toBe(true)
    expect(props.compare).not.toEqual(models)
  })

  it.each(['draft', 'version'] as const)('%s 保存后无后续修改保持 clean，服务端规范化 compare 不造成假 dirty', async kind => {
    const api = kind === 'draft' ? saveReviewApi : createReviewVersionApi
    const models = compare()
    models.results[1].issues.push({ ...issue(), start: -1, end: -1 })
    const wireCompare = CompareReview.serializeCompareReview(models)
    vi.mocked(api).mockResolvedValueOnce(response({ revision: 2, compare: wireCompare }))
    const { state, props } = mount({ compare: models, savedReview: response({ compare: wireCompare }) })
    expect(state.dirty).toBe(false)
    await state.save(kind)
    await flush()
    expect(state.dirty).toBe(false)
    expect(state.statusText).toBe('已保存')
    props.savedReview = response({ revision: 3, compare: wireCompare })
    await flush()
    expect(state.dirty).toBe(false)
    expect(state.remote?.revision).toBe(3)
  })

  it('首次读取多模型草稿必须显式加载；事件恢复完整 compare 且与服务端快照独立', async () => {
    const remote = response({ revision: 4, issues: [issue(true)], compare: compare() })
    vi.mocked(getReviewApi).mockResolvedValueOnce(remote)
    const { state, props, restore } = mount({ savedReview: null })
    await flush()
    expect(state.needsLoad).toBe(true)
    expect(props.compare).toBeUndefined()
    expect(restore).not.toHaveBeenCalled()
    await state.loadSavedDraft()
    await flush()
    expect(restore).toHaveBeenCalledWith({ issues: [issue(true)], coverage: null, compare: remote.compare })
    expect(props.compare).toEqual(remote.compare)
    expect(state.dirty).toBe(false)
    expect(state.canSave).toBe(true)
    props.compare!.results[0].issues[0].explanation = '新的模型说明'
    props.compare!.results[0].coverage!.failed_chunks[0].error_code = 'retry-failed'
    await flush()
    expect(state.dirty).toBe(true)
    expect(remote.compare!.results[0].issues[0].explanation).toBe('模型1的说明')
    expect(state.remote?.compare?.results[0].coverage?.failed_chunks[0].error_code).toBe('timeout')
  })

  it('历史多模型版本恢复逐模型结果和 coverage，只更新本地并在后续编辑时保持历史不可变', async () => {
    const models = compare()
    const historical = version({ compare: models })
    const { state, props, restore } = mount({ savedReview: response({ versions: [historical] }) })
    state.drawerOpen = true
    state.rightChoice = 'version:v1'
    await flush()
    await state.restoreVersion(state.remote!.versions[0])
    await flush()
    expect(restore).toHaveBeenCalledWith({ issues: historical.issues, coverage: historical.coverage, compare: models })
    expect(props.compare!.results[1].issues[0]).toMatchObject({ severity: 'error', explanation: '模型2的说明' })
    expect(state.dirty).toBe(true)
    expect(state.notice).toContain('尚未保存')
    expect(saveReviewApi).not.toHaveBeenCalled()
    expect(createReviewVersionApi).not.toHaveBeenCalled()
    props.compare!.results[1].issues[0].explanation = '恢复后修改'
    props.compare!.results[1].coverage!.failed_chunks[0].error_code = 'new-error'
    expect(historical.compare).toEqual(models)
    expect(state.remote!.versions[0].compare).toEqual(models)
    expect(state.remote!.versions[0].compare!.results[1].issues[0].explanation).toBe('模型2的说明')
  })

  it.each([undefined, null])('多模型工作区不把缺失 compare=%s 的旧版本伪恢复为多模型，但仍可查看', async legacyCompare => {
    const historical = version({ compare: legacyCompare })
    const models = compare()
    const { state, props, restore } = mount({ compare: models, savedReview: response({ compare: models, versions: [historical] }) })
    state.drawerOpen = true
    state.rightChoice = 'version:v1'
    await flush()
    expect(state.panes.right.text).toBe(historical.modified_text)
    await state.restoreVersion(state.remote!.versions[0])
    expect(state.notice).toContain('未保存逐模型结果')
    expect(state.notice).toContain('无法恢复为多模型')
    expect(restore).not.toHaveBeenCalled()
    expect(ElMessageBox.confirm).not.toHaveBeenCalled()
    expect(props.compare).toEqual(models)
    expect(props.issues).toEqual([issue()])
    expect(state.dirty).toBe(false)
  })

  it.each(['draft', 'version'] as const)('%s 的 409 保留本地 compare；重读不静默覆盖，明确加载后使用新 revision', async kind => {
    const api = kind === 'draft' ? saveReviewApi : createReviewVersionApi
    vi.mocked(api).mockRejectedValueOnce({ response: { status: 409, data: { detail: '冲突' } } })
    const models = compare()
    const { state, props, restore } = mount({ compare: models, savedReview: response({ compare: models }) })
    props.compare!.results[0].issues[0].explanation = '尚未保存的本地补查'
    await flush()
    await state.save(kind)
    expect(state.conflict).toBe(true)
    expect(state.dirty).toBe(true)
    expect(getReviewApi).not.toHaveBeenCalled()
    expect(restore).not.toHaveBeenCalled()
    const latest = compare()
    latest.results[1].coverage = { status: 'complete', total_chunks: 1, completed_chunks: 1, failed_chunks: [] }
    vi.mocked(getReviewApi).mockResolvedValueOnce(response({ revision: 8, compare: latest, issues: [issue(true)] }))
    await state.loadRemote()
    expect(state.canSave).toBe(false)
    expect(props.compare!.results[0].issues[0].explanation).toBe('尚未保存的本地补查')
    expect(restore).not.toHaveBeenCalled()
    vi.mocked(ElMessageBox.confirm).mockRejectedValueOnce('cancel')
    await state.loadSavedDraft()
    expect(restore).not.toHaveBeenCalled()
    expect(state.conflict).toBe(true)
    await state.loadSavedDraft()
    await flush()
    expect(restore).toHaveBeenCalledWith({ issues: [issue(true)], coverage: null, compare: latest })
    expect(props.compare).toEqual(latest)
    expect(state.conflict).toBe(false)
    expect(state.dirty).toBe(false)
    expect(state.canSave).toBe(true)
    vi.mocked(api).mockResolvedValueOnce(response({ revision: 9, compare: latest, issues: [issue(true)] }))
    await state.save(kind)
    expect(vi.mocked(api).mock.calls[1][1]).toMatchObject({ revision: 8, compare: latest })
  })

  it('父级 saved 回填不吞掉仅模型 coverage 的新改动', async () => {
    const models = compare()
    const { props, state } = mount({ compare: models, savedReview: response({ compare: models }) })
    props.compare!.results[1].coverage!.failed_chunks[0].error_code = 'retry-failed'
    props.savedReview = response({ revision: 5, compare: models })
    await flush()
    expect(state.remote?.revision).toBe(5)
    expect(state.dirty).toBe(true)
    expect(props.compare!.results[1].coverage!.failed_chunks[0].error_code).toBe('retry-failed')
  })

  it('游客多模型审阅只留内存，草稿和版本均不能保存或写本地存储', async () => {
    const storage = { getItem: vi.fn(), setItem: vi.fn() }
    vi.stubGlobal('localStorage', storage)
    const { state, root } = mount({ recordId: null, compare: compare(), savedReview: null })
    expect(textContent(root)).toContain('需登录保存')
    expect(textContent(root)).toContain('多模型审阅')
    await state.save('draft')
    await state.save('version')
    expect(getReviewApi).not.toHaveBeenCalled()
    expect(saveReviewApi).not.toHaveBeenCalled()
    expect(createReviewVersionApi).not.toHaveBeenCalled()
    expect(storage.getItem).not.toHaveBeenCalled()
    expect(storage.setItem).not.toHaveBeenCalled()
  })
})
