/**
 * TextMirror AI润色相关 API
 */
import request from '@/utils/request'
import { postSseStream } from '@/utils/sse'

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
  sensitive_words?: string[]
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
  return postSseStream<CompareStreamEvent>('/api/v1/polish/compare/stream', data, onEvent, {
    mapErrorDetail: (detail) => (typeof detail === 'string' ? detail : '参数错误'),
  })
}

/** 可用模型配置（多模型对比/校对选模型用，不含密钥） */
export interface AvailableModel {
  id: number
  name: string
  model: string
  is_active?: boolean
}

/** 获取可用于对比/校对选择的已启用模型列表 */
export function getAvailableModelsApi(): Promise<{ models: AvailableModel[] }> {
  return request.get('/polish/models')
}

// 模型列表缓存：三个页面（文本校对/文档校对/润色）共用，避免每次进页重拉；
// 管理端改配置后调用 invalidate 立即失效
let modelsCache: { data: { models: AvailableModel[] }; at: number } | null = null
let modelsInflight: Promise<{ models: AvailableModel[] }> | null = null
const MODELS_CACHE_TTL_MS = 60_000

/** 获取可用模型列表（60s 缓存 + 并发去重） */
export function getAvailableModelsCached(): Promise<{ models: AvailableModel[] }> {
  if (modelsCache && Date.now() - modelsCache.at < MODELS_CACHE_TTL_MS) {
    return Promise.resolve(modelsCache.data)
  }
  if (!modelsInflight) {
    modelsInflight = getAvailableModelsApi()
      .then(data => {
        modelsCache = { data, at: Date.now() }
        return data
      })
      .finally(() => { modelsInflight = null })
  }
  return modelsInflight
}

/** 失效模型列表缓存（管理端增删改模型配置后调用） */
export function invalidateAvailableModelsCache(): void {
  modelsCache = null
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
  return postSseStream<PolishStreamEvent>('/api/v1/polish/text/stream', data, onEvent)
}
