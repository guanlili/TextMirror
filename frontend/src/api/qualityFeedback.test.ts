import { beforeEach, describe, expect, it, vi } from 'vitest'
vi.mock('@/utils/request', () => ({ default: { get: vi.fn(), put: vi.fn(), post: vi.fn() } }))
import request from '@/utils/request'
import { evaluateQualityFeedbackApi, listQualityFeedbackApi, reviewQualityFeedbackApi, submitQualityFeedbackApi, type QualityFeedbackCreate } from './qualityFeedback'

beforeEach(() => vi.resetAllMocks())
describe('quality feedback API contract', () => {
  it('独立上报、分页查询、CAS 审核，不修改审阅状态', async () => {
    const data: QualityFeedbackCreate = { record_id: 7, original: '词', suggestion: '', start: 2, end: 3, kind: 'missed', issue_type: 'typo', note: '' }
    await submitQualityFeedbackApi(data)
    expect(request.post).toHaveBeenCalledWith('/proofread/quality-feedback', data)
    const params = { status: 'pending' as const, page: 2, page_size: 20 }
    await listQualityFeedbackApi(params)
    expect(request.get).toHaveBeenCalledWith('/admin/global-dict/quality-feedback', { params })
    const review = { revision: 1, status: 'rejected' as const, sample: null, review_note: '' }
    await reviewQualityFeedbackApi(9, review)
    expect(request.put).toHaveBeenCalledWith('/admin/global-dict/quality-feedback/9', review)
  })
  it('审核原样发送人工 golden，包含删除和换行，不生成额外评测调用', async () => {
    const data = {
      revision: 4, status: 'confirmed' as const, review_note: '人工确认',
      sample: { text: '甲错词乙', start: 1, end: 3, original: '错词', domain: 'general' as const,
        expectation: 'report' as const, issue_type: 'typo', accepted_suggestions: ['', ' 正词\n'], rejected_suggestions: ['坏词'] },
    }
    await reviewQualityFeedbackApi(9, data)
    expect(request.put).toHaveBeenCalledWith('/admin/global-dict/quality-feedback/9', data)
    expect(request.post).not.toHaveBeenCalled()
  })
  it('评测显式发送样例与模型列表并设置超时，不自动重试', async () => {
    const failure = { response: { status: 409 } }
    vi.mocked(request.post).mockRejectedValueOnce(failure)
    const data = { feedback_ids: [1, 2], config_ids: [11, 13] }
    await expect(evaluateQualityFeedbackApi(data)).rejects.toBe(failure)
    expect(request.post).toHaveBeenCalledWith('/admin/global-dict/quality-feedback/evaluate', data, { timeout: 300000 })
    expect(request.post).toHaveBeenCalledTimes(1)
  })
})
