/**
 * SSE（Server-Sent Events）流式请求工具
 *
 * fetch + ReadableStream：按空行分隔事件、解析 data: 行为 JSON 后逐事件回调。
 * 供 api/polish.ts 等 POST 流式接口复用。
 */

export interface SsePostOptions {
  /** 错误响应体 detail 字段 → 错误消息 的映射（缺省原样透传） */
  mapErrorDetail?: (detail: unknown) => string
}

/**
 * 以 POST 发起 SSE 流式请求
 * onEvent 逐事件回调；返回 abort 函数
 */
export function postSseStream<TEvent>(
  url: string,
  data: unknown,
  onEvent: (evt: TEvent) => void,
  options: SsePostOptions = {},
): { promise: Promise<void>; abort: () => void } {
  const controller = new AbortController()
  const token = localStorage.getItem('access_token') || ''

  const promise = (async () => {
    const resp = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(data),
      signal: controller.signal,
    })
    if (!resp.ok || !resp.body) {
      let detail = `HTTP ${resp.status}`
      try {
        const err = await resp.json()
        if (err?.detail) {
          detail = options.mapErrorDetail ? options.mapErrorDetail(err.detail) : err.detail
        }
      } catch { /* 非 JSON 错误体 */ }
      throw new Error(detail)
    }

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      // SSE 事件以空行分隔
      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''
      for (const part of parts) {
        const line = part.split('\n').find(l => l.startsWith('data: '))
        if (!line) continue
        try {
          onEvent(JSON.parse(line.slice(6)) as TEvent)
        } catch { /* 跳过非法 JSON */ }
      }
    }
  })()

  return { promise, abort: () => controller.abort() }
}
