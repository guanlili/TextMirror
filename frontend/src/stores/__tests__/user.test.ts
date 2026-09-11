import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/auth', () => ({
  loginApi: vi.fn(),
  getMeApi: vi.fn(),
}))

import { loginApi, getMeApi, type UserInfo } from '@/api/auth'
import { useUserStore } from '../user'

const mockedLogin = vi.mocked(loginApi)
const mockedGetMe = vi.mocked(getMeApi)

function stubLocalStorage() {
  const store: Record<string, string> = {}
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => store[k] ?? null,
    setItem: (k: string, v: string) => { store[k] = v },
    removeItem: (k: string) => { delete store[k] },
  })
}

const me: UserInfo = {
  id: 1,
  employee_id: 'admin',
  username: '管理员',
  role_id: 1,
  role_name: '超级管理员',
  role_code: 'super_admin',
  is_active: true,
  permissions: ['admin:access'],
}

describe('useUserStore', () => {
  beforeEach(() => {
    stubLocalStorage()
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('login 存 token 并拉取用户信息', async () => {
    mockedLogin.mockResolvedValue({
      access_token: 'at-1',
      refresh_token: 'rt-1',
      must_change_password: false,
    })
    mockedGetMe.mockResolvedValue(me)

    const store = useUserStore()
    const res = await store.login('admin', 'pw')

    expect(res.access_token).toBe('at-1')
    expect(store.token).toBe('at-1')
    expect(store.isLoggedIn).toBe(true)
    expect(store.userInfo?.username).toBe('管理员')
    expect(localStorage.getItem('access_token')).toBe('at-1')
  })

  it('logout 清空登录态', async () => {
    mockedLogin.mockResolvedValue({ access_token: 'at-2' })
    mockedGetMe.mockResolvedValue(me)
    const store = useUserStore()
    await store.login('admin', 'pw')
    store.logout()

    expect(store.token).toBe('')
    expect(store.isLoggedIn).toBe(false)
    expect(store.userInfo).toBeNull()
    expect(localStorage.getItem('access_token')).toBeNull()
  })

  it('hasPermission：超管直通、普通按权限列表', async () => {
    mockedLogin.mockResolvedValue({ access_token: 'at-3' })
    mockedGetMe.mockResolvedValue(me)
    const admin = useUserStore()
    await admin.login('admin', 'pw')
    expect(admin.hasPermission('anything:else')).toBe(true)

    mockedLogin.mockResolvedValue({ access_token: 'at-4' })
    mockedGetMe.mockResolvedValue({ ...me, role_code: 'user', permissions: ['proofread:text'] })
    const user = useUserStore()
    await user.login('emp1', 'pw')
    expect(user.hasPermission('proofread:text')).toBe(true)
    expect(user.hasPermission('admin:access')).toBe(false)
  })
})
