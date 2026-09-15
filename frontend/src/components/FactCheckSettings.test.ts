/* eslint-disable vue/one-component-per-file -- 组件桩用于现有无浏览器 renderer。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as Vue from 'vue'
import { createRenderer, defineComponent, h, nextTick, type Component, type VNode } from 'vue'
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'
import { ModuleKind, ScriptTarget, transpileModule } from 'typescript'
import type { FactCheckSettings, SaveFactCheckSettingsPayload } from '@/api/factCheck'

vi.mock('@/utils/request', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }))
import request from '@/utils/request'
import * as FactCheckApi from '@/api/factCheck'
import * as ReviewApi from '@/api/review'
import source from './FactCheckSettings.vue?raw'
import settingsPage from '@/views/admin/settings/index.vue?raw'

const elements = Object.fromEntries(['ElButton', 'ElCard', 'ElForm', 'ElFormItem', 'ElInput', 'ElInputNumber', 'ElOption', 'ElSelect', 'ElSwitch'].map(name => [name,
  defineComponent({ inheritAttrs: false, setup: (_props, { slots, attrs }) => () => h(name, attrs, [slots.header?.(), slots.default?.()]) }),
]))
const { descriptor } = parse(source)
const script = compileScript(descriptor, { id: 'fact-check-settings-test' })
const template = compileTemplate({ source: descriptor.template!.content, filename: 'FactCheckSettings.vue', id: 'fact-check-settings-test', compilerOptions: { bindingMetadata: script.bindings } })
if (template.errors.length) throw new Error(String(template.errors[0]))
const modules: Record<string, unknown> = { vue: Vue, 'element-plus': elements, '@/api/factCheck': FactCheckApi, '@/api/review': ReviewApi }
const compiled = { exports: {} as { default: Component; render: () => VNode } }
const code = transpileModule(`${script.content}\n${template.code}`, { compilerOptions: { module: ModuleKind.CommonJS, target: ScriptTarget.ES2020 } }).outputText
new Function('require', 'module', 'exports', code)((id: string) => {
  if (!(id in modules)) throw new Error(`Unexpected dependency: ${id}`)
  return modules[id]
}, compiled, compiled.exports)
const Settings = Object.assign(compiled.exports.default, { render: compiled.exports.render })
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
function descendants(root: HostNode): HostNode[] { return [root, ...root.children.flatMap(descendants)] }
function text(root: HostNode): string { return root.text + root.children.map(text).join('') }
interface State {
  draft: SaveFactCheckSettingsPayload; apiKey: string; keyConfigured: boolean; savedIds: Set<string>;
  modelName: string; modelSearchSupported: boolean; modelSearchReason: string;
  loaded: boolean; loading: boolean; saving: boolean; error: string; notice: string; validationError: string;
  load(): Promise<void>; save(): Promise<void>; addSource(): void; removeSource(id: string): void;
}
const cleanups: (() => void)[] = []
function mount() {
  let vnode!: VNode
  const app = renderer.createApp(defineComponent({ setup: () => () => (vnode = h(Settings)) }))
  const root = node()
  app.mount(root)
  const state = (vnode.component as unknown as { setupState: State }).setupState
  let mounted = true
  const unmount = () => { if (mounted) app.unmount(); mounted = false }
  cleanups.push(unmount)
  return { state, root, unmount }
}
async function flush() { for (let index = 0; index < 8; index++) await nextTick() }
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}
const configuration = (): FactCheckSettings => ({
  enabled: false, provider: 'model', api_key_configured: false, max_claims: 10, sources: [],
  model_name: '当前配置 / active-model', model_search_supported: true, model_search_reason: '适配器端点支持，模型及账号仍须确认',
})
let settings: FactCheckSettings
beforeEach(() => {
  vi.resetAllMocks()
  settings = configuration()
  vi.mocked(request.get).mockImplementation(async () => settings)
  vi.mocked(request.put).mockImplementation(async (_url, data) => {
    const payload = data as SaveFactCheckSettingsPayload
    settings = { ...settings, enabled: payload.enabled, provider: payload.provider, max_claims: payload.max_claims, sources: payload.sources, api_key_configured: settings.api_key_configured || !!payload.api_key }
    return settings
  })
})
afterEach(() => cleanups.splice(0).forEach(cleanup => cleanup()))

describe('FactCheckSettings', () => {
  it('默认原生搜索、关闭、10条、空信源；只读取已有配置，不探测或启动', async () => {
    const { state, root } = mount()
    expect(state.draft).toEqual({ enabled: false, provider: 'model', max_claims: 10, sources: [] })
    expect(state.loaded).toBe(false)
    expect(state.apiKey).toBe('')
    await flush()
    expect(state.loaded).toBe(true)
    expect(request.get).toHaveBeenCalledExactlyOnceWith('/admin/fact-check/settings', { signal: expect.any(AbortSignal), headers: { 'X-Silent-Error': 'true' } })
    expect(request.put).not.toHaveBeenCalled()
    expect(request.post).not.toHaveBeenCalled()
    const selector = descendants(root).find(item => item.props['aria-label'] === '事实核查搜索服务')!
    expect(selector.props.modelValue).toBe('model')
    expect(descendants(selector).filter(item => item.props.value).map(item => item.props.value)).toEqual(['model', 'tavily'])
    expect(descendants(root).some(item => item.props['aria-label'] === 'Tavily API Key')).toBe(false)
    for (const copy of [settings.model_name, '复用当前启用的模型配置', '模型版本及账号需支持', '搜索工具及 Token 费用', '运行时仍可能拒绝', '不会自动切换到 Tavily']) expect(text(root)).toContain(copy)
    expect(settingsPage).toContain('<FactCheckSettings v-if="userStore.hasPermission(\'admin:settings:edit\')" />')
  })
  it.each(['model', 'tavily'] as const)('%s 加载及保存不回显响应中的任何密钥', async provider => {
    const response = { ...configuration(), provider, api_key_configured: true, api_key: 'must-not-echo', model_api_key: 'model-secret-must-not-echo' }
    vi.mocked(request.get).mockResolvedValueOnce(response)
    vi.mocked(request.put).mockResolvedValueOnce(response)
    const { state, root } = mount(); await flush()
    expect(state.draft.provider).toBe(provider)
    expect(state.apiKey).toBe('')
    expect(state.keyConfigured).toBe(true)
    expect(state.draft).not.toHaveProperty('api_key')
    expect(state.draft).not.toHaveProperty('model_api_key')
    await state.save(); await flush()
    expect(state.apiKey).toBe('')
    expect(JSON.stringify(vi.mocked(request.put).mock.calls)).not.toContain('must-not-echo')
    expect(text(root)).not.toContain('must-not-echo')
    expect(descendants(root).some(item => String(item.props.modelValue).includes('must-not-echo'))).toBe(false)
    const input = descendants(root).find(item => item.props['aria-label'] === 'Tavily API Key')
    if (provider === 'tavily') expect(input!.props).toMatchObject({ type: 'password', autocomplete: 'new-password', modelValue: '' })
    else expect(input).toBeUndefined()
    expect(request.post).not.toHaveBeenCalled()
  })
  it('原生模式无需 Tavily 密钥即可启用，保存不发送任何密钥', async () => {
    const { state } = mount(); await flush()
    state.draft.enabled = true
    expect(state.keyConfigured).toBe(false)
    expect(state.validationError).toBe('')
    state.apiKey = 'stale-tavily-key'
    await state.save()
    expect(request.put).toHaveBeenCalledExactlyOnceWith('/admin/fact-check/settings', {
      enabled: true, provider: 'model', max_claims: 10, sources: [],
    }, { signal: expect.any(AbortSignal), headers: { 'X-Silent-Error': 'true' } })
    expect(state.apiKey).toBe('')
    expect(state.keyConfigured).toBe(false)
    expect(state.draft.provider).toBe('model')
    expect(request.get).toHaveBeenCalledTimes(1)
    expect(request.post).not.toHaveBeenCalled()
  })
  it.each([false, true])('不支持原生时不能启用但允许关闭保存，Tavily 已配置=%s 不能绕过', async keyConfigured => {
    settings.model_search_supported = false
    settings.model_search_reason = '当前端点尚无原生搜索适配器'
    settings.api_key_configured = keyConfigured
    const { state, root } = mount(); await flush()
    expect(text(root)).toContain(settings.model_search_reason)
    state.draft.enabled = true
    expect(state.validationError).toContain('不支持原生联网')
    await flush()
    expect(descendants(root).find(item => item.props['data-testid'] === 'fact-check-save-settings')!.props.disabled).toBe(true)
    await state.save()
    expect(request.put).not.toHaveBeenCalled()
    expect(state.draft.provider).toBe('model')
    state.draft.enabled = false
    expect(state.validationError).toBe('')
    await state.save()
    expect(vi.mocked(request.put).mock.calls[0][1]).toEqual({ enabled: false, provider: 'model', max_claims: 10, sources: [] })
    expect(request.get).toHaveBeenCalledTimes(1)
    expect(request.post).not.toHaveBeenCalled()
  })
  it('切换服务立即清空未发送密钥，保留已存 Tavily 密钥且不发探测请求', async () => {
    settings.provider = 'tavily'; settings.api_key_configured = true; settings.enabled = true
    const { state, root } = mount(); await flush()
    const selector = descendants(root).find(item => item.props['aria-label'] === '事实核查搜索服务')!
    const select = selector.props['onUpdate:modelValue'] as (value: string) => void
    state.apiKey = 'unsent-secret'
    select('model')
    expect(state.apiKey).toBe('')
    select('tavily')
    expect(state.apiKey).toBe('')
    expect(state.keyConfigured).toBe(true)
    expect(request.put).not.toHaveBeenCalled()
    expect(request.get).toHaveBeenCalledTimes(1)
    select('model'); await state.save()
    expect(state.keyConfigured).toBe(true)
    select('tavily'); await flush()
    expect(state.validationError).toBe('')
    expect(text(root)).toContain('已配置 Tavily 密钥')
    await state.save()
    expect(vi.mocked(request.put).mock.calls.map(call => call[1])).toEqual([
      { enabled: true, provider: 'model', max_claims: 10, sources: [] },
      { enabled: true, provider: 'tavily', max_claims: 10, sources: [] },
    ])
    expect(request.get).toHaveBeenCalledTimes(1)
    expect(request.post).not.toHaveBeenCalled()
  })
  it('新信源 UUID、默认根路径与启用，可移除；已保存项不可删除但可停用', async () => {
    const existing = { id: 'saved', name: '既有来源', domain: 'example.org', path_prefix: '/', is_enabled: true }
    vi.mocked(request.get).mockResolvedValueOnce({ ...configuration(), sources: [existing] })
    const { state } = mount(); await flush()
    state.addSource()
    expect(state.draft.sources[1]).toMatchObject({ id: expect.stringMatching(/^[a-f0-9-]{36}$/), name: '', domain: '', path_prefix: '/', is_enabled: true })
    expect(state.validationError).toContain('名称必填')
    state.removeSource('saved')
    expect(state.draft.sources).toHaveLength(2)
    state.removeSource(state.draft.sources[1].id)
    state.draft.sources[0].is_enabled = false
    await state.save()
    expect(vi.mocked(request.put).mock.calls[0][1]).toMatchObject({ sources: [{ ...existing, is_enabled: false }] })
  })
  it.each(['https://example.org', 'example.org/path', 'example.org:443', 'user@example.org', 'example.org?x=1', 'example.org#fragment', '//example.org', 'bad domain.org'])('纯域名校验拒绝 %s', async domain => {
    const { state } = mount(); await flush(); state.addSource()
    state.draft.sources[0].name = '来源'
    state.draft.sources[0].domain = domain
    expect(state.validationError).toContain('纯域名')
    await state.save()
    expect(request.put).not.toHaveBeenCalled()
  })
  it('新增来源必须填写名称；路径可留空默认/，拒绝相对路径', async () => {
    const { state } = mount(); await flush(); state.addSource()
    const entry = state.draft.sources[0]
    entry.domain = 'news.example.org'
    await state.save()
    expect(request.put).not.toHaveBeenCalled()
    entry.name = ' 新闻资料 '
    entry.path_prefix = 'relative'
    expect(state.validationError).toContain('路径前缀')
    entry.path_prefix = '/../private'
    expect(state.validationError).toContain('路径前缀')
    entry.path_prefix = ''
    await state.save()
    expect(vi.mocked(request.put).mock.calls[0][1]).toMatchObject({ sources: [{ name: '新闻资料', domain: 'news.example.org', path_prefix: '/', is_enabled: true }] })
    expect(state.savedIds.has(entry.id)).toBe(true)
    state.removeSource(entry.id)
    expect(state.draft.sources).toHaveLength(1)
  })
  it('手动 Tavily 启用前要求密钥，不受原生能力限制；保存清空新密钥，留空保持', async () => {
    settings.model_search_supported = false
    const { state } = mount(); await flush()
    state.draft.provider = 'tavily'
    state.draft.enabled = true
    expect(state.validationError).toContain('Tavily API Key')
    await state.save()
    expect(request.put).not.toHaveBeenCalled()
    state.apiKey = ' new-key '
    await state.save()
    expect(vi.mocked(request.put).mock.calls[0][1]).toEqual({ enabled: true, provider: 'tavily', max_claims: 10, sources: [], api_key: 'new-key' })
    expect(state.apiKey).toBe('')
    expect(state.keyConfigured).toBe(true)
    expect(state.notice).toContain('已保存')
    await state.save()
    expect(vi.mocked(request.put).mock.calls[1][1]).not.toHaveProperty('api_key')
  })
  it('核查条数必须正整数；读取失败不可把默认值写回', async () => {
    vi.mocked(request.get).mockRejectedValueOnce(new Error('配置读取失败'))
    const { state } = mount(); await flush()
    expect(state.loaded).toBe(false)
    expect(state.error).toContain('配置读取失败')
    await state.save()
    expect(request.put).not.toHaveBeenCalled()
    await state.load()
    for (const limit of [0, -1, 1.5, 11, NaN]) {
      state.draft.max_claims = limit
      expect(state.validationError).toContain('整数')
      await state.save()
    }
    expect(request.put).not.toHaveBeenCalled()
  })
  it('保存失败保留表单，新密钥只在主动重试时再发送，防止双击', async () => {
    const pending = deferred<FactCheckSettings>()
    settings.provider = 'tavily'
    const { state } = mount(); await flush()
    state.apiKey = 'unsaved-key'
    vi.mocked(request.put).mockRejectedValueOnce({ response: { data: { detail: '配置暂不可写' } } })
    await state.save()
    expect(state.error).toContain('配置暂不可写')
    expect(state.apiKey).toBe('unsaved-key')
    vi.mocked(request.put).mockReturnValueOnce(pending.promise)
    const saving = state.save(); await state.save()
    expect(request.put).toHaveBeenCalledTimes(2)
    pending.resolve({ ...configuration(), api_key_configured: true }); await saving
    expect(state.apiKey).toBe('')
  })
  it.each(['load', 'save'] as const)('卸载中止 %s，迟到响应不回填且清除密钥', async action => {
    const pending = deferred<FactCheckSettings>()
    if (action === 'load') vi.mocked(request.get).mockReturnValueOnce(pending.promise)
    const { state, unmount } = mount()
    if (action === 'save') {
      await flush()
      vi.mocked(request.put).mockReturnValueOnce(pending.promise)
      state.apiKey = 'sensitive'
      void state.save()
    }
    const signal = action === 'load' ? vi.mocked(request.get).mock.calls[0][1]!.signal! : vi.mocked(request.put).mock.calls[0][2]!.signal!
    unmount()
    expect(signal.aborted).toBe(true)
    expect(state.apiKey).toBe('')
    pending.resolve({ ...configuration(), max_claims: 99 }); await flush()
    expect(state.draft.max_claims).toBe(10)
    expect(state.notice).toBe('')
  })
})
