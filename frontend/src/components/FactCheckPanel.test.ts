/* eslint-disable vue/one-component-per-file -- 沿用现有无浏览器组件 renderer 测试范式。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as Vue from 'vue'
import { createRenderer, defineComponent, h, nextTick, reactive, type Component, type VNode } from 'vue'
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'
import { ModuleKind, ScriptTarget, transpileModule } from 'typescript'
import type { CreateFactCheckPayload, FactCheckClaim, FactCheckOptions, FactCheckRun, FactSearchSource } from '@/api/factCheck'

vi.mock('@/utils/request', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } }))
import request from '@/utils/request'
import * as FactCheckApi from '@/api/factCheck'
import * as ReviewApi from '@/api/review'
import source from './FactCheckPanel.vue?raw'
import traceSource from './FactCheckSearchTrace.vue?raw'
import workbenchSource from '@/views/user/fact-check/index.vue?raw'
import textPage from '@/views/user/proofread/TextProofread.vue?raw'
import documentPage from '@/views/user/proofread/DocumentProofread.vue?raw'

const user = reactive({ token: 'token', isLoggedIn: true, userInfo: { id: 1 }, hasPermission: () => true, fetchUserInfo: vi.fn() })
const route = reactive({ params: {} as { id?: string }, query: {}, fullPath: '/fact-check' })
const router = { push: vi.fn(), replace: vi.fn() }
const uploadDocumentApi = vi.fn()
const elements = Object.fromEntries(['ElButton', 'ElCheckbox', 'ElFormItem', 'ElOption', 'ElProgress', 'ElSelect', 'ElTag', 'ElInput', 'ElRadioGroup', 'ElRadioButton', 'ElPagination'].map(name => [name,
  defineComponent({ inheritAttrs: false, setup: (_props, { slots, attrs }) => () => h(name, attrs, slots.default?.()) }),
]))
const modules: Record<string, unknown> = {
  vue: Vue, 'element-plus': { ...elements, ElMessageBox: { confirm: vi.fn() } },
  'vue-router': { useRoute: () => route, useRouter: () => router },
  '@/api/factCheck': FactCheckApi, '@/api/review': ReviewApi, '@/api/document': { uploadDocumentApi },
  '@/stores/user': { useUserStore: () => user },
}
function compileComponent(content: string, filename: string) {
  const { descriptor } = parse(content)
  const script = compileScript(descriptor, { id: filename })
  const template = compileTemplate({ source: descriptor.template!.content, filename, id: filename, compilerOptions: { bindingMetadata: script.bindings } })
  if (template.errors.length) throw new Error(String(template.errors[0]))
  const compiled = { exports: {} as { default: Component; render: () => VNode } }
  const code = transpileModule(`${script.content}\n${template.code}`, { compilerOptions: { module: ModuleKind.CommonJS, target: ScriptTarget.ES2020 } }).outputText
  new Function('require', 'module', 'exports', code)((id: string) => {
    if (!(id in modules)) throw new Error(`Unexpected dependency: ${id}`)
    return modules[id]
  }, compiled, compiled.exports)
  return Object.assign(compiled.exports.default, { render: compiled.exports.render })
}
const Trace = compileComponent(traceSource, 'FactCheckSearchTrace.vue')
modules['@/components/FactCheckSearchTrace.vue'] = { default: Trace }
const Panel = compileComponent(source, 'FactCheckPanel.vue')
const Workbench = compileComponent(workbenchSource, 'FactCheckWorkbench.vue')
interface HostNode { tag: string; text: string; props: Record<string, unknown>; parent: HostNode | null; children: HostNode[] }
const node = (text = '', tag = ''): HostNode => ({ tag, text, props: {}, parent: null, children: [] })
function remove(child: HostNode) {
  const index = child.parent?.children.indexOf(child) ?? -1
  if (index >= 0) child.parent!.children.splice(index, 1)
  child.parent = null
}
function insert(child: HostNode, parent: HostNode, anchor: HostNode | null = null) {
  remove(child)
  const index = anchor ? parent.children.indexOf(anchor) : -1
  parent.children.splice(index < 0 ? parent.children.length : index, 0, child)
  child.parent = parent
}
const renderer = createRenderer<HostNode, HostNode>({
  createElement: tag => node('', tag), createText: text => node(text), createComment: () => node(),
  setText: (target, text) => { target.text = text },
  setElementText: (target, text) => { target.text = text; target.children = [] },
  patchProp: (target, key, _old, value) => { target.props[key] = value },
  parentNode: target => target.parent,
  nextSibling: target => target.parent?.children[target.parent.children.indexOf(target) + 1] ?? null,
  insert, remove,
  insertStaticContent(content, parent, anchor) { const target = node(content); insert(target, parent, anchor); return [target, target] },
})
function descendants(root: HostNode): HostNode[] { return [root, ...root.children.flatMap(descendants)] }
function text(root: HostNode): string { return root.text + root.children.map(text).join('') }
interface State {
  expanded: boolean; options: FactCheckOptions | null; active: FactCheckRun | null; history: FactCheckRun[];
  mode: 'web' | 'trusted'; sourceIds: string[]; consented: boolean; blockedReason: string; canStart: boolean;
  busy: string; error: string; errorKind: string; pendingCreate: CreateFactCheckPayload | null; selectedClaim: number | null;
  loadPanel(): Promise<void>; startRun(): Promise<void>; refreshRun(): Promise<void>; cancelRun(): Promise<void>; selectRun(id: number): void;
  claimContext(claim: FactCheckClaim): { before: string; target: string; after: string } | null; safeEvidenceUrl(raw: string): string;
}
const cleanups: (() => void)[] = []
function mount(recordId: number | null = 7, sourceText = '𠮷事实甲与事实甲') {
  const props = reactive({ recordId, sourceText })
  const started = vi.fn()
  let vnode!: VNode
  const app = renderer.createApp(defineComponent({ setup: () => () => (vnode = h(Panel, { ...props, onStarted: started })) }))
  app.component('RouterLink', defineComponent({ setup: (_props, { slots, attrs }) => () => h('router-link', attrs, slots.default?.()) }))
  const root = node()
  app.mount(root)
  const state = (vnode.component as unknown as { setupState: State }).setupState
  let mounted = true
  const unmount = () => { if (mounted) app.unmount(); mounted = false }
  cleanups.push(unmount)
  return { props, state, root, unmount, started }
}
async function flush() { for (let index = 0; index < 8; index++) await nextTick() }
async function open(state: State) { state.expanded = true; await flush() }
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}
const configuration = (): FactCheckOptions => ({ available: true, unavailable_reason: '', provider: 'model', model_name: '当前配置 / active-model', max_claims: 10, max_text_chars: 20000, daily_limit: 20, sources: [
  { id: 's1', name: '来源一', domain: 'one.example.org', path_prefix: '/', is_enabled: true },
  { id: 's2', name: '来源二', domain: 'two.example.org', path_prefix: '/', is_enabled: false },
  { id: 's3', name: '来源三', domain: 'three.example.org', path_prefix: '/', is_enabled: true },
] })
const run = (patch: Partial<FactCheckRun> = {}): FactCheckRun => ({ title: '材料', source_kind: 'record', file_id: null, parent_run_id: null, stage: 'extract', depth: 'standard', confirm_claims: false, max_claims: 10, id: 21, record_id: 7, mode: 'web', provider: 'model', status: 'PENDING', progress: 0, message: '', error_code: null, result: null, source_hash: 'hash', created_at: '2026-08-01T10:00:00Z', finished_at: null, source_ids: [], ...patch })
const claim = (patch: Partial<FactCheckClaim> = {}): FactCheckClaim => ({ id: 'c1', original: '事实甲', start: 5, end: 8, statement: '事实陈述', verdict: 'insufficient', reason: '暂未取得足够证据', suggestion: '建议人工查阅原始资料', evidence: [], checked: true, ...patch })
const report = (status: 'partial' | 'complete' = 'partial', claims: FactCheckClaim[] = []): NonNullable<FactCheckRun['result']> => ({
  claims, coverage: { status, extracted: claims.length, checked: claims.filter(item => item.checked).length, unverified: claims.filter(item => !item.checked).length, reason: '' },
  usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15, search_queries: 0, pages_fetched: 0 }, checked_at: '2026-09-16',
})
let runs: FactCheckRun[]
let options: FactCheckOptions
let workbenchRun: FactCheckRun | null
beforeEach(() => {
  vi.resetAllMocks()
  vi.useFakeTimers()
  user.token = 'token'; user.isLoggedIn = true
  route.params = {}; route.query = {}; route.fullPath = '/fact-check'
  runs = []; options = configuration(); workbenchRun = null
  vi.mocked(request.get).mockImplementation(async (path, config) => {
    if (path === '/fact-check/options') return options
    if (path === '/fact-check/runs') return runs.filter(item => item.record_id === (config?.params as { record_id?: number })?.record_id)
    if (path === '/fact-check/history') return { items: runs, total: runs.length }
    if (path.endsWith('/source')) return { text: '𠮷事实甲与事实甲', source_hash: 'hash' }
    if (path.endsWith('/reviews')) return []
    return workbenchRun ?? run({ status: 'SUCCESS', progress: 100 })
  })
  vi.mocked(request.post).mockResolvedValue(run())
})
afterEach(() => { cleanups.splice(0).forEach(cleanup => cleanup()); vi.useRealTimers() })

describe('FactCheckPanel', () => {
  it('默认折叠零请求，展开仅加载配置和历史，明确原始版本及两模式联网', async () => {
    const { state, root } = mount()
    expect(state.expanded).toBe(false)
    expect(request.get).not.toHaveBeenCalled()
    expect(request.post).not.toHaveBeenCalled()
    const toggle = descendants(root).find(item => item.props['data-testid'] === 'fact-check-toggle')!
    expect(toggle.props['aria-expanded']).toBe(false)
    ;(toggle.props.onClick as () => void)()
    await flush()
    expect(request.get).toHaveBeenCalledTimes(2)
    expect(state.sourceIds).toEqual(['s1', 's3'])
    expect(state.consented).toBe(false)
    expect(state.canStart).toBe(false)
    await state.startRun()
    expect(request.post).not.toHaveBeenCalled()
    expect(text(root)).toContain('两种模式都会联网')
    expect(text(root)).toContain('不是采纳修改后的文本')
    expect(text(root)).toContain('内容可对外检索；涉密材料请勿使用')
  })
  it('仅显式启动成功发出原记录ID，供父页保留刷新恢复地址', async () => {
    const { state, started } = mount(); await open(state)
    expect(started).not.toHaveBeenCalled()
    state.consented = true
    await state.startRun()
    expect(started).toHaveBeenCalledExactlyOnceWith(7)
  })
  it.each([
    ['model', '当前模型配置不支持原生联网'],
    ['tavily', '未配置 Tavily 密钥'],
  ] as const)('%s 不可用明确提示，不自动切换但保留历史查看', async (provider, reason) => {
    options.provider = provider; options.available = false; options.unavailable_reason = reason
    runs = [run({ status: 'SUCCESS', provider })]
    const { state } = mount()
    await open(state)
    state.consented = true
    expect(state.blockedReason).toContain(reason)
    expect(state.options?.provider).toBe(provider)
    expect(state.history).toHaveLength(1)
    await state.startRun()
    await vi.advanceTimersByTimeAsync(10000)
    expect(request.post).not.toHaveBeenCalled()
    expect(request.get).toHaveBeenCalledTimes(2)
  })
  it.each(['model', 'tavily'] as const)('%s 展示当前服务与模型，确认前不启动；启动契约不增加模型或密钥', async provider => {
    options.provider = provider
    vi.mocked(request.post).mockResolvedValueOnce(run({ provider }))
    const { state, root } = mount(); await open(state)
    const label = provider === 'model' ? '模型原生联网' : 'Tavily'
    const current = descendants(root).find(item => item.props['data-testid'] === 'fact-check-current-provider')!
    expect(text(current)).toBe(`当前搜索服务：${label} · 当前模型配置：${options.model_name}`)
    await vi.advanceTimersByTimeAsync(10000)
    await state.startRun()
    expect(request.post).not.toHaveBeenCalled()
    expect(request.get).toHaveBeenCalledTimes(2)
    state.consented = true
    await state.startRun(); await flush()
    expect(request.post).toHaveBeenCalledExactlyOnceWith('/fact-check/runs', {
      record_id: 7, mode: 'web', source_ids: [], allow_external_search: true, request_id: expect.stringMatching(/^[a-f0-9-]{36}$/),
    }, { signal: expect.any(AbortSignal), headers: { 'X-Silent-Error': 'true' } })
    expect(state.active?.provider).toBe(provider)
    const reportProvider = descendants(root).find(item => item.props['data-testid'] === 'fact-check-run-provider')!
    expect(text(reportProvider)).toBe(`本次搜索服务：${label}`)
  })
  it.each(['model', 'tavily'] as const)('历史 %s 使用已保存 provider，刷新设置或切换详情不能重标旧报告', async provider => {
    const historical = run({ provider, status: 'SUCCESS' })
    runs = [historical]
    options.provider = provider === 'model' ? 'tavily' : 'model'
    const { state, root } = mount(); await open(state)
    const label = provider === 'model' ? '模型原生联网' : 'Tavily'
    const assertSavedProvider = () => {
      expect(text(descendants(root).find(item => item.props['data-testid'] === 'fact-check-run-provider')!)).toBe(`本次搜索服务：${label}`)
      const historySelect = descendants(root).find(item => item.props['aria-label'] === '事实核查历史')!
      expect(descendants(historySelect).find(item => item.props.value === historical.id)!.props.label).toContain(` · ${label} · `)
      expect(text(descendants(root).find(item => item.props['data-testid'] === 'fact-check-report')!)).not.toContain(options.model_name)
    }
    assertSavedProvider()
    options = { ...options, model_name: '另一配置 / new-model' }
    await state.loadPanel(); await flush()
    assertSavedProvider()
    vi.mocked(request.get).mockResolvedValueOnce(historical)
    state.selectRun(historical.id); await flush()
    assertSavedProvider()
    expect(request.post).not.toHaveBeenCalled()
  })
  it('原生搜索运行时被模型或账号拒绝，只显示失败，不回退或自动再创建', async () => {
    vi.mocked(request.post).mockResolvedValueOnce(run({ status: 'FAILURE', error_code: 'MODEL_SEARCH_REJECTED', message: '当前模型或账号拒绝原生搜索' }))
    const { state, root } = mount(); await open(state); state.consented = true
    await state.startRun(); await flush()
    expect(text(root)).toContain('当前模型或账号拒绝原生搜索')
    expect(state.active?.provider).toBe('model')
    await vi.advanceTimersByTimeAsync(10000)
    expect(request.post).toHaveBeenCalledTimes(1)
    expect(request.get).toHaveBeenCalledTimes(2)
    expect(request.put).not.toHaveBeenCalled()
  })
  it.each(['guest', 'record'] as const)('%s 不发送请求，提示先登录/取得记录', async kind => {
    if (kind === 'guest') { user.isLoggedIn = false; user.token = '' }
    const { state } = mount(kind === 'record' ? null : 7)
    await open(state)
    state.consented = true
    expect(state.blockedReason).toContain('登录')
    await state.startRun()
    expect(request.get).not.toHaveBeenCalled()
    expect(request.post).not.toHaveBeenCalled()
  })
  it('按 Unicode 字符计算 20000 上限，不截断上传', async () => {
    const { state, props } = mount(7, '𠮷'.repeat(20000))
    await open(state); state.consented = true
    expect(state.canStart).toBe(true)
    props.sourceText += '𠮷'
    await flush(); state.consented = true
    expect(state.blockedReason).toContain('超过 20000')
    await state.startRun()
    expect(request.post).not.toHaveBeenCalled()
  })
  it('可信信源默认全选已启用项，空选择或失效 ID 禁止启动；web 不携带信源', async () => {
    const { state } = mount()
    await open(state); state.mode = 'trusted'; state.consented = true
    expect(state.canStart).toBe(true)
    state.sourceIds = []
    expect(state.canStart).toBe(false)
    state.sourceIds = ['s2']
    expect(state.canStart).toBe(false)
    state.mode = 'web'
    await state.startRun()
    expect(request.post).toHaveBeenCalledWith('/fact-check/runs', expect.objectContaining({ record_id: 7, mode: 'web', source_ids: [], allow_external_search: true, request_id: expect.stringMatching(/^[a-f0-9-]{36}$/) }), expect.objectContaining({ signal: expect.any(AbortSignal) }))
  })
  it('trusted 无信源时解释配置要求，不虚构站点', async () => {
    options.sources = []
    const { state } = mount(); await open(state)
    state.mode = 'trusted'; state.consented = true
    expect(state.sourceIds).toEqual([])
    expect(state.blockedReason).toContain('没有已启用的可信信源')
    expect(state.canStart).toBe(false)
  })
  it('显式启动携带信源，防双击并每 1800ms 带 signal 静默轮询，终态停止', async () => {
    const pending = deferred<FactCheckRun>()
    vi.mocked(request.post).mockReturnValueOnce(pending.promise)
    const { state } = mount(); await open(state)
    state.mode = 'trusted'; state.consented = true
    const starting = state.startRun()
    await state.startRun()
    expect(request.post).toHaveBeenCalledTimes(1)
    expect(vi.mocked(request.post).mock.calls[0][1]).toMatchObject({ mode: 'trusted', source_ids: ['s1', 's3'] })
    pending.resolve(run({ mode: 'trusted', source_ids: ['s1', 's3'] })); await starting
    await vi.advanceTimersByTimeAsync(1799)
    expect(request.get).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(1)
    expect(request.get).toHaveBeenLastCalledWith('/fact-check/runs/21', { signal: expect.any(AbortSignal), headers: { 'X-Silent-Error': 'true' } })
    expect(state.active?.status).toBe('SUCCESS')
    await vi.advanceTimersByTimeAsync(10000)
    expect(request.get).toHaveBeenCalledTimes(3)
  })
  it('启动断网后保留请求编号，只有再次点击才幂等重试', async () => {
    vi.mocked(request.post).mockRejectedValueOnce(new Error('断网'))
    const { state } = mount(); await open(state); state.consented = true
    await state.startRun()
    const payload = vi.mocked(request.post).mock.calls[0][1]
    expect(state.pendingCreate).toEqual(payload)
    await vi.advanceTimersByTimeAsync(20000)
    expect(request.post).toHaveBeenCalledTimes(1)
    await state.startRun()
    expect(vi.mocked(request.post).mock.calls[1][1]).toEqual(payload)
    expect(state.pendingCreate).toBeNull()
  })
  it('已保存历史刷新恢复运行，保留最近20次且不创建任务', async () => {
    runs = Array.from({ length: 25 }, (_, index) => run({ id: index + 1, status: index === 22 ? 'RUNNING' : 'SUCCESS' }))
    const { state } = mount(); await open(state)
    expect(state.history).toHaveLength(20)
    expect(state.history[0].id).toBe(25)
    expect(state.active?.id).toBe(23)
    vi.mocked(request.get).mockResolvedValueOnce(run({ id: 23, status: 'SUCCESS' }))
    await vi.advanceTimersByTimeAsync(1800)
    expect(request.get).toHaveBeenLastCalledWith('/fact-check/runs/23', expect.anything())
    expect(request.post).not.toHaveBeenCalled()
  })
  it('轮询失败暂停，显示重试而非无限请求；手动重试继续', async () => {
    runs = [run()]
    const { state, root } = mount(); await open(state)
    vi.mocked(request.get).mockRejectedValueOnce(new Error('网络断开'))
    await vi.advanceTimersByTimeAsync(1800)
    expect(state.error).toContain('已暂停自动刷新')
    expect(text(root)).toContain('重试读取状态')
    await vi.advanceTimersByTimeAsync(10000)
    expect(request.get).toHaveBeenCalledTimes(3)
    await state.refreshRun()
    expect(state.error).toBe('')
    expect(state.active?.status).toBe('SUCCESS')
  })
  it('取消会中断在途轮询，迟到状态不得覆盖 CANCELLED', async () => {
    runs = [run()]
    const pending = deferred<FactCheckRun>()
    const { state } = mount(); await open(state)
    vi.mocked(request.get).mockReturnValueOnce(pending.promise)
    const polling = state.refreshRun()
    const signal = vi.mocked(request.get).mock.calls.slice(-1)[0][1]!.signal!
    vi.mocked(request.post).mockResolvedValueOnce(run({ status: 'CANCELLED' }))
    await state.cancelRun()
    expect(signal.aborted).toBe(true)
    expect(request.post).toHaveBeenLastCalledWith('/fact-check/runs/21/cancel', undefined, expect.objectContaining({ signal: expect.any(AbortSignal) }))
    pending.resolve(run({ status: 'RUNNING' })); await polling
    expect(state.active?.status).toBe('CANCELLED')
    await vi.advanceTimersByTimeAsync(10000)
    expect(request.get).toHaveBeenCalledTimes(3)
  })
  it.each(['record', 'text', 'close', 'unmount'] as const)('切换 %s 中止在途加载，迟到配置/历史不污染新记录', async action => {
    const pending = deferred<FactCheckOptions>()
    vi.mocked(request.get).mockReturnValueOnce(pending.promise)
    const { state, props, unmount } = mount(); state.expanded = true
    const signal = vi.mocked(request.get).mock.calls[0][1]!.signal!
    if (action === 'record') props.recordId = 8
    else if (action === 'text') props.sourceText = '新原文'
    else if (action === 'close') state.expanded = false
    else unmount()
    await flush()
    expect(signal.aborted).toBe(true)
    const before = state.options
    pending.resolve({ ...configuration(), max_claims: 999 }); await flush()
    expect(state.options).toEqual(before)
    expect(state.consented).toBe(false)
    expect(request.post).not.toHaveBeenCalled()
  })
  it('启动响应迟到不会把旧 run 填入新 record，重新确认前不能启动', async () => {
    const pending = deferred<FactCheckRun>()
    vi.mocked(request.post).mockReturnValueOnce(pending.promise)
    const { state, props } = mount(); await open(state); state.consented = true
    const starting = state.startRun()
    const signal = vi.mocked(request.post).mock.calls[0][2]!.signal!
    props.recordId = 8; await flush()
    expect(signal.aborted).toBe(true)
    pending.resolve(run()); await starting
    expect(state.active).toBeNull()
    expect(state.pendingCreate).toBeNull()
    expect(state.canStart).toBe(false)
  })
  it('切换历史中止旧详情请求，返回错记录/错任务均拒绝', async () => {
    runs = [run({ id: 20, status: 'SUCCESS' }), run({ status: 'SUCCESS' })]
    const { state } = mount(); await open(state)
    const pending = deferred<FactCheckRun>()
    vi.mocked(request.get).mockReturnValueOnce(pending.promise)
    state.selectRun(20)
    const signal = vi.mocked(request.get).mock.calls.slice(-1)[0][1]!.signal!
    state.selectRun(21); await flush()
    pending.resolve(run({ id: 20 })); await flush()
    expect(signal.aborted).toBe(true)
    expect(state.active?.id).toBe(21)
    vi.mocked(request.get).mockResolvedValueOnce(run({ record_id: 8 }))
    await state.refreshRun()
    expect(state.error).toContain('不匹配')
    vi.mocked(request.get).mockResolvedValueOnce(run({ id: 99 }))
    await state.refreshRun()
    expect(state.active?.id).toBe(21)
    expect(state.error).toContain('不匹配')
  })
  it('卸载清理轮询，401 提示重新登录且停止重试', async () => {
    runs = [run()]
    const { state, unmount } = mount(); await open(state)
    vi.mocked(request.get).mockRejectedValueOnce({ response: { status: 401 } })
    await vi.advanceTimersByTimeAsync(1800)
    expect(state.error).toContain('重新登录')
    expect(state.canStart).toBe(false)
    await state.refreshRun()
    expect(request.get).toHaveBeenCalledTimes(3)
    unmount(); await vi.advanceTimersByTimeAsync(10000)
    expect(request.get).toHaveBeenCalledTimes(3)
  })
  it('Unicode 半开区间核对 original，不猜重复词位置；无效范围不高亮', () => {
    const { state } = mount()
    expect(state.claimContext(claim())).toEqual({ before: '𠮷事实甲与', target: '事实甲', after: '' })
    for (const patch of [{ start: 6, end: 9 }, { start: -1 }, { start: 1.5 }, { end: 5 }, { original: '事实乙' }, { start: 0, end: 3 }, { end: NaN }]) {
      expect(state.claimContext(claim(patch))).toBeNull()
    }
  })
  it.each(['javascript:alert(1)', 'data:text/html,<script>', 'vbscript:msgbox(1)', '//example.org', '/relative', 'https:\\example.org', 'https://', 'https://user:pass@example.org', 'https://example.org/\npath'])('拦截证据链接 %s', url => {
    expect(mount().state.safeEvidenceUrl(url)).toBe('')
  })
  it.each(['pending', 'searching', 'fetching', 'complete', 'partial', 'failed'] as const)('反证轮 %s 展示实际状态、模型口径检查及安全引文上下文', async status => {
    const xss = '<img src=x onerror=alert(1)>'
    const current = claim({ search_rounds: [
      { kind: 'initial', query: '原查询', status: 'complete', pages_fetched: 1, error_codes: [] },
      { kind: 'counter', query: `反证 更正 ${xss}`, status, pages_fetched: 0, error_codes: status === 'failed' ? ['SEARCH_UNAVAILABLE'] : [] },
    ], evidence: [{
      id: 'e1', title: '正文来源', url: 'https://example.org/page', quote: '引文𠮷', stance: 'context',
      publisher: '发布方', published_at: null, retrieved_at: '2026-09-01',
      checks: {
        subject: { status: 'match', reason: '同一主体' },
        event_time: { status: 'mismatch', reason: `历史而非当期 ${xss}` },
        scope_unit: { status: 'unknown', reason: '未说明统计范围' },
      },
      body_sha256: 'a'.repeat(64), body_hash_scope: 'normalized_model_visible_text_utf8', body_text_length: 100,
      quote_start: 30, quote_end: 33, context_before: `前文 ${xss}`, context_after: ' 后文',
    }] })
    runs = [run({ status: 'SUCCESS', result: {
      claims: [current], coverage: { extracted: 1, checked: 1, unverified: 0, status: 'partial', reason: '部分完成' },
      usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15, search_queries: 2, pages_fetched: 1 }, checked_at: '2026-09-01',
    } })]
    const { state, root } = mount(); await open(state)
    const statusTag = descendants(root).find(item => item.tag === 'ElTag' && text(item) === '核查部分完成')!
    expect(statusTag.props.type).toBe('warning')
    const labels = { pending: '未执行', searching: '检索中', fetching: '抓取中', complete: '检索与抓取完成', partial: '抓取不完整', failed: '检索失败' }
    const rounds = descendants(root).find(item => item.props['data-testid'] === 'fact-check-search-rounds')!
    expect(text(rounds)).toContain(`反证/更正轮：${labels[status]}`)
    if (status === 'failed') expect(text(rounds)).toContain('SEARCH_UNAVAILABLE')
    expect(descendants(root).some(item => item.props['data-testid'] === 'fact-check-quote-context')).toBe(false)
    state.selectedClaim = 0; await flush()
    const context = descendants(root).find(item => item.props['data-testid'] === 'fact-check-quote-context')!
    expect(text(context)).toBe(`前文 ${xss}引文𠮷 后文`)
    expect(descendants(context).find(item => item.tag === 'mark')!.text).toBe('引文𠮷')
    for (const label of ['主体/事件：一致', '事件时间：不一致', '统计范围/单位：无法确定', '[30, 33)', '不是原始 HTML 或完整网页', 'a'.repeat(64), '并未独立验证语义', '反证轮完成不代表找到反证']) expect(text(root)).toContain(label)
    expect(descendants(root).some(item => 'innerHTML' in item.props || item.tag === 'img')).toBe(false)
    expect(request.post).not.toHaveBeenCalled()
  })
  it('实际渲染安全链接与 Unicode 高亮，所有报告内容仅作文本；四种结论及覆盖不混入审阅', async () => {
    const xss = '<img src=x onerror=alert(1)>'
    runs = [run({ status: 'SUCCESS', result: {
      claims: ['supported', 'refuted', 'insufficient', 'conflicting'].map((verdict, index) => claim({ id: String(index), verdict: verdict as FactCheckClaim['verdict'], reason: xss, evidence: [
        { id: 'e1', title: xss, url: 'https://example.org/page', quote: xss, publisher: '来源', stance: 'supports', published_at: null, retrieved_at: '2026-08-01' },
        { id: 'e2', title: '恶意来源', url: 'javascript:alert(1)', quote: '', publisher: '', stance: 'context', published_at: null, retrieved_at: '2026-08-01' },
      ] })),
      coverage: { status: 'partial', extracted: 4, checked: 3, unverified: 1, reason: '预算耗尽' },
      usage: { prompt_tokens: 1, completion_tokens: 2, total_tokens: 3, search_queries: 2, pages_fetched: 2 }, checked_at: '2026-08-01',
    } })]
    const { state, root } = mount(); await open(state)
    const button = descendants(root).find(item => item.props['data-testid'] === 'fact-check-claim')!
    ;(button.props.onClick as () => void)(); await flush()
    const nodes = descendants(root)
    expect(nodes.filter(item => item.tag === 'a')).toHaveLength(1)
    expect(nodes.find(item => item.tag === 'a')!.props).toMatchObject({ href: 'https://example.org/page', target: '_blank', rel: 'noopener noreferrer' })
    expect(nodes.find(item => item.tag === 'mark')!.text).toBe('事实甲')
    expect(nodes.some(item => 'innerHTML' in item.props || item.tag === 'img')).toBe(false)
    for (const label of ['证据支持', '证据反驳', '证据不足', '证据冲突', '识别 4', '已检查 3', '未检查 1', '不保证', '人工参考建议', '链接不可用', '搜索回答与搜索元数据本身不是证据', '请核对引用原文']) expect(text(root)).toContain(label)
    expect(request.post).not.toHaveBeenCalled()
    expect(source).not.toContain('v-html')
    expect(text(root)).toContain('此报告未记录反证轮状态')
    expect(text(root)).toContain('此报告未记录结构化口径检查，不能视为检查通过')
    expect(text(root)).toContain('此报告未记录正文指纹或引文上下文')
    for (const page of [textPage, documentPage]) {
      expect(page).toContain('<FactCheckPanel :record-id="recordId" :source-text="sourceText" @started="router.replace({ query: { ...route.query, review: String($event) } })" />')
    }
  })
})

interface WorkbenchState { run: FactCheckRun | null; history: FactCheckRun[]; verdictFilter: string }
function mountWorkbench(current?: FactCheckRun) {
  workbenchRun = current ?? null
  route.params = current ? { id: String(current.id) } : {}
  route.fullPath = current ? `/fact-check/${current.id}` : '/fact-check'
  let vnode!: VNode
  const app = renderer.createApp(defineComponent({ setup: () => () => (vnode = h(Workbench)) }))
  for (const [name, component] of Object.entries(elements)) app.component(name, component)
  app.component('RouterLink', defineComponent({ setup: (_props, { slots, attrs }) => () => h('router-link', attrs, slots.default?.()) }))
  const root = node()
  app.mount(root)
  const state = (vnode.component as unknown as { setupState: WorkbenchState }).setupState
  let mounted = true
  const unmount = () => { if (mounted) app.unmount(); mounted = false }
  cleanups.push(unmount)
  return { state, root, unmount }
}
async function mountReport(view: 'panel' | 'workbench', current: FactCheckRun) {
  runs = [current]
  if (view === 'workbench') {
    const mounted = mountWorkbench(current); await flush()
    return { ...mounted, current: () => mounted.state.run, showClaim: async () => {} }
  }
  const mounted = mount(); await open(mounted.state)
  return { ...mounted, current: () => mounted.state.active, showClaim: async () => { mounted.state.selectedClaim = 0; await flush() } }
}
function expectNoWrites() {
  expect(request.post).not.toHaveBeenCalled()
  expect(request.put).not.toHaveBeenCalled()
  expect(request.delete).not.toHaveBeenCalled()
  expect(uploadDocumentApi).not.toHaveBeenCalled()
}
function expectNoClaimInstructions(root: HostNode) {
  for (const instruction of ['请查看各条事实', '请逐条查看', '选择一个事实项', '请核对引用原文']) expect(text(root)).not.toContain(instruction)
  expect(descendants(root).some(item => item.props['data-testid'] === 'fact-check-claim' || String(item.props.class).includes('claim-item'))).toBe(false)
}
function reportLabel(root: HostNode) {
  return text(descendants(root).find(item => item.tag === 'ElTag')!)
}
function coverageHeading(root: HostNode) {
  const coverage = descendants(root).find(item => item.props['data-testid'] === 'fact-check-coverage')!
  return text(descendants(coverage).find(item => item.tag === 'strong')!)
}

describe.each(['panel', 'workbench'] as const)('%s 实际渲染：空报告与失败语义', view => {
  it.each(['historical', 'location', 'no-facts'] as const)('%s 区分提取失败与无事实，替换误导的完成消息且只读', async kind => {
    const current = run({ id: 14, status: kind === 'location' ? 'FAILURE' : 'SUCCESS',
      error_code: kind === 'location' ? 'EXTRACTION_LOCATION_FAILED' : null,
      message: '核查完成（旧消息）', result: report(kind === 'no-facts' ? 'complete' : 'partial'),
    })
    const saved = structuredClone(current)
    const mounted = await mountReport(view, current)
    const label = kind === 'no-facts' ? '未识别到可核查事实' : '事实提取失败'
    expect(reportLabel(mounted.root)).toBe(label)
    expect(text(mounted.root)).not.toContain('核查完成（旧消息）')
    expect(text(mounted.root)).not.toContain('预算范围内完成')
    expect(text(mounted.root)).not.toContain('部分完成')
    expectNoClaimInstructions(mounted.root)
    if (kind === 'no-facts') {
      expect(text(mounted.root)).toContain('本次未进行搜索或证据核查')
      expect(text(mounted.root)).toContain('不代表全文事实正确')
      expect(text(mounted.root)).not.toContain('事实提取失败')
    } else {
      expect(text(mounted.root)).toContain('未能获得可准确定位到原文的事实项')
      expect(text(mounted.root)).toContain('不代表全文没有事实或事实正确')
      expect(text(mounted.root)).not.toContain('未识别到可核查事实')
    }
    if (view === 'panel') {
      expect(coverageHeading(mounted.root)).toBe(label)
      expect(text(mounted.root)).toContain('识别 0 · 已检查 0 · 未检查 0')
      expect(text(mounted.root)).toContain('查询 0 次 · 抓取 0 页')
      const option = descendants(mounted.root).find(item => item.tag === 'ElOption' && item.props.value === current.id)!
      expect(option.props.label).toContain(label)
    } else {
      for (const pane of ['claims-pane', 'evidence-pane']) {
        expect(text(descendants(mounted.root).find(item => String(item.props.class).includes(pane))!)).toContain(label)
      }
      const metrics = descendants(mounted.root).find(item => item.props.class === 'metrics')!
      expect(descendants(metrics).filter(item => item.tag === 'strong').map(text)).toEqual(['0', '0', '0', '0', '0', '0'])
    }
    await vi.advanceTimersByTimeAsync(10000)
    expect(request.get).toHaveBeenCalledTimes(view === 'panel' ? 2 : 3)
    expectNoWrites()
    expect(mounted.current()).toEqual(saved)
    expect(current).toEqual(saved)
  })
  it.each(['MODEL_OUTPUT_INVALID', 'MODEL_UNAVAILABLE', 'SEARCH_UNAVAILABLE'])('%s 失败不推断无事实或定位失败，保留原错误', async error_code => {
    const current = run({ status: 'FAILURE', error_code, message: '模型或网络请求失败', result: report('complete') })
    const { root } = await mountReport(view, current)
    expect(reportLabel(root)).toBe(view === 'panel' ? '核查失败' : '执行失败')
    expect(text(root)).toContain('模型或网络请求失败')
    expect(text(root)).toContain(error_code)
    expect(text(root)).not.toContain('未识别到可核查事实')
    expect(text(root)).not.toContain('事实提取失败')
    expect(text(root)).not.toContain('预算范围内完成')
    expectNoClaimInstructions(root)
    await vi.advanceTimersByTimeAsync(10000)
    expect(request.get).toHaveBeenCalledTimes(view === 'panel' ? 2 : 3)
    expectNoWrites()
  })
  it.each(['PENDING', 'RUNNING', 'WAITING_CONFIRMATION', 'CANCELLED'] as const)('%s 空报告不会变成成功或提取失败', async status => {
    const labels = view === 'panel'
      ? { PENDING: '等待核查', RUNNING: '正在核查', WAITING_CONFIRMATION: '待确认事实，请进入工作台', CANCELLED: '已取消' }
      : { PENDING: '等待执行', RUNNING: '核查中', WAITING_CONFIRMATION: '待确认事实', CANCELLED: '已取消' }
    for (const coverage of ['partial', 'complete'] as const) {
      const current = run({ status, result: report(coverage), message: '当前任务状态' })
      const mounted = await mountReport(view, current)
      expect(reportLabel(mounted.root)).toBe(labels[status])
      expect(text(mounted.root)).toContain('当前任务状态')
      for (const unexpected of ['未识别到可核查事实', '事实提取失败', '预算范围内完成', '部分完成']) expect(text(mounted.root)).not.toContain(unexpected)
      if (view === 'panel') expect(coverageHeading(mounted.root)).toBe(labels[status])
      expectNoClaimInstructions(mounted.root)
      expect(mounted.current()?.status).toBe(status)
      mounted.unmount()
    }
    expectNoWrites()
  })
  it.each(['SUCCESS', 'FAILURE'] as const)('%s 缺少报告不能推断无事实或定位失败', async status => {
    const { root } = await mountReport(view, run({ status, result: null, message: '历史任务消息' }))
    expect(text(root)).toContain('历史任务消息')
    expect(text(root)).not.toContain('未识别到可核查事实')
    expect(text(root)).not.toContain('事实提取失败')
    expectNoClaimInstructions(root)
    expectNoWrites()
  })
  it('部分定位成功的报告保留事实项及未完成说明', async () => {
    const current = run({ status: 'SUCCESS', result: report('partial', [claim({ checked: false })]) })
    current.result!.coverage.reason = '部分事实无法准确定位，已保留有效事实项'
    const { root, current: displayed } = await mountReport(view, current)
    expect(reportLabel(root)).toBe(view === 'panel' ? '核查部分完成' : '核查不完整')
    expect(text(root)).toContain('事实陈述')
    expect(text(root)).toContain('未检查')
    expect(text(root)).toContain(current.result!.coverage.reason)
    expect(text(root)).not.toContain('事实提取失败')
    expect(text(root)).not.toContain('未识别到可核查事实')
    expect(displayed()?.result?.claims).toEqual(current.result!.claims)
    if (view === 'panel') expect(coverageHeading(root)).toBe('部分完成')
    expectNoWrites()
  })
  it('DNS 抓取失败且零正文仍显示核查受阻，保留检索资料而非无事实', async () => {
    const current = run({ status: 'SUCCESS', result: report('partial', [claim({ search_rounds: [{
      kind: 'initial', query: '官方成立日期', status: 'partial', pages_fetched: 0, error_codes: ['DNS_FAILED'],
      sources: [{ url: 'https://example.org/source', title: '候选官方公告', origin: 'search', status: 'failed', reason: 'DNS 解析失败', error_code: 'DNS_FAILED', evidence_id: null }],
    }] })]) })
    current.result!.usage.search_queries = 1
    const mounted = await mountReport(view, current)
    expect(reportLabel(mounted.root)).toBe('核查受阻')
    expect(text(mounted.root)).toContain('未取得可核对的正文')
    await mounted.showClaim()
    for (const expected of ['事实陈述', 'DNS_FAILED', '候选官方公告', '未读到正文，无法判断支持或反驳']) expect(text(mounted.root)).toContain(expected)
    expect(text(mounted.root)).not.toContain('事实提取失败')
    expect(text(mounted.root)).not.toContain('未识别到可核查事实')
    expect(descendants(mounted.root).some(item => item.tag === 'a')).toBe(false)
    await vi.advanceTimersByTimeAsync(10000)
    expect(request.get).toHaveBeenCalledTimes(view === 'panel' ? 2 : 3)
    expectNoWrites()
  })
  it('轻量历史摘要不推断核查结论，也不逐条加载完整报告', async () => {
    runs = [
      run({ id: 14, status: 'SUCCESS', result: null }),
      run({ id: 15, status: 'FAILURE', error_code: 'EXTRACTION_LOCATION_FAILED', result: null }),
    ]
    const saved = structuredClone(runs)
    const mounted = view === 'panel' ? mount() : mountWorkbench()
    if (view === 'panel') await open(mounted.state as State)
    else await flush()
    const expected = ['任务已结束，查看报告', '事实提取失败']
    for (const [index, item] of runs.entries()) {
      if (view === 'panel') {
        expect(descendants(mounted.root).find(node => node.tag === 'ElOption' && node.props.value === item.id)!.props.label).toContain(expected[index])
      } else {
        const row = descendants(mounted.root).filter(node => node.props.class === 'history-item')[index]
        expect(reportLabel(row)).toBe(expected[index])
      }
    }
    expect(runs).toEqual(saved)
    expect(request.get).toHaveBeenCalledTimes(2)
    expectNoWrites()
  })
  it('历史标签与详情语义一致，但不重写 SUCCESS/FAILURE 等保存状态', async () => {
    runs = [
      run({ id: 14, status: 'SUCCESS', result: report('partial') }),
      run({ id: 15, status: 'FAILURE', error_code: 'EXTRACTION_LOCATION_FAILED', result: report('partial') }),
      run({ id: 16, status: 'SUCCESS', result: report('complete') }),
      run({ id: 17, status: 'FAILURE', error_code: 'MODEL_UNAVAILABLE', result: report('complete') }),
      run({ id: 18, status: 'SUCCESS', result: report('partial', [claim()]) }),
      run({ id: 19, status: 'CANCELLED', result: report('complete') }),
    ]
    const saved = structuredClone(runs)
    const mounted = view === 'panel' ? mount() : mountWorkbench()
    if (view === 'panel') await open(mounted.state as State)
    else await flush()
    const expected = ['事实提取失败', '事实提取失败', '未识别到可核查事实', view === 'panel' ? '核查失败' : '执行失败', view === 'panel' ? '核查部分完成' : '核查不完整', '已取消']
    for (const [index, item] of runs.entries()) {
      if (view === 'panel') {
        expect(descendants(mounted.root).find(node => node.tag === 'ElOption' && node.props.value === item.id)!.props.label).toContain(expected[index])
      } else {
        const row = descendants(mounted.root).filter(node => node.props.class === 'history-item')[index]
        expect(reportLabel(row)).toBe(expected[index])
      }
    }
    expect(mounted.state.history).toEqual(view === 'panel' ? [...saved].reverse() : saved)
    expect(runs).toEqual(saved)
    await vi.advanceTimersByTimeAsync(10000)
    expect(request.get).toHaveBeenCalledTimes(2)
    expectNoWrites()
  })
})

const searchSource = (patch: Partial<FactSearchSource> = {}): FactSearchSource => ({
  url: 'https://example.org/source', title: '候选公告', origin: 'search', status: 'fetched', reason: '正文已读取', error_code: null, evidence_id: null, ...patch,
})
function mountTrace(current: FactCheckClaim) {
  const props = reactive({ claim: current })
  const showEvidence = vi.fn()
  const app = renderer.createApp(defineComponent({ setup: () => () => h(Trace, { ...props, onShowEvidence: showEvidence }) }))
  const root = node()
  app.mount(root)
  cleanups.push(() => app.unmount())
  return { root, props, showEvidence }
}

describe('FactCheckSearchTrace', () => {
  it('保留抓取受阻的资料名、链接与原因，不把搜索标题变成佐证', () => {
    const current = claim({ search_rounds: [{ kind: 'initial', query: '成立日期 官方来源', status: 'partial', pages_fetched: 0, error_codes: ['UNSAFE_ADDRESS'], sources: [searchSource({ status: 'failed', error_code: 'UNSAFE_ADDRESS', reason: '页面解析到非公网或保留地址。' })] }] })
    const { root } = mountTrace(current)
    for (const expected of ['成立日期 官方来源', '候选公告', 'https://example.org/source', '抓取受阻', 'UNSAFE_ADDRESS', '未读到正文，无法判断支持或反驳']) expect(text(root)).toContain(expected)
    expect(descendants(root).some(item => item.tag === 'a' || item.tag === 'blockquote')).toBe(false)
    expect(request.post).not.toHaveBeenCalled()
  })
  it.each(['supports', 'refutes', 'context'] as const)('%s 显示每份资料的逐字引文、对应关系，并跳到同一事实的证据', async stance => {
    const current = claim({ evidence: [{ id: 'c1-e1', title: '原始公告', url: 'https://example.org/source', quote: '固定测试引文', stance, publisher: '测试机构', published_at: null, retrieved_at: '2026-09-16', checks: {
      subject: { status: 'match', reason: '正文描述同一主体和事件' }, event_time: { status: 'match', reason: '引用对应同一事件时间范围' }, scope_unit: { status: 'not_applicable', reason: '没有统计口径' },
    } }], search_rounds: [
      { kind: 'initial', query: '查询一', status: 'complete', pages_fetched: 1, error_codes: [], sources: [searchSource({ evidence_id: 'c1-e1' })] },
      { kind: 'counter', query: '查询二 更正', status: 'complete', pages_fetched: 0, error_codes: [], sources: [searchSource({ status: 'duplicate', evidence_id: 'c1-e1', reason: '与上一轮同一材料，不是独立佐证' })] },
    ] })
    const { root, showEvidence } = mountTrace(current)
    const labels = { supports: '支持这条事实', refutes: '反驳这条事实', context: '仅作背景，不能单独支持或反驳' }
    expect(text(root)).toContain(labels[stance])
    expect(text(root)).toContain('固定测试引文')
    expect(text(root)).toContain('正文描述同一主体和事件')
    expect(text(root)).toContain('不是独立佐证')
    expect(text(descendants(root).find(item => item.props.class === 'trace-counts')!)).toContain('候选链接 1')
    const jump = descendants(root).find(item => item.tag === 'button')!
    ;(jump.props.onClick as () => void)(); await flush()
    expect(showEvidence).toHaveBeenCalledExactlyOnceWith('c1-e1')
  })
  it('已读取但未引用、超出预算、等待中的材料不会编造支持或反驳理由', () => {
    const current = claim({ checked: false, search_rounds: [{ kind: 'followup', query: '原始出处', status: 'fetching', pages_fetched: 1, error_codes: [], sources: [
      searchSource({ title: '读取但未引用', reason: '正文未形成可用引用' }),
      searchSource({ url: 'https://example.org/extra', title: '补充资料', status: 'skipped', origin: 'supplemental', reason: '超过本轮抓取预算' }),
      searchSource({ url: 'https://example.org/waiting', title: '等待资料', status: 'pending', reason: '' }),
    ] }] })
    const { root } = mountTrace(current)
    for (const expected of ['读取但未引用', '未形成可用引用', '超过本轮抓取预算', '用户补充', '尚未形成正文证据', '不等于该资料无关或陈述为假']) expect(text(root)).toContain(expected)
    expect(descendants(root).some(item => item.tag === 'blockquote')).toBe(false)
  })
  it.each([{ sources: undefined }, { sources: null }, { sources: [] }])('区分旧记录未保存与搜索无结果 $sources', ({ sources }) => {
    const { root } = mountTrace(claim({ search_rounds: [{ kind: 'initial', query: '查询', status: 'complete', pages_fetched: 0, error_codes: [], sources }] }))
    expect(text(root)).toContain(sources == null ? '历史记录未保存候选资料列表' : '本轮未返回候选资料')
    expect(text(root)).not.toContain(sources == null ? '本轮未返回候选资料' : '历史记录未保存候选资料列表')
  })
  it('恶意标题与错误原因仅作文本，危险地址及悬空引用不可点击或变成证据', () => {
    const xss = '<img src=x onerror=alert(1)>'
    const { root } = mountTrace(claim({ search_rounds: [{ kind: 'initial', query: xss, status: 'partial', pages_fetched: 0, error_codes: [], sources: [
      searchSource({ title: xss, url: 'javascript:alert(1)', reason: xss, evidence_id: 'another-claim-evidence' }),
      searchSource({ title: '带凭据地址', url: 'https://user:password@example.org/path' }),
      searchSource({ title: '保留地址', url: 'http://127.0.0.1/private', status: 'failed', reason: 'UNSAFE_ADDRESS' }),
    ] }] }))
    expect(text(root)).toContain(xss)
    expect(descendants(root).some(item => item.tag === 'a' || item.tag === 'img' || item.tag === 'blockquote' || 'innerHTML' in item.props)).toBe(false)
  })
})
