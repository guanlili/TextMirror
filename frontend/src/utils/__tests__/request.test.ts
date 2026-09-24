import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import axios, { AxiosError, AxiosHeaders, type AxiosAdapter, type AxiosResponse } from 'axios'

vi.mock('element-plus', () => ({ ElMessage: { error: vi.fn(), warning: vi.fn() } }))
vi.mock('@/router', () => ({ default: { push: vi.fn() } }))

import { ElMessage } from 'element-plus'
import router from '@/router'
import request from '../request'

const paths = ['/protected/one', '/protected/two', '/protected/three']
const originalAdapter = request.defaults.adapter
const errors = new Map<string, AxiosError>()
// Keep real Axios request/response interceptors; mock only network transport.
const adapter = vi.fn<AxiosAdapter>(async config => {
  if (config.headers.Authorization === 'Bearer old-access') {
    const response = { config, status: 401, statusText: 'Unauthorized', headers: {}, data: {} }
    const error = new AxiosError('Unauthorized', 'ERR_BAD_REQUEST', config, undefined, response)
    errors.set(config.url!, error)
    throw error
  }
  return { config, status: 200, statusText: 'OK', headers: {}, data: { url: config.url } }
})

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}

function refreshResponse(accessToken?: string): AxiosResponse {
  return {
    data: { access_token: accessToken, refresh_token: 'new-refresh' },
    status: 200, statusText: 'OK', headers: {}, config: { headers: new AxiosHeaders() },
  }
}

function startBatch() {
  // Attach rejection handlers before allowing any transport to fail.
  return Promise.allSettled(paths.map(path => request.get(path)))
}

async function waitForRefresh() {
  await vi.waitFor(() => {
    expect(adapter).toHaveBeenCalledTimes(paths.length)
    expect(axios.post).toHaveBeenCalledOnce()
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  errors.clear()
  const storage = new Map<string, string>([
    ['access_token', 'old-access'], ['refresh_token', 'old-refresh'],
  ])
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => { storage.set(key, String(value)) },
    removeItem: (key: string) => { storage.delete(key) },
  })
  request.defaults.adapter = adapter
  vi.spyOn(axios, 'post')
})
afterEach(() => {
  request.defaults.adapter = originalAdapter
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('concurrent 401 refresh', () => {
  it('refreshes once, replays every request with the new token and fulfills all promises', async () => {
    const refresh = deferred<AxiosResponse>()
    vi.mocked(axios.post).mockReturnValueOnce(refresh.promise)
    const settled = startBatch()
    await waitForRefresh()
    expect(axios.post).toHaveBeenCalledExactlyOnceWith('/api/v1/auth/refresh', { refresh_token: 'old-refresh' })
    refresh.resolve(refreshResponse('new-access'))
    expect(await settled).toEqual(paths.map(url => ({ status: 'fulfilled', value: { url } })))
    expect(adapter).toHaveBeenCalledTimes(paths.length * 2)
    for (const [config] of adapter.mock.calls.slice(paths.length)) {
      expect(config.headers.Authorization).toBe('Bearer new-access')
      expect(config).toMatchObject({ _retried: true })
    }
    expect(localStorage.getItem('access_token')).toBe('new-access')
    expect(localStorage.getItem('refresh_token')).toBe('new-refresh')
    expect(axios.post).toHaveBeenCalledOnce()
    expect(router.push).not.toHaveBeenCalled()
    expect(ElMessage.error).not.toHaveBeenCalled()
  })

  it.each(['reject', 'missing-token'] as const)('%s settles all failures, redirects once and permits a later refresh', async (failure) => {
    const refresh = deferred<AxiosResponse>()
    vi.mocked(axios.post).mockReturnValueOnce(refresh.promise)
    const settled = startBatch()
    await waitForRefresh()
    if (failure === 'reject') refresh.reject(new Error('Refresh failed'))
    else refresh.resolve(refreshResponse())
    expect(await settled).toEqual(paths.map(path => ({ status: 'rejected', reason: errors.get(path) })))
    expect(adapter).toHaveBeenCalledTimes(paths.length)
    expect(axios.post).toHaveBeenCalledOnce()
    expect(router.push).toHaveBeenCalledExactlyOnceWith({ name: 'Login' })
    expect(ElMessage.error).toHaveBeenCalledExactlyOnceWith('登录已过期，请重新登录')
    expect(localStorage.getItem('access_token')).toBeNull()
    expect(localStorage.getItem('refresh_token')).toBeNull()

    // A new login/session must not inherit a stuck refresh flag or stale queue.
    localStorage.setItem('access_token', 'old-access')
    localStorage.setItem('refresh_token', 'retry-refresh')
    const retry = deferred<AxiosResponse>()
    vi.mocked(axios.post).mockReturnValueOnce(retry.promise)
    const retried = startBatch()
    await vi.waitFor(() => {
      expect(adapter).toHaveBeenCalledTimes(paths.length * 2)
      expect(axios.post).toHaveBeenCalledTimes(2)
    })
    expect(axios.post).toHaveBeenLastCalledWith('/api/v1/auth/refresh', { refresh_token: 'retry-refresh' })
    retry.resolve(refreshResponse('retry-access'))
    expect(await retried).toEqual(paths.map(url => ({ status: 'fulfilled', value: { url } })))
    expect(adapter).toHaveBeenCalledTimes(paths.length * 3)
    expect(axios.post).toHaveBeenCalledTimes(2)
    expect(localStorage.getItem('access_token')).toBe('retry-access')
    expect(router.push).toHaveBeenCalledOnce()
    expect(ElMessage.error).toHaveBeenCalledOnce()
  }, 1500)
})
