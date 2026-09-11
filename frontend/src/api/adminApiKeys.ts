/**
 * TextMirror 管理后台 - API 密钥全量管理 API
 */
import request from '@/utils/request'

/** 管理端密钥列表项 */
export interface AdminApiKeyItem {
  id: number
  user_id: number
  username: string
  employee_id: string
  name: string
  key_display: string
  daily_quota: number | null
  expires_at: string | null
  last_used_at: string | null
  created_at: string | null
  is_active: boolean
  status: 'active' | 'revoked' | 'expired'
  used_today: number | null
  used_7d?: number
  remark: string | null
  webhook_url?: string | null
  webhook_last?: {
    event: string
    status: 'delivered' | 'failed'
    status_code: number
    error: string
    timestamp: string
  } | null
}

export interface AdminApiKeyListResponse {
  items: AdminApiKeyItem[]
  total: number
  page: number
  page_size: number
}

export interface AdminApiKeyListParams {
  page?: number
  page_size?: number
  keyword?: string
  key_status?: 'active' | 'revoked' | 'expired'
}

export interface AdminApiKeyUpdatePayload {
  is_active?: boolean
  daily_quota?: number | null
  remark?: string | null
}

/** 全量密钥列表 */
export function listAdminApiKeysApi(params: AdminApiKeyListParams): Promise<AdminApiKeyListResponse> {
  return request.get('/admin/api-keys', { params })
}

/** 更新密钥（吊销/恢复/调配额/改备注） */
export function updateAdminApiKeyApi(id: number, payload: AdminApiKeyUpdatePayload): Promise<{ message: string; changes: Record<string, unknown> }> {
  return request.patch(`/admin/api-keys/${id}`, payload)
}
