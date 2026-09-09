/**
 * TextMirror 用户状态管理
 * 管理登录态、用户信息、权限列表
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { loginApi, getMeApi, type UserInfo } from '@/api/auth'

export const useUserStore = defineStore('user', () => {
  // 状态
  const token = ref<string>(localStorage.getItem('access_token') || '')
  const userInfo = ref<UserInfo | null>(null)
  const permissions = ref<string[]>([])

  // 计算属性
  const isLoggedIn = computed(() => !!token.value)
  const isSuperAdmin = computed(() => userInfo.value?.role_code === 'super_admin')

  // 登录
  async function login(employeeId: string, password: string) {
    const res = await loginApi(employeeId, password)
    token.value = res.access_token
    localStorage.setItem('access_token', res.access_token)
    if (res.refresh_token) {
      localStorage.setItem('refresh_token', res.refresh_token)
    }
    // 登录后获取用户信息
    await fetchUserInfo()
    return res
  }

  // 获取用户信息
  async function fetchUserInfo() {
    const res = await getMeApi()
    userInfo.value = res
    permissions.value = res.permissions || []
    return res
  }

  // 登出
  function logout() {
    token.value = ''
    userInfo.value = null
    permissions.value = []
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
  }

  // 检查是否有某个权限
  function hasPermission(permissionCode: string): boolean {
    if (isSuperAdmin.value) return true
    return permissions.value.includes(permissionCode)
  }

  return {
    token,
    userInfo,
    permissions,
    isLoggedIn,
    isSuperAdmin,
    login,
    fetchUserInfo,
    logout,
    hasPermission,
  }
})
