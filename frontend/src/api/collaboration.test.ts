import { beforeEach, describe, expect, it, vi } from 'vitest'
vi.mock('@/utils/request', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }))
import request from '@/utils/request'
import { createCollaborationApi, createCollaborationId, getCollaborationSnapshotApi } from './collaboration'
import { createReviewVersionApi, getReviewApi, saveReviewApi } from './review'

beforeEach(() => vi.resetAllMocks())
describe('collaboration contract', () => {
  it('uses authenticated shared request, strict submit fields, and a read-only owner snapshot GET', async () => {
    const signal = new AbortController().signal
    const data = { text: '𠮷原文', domain: 'auto', config_id: 8, request_id: createCollaborationId(), depth: 'deep', secret: 'not-on-wire' }
    vi.mocked(request.post).mockResolvedValue({ task_id: 'task', message: 'queued' })
    expect(await createCollaborationApi(data, signal)).toEqual({ task_id: 'task', message: 'queued' })
    expect(request.post).toHaveBeenCalledExactlyOnceWith('/proofread/collaborate', {
      text: data.text, domain: 'auto', config_id: 8, request_id: data.request_id,
    }, { signal, headers: { 'X-Silent-Error': 'true' } })
    await getCollaborationSnapshotApi('task/id', signal)
    expect(request.get).toHaveBeenCalledExactlyOnceWith('/proofread/collaborate/task%2Fid', { signal, headers: { 'X-Silent-Error': 'true' } })
  })
  it('uses secure RFC4122 v4 IDs without crypto.randomUUID dependency', () => {
    const getRandomValues = vi.fn((bytes: Uint8Array) => { bytes.fill(255); return bytes })
    vi.stubGlobal('crypto', { getRandomValues })
    try {
      expect(createCollaborationId()).toBe('ffffffff-ffff-4fff-bfff-ffffffffffff')
      expect(getRandomValues).toHaveBeenCalledOnce()
    } finally { vi.unstubAllGlobals() }
  })
  it('omits an unspecified config and never creates multiple role submissions', async () => {
    await createCollaborationApi({ text: '文', domain: 'general', request_id: 'id' })
    expect(request.post).toHaveBeenCalledOnce()
    expect(vi.mocked(request.post).mock.calls[0][1]).not.toHaveProperty('config_id')
  })
  it('retains readonly original report in responses but strips it from draft and version saves', async () => {
    const collaboration = { status: 'partial', roles: [], findings: [], reviewed_count: 0, review_limit: 20, config_id: 1, model_name: 'model' }
    const data = { revision: 2, issues: [{ original: '文', suggestion: '文字', type: 'style', severity: 'info', start: 0, end: 1, found_by: ['language'], review_status: 'confirmed' }], collaboration }
    vi.mocked(request.get).mockResolvedValue(data)
    vi.mocked(request.put).mockResolvedValue(data)
    vi.mocked(request.post).mockResolvedValue(data)
    expect((await getReviewApi(4)).collaboration).toBe(collaboration)
    expect((await saveReviewApi(4, data)).collaboration).toBe(collaboration)
    expect((await createReviewVersionApi(4, data)).collaboration).toBe(collaboration)
    for (const call of [...vi.mocked(request.put).mock.calls, ...vi.mocked(request.post).mock.calls]) {
      expect(call[1]).not.toHaveProperty('collaboration')
      expect((call[1] as typeof data).issues[0]).not.toHaveProperty('found_by')
      expect((call[1] as typeof data).issues[0]).not.toHaveProperty('review_status')
    }
    expect(data.issues[0].found_by).toEqual(['language'])
  })
})
