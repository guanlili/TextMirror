/**
 * TextMirror API 密钥管理 API
 */
import request from '@/utils/request'

export interface ApiKeyItem {
  id: number
  name: string
  key_display: string
  daily_quota: number | null
  expires_at?: string
  last_used_at?: string | null
  created_at?: string
  is_active: boolean
  status: 'active' | 'revoked' | 'expired'
  used_today: number | null
  used_7d?: number
  remark?: string | null
  webhook_url?: string | null
  webhook_last?: WebhookDelivery | null
}

export interface WebhookDelivery {
  event: string
  job_id: string
  status: 'delivered' | 'failed'
  status_code: number
  error: string
  attempt: number
  timestamp: string
}

export interface ApiKeyListResponse {
  items: ApiKeyItem[]
  total: number
}

export interface ApiKeyCreateResponse {
  id: number
  name: string
  key: string
  key_display: string
  daily_quota: number | null
  expires_at?: string
  created_at?: string
}

export interface ApiKeyCreatePayload {
  name?: string
  daily_quota?: number | null
  expires_at?: string | null
  remark?: string
}

/** 获取我的密钥列表 */
export function listApiKeysApi(): Promise<ApiKeyListResponse> {
  return request.get('/api-keys')
}

/** 创建密钥（完整明文仅返回一次） */
export function createApiKeyApi(payload: ApiKeyCreatePayload): Promise<ApiKeyCreateResponse> {
  return request.post('/api-keys', payload)
}

/** 吊销密钥 */
export function revokeApiKeyApi(id: number): Promise<void> {
  return request.delete(`/api-keys/${id}`)
}

/** 设置回调地址（每次设置轮换签名密钥，明文仅返回一次） */
export function setWebhookApi(id: number, url: string): Promise<{ url: string; secret: string }> {
  return request.put(`/api-keys/${id}/webhook`, { url })
}

/** 清除回调 */
export function clearWebhookApi(id: number): Promise<void> {
  return request.delete(`/api-keys/${id}/webhook`)
}

/** 发送测试回调 */
export function testWebhookApi(id: number): Promise<{ message: string; status_code: number }> {
  return request.post(`/api-keys/${id}/webhook/test`)
}
