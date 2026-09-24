/** 无浏览器的真实 SFC 生命周期/模板回归与 UI 测试桩。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as Vue from 'vue'
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'
import { ModuleKind, ScriptTarget, transpileModule } from 'typescript'
import type { Component, ComponentPublicInstance, VNode } from 'vue'
import type { ReviewResponse } from '@/api/review'
import type { HistoryDetail } from '@/api/history'
import type { ReviewIssue } from '@/utils/review'
import textSource from './TextProofread.vue?raw'
import documentSource from './DocumentProofread.vue?raw'
import historySource from '../history/index.vue?raw'
import workspaceSource from '@/components/ReviewWorkspace.vue?raw'
import issueCardSource from '@/components/IssueCard.vue?raw'

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
  ElMessageBox: { confirm: vi.fn().mockResolvedValue(true) },
}))
vi.mock('@/api/proofread', () => ({ textProofreadApi: vi.fn(), proofreadCompareApi: vi.fn(), submitIssueFeedbackApi: vi.fn().mockResolvedValue({}) }))
vi.mock('@/api/review', () => ({
  getReviewApi: vi.fn(), saveReviewApi: vi.fn(), createReviewVersionApi: vi.fn(),
  exportReviewApi: vi.fn(), exportDocumentReviewApi: vi.fn(), getReviewErrorDetail: () => '请求失败',
}))
vi.mock('@/api/document', () => ({ uploadDocumentApi: vi.fn(), fetchExtractedTextApi: vi.fn(), exportRevisedTextApi: vi.fn(), exportReportApi: vi.fn() }))
vi.mock('@/api/tasks', () => ({ asyncDocumentProofreadApi: vi.fn(), streamTaskStatus: vi.fn(), cancelTaskApi: vi.fn() }))
vi.mock('@/api/polish', () => ({ getAvailableModelsCached: vi.fn().mockResolvedValue({ models: [] }) }))
vi.mock('@/api/history', () => ({ listHistoryApi: vi.fn(), getHistoryDetailApi: vi.fn(), deleteHistoryApi: vi.fn() }))
import * as ElementPlus from 'element-plus'
import * as ProofreadApi from '@/api/proofread'
import * as ReviewApi from '@/api/review'
import * as DocumentApi from '@/api/document'
import * as TasksApi from '@/api/tasks'
import * as PolishApi from '@/api/polish'
import * as HistoryApi from '@/api/history'
import * as ReviewComposable from '@/composables/useProofreadReview'
import * as ReviewUtils from '@/utils/review'
import * as ReviewVersions from '@/utils/reviewVersions'
import * as CompareReview from '@/utils/compareReview'
import * as ProofreadUtils from '@/utils/proofread'
import * as CollaborationUtils from '@/utils/collaboration'
import * as Format from '@/utils/format'

const route = Vue.reactive({ query: {} as Record<string, string> })
const replace = vi.fn(async ({ query }: { query: Record<string, string> }) => { route.query = query; await Vue.nextTick() })
const push = vi.fn()
const router = { useRoute: () => route, useRouter: () => ({ replace, push }), onBeforeRouteLeave: vi.fn() }
const stub = Vue.defineComponent({ setup: (_, { slots }) => () => Vue.h('stub', slots.default?.()) })
const modules: Record<string, unknown> = {
  vue: Vue, 'vue-router': router, 'element-plus': { ...ElementPlus,
    ElTag: stub, ElAlert: stub, ElButton: stub, ElDrawer: stub, ElInput: stub, ElOption: stub, ElSelect: stub,
  },
  '@/api/proofread': ProofreadApi, '@/api/review': ReviewApi, '@/api/document': DocumentApi,
  '@/api/tasks': TasksApi, '@/api/polish': PolishApi, '@/api/history': HistoryApi,
  '@/api/collaboration': { MAX_COLLABORATION_CHARS: 8000 },
  '@/composables/useProofreadReview': ReviewComposable,
  '@/utils/review': ReviewUtils, '@/utils/reviewVersions': ReviewVersions,
  '@/utils/compareReview': CompareReview, '@/utils/proofread': ProofreadUtils,
  '@/utils/collaboration': CollaborationUtils, '@/utils/format': Format,
  '@/stores/user': { useUserStore: () => ({ isLoggedIn: false, hasPermission: () => false }) },
  '@/composables/useCollaboration': { useCollaboration: () => ({
    taskId: Vue.ref(''), report: Vue.ref(null), status: Vue.ref(''), message: Vue.ref(''),
    error: Vue.ref(''), busy: Vue.ref(false), pending: Vue.ref(false), monitoring: Vue.ref(false),
    cancelling: Vue.ref(false), cancelRequested: Vue.ref(false), terminal: Vue.ref(false), locked: Vue.ref(false),
    snapshot: Vue.ref(null), reset: vi.fn(), resume: vi.fn(), submit: vi.fn(), reconnect: vi.fn(), cancel: vi.fn(),
  }) },
}
function compile(source: string, id: string): Component {
  const { descriptor } = parse(source)
  const script = compileScript(descriptor, { id })
  const template = compileTemplate({ source: descriptor.template!.content, filename: `${id}.vue`, id,
    compilerOptions: { bindingMetadata: script.bindings, expressionPlugins: ['typescript'] } })
  if (template.errors.length) throw new Error(String(template.errors[0]))
  const compiled = transpileModule(`${script.content}\n${template.code}`, {
    compilerOptions: { module: ModuleKind.CommonJS, target: ScriptTarget.ES2020 },
  }).outputText
  const mod = { exports: {} as { default: Component; render: () => VNode } }
  new Function('require', 'module', 'exports', compiled)((key: string) => {
    if (key in modules) return modules[key]
    if (key.startsWith('@/components/')) return { default: stub }
    throw new Error(`Unexpected dependency: ${key}`)
  }, mod, mod.exports)
  return Object.assign(mod.exports.default, { render: mod.exports.render })
}
modules['@/components/ReviewWorkspace.vue'] = { default: compile(workspaceSource, 'ReviewWorkspace') }
modules['@/components/IssueCard.vue'] = { default: compile(issueCardSource, 'IssueCard') }
const TextPage = compile(textSource, 'TextProofread')
const DocumentPage = compile(documentSource, 'DocumentProofread')
const HistoryPage = compile(historySource, 'HistoryPage')

interface Node { text: string; props: Record<string, unknown>; children: Node[]; parent: Node | null }
const node = (text = ''): Node => ({ text, props: {}, children: [], parent: null })
function remove(child: Node) {
  if (child.parent) child.parent.children.splice(child.parent.children.indexOf(child), 1)
  child.parent = null
}
function insert(child: Node, parent: Node, anchor: Node | null = null) {
  remove(child)
  const index = anchor ? parent.children.indexOf(anchor) : -1
  parent.children.splice(index < 0 ? parent.children.length : index, 0, child)
  child.parent = parent
}
const renderer = Vue.createRenderer<Node, Node>({
  createElement: () => node(), createText: text => node(text), createComment: () => node(),
  setText: (target, text) => { target.text = text }, setElementText: (target, text) => { target.text = text; target.children = [] },
  patchProp: (target, key, _old, value) => { target.props[key] = value },
  parentNode: target => target.parent, nextSibling: target => target.parent?.children[target.parent.children.indexOf(target) + 1] ?? null,
  insert, remove, insertStaticContent: (text, parent, anchor) => { const target = node(text); insert(target, parent, anchor); return [target, target] },
})
function rendered(target: Node): string {
  return `${target.text}${target.props.description ?? ''}${target.children.map(rendered).join('')}`
}
interface PageState {
  inputText: string; originalText: string; sourceText: string; currentText: string
  recordId: number | null; showResult: boolean; step: string; hasUnsavedChanges: boolean
  selectedFile: File | null; compareResult: unknown; savedReview: ReviewResponse | null
  issues: ReviewIssue[]; detail: HistoryDetail | null; showDetail: boolean
  handleProofread(): Promise<void>; handleStartProofread(): Promise<void>; handleReupload(): Promise<void>
  goBack(): Promise<void>; acceptIssue(issue: ReviewIssue): void; markSaved(response: ReviewResponse): void
  handleReviewSaved(response: ReviewResponse): Promise<void>; openDetail(row: { id: number }): Promise<void>
  continueReview(row: { id: number; type: string }): void
}
const cleanup: Array<() => void> = []
function mount(component: Component) {
  const root = node()
  const app = renderer.createApp(component)
  // 无需加载 Element Plus DOM 实现；仍运行真实父页面与 ReviewWorkspace 模板和监听。
  for (const name of ['ElCard', 'ElTag', 'ElButton', 'ElInput', 'ElRadioGroup', 'ElRadioButton', 'ElSelect', 'ElOption',
    'ElIcon', 'ElAlert', 'ElDivider', 'ElDrawer', 'ElEmpty', 'ElPagination', 'ElPopconfirm',
    'ElUpload', 'ElSteps', 'ElStep', 'ElProgress', 'ElTooltip', 'ElDropdown', 'ElDropdownMenu', 'ElDropdownItem']) app.component(name, stub)
  app.component('ElTable', Vue.defineComponent({ setup: (_, { attrs, slots }) => {
    Vue.provide('rows', Vue.computed(() => attrs.data as unknown[]))
    return () => Vue.h('table', slots.default?.())
  } }))
  app.component('ElTableColumn', Vue.defineComponent({ setup: (_, { slots }) => {
    const rows = Vue.inject<Vue.ComputedRef<unknown[]>>('rows')!
    return () => Vue.h('column', rows.value.flatMap(row => slots.default?.({ row }) || []))
  } }))
  app.directive('loading', {})
  app.config.warnHandler = () => {} // 与测试无关的图标/DOM 组件已桩化。
  const vm = app.mount(root) as ComponentPublicInstance
  const state = (vm.$ as unknown as { setupState: PageState }).setupState
  const unmount = () => app.unmount()
  cleanup.push(unmount)
  return { state, root, unmount }
}
async function flush() { for (let i = 0; i < 10; i++) { await Promise.resolve(); await Vue.nextTick() } }
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}
const source = '甲错词乙'
const issue = (): ReviewIssue => ({ original: '错词', suggestion: '正词', type: 'typo', severity: 'warning', start: 1, end: 3 })
const coverage = { status: 'complete' as const, total_chunks: 1, completed_chunks: 1, failed_chunks: [] }
function response(id = 7): ReviewResponse {
  return { record_id: id, revision: 0, original_text: source, domain: 'general', depth: 'standard',
    config_id: null, issues: [issue()], coverage, modified_text: source, versions: [] }
}
function result(recordId: number | null = 7) {
  return { record_id: recordId, issues: [issue()], domain: 'general', depth: 'standard', config_id: null,
    coverage, filename: '稿件.docx', file_id: 'file-new', total_issues: 1 }
}
beforeEach(() => {
  vi.clearAllMocks()
  route.query = {}
  replace.mockImplementation(async ({ query }) => { route.query = query; await Vue.nextTick() })
  const storage = new Map<string, string>()
  vi.stubGlobal('sessionStorage', { getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value), removeItem: (key: string) => storage.delete(key) })
  vi.stubGlobal('window', { addEventListener: vi.fn(), removeEventListener: vi.fn() })
  vi.mocked(ReviewApi.getReviewApi).mockImplementation(async id => response(id))
  vi.mocked(ProofreadApi.textProofreadApi).mockResolvedValue(result() as never)
  vi.mocked(DocumentApi.uploadDocumentApi).mockResolvedValue({ file_id: 'file-new', filename: '稿件.docx', extracted_text: source, text_length: 4 } as never)
  vi.mocked(DocumentApi.fetchExtractedTextApi).mockResolvedValue({ file_id: 'file-new', extracted_text: source, extracted_html: '' } as never)
  vi.mocked(TasksApi.asyncDocumentProofreadApi).mockResolvedValue({ task_id: 'task-new' } as never)
  vi.mocked(TasksApi.streamTaskStatus).mockResolvedValue({ status: 'SUCCESS', result: result() } as never)
  vi.mocked(HistoryApi.listHistoryApi).mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 })
})
afterEach(() => { cleanup.splice(0).forEach(fn => fn()); vi.unstubAllGlobals() })

async function start(component: Component) {
  const page = mount(component)
  await flush()
  if (component === TextPage) { page.state.inputText = source; await page.state.handleProofread() }
  else { page.state.selectedFile = { name: '稿件.docx' } as File; await page.state.handleStartProofread() }
  await flush()
  return page
}
describe('首次普通校对的稳定审阅地址', () => {
  for (const [name, component] of [['文本', TextPage], ['文档', DocumentPage]] as const) {
    it(`${name}首次成功只读一次元数据，刷新只恢复报告而不重新校对`, async () => {
      route.query = { keep: 'filter' }
      const page = await start(component)
      expect(route.query).toEqual({ review: '7', keep: 'filter' })
      expect(page.state.recordId).toBe(7)
      expect(ReviewApi.getReviewApi).toHaveBeenCalledTimes(1) // Workspace 元数据，不是路由重新恢复。
      expect(sessionStorage.getItem('textmirror_task_snapshot')).toBeNull()
      page.state.acceptIssue(page.state.issues[0])
      await flush()
      expect(page.state.currentText).toBe('甲正词乙')
      expect(page.state.hasUnsavedChanges).toBe(true)
      expect(ReviewApi.getReviewApi).toHaveBeenCalledTimes(1)
      page.unmount()
      vi.clearAllMocks()
      const refreshed = mount(component)
      await flush()
      expect(refreshed.state.recordId).toBe(7)
      expect(refreshed.state.currentText).toBe(source) // 不承诺未保存编辑刷新保留。
      expect(ReviewApi.getReviewApi).toHaveBeenCalledTimes(1)
      expect(ProofreadApi.textProofreadApi).not.toHaveBeenCalled()
      expect(TasksApi.asyncDocumentProofreadApi).not.toHaveBeenCalled()
      expect(TasksApi.streamTaskStatus).not.toHaveBeenCalled()
    })
    it(`${name}刷新恢复已保存决策，不重复加载或提交`, async () => {
      route.query = { review: '7' }
      const saved = { ...response(), revision: 2, issues: [{ ...issue(), _accepted: true, _ignored: false }] }
      vi.mocked(ReviewApi.getReviewApi).mockResolvedValue(saved)
      const page = mount(component)
      await flush()
      expect(page.state.currentText).toBe('甲正词乙')
      expect(page.state.hasUnsavedChanges).toBe(false)
      expect(ReviewApi.getReviewApi).toHaveBeenCalledTimes(1)
      expect(ProofreadApi.textProofreadApi).not.toHaveBeenCalled()
      expect(TasksApi.asyncDocumentProofreadApi).not.toHaveBeenCalled()
    })
    it(`${name}旧 review 恢复失败后，新请求解除旧绑定并拒绝延迟旧保存`, async () => {
      route.query = { review: '3' }
      vi.mocked(ReviewApi.getReviewApi).mockRejectedValueOnce(new Error('record missing'))
      const page = mount(component)
      await flush()
      expect(route.query.review).toBe('3')
      page.state.inputText = source
      page.state.selectedFile = { name: '稿件.docx' } as File
      if (component === TextPage) {
        vi.mocked(ProofreadApi.textProofreadApi).mockImplementationOnce(async () => {
          expect(route.query.review).toBeUndefined()
          return result() as never
        })
        await page.state.handleProofread()
      } else {
        vi.mocked(DocumentApi.uploadDocumentApi).mockImplementationOnce(async () => {
          expect(route.query.review).toBeUndefined()
          return { file_id: 'file-new', filename: '稿件.docx', extracted_text: source, text_length: 4 } as never
        })
        await page.state.handleStartProofread()
      }
      await flush()
      page.state.acceptIssue(page.state.issues[0])
      if (component === TextPage) page.state.markSaved(response(3))
      else await page.state.handleReviewSaved(response(3))
      expect(route.query.review).toBe('7')
      expect(page.state.recordId).toBe(7)
      expect(page.state.currentText).toBe('甲正词乙')
      expect(page.state.hasUnsavedChanges).toBe(true)
      expect(page.state.savedReview).toBeNull()
    })
    it(`${name}解绑旧 review 的导航被取消时不提交新请求`, async () => {
      route.query = { review: '3' }
      vi.mocked(ReviewApi.getReviewApi).mockRejectedValueOnce(new Error('record missing'))
      const page = mount(component)
      await flush()
      replace.mockResolvedValue(new Error('navigation aborted') as never)
      page.state.inputText = source
      page.state.selectedFile = { name: '稿件.docx' } as File
      if (component === TextPage) await page.state.handleProofread()
      else await page.state.handleStartProofread()
      expect(route.query.review).toBe('3')
      expect(ProofreadApi.textProofreadApi).not.toHaveBeenCalled()
      expect(DocumentApi.uploadDocumentApi).not.toHaveBeenCalled()
      expect(TasksApi.asyncDocumentProofreadApi).not.toHaveBeenCalled()
    })
    it(`${name}更新地址期间采纳不会被恢复流程抹去`, async () => {
      const navigation = deferred<void>()
      replace.mockImplementation(async ({ query }) => { route.query = query; await navigation.promise })
      const page = mount(component)
      await flush()
      page.state.inputText = source
      page.state.selectedFile = { name: '稿件.docx' } as File
      const running = component === TextPage ? page.state.handleProofread() : page.state.handleStartProofread()
      await flush()
      page.state.acceptIssue(page.state.issues[0])
      if (component === DocumentPage) expect(sessionStorage.getItem('textmirror_task_snapshot')).not.toBeNull()
      navigation.resolve()
      await running
      await flush()
      expect(page.state.currentText).toBe('甲正词乙')
      expect(page.state.hasUnsavedChanges).toBe(true)
      expect(ReviewApi.getReviewApi).toHaveBeenCalledTimes(1)
    })
    it(`${name}匿名结果无 id 仍能审阅，旧 review 不串入新请求`, async () => {
      route.query = { review: '3' }
      const page = mount(component)
      await flush()
      if (component === TextPage) await page.state.goBack()
      else await page.state.handleReupload()
      expect(route.query.review).toBeUndefined()
      vi.mocked(ProofreadApi.textProofreadApi).mockResolvedValue(result(null) as never)
      vi.mocked(TasksApi.streamTaskStatus).mockResolvedValue({ status: 'SUCCESS', result: result(null) } as never)
      vi.mocked(ReviewApi.getReviewApi).mockClear()
      page.state.inputText = source
      page.state.selectedFile = { name: '稿件.docx' } as File
      if (component === TextPage) await page.state.handleProofread()
      else await page.state.handleStartProofread()
      await flush()
      expect(page.state.recordId).toBeNull()
      expect(route.query.review).toBeUndefined()
      page.state.acceptIssue(page.state.issues[0])
      expect(page.state.currentText).toBe('甲正词乙')
      expect(ReviewApi.getReviewApi).not.toHaveBeenCalled()
      if (component === TextPage) page.state.markSaved(response(3))
      else await page.state.handleReviewSaved(response(3))
      expect(route.query.review).toBeUndefined()
      expect(page.state.savedReview).toBeNull()
    })
  }
  it('文档地址更新失败时保留任务快照，正常结果仍可审阅', async () => {
    replace.mockRejectedValue(new Error('navigation failed'))
    const page = await start(DocumentPage)
    expect(page.state.step).toBe('result')
    expect(page.state.recordId).toBe(7)
    expect(sessionStorage.getItem('textmirror_task_snapshot')).not.toBeNull()
    expect(ElementPlus.ElMessage.warning).toHaveBeenCalled()
  })
  it('文本页卸载后的校对响应不再更新 query', async () => {
    const pending = deferred<ReturnType<typeof result>>()
    vi.mocked(ProofreadApi.textProofreadApi).mockReturnValue(pending.promise as never)
    const page = mount(TextPage)
    await flush()
    page.state.inputText = source
    const running = page.state.handleProofread()
    await flush()
    page.unmount()
    pending.resolve(result())
    await running
    expect(replace).not.toHaveBeenCalled()
  })
})

describe('历史列表与详情模板', () => {
  it.each([
    ['single', 'complete', '单模型', '覆盖完整'],
    ['compare', 'partial', '对比', '部分完成'],
    ['collaboration', 'unknown', '协作', '覆盖未知'],
  ] as const)('展示 %s 模式、覆盖及保存决策，详情读取合并 issues', async (mode, status, modeText, statusText) => {
    const detail: HistoryDetail = { id: 7, type: 'text', domain: 'general', original_text: source,
      mode, coverage_status: status, total_issues: 99, result: { compare: true },
      issues: [{ ...issue(), _accepted: true }],
      review_summary: { total: 1, accepted: 1, ignored: 0, pending: 0, failed_models: mode === 'compare' ? 1 : 0 } }
    vi.mocked(HistoryApi.listHistoryApi).mockResolvedValue({ items: [{ ...detail, text_preview: source }], total: 1, page: 1, page_size: 20 })
    vi.mocked(HistoryApi.getHistoryDetailApi).mockResolvedValue(detail)
    const page = mount(HistoryPage)
    await flush()
    expect(rendered(page.root)).toContain(modeText)
    expect(rendered(page.root)).toContain(statusText)
    expect(rendered(page.root)).toContain('已采纳 1 / 已忽略 0 / 待处理 0')
    await page.state.openDetail({ id: 7 })
    await flush()
    expect(rendered(page.root)).toContain('问题列表 (1)')
    expect(rendered(page.root)).toContain('正词')
    expect(rendered(page.root)).not.toContain('99 个问题')
    if (mode === 'compare') expect(rendered(page.root)).toContain('1 个模型失败')
    page.state.continueReview(detail)
    expect(push).toHaveBeenCalledWith({ path: '/proofread/text', query: { review: '7' } })
  })
  it.each(['partial', 'unknown'] as const)('零问题但覆盖 %s 不显示全文无问题', async status => {
    const page = mount(HistoryPage)
    await flush()
    page.state.detail = { id: 1, type: 'document', domain: 'general', original_text: source, issues: [],
      mode: 'single', coverage_status: status, total_issues: 0,
      review_summary: { total: 0, accepted: 0, ignored: 0, pending: 0, failed_models: 0 } }
    page.state.showDetail = true
    await flush()
    expect(rendered(page.root)).toContain(status === 'partial' ? '仍有未完成审校' : '无法确认全文完成')
    page.state.continueReview(page.state.detail)
    expect(push).toHaveBeenCalledWith({ path: '/proofread/document', query: { review: '1' } })
  })
})
