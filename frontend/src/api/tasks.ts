/**
 * TextMirror 异步任务 API
 *
 * 轮询：递归 setTimeout 单飞 + 指数退避 + AbortSignal + 总 deadline
 * SSE：fetch + ReadableStream，支持 Authorization 头 + access_token 查询参数
 */
import request from '@/utils/request'

export interface TaskStatus {
  task_id: string
  status: string
  progress: number
  message: string
  step?: string
  result?: any
  error?: string
}

export interface SubmitResponse {
  task_id: string
  message: string
  access_token?: string
}

/** 查询任务状态（支持游客 access_token） */
export function getTaskStatusApi(
  taskId: string,
  accessToken?: string,
  signal?: AbortSignal,
): Promise<TaskStatus> {
  const params: Record<string, string> = {}
  if (accessToken) params.access_token = accessToken
  return request.get(`/tasks/${taskId}`, {
    params,
    signal,
    headers: { 'X-Silent-Error': 'true' },
  })
}

/** 提交异步文档校对 */
export function asyncDocumentProofreadApi(
  data: {
    file_id: string
    check_types?: string[]
    domain?: string
    config_id?: number
  },
  options: {
    idempotencyKey?: string
    signal?: AbortSignal
  } = {},
): Promise<SubmitResponse> {
  const headers: Record<string, string> = {}
  if (options.idempotencyKey) headers['Idempotency-Key'] = options.idempotencyKey
  return request.post('/document/proofread/async', data, {
    headers,
    signal: options.signal,
  })
}

/** 取消任务 */
export function cancelTaskApi(
  taskId: string,
  accessToken?: string,
  signal?: AbortSignal,
): Promise<void> {
  const params: Record<string, string> = {}
  if (accessToken) params.access_token = accessToken
  return request.post(`/tasks/${taskId}/cancel`, null, { params, signal })
}

const TERMINAL_STATUSES = new Set(['SUCCESS', 'FAILURE', 'REVOKED', 'CANCELLED'])

/**
 * 轮询任务状态（递归 setTimeout，单飞 + 指数退避）
 */
export function pollTaskStatus(
  taskId: string,
  onProgress?: (status: TaskStatus) => void,
  options: {
    accessToken?: string
    signal?: AbortSignal
    initialInterval?: number
    maxInterval?: number
    deadline?: number
  } = {},
): Promise<TaskStatus> {
  const {
    accessToken,
    signal,
    initialInterval = 1000,
    maxInterval = 5000,
    deadline = 30 * 60 * 1000,
  } = options

  return new Promise((resolve, reject) => {
    const startTime = Date.now()
    let interval = initialInterval
    let inFlight = false

    function tick() {
      if (signal?.aborted) {
        reject(new DOMException('Aborted', 'AbortError'))
        return
      }
      if (Date.now() - startTime > deadline) {
        reject(new Error('任务轮询超时'))
        return
      }
      if (inFlight) {
        setTimeout(tick, 200)
        return
      }

      inFlight = true
      getTaskStatusApi(taskId, accessToken, signal).then((status) => {
        inFlight = false
        if (signal?.aborted) {
          reject(new DOMException('Aborted', 'AbortError'))
          return
        }
        onProgress?.(status)

        if (TERMINAL_STATUSES.has(status.status)) {
          resolve(status)
          return
        }

        interval = Math.min(interval * 1.5, maxInterval)
        setTimeout(tick, interval)
      }).catch((err) => {
        inFlight = false
        if (signal?.aborted || err?.name === 'AbortError' || err?.code === 'ERR_CANCELED') {
          reject(new DOMException('Aborted', 'AbortError'))
          return
        }
        interval = Math.min(interval * 2, maxInterval)
        setTimeout(tick, interval)
      })
    }

    tick()
  })
}

/**
 * SSE 订阅任务状态；非终态断流或连接失败时降级为轮询。
 */
export async function streamTaskStatus(
  taskId: string,
  onProgress?: (status: TaskStatus) => void,
  options: {
    accessToken?: string
    signal?: AbortSignal
  } = {},
): Promise<TaskStatus> {
  const { accessToken, signal } = options
  const token = localStorage.getItem('access_token') || ''
  const params = new URLSearchParams()
  if (token) params.set('token', token)
  if (accessToken) params.set('access_token', accessToken)

  const url = `/api/v1/tasks/${taskId}/stream${params.toString() ? `?${params}` : ''}`
  const headers: Record<string, string> = {}
  if (token) headers.Authorization = `Bearer ${token}`

  const ctrl = new AbortController()
  const onAbort = () => ctrl.abort()
  signal?.addEventListener('abort', onAbort)

  try {
    const response = await fetch(url, { headers, signal: ctrl.signal })
    if (!response.ok) throw new Error(`SSE 连接失败: ${response.status}`)

    const reader = response.body?.getReader()
    if (!reader) throw new Error('ReadableStream 不可用')

    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (line.startsWith(': ') || !line.startsWith('data: ')) continue
        const jsonStr = line.slice(6).trim()
        if (!jsonStr) continue
        try {
          const status: TaskStatus = JSON.parse(jsonStr)
          if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
          onProgress?.(status)
          if (TERMINAL_STATUSES.has(status.status)) {
            await reader.cancel()
            return status
          }
        } catch (err: any) {
          if (err?.name === 'AbortError') throw err
        }
      }
    }

    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
    return await pollTaskStatus(taskId, onProgress, { accessToken, signal })
  } catch (err: any) {
    if (signal?.aborted || err?.name === 'AbortError') {
      throw new DOMException('Aborted', 'AbortError')
    }
    return await pollTaskStatus(taskId, onProgress, { accessToken, signal })
  } finally {
    signal?.removeEventListener('abort', onAbort)
  }
}
