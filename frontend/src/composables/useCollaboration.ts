import { computed, onBeforeUnmount, ref, shallowRef } from 'vue'
import {
  createCollaborationApi, createCollaborationId, getCollaborationSnapshotApi, MAX_COLLABORATION_CHARS,
  type CollaborationCreate, type CollaborationInput, type CollaborationReport, type CollaborationResult, type CollaborationSnapshot,
} from '@/api/collaboration'
import { cancelTaskApi, getTaskStatusApi } from '@/api/tasks'
import { getReviewErrorDetail } from '@/api/review'

const terminalStates = new Set(['SUCCESS', 'FAILURE', 'REVOKED', 'CANCELLED'])

/** 单飞轮询；断线只重连读取，提交不确定时保留原 UUID 和完整参数。原文仅在内存。 */
export function useCollaboration(options: {
  canStart: () => boolean
  onQueued: (taskId: string) => void | Promise<unknown>
  onSnapshot: (snapshot: CollaborationSnapshot) => void
  onSuccess: (result: CollaborationResult, snapshot: CollaborationSnapshot) => void | Promise<unknown>
}) {
  const taskId = ref('')
  const report = shallowRef<CollaborationReport | null>(null)
  const snapshot = shallowRef<CollaborationSnapshot | null>(null)
  const pending = shallowRef<CollaborationCreate | null>(null)
  const status = ref('')
  const message = ref('')
  const error = ref('')
  const busy = ref(false)
  const monitoring = ref(false)
  const cancelling = ref(false)
  const cancelRequested = ref(false)
  const terminal = computed(() => terminalStates.has(status.value))
  const locked = computed(() => busy.value || !!pending.value || (!!taskId.value && !terminal.value))
  let alive = true
  let sequence = 0
  let controller: AbortController | null = null
  let cancelController: AbortController | null = null
  let timer: ReturnType<typeof setTimeout> | null = null
  let delivered = false

  function stop() {
    sequence++
    controller?.abort()
    controller = null
    if (timer !== null) clearTimeout(timer)
    timer = null
    monitoring.value = false
    busy.value = false
  }
  function current(token: number) { return alive && token === sequence }
  function reset() {
    stop()
    cancelController?.abort()
    cancelController = null
    taskId.value = ''; report.value = null; snapshot.value = null; pending.value = null
    status.value = ''; message.value = ''; error.value = ''; delivered = false
    cancelling.value = false; cancelRequested.value = false
  }
  function showError(cause: unknown) {
    error.value = getReviewErrorDetail(cause)
  }

  async function readStatus(token: number, signal: AbortSignal) {
    try {
      const state = await getTaskStatusApi(taskId.value, undefined, signal)
      if (!current(token)) return
      if (state.task_id !== taskId.value) throw new Error('任务编号不匹配，请重新读取')
      status.value = state.status
      message.value = state.message || state.error || ''
      if (state.collaboration) report.value = state.collaboration
      if (state.status === 'SUCCESS') {
        const result = state.result as CollaborationResult | undefined
        if (!result?.collaboration || !Array.isArray(result.issues) || !result.record_id || !snapshot.value) {
          throw new Error('任务已结束，但审阅结果尚不可用，请重连读取')
        }
        report.value = result.collaboration
        if (!delivered) {
          await options.onSuccess(result, snapshot.value)
          if (!current(token)) return
          delivered = true
        }
      }
      if (terminal.value) { monitoring.value = false; return }
      timer = setTimeout(() => { timer = null; void readStatus(token, signal) }, 1500)
    } catch (cause) {
      if (!current(token)) return
      monitoring.value = false
      showError(cause)
    }
  }

  async function reconnect() {
    if (!alive || !taskId.value || busy.value) return
    stop()
    const token = sequence
    controller = new AbortController()
    const signal = controller.signal
    monitoring.value = true
    error.value = ''
    try {
      // 刷新恢复只读取服务端白名单输入快照，不创建任务。
      if (!snapshot.value) {
        const restored = await getCollaborationSnapshotApi(taskId.value, signal)
        if (!current(token)) return
        if (restored.task_id !== taskId.value) throw new Error('输入快照与任务编号不匹配')
        snapshot.value = restored
        options.onSnapshot(restored)
      }
      await readStatus(token, signal)
    } catch (cause) {
      if (current(token)) { monitoring.value = false; showError(cause) }
    }
  }

  async function resume(id: string) {
    reset()
    taskId.value = id
    status.value = 'PENDING'
    await reconnect()
  }

  async function submit(input: CollaborationInput) {
    if (!alive || busy.value || taskId.value || !options.canStart()) return
    if (!input.text.trim() || Array.from(input.text).length > MAX_COLLABORATION_CHARS) {
      error.value = '请输入 1–8000 个 Unicode 字符，不会截断提交'
      return
    }
    // 不允许不确定请求变更参数后重试。
    if (pending.value && (pending.value.text !== input.text || pending.value.domain !== input.domain || pending.value.config_id !== input.config_id)) {
      error.value = '前次提交尚未确认，请用原参数重试同一请求'
      return
    }
    stop()
    busy.value = true
    const token = sequence
    controller = new AbortController()
    error.value = ''
    try {
      pending.value ??= { ...input, request_id: createCollaborationId() }
      const response = await createCollaborationApi(pending.value, controller.signal)
      if (!current(token)) return
      if (!response.task_id) throw new Error('提交响应缺少任务编号，请重试同一请求')
      snapshot.value = { task_id: response.task_id, text: pending.value.text, domain: pending.value.domain, config_id: pending.value.config_id ?? null }
      taskId.value = response.task_id
      status.value = 'PENDING'
      message.value = response.message
      pending.value = null
      await options.onQueued(response.task_id)
    } catch (cause) {
      if (!current(token)) return
      const httpStatus = (cause as { response?: { status?: number } })?.response?.status
      if (httpStatus && httpStatus >= 400 && httpStatus < 500 && httpStatus !== 408) pending.value = null
      showError(cause)
    } finally {
      if (current(token)) busy.value = false
    }
    if (current(token) && taskId.value) await reconnect()
  }

  async function cancel() {
    if (!alive || !taskId.value || terminal.value || cancelling.value || cancelRequested.value) return
    const id = taskId.value
    cancelController = new AbortController()
    cancelling.value = true
    try {
      await cancelTaskApi(id, undefined, cancelController.signal)
      if (!alive || taskId.value !== id) return
      cancelRequested.value = true
      // API 收到取消请求不等于任务已取消；继续读服务端终态，允许先完成。
    } catch (cause) {
      if (alive && taskId.value === id) showError(cause)
    } finally {
      if (alive && taskId.value === id) cancelling.value = false
    }
  }

  onBeforeUnmount(() => { alive = false; stop(); cancelController?.abort() })
  return { taskId, report, snapshot, pending, status, message, error, busy, monitoring, cancelling, cancelRequested, terminal, locked, submit, resume, reconnect, cancel, reset }
}
