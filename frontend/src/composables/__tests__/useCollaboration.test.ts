import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createRenderer, defineComponent, h } from 'vue'
vi.mock('@/utils/request', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
import request from '@/utils/request'
import { useCollaboration } from '../useCollaboration'
import type { CollaborationReport, CollaborationResult } from '@/api/collaboration'
import type { TaskStatus } from '@/api/tasks'

// Minimal host: lifecycle tests require no DOM or browser dependencies.
const renderer = createRenderer<object, object>({
  createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
  setText() {}, setElementText() {}, patchProp() {}, insert() {}, remove() {},
  parentNode: () => null, nextSibling: () => null,
})
const cleanups: (() => void)[] = []
function mount(allowed = true) {
  let flow!: ReturnType<typeof useCollaboration>
  const onQueued = vi.fn(), onSnapshot = vi.fn(), onSuccess = vi.fn()
  const app = renderer.createApp(defineComponent({ setup() {
    flow = useCollaboration({ canStart: () => allowed, onQueued, onSnapshot, onSuccess })
    return () => h('div')
  } }))
  app.mount({})
  let mounted = true
  const unmount = () => { if (mounted) app.unmount(); mounted = false }
  cleanups.push(unmount)
  return { flow, onQueued, onSnapshot, onSuccess, unmount }
}
const input = { text: '𠮷帐号', domain: 'general', config_id: 2 }
const snapshot = { ...input, task_id: 'task-1' }
const report = (status: CollaborationReport['status'] = 'running'): CollaborationReport => ({ status, roles: [], findings: [], reviewed_count: 0, review_limit: 20, config_id: 2, model_name: 'model' })
const task = (status = 'RUNNING', extra: Partial<TaskStatus> = {}): TaskStatus => ({ task_id: 'task-1', status, progress: 30, message: 'working', collaboration: report(), ...extra })
const result = (): CollaborationResult => ({ record_id: 4, issues: [], total_issues: 0, chunks_count: 1, usage: {}, domain: 'general', check_types: [], collaboration: report('complete') })
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done }); return { promise, resolve } }
beforeEach(() => {
  vi.resetAllMocks(); vi.useFakeTimers()
  vi.mocked(request.post).mockResolvedValue({ task_id: 'task-1', message: 'queued' })
  vi.mocked(request.get).mockImplementation(async path => path.startsWith('/proofread/') ? snapshot : task())
})
afterEach(() => { cleanups.splice(0).forEach(cleanup => cleanup()); vi.useRealTimers() })

