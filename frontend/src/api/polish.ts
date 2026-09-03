/**
 * TextMirror AI润色相关 API
 */
import request from '@/utils/request'

/** 润色风格项 */
export interface PolishStyle {
  key: string
  name: string
  description: string
}

/** 润色风格列表响应 */
export interface PolishStylesResponse {
  styles: PolishStyle[]
}

/** 单个润色版本 */
export interface PolishVersion {
  label: string
  level: string
  content: string
}

/** 润色响应 */
export interface PolishResponse {
  versions: PolishVersion[]
  style: string
  style_name: string
  usage: Record<string, number>
}

/**
 * 获取所有润色风格
 */
export function getPolishStylesApi(): Promise<PolishStylesResponse> {
  return request.get('/polish/styles')
}

/**
 * AI文本润色
 */
export function textPolishApi(data: {
  text: string
  style: string
}): Promise<PolishResponse> {
  return request.post('/polish/text', data, { timeout: 300000 })
}

/** 流式对比事件 */
export interface CompareStreamEvent {
  event: 'meta' | 'delta' | 'done' | 'error' | 'end'
  config_id?: number
  config_name?: string
  model?: string
  content?: string
  message?: string
  elapsed_ms?: number
  style?: string
  style_name?: string
  models?: { config_id: number; config_name: string; model: string }[]
}

/**
 * 多模型对比润色（SSE 流式）
 * onEvent 逐事件回调；返回 abort 函数
 */
export function polishCompareStreamApi(
  data: { text: string; style: string; config_ids: number[] },
  onEvent: (evt: CompareStreamEvent) => void,
): { promise: Promise<void>; abort: () => void } {
  const controller = new AbortController()
  const token = localStorage.getItem('access_token') || ''

  const promise = (async () => {
    const resp = await fetch('/api/v1/polish/compare/stream', {
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
        if (err?.detail) detail = typeof err.detail === 'string' ? err.detail : '参数错误'
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
      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''
      for (const part of parts) {
        const line = part.split('\n').find(l => l.startsWith('data: '))
        if (!line) continue
        try {
          onEvent(JSON.parse(line.slice(6)))
        } catch { /* 跳过非法 JSON */ }
      }
    }
  })()

  return { promise, abort: () => controller.abort() }
}

/** 可用模型配置（多模型对比用，不含密钥） */
export interface AvailableModel {
  id: number
  name: string
  model: string
}

/** 获取可用于对比的已启用模型列表 */
export function getAvailableModelsApi(): Promise<{ models: AvailableModel[] }> {
  return request.get('/polish/models')
}

/** 多模型对比：单模型结果 */
export interface ModelCompareItem {
  config_id: number
  config_name: string
  model: string
  content: string
  success: boolean
  error?: string
  elapsed_ms: number
}

/** 多模型对比响应 */
export interface PolishCompareResponse {
  style: string
  style_name: string
  results: ModelCompareItem[]
}

/**
 * 多模型对比润色：同一段文本并发跑多个已配置模型
 */
export function polishCompareApi(data: {
  text: string
  style: string
  config_ids: number[]
}): Promise<PolishCompareResponse> {
  return request.post('/polish/compare', data, { timeout: 300000 })
}

/** 流式润色事件 */
export interface PolishStreamEvent {
  event: 'meta' | 'delta' | 'done' | 'error' | 'end' | 'fatal'
  level?: string
  label?: string
  content?: string
  message?: string
  style?: string
  style_name?: string
}

/**
 * AI文本润色（SSE 流式）
 * onEvent 逐事件回调；返回 abort 函数
 */
export function textPolishStreamApi(
  data: { text: string; style: string },
  onEvent: (evt: PolishStreamEvent) => void,
): { promise: Promise<void>; abort: () => void } {
  const controller = new AbortController()
  const token = localStorage.getItem('access_token') || ''

  const promise = (async () => {
    const resp = await fetch('/api/v1/polish/text/stream', {
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
        if (err?.detail) detail = err.detail
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
          onEvent(JSON.parse(line.slice(6)))
        } catch { /* 跳过非法 JSON */ }
      }
    }
  })()

  return { promise, abort: () => controller.abort() }
}
