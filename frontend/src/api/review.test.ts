import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/utils/request', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn() } }))

import request from '@/utils/request'
import {
  createReviewVersionApi,
  exportDocumentReviewApi,
  exportReviewApi,
  getReviewApi,
  getReviewErrorDetail,
  saveReviewApi,
  type SaveReviewPayload,
} from './review'

const payload: SaveReviewPayload = {
  revision: 4,
  issues: [{ original: '帐号', suggestion: '账号', start: 0, end: 2, severity: 'warning', type: 'typo', _accepted: true }],
  coverage: { status: 'partial', total_chunks: 1, completed_chunks: 0, failed_chunks: [{ chunk_index: 0, start: 0, end: 2, text: '帐号', error_code: 'timeout' }] },
  depth: 'deep',
  config_id: 2,
}

beforeEach(() => vi.resetAllMocks())

describe('review APIs', () => {
  it('GET/PUT/POST 使用约定路径，传递 revision、coverage 和 AbortSignal', async () => {
    const signal = new AbortController().signal
    const response = { revision: 5, record_id: 7 }
    vi.mocked(request.get).mockResolvedValue(response)
    vi.mocked(request.put).mockResolvedValue(response)
    vi.mocked(request.post).mockResolvedValue(response)
    expect(await getReviewApi(7, { signal })).toEqual(response)
    expect(request.get).toHaveBeenCalledWith('/history/7/review', expect.objectContaining({ signal }))
    expect(await saveReviewApi(7, payload, { signal })).toEqual(response)
    expect(request.put).toHaveBeenCalledWith('/history/7/review', payload, expect.objectContaining({ signal }))
    expect(await createReviewVersionApi(7, payload, { signal })).toEqual(response)
    expect(request.post).toHaveBeenCalledWith('/history/7/versions', payload, expect.objectContaining({ signal }))
    expect(request.post).not.toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ label: expect.anything() }), expect.anything())
  })

  it('所有写入口仅发送契约字段，将未定位范围归一为空且不更改本地补丁', async () => {
    const issues = [
      { ...payload.issues[0], _patch: { start: 0, end: 2, original: '帐号', replacement: '账号' } },
      { ...payload.issues[0], start: -1, end: -1, _accepted: false },
    ]
    const before = JSON.stringify(issues)
    vi.mocked(request.put).mockResolvedValue({})
    vi.mocked(request.post).mockResolvedValue({})
    await saveReviewApi(7, { ...payload, issues })
    await createReviewVersionApi(7, { ...payload, issues })
    await exportDocumentReviewApi('file', { issues, format: 'docx' })
    const calls = [...vi.mocked(request.put).mock.calls, ...vi.mocked(request.post).mock.calls]
    for (const call of calls) {
      const wire = JSON.parse(JSON.stringify(call[1])).issues
      expect(wire[0]).not.toHaveProperty('_patch')
      expect(wire[0]).toMatchObject({ start: 0, end: 2, _accepted: true })
      expect(wire[1]).toMatchObject({ start: null, end: null, _accepted: false })
    }
    expect(JSON.stringify(issues)).toBe(before)
  })

  it.each([
    ['草稿', saveReviewApi, 'put', '/history/7/review'],
    ['版本', createReviewVersionApi, 'post', '/history/7/versions'],
  ] as const)('%s 保存携带逐模型白名单快照，去掉嵌套运行时状态但保留顶层决策', async (_name, save, method, path) => {
    const accepted = { ...payload.issues[0], _ignored: false,
      _patch: { start: 0, end: 2, original: '帐号', replacement: '账号' }, runtime: 'only-local' }
    const ignored = { ...accepted, start: -1, end: -1, _accepted: false, _ignored: true }
    const model = {
      config_id: 11, config_name: '模型甲', model: 'model-a', domain: 'legal', depth: 'deep',
      success: true, error: null, elapsed_ms: 50, total_issues: 999, runtime: 'only-local',
      issues: [accepted, ignored], coverage: structuredClone(payload.coverage!),
    }
    const compare = { results: [model, { ...model, config_id: 22, config_name: '模型乙',
      issues: [{ ...accepted, explanation: '乙模型独立说明', severity: 'error' }] }], consensus_originals: ['帐号'] }
    const input = { ...payload, issues: [accepted, ignored], compare }
    const before = structuredClone(input)
    const signal = new AbortController().signal
    const response = { record_id: 7, revision: 5 }
    vi.mocked(request[method]).mockResolvedValue(response)
    expect(await save(7, input, { signal })).toBe(response)
    const sent = vi.mocked(request[method]).mock.calls[0][1] as SaveReviewPayload
    expect(request[method]).toHaveBeenCalledWith(path, sent, expect.objectContaining({ signal }))
    expect(Object.keys(sent.compare!)).toEqual(['results'])
    expect(sent.compare!.results[0]).toEqual({
      config_id: 11, config_name: '模型甲', model: 'model-a', domain: 'legal', depth: 'deep',
      success: true, error: null, elapsed_ms: 50, coverage: payload.coverage,
      issues: [
        { original: '帐号', suggestion: '账号', type: 'typo', severity: 'warning', explanation: '', start: 0, end: 2, _accepted: false, _ignored: false },
        { original: '帐号', suggestion: '账号', type: 'typo', severity: 'warning', explanation: '', start: null, end: null, _accepted: false, _ignored: false },
      ],
    })
    expect(sent.compare!.results[1].issues[0]).toMatchObject({ explanation: '乙模型独立说明', severity: 'error', _accepted: false, _ignored: false })
    expect(sent.issues[0]).toMatchObject({ start: 0, end: 2, _accepted: true, _ignored: false })
    expect(sent.issues[1]).toMatchObject({ start: null, end: null, _accepted: false, _ignored: true })
    for (const item of [...sent.issues, ...sent.compare!.results.flatMap(result => result.issues)]) {
      expect(item).not.toHaveProperty('_patch')
      expect(item).not.toHaveProperty('runtime')
    }
    expect(input).toEqual(before)
    expect(sent.compare!.results[0].issues).not.toBe(input.compare.results[0].issues)
    expect(sent.compare!.results[0].coverage).not.toBe(input.compare.results[0].coverage)
    sent.compare!.results[0].coverage!.failed_chunks[0].error_code = 'changed-after-request'
    sent.compare!.results[0].issues[0].explanation = 'changed-after-request'
    expect(input).toEqual(before)
  })

  it.each([
    ['草稿', saveReviewApi, 'put'], ['版本', createReviewVersionApi, 'post'],
  ] as const)('%s 普通保存不凭空添加 compare，显式清除 compare 发送 null', async (_name, save, method) => {
    vi.mocked(request[method]).mockResolvedValue({})
    await save(7, payload)
    expect(vi.mocked(request[method]).mock.calls[0][1]).not.toHaveProperty('compare')
    await save(7, { ...payload, compare: null })
    expect(vi.mocked(request[method]).mock.calls[1][1]).toHaveProperty('compare', null)
    await save(7, { ...payload, compare: undefined })
    expect(vi.mocked(request[method]).mock.calls[2][1]).toHaveProperty('compare', null)
  })

  it('版本支持可选名称，错误仍向调用方抛出', async () => {
    const failure = { response: { status: 422, data: { detail: '版本已达到20个上限' } } }
    vi.mocked(request.post).mockRejectedValue(failure)
    await expect(createReviewVersionApi(7, { ...payload, label: '终稿' })).rejects.toBe(failure)
    expect(getReviewErrorDetail(failure)).toBe('版本已达到20个上限')
  })

  it('409 不自动重试、不查询最新 revision，也不吞掉原错误', async () => {
    const failure = { response: { status: 409, data: { detail: '草稿已更新' } } }
    vi.mocked(request.put).mockRejectedValue(failure)
    await expect(saveReviewApi(7, payload)).rejects.toBe(failure)
    expect(request.put).toHaveBeenCalledTimes(1)
    expect(request.get).not.toHaveBeenCalled()
  })

  it('历史导出和无 record 文档导出返回 Blob、不自行下载或创建记录', async () => {
    const blob = new Blob(['内容'], { type: 'text/plain' })
    const signal = new AbortController().signal
    vi.mocked(request.post).mockResolvedValue(blob)
    expect(await exportReviewApi(7, { revision: 4, version_id: 'v1', format: 'txt' }, { signal })).toBe(blob)
    expect(request.post).toHaveBeenLastCalledWith('/history/7/export', { revision: 4, version_id: 'v1', format: 'txt' }, expect.objectContaining({ responseType: 'blob', signal }))
    expect(await exportDocumentReviewApi('file/id', { issues: payload.issues, format: 'docx' })).toBe(blob)
    expect(request.post).toHaveBeenLastCalledWith('/document/file%2Fid/export', { issues: payload.issues, format: 'docx' }, expect.objectContaining({ responseType: 'blob' }))
  })

  it('解析 Blob JSON detail，保留原始错误对象和 status', async () => {
    const failure = { message: 'HTTP 409', response: { status: 409, data: new Blob([JSON.stringify({ detail: '请先保存当前草稿' })], { type: 'application/json' }) as unknown } }
    vi.mocked(request.post).mockRejectedValue(failure)
    await expect(exportReviewApi(7, { revision: 4, format: 'docx' })).rejects.toBe(failure)
    expect(failure.response.status).toBe(409)
    expect(failure.response.data).toEqual({ detail: '请先保存当前草稿' })
    expect(getReviewErrorDetail(failure)).toBe('请先保存当前草稿')
    expect(failure.message).toBe('请先保存当前草稿')
  })

  it('Blob 验证错误数组转成可读文本', async () => {
    const failure = { response: { status: 422, data: new Blob([JSON.stringify({ detail: [{ msg: '位置无效' }, { msg: '版本不存在' }] })]) } }
    vi.mocked(request.post).mockRejectedValue(failure)
    await expect(exportDocumentReviewApi('file', { issues: payload.issues, format: 'txt' })).rejects.toBe(failure)
    expect(getReviewErrorDetail(failure)).toBe('位置无效；版本不存在')
  })

  it('非 JSON Blob 或网络错误保留拒绝，不产生空成功文件', async () => {
    const failure = { message: '网络网关失败', response: { status: 502, data: new Blob(['<html>bad gateway</html>']) } }
    vi.mocked(request.post).mockRejectedValue(failure)
    await expect(exportReviewApi(7, { revision: 4, format: 'txt' })).rejects.toBe(failure)
    expect(getReviewErrorDetail(failure)).toBe('网络网关失败')
    expect(failure.response.data).toBeInstanceOf(Blob)
    const network = new Error('断网')
    vi.mocked(request.get).mockRejectedValue(network)
    await expect(getReviewApi(7)).rejects.toBe(network)
  })
})
