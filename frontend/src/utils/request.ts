/**
 * TextMirror Axios 请求封装
 * 统一处理：Token注入、错误提示、响应拦截、Token自动刷新
 */
import axios, { AxiosInstance, InternalAxiosRequestConfig, AxiosResponse, AxiosError } from 'axios'
import { ElMessage } from 'element-plus'
import router from '@/router'

// 创建 Axios 实例
const request: AxiosInstance = axios.create({
  baseURL: '/api/v1',
  timeout: 120000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// ---- Token 刷新状态 ----
let isRefreshing = false
let pendingRequests: Array<(token: string) => void> = []

async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = localStorage.getItem('refresh_token')
  if (!refreshToken) return null

  // 用原生 axios 避免触发本拦截器递归
  const res = await axios.post('/api/v1/auth/refresh', {
    refresh_token: refreshToken,
  })

  const newAccessToken = res.data.access_token
  const newRefreshToken = res.data.refresh_token

  localStorage.setItem('access_token', newAccessToken)
  if (newRefreshToken) {
    localStorage.setItem('refresh_token', newRefreshToken)
  }

  return newAccessToken
}

function clearAuthAndRedirect() {
  localStorage.removeItem('access_token')
  localStorage.removeItem('refresh_token')
  ElMessage.error('登录已过期，请重新登录')
  router.push({ name: 'Login' })
}

// 请求拦截器：注入 Token
request.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = localStorage.getItem('access_token')
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// 响应拦截器：统一错误处理
request.interceptors.response.use(
  (response: AxiosResponse) => {
    return response.data
  },
  async (error: AxiosError) => {
    if (error.config?.headers?.['X-Silent-Error'] === 'true') {
      return Promise.reject(error)
    }
    if (error.response) {
      const { status, data } = error.response

      switch (status) {
        case 401: {
          const failConfig = error.config as InternalAxiosRequestConfig & { _retried?: boolean }
          if (!failConfig) {
            clearAuthAndRedirect()
            return Promise.reject(error)
          }

          // 防循环：刷新接口本身返回 401 说明 refresh token 也失效了
          if (failConfig.url?.includes('/auth/refresh')) {
            clearAuthAndRedirect()
            return Promise.reject(error)
          }

          // 没有 refresh token，直接跳登录
          if (!localStorage.getItem('refresh_token')) {
            clearAuthAndRedirect()
            return Promise.reject(error)
          }

          // 已有刷新请求在进行中，排队等待
          if (isRefreshing) {
            return new Promise((resolve) => {
              pendingRequests.push((newToken: string) => {
                failConfig.headers.Authorization = `Bearer ${newToken}`
                resolve(request(failConfig))
              })
            })
          }

          isRefreshing = true
          try {
            const newToken = await refreshAccessToken()
            if (newToken) {
              pendingRequests.forEach((cb) => cb(newToken))
              pendingRequests = []

              failConfig.headers.Authorization = `Bearer ${newToken}`
              return request(failConfig)
            }
          } catch {
            // 刷新失败，清空队列
            pendingRequests = []
          } finally {
            isRefreshing = false
          }

          clearAuthAndRedirect()
          return Promise.reject(error)
        }
        case 403:
          ElMessage.error((data as any)?.detail || '无权执行此操作')
          break
        case 404:
          ElMessage.error('请求的资源不存在')
          break
        case 422:
          ElMessage.error((data as any)?.detail?.[0]?.msg || '请求参数错误')
          break
        case 429:
          ElMessage.warning('请求过于频繁，请稍后再试')
          break
        case 500:
          ElMessage.error('服务器内部错误，请稍后再试')
          break
        case 503:
          ElMessage.error((data as any)?.detail || '服务暂时不可用，请稍后再试')
          break
        default:
          ElMessage.error((data as any)?.detail || `请求失败 (${status})`)
      }
    } else if (error.code === 'ECONNABORTED') {
      ElMessage.error('请求超时，请检查网络')
    } else {
      ElMessage.error('网络连接异常')
    }

    return Promise.reject(error)
  }
)

export default request