describe('collaboration lifecycle', () => {
  it('does nothing on mount; login/permission prerequisite blocks POST', async () => {
    const { flow } = mount(false)
    await flow.submit(input)
    expect(request.post).not.toHaveBeenCalled()
    expect(request.get).not.toHaveBeenCalled()
  })
  it('counts Unicode characters, rejects >8000 without truncation', async () => {
    const { flow } = mount()
    await flow.submit({ ...input, text: '𠮷'.repeat(8001) })
    expect(request.post).not.toHaveBeenCalled()
    expect(flow.error.value).toContain('8000')
    await flow.submit({ ...input, text: '𠮷'.repeat(8000) })
    expect((vi.mocked(request.post).mock.calls[0][1] as { text: string }).text).toHaveLength(16000)
  })
  it('preserves one request UUID and exact parameters after timeout, never silently duplicates', async () => {
    const { flow, onQueued } = mount()
    vi.mocked(request.post).mockRejectedValueOnce(new Error('timeout'))
    await flow.submit(input)
    const pending = flow.pending.value
    expect(pending?.request_id).toMatch(/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/)
    expect(flow.locked.value).toBe(true)
    await flow.submit({ ...input, domain: 'legal' })
    expect(request.post).toHaveBeenCalledOnce()
    await flow.submit(input)
    expect(vi.mocked(request.post).mock.calls[1][1]).toEqual(pending)
    expect(onQueued).toHaveBeenCalledExactlyOnceWith('task-1')
    await flow.submit(input)
    expect(request.post).toHaveBeenCalledTimes(2)
  })
  it('unlocks rejected validation requests rather than forcing a stale UUID', async () => {
    const { flow } = mount()
    vi.mocked(request.post).mockRejectedValueOnce({ response: { status: 422, data: { detail: 'invalid' } } })
    await flow.submit(input)
    expect(flow.pending.value).toBeNull()
    expect(flow.locked.value).toBe(false)
  })
  it('refresh reads only owner snapshot and task; reconnect never resubmits', async () => {
    const { flow, onSnapshot } = mount()
    await flow.resume('task-1')
    expect(onSnapshot).toHaveBeenCalledExactlyOnceWith(snapshot)
    expect(flow.report.value?.status).toBe('running')
    expect(request.post).not.toHaveBeenCalled()
    vi.mocked(request.get).mockRejectedValueOnce(new Error('offline'))
    await vi.advanceTimersByTimeAsync(1500)
    expect(flow.monitoring.value).toBe(false)
    expect(flow.error.value).toBe('offline')
    await vi.advanceTimersByTimeAsync(5000)
    expect(request.get).toHaveBeenCalledTimes(3)
    await flow.reconnect()
    expect(flow.monitoring.value).toBe(true)
    expect(request.post).not.toHaveBeenCalled()
  })
  it('snapshot auth failure can be retried without exposing or creating input', async () => {
    const { flow, onSnapshot } = mount()
    vi.mocked(request.get).mockRejectedValueOnce({ response: { status: 403, data: { detail: 'owner only' } } })
    await flow.resume('task-1')
    expect(onSnapshot).not.toHaveBeenCalled()
    expect(flow.snapshot.value).toBeNull()
    await flow.reconnect()
    expect(onSnapshot).toHaveBeenCalledOnce()
    expect(request.post).not.toHaveBeenCalled()
  })
  it.each(['FAILURE', 'CANCELLED', 'REVOKED'])('%s retains prior findings view-only and never initializes editable review', async status => {
    const { flow, onSuccess } = mount()
    const partial = report('partial')
    vi.mocked(request.get).mockResolvedValueOnce(task(status, { collaboration: partial }))
    await flow.submit(input)
    expect(flow.report.value).toBe(partial)
    expect(flow.terminal.value).toBe(true)
    expect(onSuccess).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
  })
  it('displays the human-readable cancellation message rather than the error code', async () => {
    const { flow } = mount()
    vi.mocked(request.get).mockResolvedValueOnce(task('CANCELLED', {
      message: '协作审校已取消', error: 'USER_CANCELLED', collaboration: report('partial'),
    }))
    await flow.submit(input)
    expect(flow.message.value).toBe('协作审校已取消')
  })
  it('initializes successful review once; repeated reads cannot erase saved edits', async () => {
    const { flow, onSuccess } = mount()
    const success = result()
    vi.mocked(request.get).mockResolvedValue(task('SUCCESS', { result: success, collaboration: success.collaboration }))
    await flow.submit(input)
    expect(onSuccess).toHaveBeenCalledExactlyOnceWith(success, snapshot)
    await flow.reconnect()
    expect(onSuccess).toHaveBeenCalledOnce()
    expect(vi.getTimerCount()).toBe(0)
  })
  it('does not confuse cancel acknowledgement with cancellation; completed request may win', async () => {
    const { flow, onSuccess } = mount()
    await flow.submit(input)
    await flow.cancel()
    expect(request.post).toHaveBeenLastCalledWith('/tasks/task-1/cancel', null, expect.objectContaining({ signal: expect.any(AbortSignal) }))
    expect(flow.cancelRequested.value).toBe(true)
    expect(flow.terminal.value).toBe(false)
    vi.mocked(request.get).mockResolvedValueOnce(task('SUCCESS', { result: result() }))
    await vi.advanceTimersByTimeAsync(1500)
    expect(onSuccess).toHaveBeenCalledOnce()
    expect(flow.status.value).toBe('SUCCESS')
  })
  it('unmount aborts in-flight status, clears timers, ignores late events and never cancels server task', async () => {
    const { flow, onSuccess, unmount } = mount()
    await flow.submit(input)
    const late = deferred<TaskStatus>()
    vi.mocked(request.get).mockReturnValueOnce(late.promise)
    await vi.advanceTimersByTimeAsync(1500)
    const calls = vi.mocked(request.get).mock.calls
    const signal = calls[calls.length - 1][1]!.signal!
    unmount()
    expect(signal.aborted).toBe(true)
    late.resolve(task('SUCCESS', { result: result() }))
    await vi.advanceTimersByTimeAsync(5000)
    expect(onSuccess).not.toHaveBeenCalled()
    expect(request.post).toHaveBeenCalledOnce()
    expect(vi.getTimerCount()).toBe(0)
  })
  it('unmount during submit suppresses query changes and subsequent monitoring', async () => {
    const { flow, onQueued, unmount } = mount()
    const late = deferred<{ task_id: string; message: string }>()
    vi.mocked(request.post).mockReturnValueOnce(late.promise)
    const submitting = flow.submit(input)
    const signal = vi.mocked(request.post).mock.calls[0][2]!.signal!
    unmount()
    expect(signal.aborted).toBe(true)
    late.resolve({ task_id: 'task-1', message: 'queued' })
    await submitting
    expect(onQueued).not.toHaveBeenCalled()
    expect(request.get).not.toHaveBeenCalled()
  })
})
