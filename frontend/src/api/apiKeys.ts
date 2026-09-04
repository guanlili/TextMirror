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
  remark?: string | null
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
