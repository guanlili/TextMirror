/* eslint-disable vue/one-component-per-file -- 组件桩用于无浏览器 renderer。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as Vue from 'vue'
import { createRenderer, defineComponent, h, nextTick, reactive, type Component, type VNode } from 'vue'
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'
import { ModuleKind, ScriptTarget, transpileModule } from 'typescript'
import type { ReviewIssue } from '@/composables/useProofreadReview'
import type { QualityFeedback, FeedbackKind } from '@/api/qualityFeedback'

vi.mock('@/utils/request', () => ({ default: { post: vi.fn(), get: vi.fn(), put: vi.fn() } }))
import request from '@/utils/request'
import * as FeedbackApi from '@/api/qualityFeedback'
import * as ReviewApi from '@/api/review'
import * as FeedbackUtils from '@/utils/qualityFeedback'
import * as ProofreadUtils from '@/utils/proofread'
import source from './QualityFeedbackDialog.vue?raw'

const { descriptor } = parse(source)
const script = compileScript(descriptor, { id: 'feedback-dialog-test' })
const template = compileTemplate({ source: descriptor.template!.content, filename: 'QualityFeedbackDialog.vue', id: 'feedback-dialog-test', compilerOptions: { bindingMetadata: script.bindings } })
if (template.errors.length) throw new Error(String(template.errors[0]))
const modules: Record<string, unknown> = {
  vue: Vue, '@/api/qualityFeedback': FeedbackApi, '@/api/review': ReviewApi,
  '@/utils/qualityFeedback': FeedbackUtils, '@/utils/proofread': ProofreadUtils,
}
const compiledModule = { exports: {} as { default: Component; render: () => VNode } }
const code = transpileModule(`${script.content}\n${template.code}`, { compilerOptions: { module: ModuleKind.CommonJS, target: ScriptTarget.ES2020 } }).outputText
new Function('require', 'module', 'exports', code)((id: string) => {
  if (!(id in modules)) throw new Error(`Unexpected dependency: ${id}`)
  return modules[id]
}, compiledModule, compiledModule.exports)
const Dialog = Object.assign(compiledModule.exports.default, { render: compiledModule.exports.render })
interface HostNode { text: string; props: Record<string, unknown>; parent: HostNode | null; children: HostNode[] }
const node = (text = ''): HostNode => ({ text, props: {}, parent: null, children: [] })
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
  createElement: () => node(), createText: text => node(text), createComment: () => node(),
  setText: (target, text) => { target.text = text },
  setElementText: (target, text) => { target.text = text; target.children = [] },
  patchProp: (target, key, _old, value) => { target.props[key] = value },
  parentNode: target => target.parent,
  nextSibling: target => target.parent?.children[target.parent.children.indexOf(target) + 1] ?? null,
  insert, remove,
  insertStaticContent(content, parent, anchor) { const target = node(content); insert(target, parent, anchor); return [target, target] },
})
interface State {
  visible: boolean; kind: FeedbackKind; original: string; suggestion: string; issueType: string; start: number | null;
  note: string; error: string; success: string; submitting: boolean; validationError: string;
  open: (issue?: ReviewIssue) => void; submit: () => Promise<void>; captureSelection: (event: Event) => void;
}
const cleanups: (() => void)[] = []
function mount(recordId: number | null = 7) {
  const props = reactive({ recordId, sourceText: '𠮷帐号与帐号' })
  let vnode!: VNode
  const app = renderer.createApp(defineComponent({ setup: () => () => (vnode = h(Dialog, props)) }))
  for (const name of ['ElDialog', 'ElForm', 'ElFormItem', 'ElInput', 'ElSelect', 'ElOption', 'ElRadioGroup', 'ElRadioButton', 'ElAlert', 'ElButton']) {
    app.component(name, defineComponent({ inheritAttrs: false, setup: (_props, { slots, attrs }) => () => name === 'ElDialog' && !attrs.modelValue ? null : h('div', attrs, [slots.default?.(), slots.footer?.()]) }))
  }
  const root = node()
  app.mount(root)
  const state = (vnode.component as unknown as { setupState: State }).setupState
  let mounted = true
  const unmount = () => { if (mounted) app.unmount(); mounted = false }
  cleanups.push(unmount)
  return { props, state, unmount }
}
const issue = (): ReviewIssue => ({ original: '帐号', suggestion: '账号', type: 'typo', severity: 'warning', start: 4, end: 6, _accepted: false, _ignored: true })
const result = { id: 1, status: 'pending' } as QualityFeedback
function deferred() {
  let resolve!: (value: QualityFeedback) => void
  const promise = new Promise<QualityFeedback>(done => { resolve = done })
  return { promise, resolve }
}
beforeEach(() => { vi.clearAllMocks(); vi.mocked(request.post).mockResolvedValue(result) })
afterEach(() => cleanups.splice(0).forEach(cleanup => cleanup()))

describe('QualityFeedbackDialog', () => {
  it('保留一键忽略，补原因默认保留表达且不修改原审阅状态', async () => {
    const { state } = mount()
    const input = issue()
    state.open(input)
    expect(request.post).not.toHaveBeenCalled()
    expect(state.kind).toBe('preference')
    await state.submit()
    expect(request.post).toHaveBeenCalledWith('/proofread/quality-feedback', { record_id: 7, kind: 'preference', original: '帐号', suggestion: '账号', issue_type: 'typo', start: 4, end: 6, note: '' })
    expect(input).toEqual(issue())
    expect(state.success).toContain('等待人工确认')
    await state.submit()
    expect(request.post).toHaveBeenCalledTimes(1)
  })
  it('漏检重复片段必须选位置，原生选区从 UTF-16 转码点', async () => {
    const { state } = mount()
    state.open()
    state.original = '帐号'
    expect(state.start).toBeNull()
    await state.submit()
    expect(request.post).not.toHaveBeenCalled()
    state.captureSelection({ target: { selectionStart: 5, selectionEnd: 7 } } as unknown as Event)
    expect(state.start).toBe(4)
    expect(state.validationError).toBe('')
    await state.submit()
    expect(request.post).toHaveBeenCalledWith('/proofread/quality-feedback', expect.objectContaining({ kind: 'missed', start: 4, end: 6 }))
  })
  it('单一片段自动定位，修改片段不会沿用旧位置', () => {
    const { state } = mount()
    state.open()
    state.original = '𠮷帐号'
    expect(state.start).toBe(0)
    state.original = '帐号'
    expect(state.start).toBeNull()
    state.original = '修改后的账号'
    expect(state.validationError).not.toBe('')
  })
  it('游客和未定位建议不能上报，不猜第一个重复词', async () => {
    const guest = mount(null).state
    guest.open(issue())
    await guest.submit()
    expect(guest.validationError).toContain('登录')
    const { state } = mount()
    state.open({ ...issue(), start: null, end: null })
    await state.submit()
    expect(state.validationError).toContain('尚未准确定位')
    expect(request.post).not.toHaveBeenCalled()
  })
  it('限制输入码点长度但允许可选字段留空', async () => {
    const { state } = mount()
    state.open(issue())
    state.note = '𠮷'.repeat(1000)
    expect(state.validationError).toBe('')
    state.note += '字'
    expect(state.validationError).toContain('1000')
    state.note = ''
    state.suggestion = '字'.repeat(501)
    await state.submit()
    expect(state.validationError).toContain('500')
    expect(request.post).not.toHaveBeenCalled()
  })
  it('失败保留填写内容，明确提示先保存草稿后可重试', async () => {
    const { state } = mount()
    state.open(issue())
    state.note = '此处属于专业术语'
    vi.mocked(request.post).mockRejectedValueOnce({ response: { data: { detail: '请先保存审阅草稿' } } })
    await state.submit()
    expect(state.note).toBe('此处属于专业术语')
    expect(state.error).toBe('请先保存审阅草稿')
    await state.submit()
    expect(state.success).toContain('等待人工确认')
  })
  it.each(['record', 'close', 'unmount'] as const)('切换 %s 后迟到响应不能污染新表单', async action => {
    const pending = deferred()
    vi.mocked(request.post).mockReturnValueOnce(pending.promise)
    const { props, state, unmount } = mount()
    state.open(issue())
    const saving = state.submit()
    await state.submit()
    expect(request.post).toHaveBeenCalledTimes(1)
    if (action === 'record') props.recordId = 8
    else if (action === 'close') state.visible = false
    else unmount()
    await nextTick()
    if (action !== 'unmount') state.open()
    pending.resolve(result)
    await saving
    expect(state.success).toBe('')
    expect(state.submitting).toBe(false)
  })
  it('已有审核结果重复提交时不声称仍待确认', async () => {
    const { state } = mount()
    state.open(issue())
    vi.mocked(request.post).mockResolvedValueOnce({ ...result, status: 'rejected' })
    await state.submit()
    expect(state.success).toContain('经过审核')
  })
})
