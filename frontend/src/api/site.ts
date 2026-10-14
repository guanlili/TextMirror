/**
 * TextMirror 站点配置 API
 */
import request from '@/utils/request'

export interface SiteConfig {
  platform_name: string
  platform_subtitle: string
  favicon_url: string
  login_slogan?: string
  footer_text?: string
  guest_mode_enabled?: string
  quick_login_enabled?: string
}

/** 获取站点公开配置（无需登录）。短超时：后端不可达时快速降级默认值，避免导航守卫长挂白屏 */
export function getSiteInfoApi(): Promise<SiteConfig> {
  return request.get('/site/info', { timeout: 8000 })
}

/** 管理员获取站点配置 */
export function getAdminSiteConfigApi(): Promise<SiteConfig> {
  return request.get('/admin/settings/site')
}

/** 管理员更新站点配置 */
export function updateAdminSiteConfigApi(data: Partial<SiteConfig>): Promise<SiteConfig> {
  return request.put('/admin/settings/site', data)
}

/** 管理员上传图标文件 */
export function uploadIconApi(file: File): Promise<{ url: string; filename: string }> {
  const formData = new FormData()
  formData.append('file', file)
  return request.post('/admin/settings/upload-icon', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}
