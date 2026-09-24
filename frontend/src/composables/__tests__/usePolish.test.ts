import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createRenderer, defineComponent, h } from 'vue'

vi.mock('element-plus', () => ({
  ElMessage: { error: vi.fn(), warning: vi.fn(), success: vi.fn() },
}))
vi.mock('@/api/polish', () => ({
  getPolishStylesApi: vi.fn(),
  getAvailableModelsCached: vi.fn(),
  textPolishApi: vi.fn(),
  textPolishStreamApi: vi.fn(),
  polishCompareApi: vi.fn(),
  polishCompareStreamApi: vi.fn(),
}))

import { ElMessage } from 'element-plus'
import {
  getPolishStylesApi, getAvailableModelsCached,
  textPolishApi, textPolishStreamApi, polishCompareApi, polishCompareStreamApi,
} from '@/api/polish'
import { usePolish } from '../usePolish'

// Exercise real lifecycle hooks without a browser/DOM dependency.
const renderer = createRenderer<object, object>({
  createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
  setText() {}, setElementText() {}, patchProp() {}, insert() {}, remove() {},
  parentNode: () => null, nextSibling: () => null,
})
const cleanups: (() => void)[] = []
const text = '这是一段用于验证流式润色取消行为的原文。'
const syncVersions = [{ level: 'light', label: '轻量润色', content: '同步结果' }]
const models = [
  { config_id: 1, config_name: '模型一', model: 'model-1' },
  { config_id: 2, config_name: '模型二', model: 'model-2' },
]
const syncResults = models.map(model => ({ ...model, content: '同步对比结果', success: true, elapsed_ms: 10 }))

function mount() {
  let flow!: ReturnType<typeof usePolish>
  const app = renderer.createApp(defineComponent({ setup() {
    flow = usePolish()
    return () => h('div')
  } }))
  app.mount({})
  flow.inputText.value = text
  flow.originalText.value = text
  flow.selectedModelIds.value = [1, 2]
  let mounted = true
  const unmount = () => { if (mounted) app.unmount(); mounted = false }
  cleanups.push(unmount)
  return { flow, unmount }
}

function controlledStream() {
  let reject!: (reason: unknown) => void
  const promise = new Promise<void>((_resolve, fail) => { reject = fail })
  const abort = vi.fn(() => reject(new DOMException('Cancelled', 'AbortError')))
  return { promise, abort, reject }
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.stubGlobal('sessionStorage', { getItem: () => null })
  vi.mocked(getPolishStylesApi).mockResolvedValue({ styles: [] })
  vi.mocked(getAvailableModelsCached).mockResolvedValue({ models: [] })
  vi.mocked(textPolishApi).mockResolvedValue({ versions: syncVersions, style: 'formal', style_name: '正式规范', usage: {} })
  vi.mocked(polishCompareApi).mockResolvedValue({ results: syncResults, style: 'formal', style_name: '正式规范' })
})
afterEach(() => {
  cleanups.splice(0).forEach(cleanup => cleanup())
  vi.unstubAllGlobals()
})

describe.each(['handlePolish', 'handleRegenerate'] as const)('%s streaming', (action) => {
  it.each(['stop', 'unmount'] as const)('%s before first delta never falls back and releases loading', async (cancel) => {
    const stream = controlledStream()
    vi.mocked(textPolishStreamApi).mockReturnValueOnce(stream)
    const { flow, unmount } = mount()
    const operation = flow[action]()
    expect(flow.loading.value).toBe(true)
    expect(flow.streaming.value).toBe(true)
    if (cancel === 'stop') flow.stopStreaming()
    else unmount()
    await operation
    expect(stream.abort).toHaveBeenCalledOnce()
    expect(textPolishApi).not.toHaveBeenCalled()
    expect(flow.versions.value).toEqual([])
    expect(flow.loading.value).toBe(false)
    expect(flow.streaming.value).toBe(false)
    expect(flow.regenerating.value).toBe(false)
    expect(ElMessage.success).not.toHaveBeenCalled()
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(ElMessage.warning).not.toHaveBeenCalled()
    // Cleanup must not retain an already-finished abort callback.
    flow.stopStreaming()
    expect(stream.abort).toHaveBeenCalledOnce()
  })

  it('ordinary failure before first delta still falls back', async () => {
    const stream = controlledStream()
    vi.mocked(textPolishStreamApi).mockReturnValueOnce(stream)
    const { flow } = mount()
    const operation = flow[action]()
    stream.reject(new Error('Connection failed'))
    await operation
    expect(textPolishApi).toHaveBeenCalledExactlyOnceWith({ text, style: 'formal' })
    expect(flow.versions.value).toEqual(syncVersions)
    expect(flow.loading.value).toBe(false)
    expect(flow.streaming.value).toBe(false)
    expect(flow.regenerating.value).toBe(false)
  })

  it.each(['cancel', 'error'] as const)('%s after delta preserves partial results without fallback', async (failure) => {
    const stream = controlledStream()
    vi.mocked(textPolishStreamApi).mockImplementationOnce((_data, onEvent) => {
      onEvent({ event: 'delta', level: 'light', content: '已有部分结果' })
      return stream
    })
    const { flow } = mount()
    // First polish has no previous result, unlike regeneration.
    if (action === 'handlePolish') flow.originalText.value = ''
    const operation = flow[action]()
    if (failure === 'cancel') flow.stopStreaming()
    else stream.reject(new Error('Connection failed'))
    await operation
    expect(textPolishApi).not.toHaveBeenCalled()
    expect(flow.versions.value[0].content).toBe('已有部分结果')
    expect(flow.originalText.value).toBe(text)
    expect(flow.loading.value).toBe(false)
    expect(flow.streaming.value).toBe(false)
    expect(flow.regenerating.value).toBe(false)
    if (failure === 'cancel') {
      expect(ElMessage.success).not.toHaveBeenCalled()
      expect(ElMessage.warning).not.toHaveBeenCalled()
    } else {
      expect(ElMessage.warning).toHaveBeenCalledOnce()
    }
  })
})

describe('handleCompare streaming', () => {
  it.each([false, true])('unmount abort skips fallback and preserves partial results (delta: %s)', async (withDelta) => {
    const stream = controlledStream()
    vi.mocked(polishCompareStreamApi).mockImplementationOnce((_data, onEvent) => {
      onEvent({ event: 'meta', models })
      if (withDelta) onEvent({ event: 'delta', config_id: 1, content: '部分对比结果' })
      return stream
    })
    const { flow, unmount } = mount()
    const operation = flow.handleCompare()
    expect(flow.comparing.value).toBe(true)
    unmount()
    await operation
    expect(stream.abort).toHaveBeenCalledOnce()
    expect(polishCompareApi).not.toHaveBeenCalled()
    expect(flow.compareResults.value[0].content).toBe(withDelta ? '部分对比结果' : '')
    expect(flow.comparing.value).toBe(false)
    expect(ElMessage.success).not.toHaveBeenCalled()
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(ElMessage.warning).not.toHaveBeenCalled()
  })

  it('ordinary failure before first delta still falls back', async () => {
    const stream = controlledStream()
    vi.mocked(polishCompareStreamApi).mockReturnValueOnce(stream)
    const { flow } = mount()
    const operation = flow.handleCompare()
    stream.reject(new Error('Connection failed'))
    await operation
    expect(polishCompareApi).toHaveBeenCalledExactlyOnceWith({ text, style: 'formal', config_ids: [1, 2] })
    expect(flow.compareResults.value).toEqual(syncResults)
    expect(flow.comparing.value).toBe(false)
  })
})
