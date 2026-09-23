import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/utils/request', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }))
import request from '@/utils/request'
import {
  cancelFactCheckRunApi, createFactCheckId, createFactCheckRunApi, getFactCheckOptionsApi, getFactCheckRunApi,
  getFactCheckSettingsApi, listFactCheckRunsApi, saveFactCheckSettingsApi,
  factCheckHistoryApi, factCheckSourceApi, executeFactCheckApi, deepenFactCheckApi, addFactCheckReviewApi, exportFactCheckApi,
  type CreateFactCheckPayload, type FactCheckOptions, type FactCheckRun, type FactCheckSettings, type SaveFactCheckSettingsPayload,
} from './factCheck'

beforeEach(() => vi.resetAllMocks())

describe('fact-check API contract', () => {
  it('独立文本与文档输入不需要审校记录', async () => {
    const common = { mode: 'web' as const, source_ids: [], allow_external_search: true as const, request_id: createFactCheckId() }
    await createFactCheckRunApi({ ...common, text: '待核查事实', confirm_claims: true })
    expect(request.post).toHaveBeenLastCalledWith('/fact-check/runs', { ...common, text: '待核查事实', confirm_claims: true, title: undefined }, expect.anything())
    await createFactCheckRunApi({ ...common, file_id: 'uploaded-file' })
    expect(vi.mocked(request.post).mock.calls[1][1]).toMatchObject({ file_id: 'uploaded-file' })
    expect(vi.mocked(request.post).mock.calls[1][1]).not.toHaveProperty('record_id')
  })
  it('工作台分页和原文请求使用统一鉴权客户端', async () => {
    const signal = new AbortController().signal
    await factCheckHistoryApi({ page: 2, page_size: 20, q: '报告' }, { signal })
    expect(request.get).toHaveBeenLastCalledWith('/fact-check/history', { signal, headers: { 'X-Silent-Error': 'true' }, params: { page: 2, page_size: 20, q: '报告' } })
    await factCheckSourceApi(21, { signal })
    expect(request.get).toHaveBeenLastCalledWith('/fact-check/runs/21/source', expect.objectContaining({ signal }))
  })
  it('确认、单条深入与复核均传递明确的请求编号', async () => {
    const request_id = createFactCheckId()
    const execute = { claims: [{ id: 'c1', statement: '确认陈述' }], request_id }
    await executeFactCheckApi(21, execute)
    expect(request.post).toHaveBeenLastCalledWith('/fact-check/runs/21/execute', execute, expect.anything())
    const deepen = { claim_id: 'c1', request_id, allow_external_search: true as const, supplemental_urls: [] }
    await deepenFactCheckApi(21, deepen)
    expect(request.post).toHaveBeenLastCalledWith('/fact-check/runs/21/deepen', deepen, expect.anything())
    const review = { claim_id: 'c1', request_id, decision: 'disagree' as const, note: '核对依据' }
    await addFactCheckReviewApi(21, review)
    expect(request.post).toHaveBeenLastCalledWith('/fact-check/runs/21/reviews', review, expect.anything())
  })
  it('打印报告按blob鉴权下载而不是公开URL', async () => {
    await exportFactCheckApi(21, 'html')
    expect(request.get).toHaveBeenLastCalledWith('/fact-check/runs/21/export', { headers: { 'X-Silent-Error': 'true' }, params: { format: 'html' }, responseType: 'blob' })
  })
  it('无需 secure-context randomUUID 也能生成安全的 UUID v4', () => {
    const getRandomValues = crypto.getRandomValues.bind(crypto)
    vi.stubGlobal('crypto', { getRandomValues })
    try {
      const values = Array.from({ length: 30 }, () => createFactCheckId())
      expect(new Set(values).size).toBe(values.length)
      for (const value of values) expect(value).toMatch(/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/)
    } finally {
      vi.unstubAllGlobals()
    }
  })
  it('使用 request 的 /api/v1 基址，读取配置、历史、详情及管理设置并传递鉴权请求选项', async () => {
    const signal = new AbortController().signal
    const config = { signal, headers: { 'X-Silent-Error': 'true' } }
    vi.mocked(request.get).mockResolvedValue({})
    await getFactCheckOptionsApi({ signal })
    expect(request.get).toHaveBeenLastCalledWith('/fact-check/options', config)
    await listFactCheckRunsApi(7, { signal })
    expect(request.get).toHaveBeenLastCalledWith('/fact-check/runs', { ...config, params: { record_id: 7 } })
    await getFactCheckRunApi(21, { signal })
    expect(request.get).toHaveBeenLastCalledWith('/fact-check/runs/21', config)
    await getFactCheckSettingsApi({ signal })
    expect(request.get).toHaveBeenLastCalledWith('/admin/fact-check/settings', config)
    expect(request.post).not.toHaveBeenCalled()
  })
  it('启动仅发送契约字段和同一个幂等编号，不发送原文或本地状态', async () => {
    const payload: CreateFactCheckPayload = { record_id: 7, mode: 'trusted', source_ids: ['source-1'], allow_external_search: true, request_id: crypto.randomUUID() }
    const signal = new AbortController().signal
    const result = { id: 21, status: 'PENDING' }
    vi.mocked(request.post).mockResolvedValue(result)
    expect(await createFactCheckRunApi({ ...payload, text: '不得发送', local: true } as CreateFactCheckPayload, { signal })).toBe(result)
    await createFactCheckRunApi(payload, { signal })
    for (const call of vi.mocked(request.post).mock.calls) {
      expect(call).toEqual(['/fact-check/runs', payload, { signal, headers: { 'X-Silent-Error': 'true' } }])
      expect((call[1] as CreateFactCheckPayload).source_ids).not.toBe(payload.source_ids)
    }
    await cancelFactCheckRunApi(21, { signal })
    expect(request.post).toHaveBeenLastCalledWith('/fact-check/runs/21/cancel', undefined, { signal, headers: { 'X-Silent-Error': 'true' } })
  })
  it('Tavily 保存裁剪字段、携带 provider，空密钥省略、默认根路径', async () => {
    const signal = new AbortController().signal
    vi.mocked(request.put).mockResolvedValue({ api_key_configured: true })
    const source = { id: 'source-1', name: '  来源一 ', domain: ' example.org ', path_prefix: ' ', is_enabled: false, local: true }
    await saveFactCheckSettingsApi({ enabled: false, provider: 'tavily', model_config_id: null, max_claims: 10, api_key: ' ', sources: [source] }, { signal })
    expect(request.put).toHaveBeenLastCalledWith('/admin/fact-check/settings', {
      enabled: false, provider: 'tavily', model_config_id: null, max_claims: 10, sources: [{ id: 'source-1', name: '来源一', domain: 'example.org', path_prefix: '/', is_enabled: false }],
    }, { signal, headers: { 'X-Silent-Error': 'true' } })
    expect(source.name).toBe('  来源一 ')
    await saveFactCheckSettingsApi({ enabled: true, provider: 'tavily', model_config_id: null, max_claims: 10, api_key: ' new-secret ', sources: [] })
    expect(request.put).toHaveBeenLastCalledWith('/admin/fact-check/settings', { enabled: true, provider: 'tavily', model_config_id: null, max_claims: 10, api_key: 'new-secret', sources: [] }, expect.anything())
  })
  it.each([true, false])('原生模式保存 enabled=%s 始终不发送密钥或只读模型信息', async enabled => {
    const payload = {
      enabled, provider: 'model', model_config_id: null, max_claims: 10, sources: [], api_key: 'unsent-tavily-secret',
      model_api_key: 'must-not-send', model_name: 'active-model', model_search_supported: true,
      model_search_reason: 'endpoint supported', api_key_configured: true,
    } satisfies SaveFactCheckSettingsPayload & Partial<FactCheckSettings> & { model_api_key: string }
    await saveFactCheckSettingsApi(payload)
    expect(request.put).toHaveBeenCalledExactlyOnceWith('/admin/fact-check/settings', {
      enabled, provider: 'model', model_config_id: null, max_claims: 10, sources: [],
    }, { headers: { 'X-Silent-Error': 'true' } })
    expect(payload.api_key).toBe('unsent-tavily-secret')
    expect(request.get).not.toHaveBeenCalled()
    expect(request.post).not.toHaveBeenCalled()
  })
  it.each(['model', 'tavily'] as const)('%s 响应保留当前模型描述与已保存任务 provider，不触发探测或启动', async provider => {
    const settings: FactCheckSettings = {
      enabled: true, provider, api_key_configured: false, model_name: '当前配置 / active-model',
      model_search_supported: true, model_search_reason: '适配器端点支持，账号可能在运行时拒绝', max_claims: 10, sources: [],
    }
    const options: FactCheckOptions = {
      available: true, unavailable_reason: '', provider, model_name: settings.model_name,
      max_claims: 10, max_text_chars: 20000, daily_limit: 20, sources: [],
    }
    const run: FactCheckRun = {
      id: 21, record_id: 7, title: '材料', source_kind: 'record', file_id: null, parent_run_id: null, stage: 'complete', depth: 'standard', confirm_claims: false, max_claims: 10,
      provider, mode: 'web', status: 'SUCCESS', progress: 100, message: '', error_code: null,
      result: null, source_hash: 'hash', source_ids: [], created_at: '2026-09-11T10:00:00Z', finished_at: '2026-09-11T10:01:00Z',
    }
    vi.mocked(request.get).mockResolvedValueOnce(settings).mockResolvedValueOnce(options).mockResolvedValueOnce(run).mockResolvedValueOnce([run])
    expect(await getFactCheckSettingsApi()).toEqual(settings)
    expect(await getFactCheckOptionsApi()).toEqual(options)
    expect(await getFactCheckRunApi(21)).toEqual(run)
    expect(await listFactCheckRunsApi(7)).toEqual([run])
    expect(request.get).toHaveBeenCalledTimes(4)
    expect(request.post).not.toHaveBeenCalled()
    expect(request.put).not.toHaveBeenCalled()
  })
  it('拒绝原样向组件传播，不吞掉状态、不自动重试', async () => {
    const error = { response: { status: 429, data: { detail: '已达到每日限制' } } }
    vi.mocked(request.post).mockRejectedValue(error)
    await expect(createFactCheckRunApi({ record_id: 7, mode: 'web', source_ids: [], allow_external_search: true, request_id: crypto.randomUUID() })).rejects.toBe(error)
    expect(request.post).toHaveBeenCalledTimes(1)
    expect(request.get).not.toHaveBeenCalled()
  })
})
