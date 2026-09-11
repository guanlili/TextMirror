/**
 * TextMirror 认证 API
 */
import request from '@/utils/request'

export interface UserInfo {
  id: number
  employee_id: string
  username: string
  phone?: string
  gender?: string
  avatar?: string
  department?: string
  role_id: number
  role_name?: string
  role_code?: string
  permissions: string[]
}

export interface AuthTokens {
  access_token: string
  refresh_token?: string
  is_new_user?: boolean
  must_change_password?: boolean
}

export interface FeishuConfig {
  app_id: string
  redirect_uri: string
  enabled: boolean
}

/** 工号密码登录 */
export function loginApi(employeeId: string, password: string): Promise<AuthTokens> {
  return request.post('/auth/login', { employee_id: employeeId, password })
}

/** 一键登录（内网演示账号：admin / demo） */
export function quickLoginApi(account: 'admin' | 'demo'): Promise<AuthTokens> {
  return request.post(`/auth/quick-login?account=${account}`)
}

/** 获取当前登录用户信息 */
export function getMeApi(): Promise<UserInfo> {
  return request.get('/auth/me')
}

/** 获取飞书登录配置（无需登录） */
export function getFeishuConfigApi(): Promise<FeishuConfig> {
  return request.get('/auth/feishu/config')
}

/** 飞书授权回调：用 code 换 token */
export function feishuCallbackApi(code: string): Promise<AuthTokens> {
  return request.post('/auth/feishu/callback', { code })
}
