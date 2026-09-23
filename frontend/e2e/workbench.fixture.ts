import { test as base, expect, type Page, type Route } from '@playwright/test'
import { Buffer } from 'node:buffer'
import type { DomainPromptsConfig, LLMConfigItem, LLMProviderOption } from '../src/api/admin'
import type { ReviewResponse, SaveReviewPayload } from '../src/api/review'
import type { ProofreadCoverage } from '../src/api/proofread'
import type { ReviewIssue } from '../src/utils/review'

export const authTokens = { access: 'browser-test-not-a-real-token', refresh: 'browser-test-not-a-real-refresh-token' }
export const source = '首段😀：方案已经完膳。第二段：工做安排保持不变。'
export const corrected = source.replace('完膳', '完善')
export const exportedBytes = Buffer.from('Mock export transport fixture; DOCX generation is covered by backend tests.')

function issueAt(text: string, original: string, suggestion: string): ReviewIssue {
  const start = Array.from(text.slice(0, text.indexOf(original))).length
  return { original, suggestion, start, end: start + Array.from(original).length,
    type: 'typo', severity: 'warning', explanation: '候选错别字，请人工确认。' }
}

export function sample(large = false): ReviewResponse {
  let text = source
  let issues = [issueAt(text, '完膳', '完善'), issueAt(text, '工做', '工作')]
  if (large) {
    text = Array.from({ length: 300 }, (_, i) => `第${i + 1}项工做将在本周完成。负责人需要逐项记录执行情况，并与相关部门核对材料，尚未确认的事项留待下次会议讨论。\n`).join('')
    issues = Array.from({ length: 300 }, (_, i) => issueAt(text, `第${i + 1}项工做`, `第${i + 1}项工作`))
  }
  return { record_id: 7, revision: 0, original_text: text, modified_text: text,
    source_file_id: 'browser-file', source_filename: '浏览器回归.txt', domain: 'general', depth: 'standard',
    config_id: null, issues, versions: [],
    coverage: { status: 'complete', total_chunks: 1, completed_chunks: 1, failed_chunks: [] } }
}

function appliedText(text: string, issues: ReviewIssue[]): string {
  const chars = Array.from(text)
  for (const issue of issues.filter(item => item._accepted).sort((a, b) => b.start! - a.start!)) {
    expect(chars.slice(issue.start!, issue.end!).join('')).toBe(issue.original)
    chars.splice(issue.start!, issue.end! - issue.start!, ...Array.from(issue.suggestion || ''))
  }
  return chars.join('')
}

interface ApiCall { method: string; path: string; body: unknown }
export interface Scenario {
  admin: boolean
  review: ReviewResponse
  uploadStatus: number
  saveStatus: number
  exportStatus: number
  stream: 'complete' | 'disconnect' | 'pending'
  cancelled: boolean
  calls: ApiCall[]
}

export const test = base.extend<{ scenario: Scenario }>({
  scenario: [async ({ context, baseURL }, use) => {
    const scenario: Scenario = { admin: false, review: sample(), uploadStatus: 200, saveStatus: 200,
      exportStatus: 200, stream: 'complete', cancelled: false, calls: [] }
    let domainPrompts: DomainPromptsConfig = { general: '', official: '', legal: '' }
    const unexpected: string[] = []
    const pageErrors: string[] = []
    const monitor = (page: Page) => {
      page.on('pageerror', error => pageErrors.push(error.message))
      page.on('console', message => {
        if (message.type() === 'error' && !/^Failed to load resource: the server responded with a status of (409|413|503)\b/.test(message.text())) {
          pageErrors.push(message.text())
        }
      })
    }
    context.pages().forEach(monitor)
    context.on('page', monitor)
    await context.addInitScript(({ origin, tokens }) => {
      if (window.location.origin !== origin) return
      localStorage.setItem('access_token', tokens.access)
      localStorage.setItem('refresh_token', tokens.refresh)
    }, { origin: new URL(baseURL!).origin, tokens: authTokens })
    const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, json: body })
    await context.route('**/*', async route => {
      const request = route.request()
      const url = new URL(request.url())
      if (url.origin !== new URL(baseURL!).origin) {
        unexpected.push(`external: ${request.method()} ${url.origin}${url.pathname}`)
        return route.abort('blockedbyclient')
      }
      if (!url.pathname.startsWith('/api/')) {
        if (request.method() === 'GET') return route.continue()
        unexpected.push(`non-API write: ${request.method()} ${url.pathname}`)
        return route.abort('blockedbyclient')
      }
      const path = url.pathname.replace('/api/v1', '')
      const method = request.method()
      const body = request.headers()['content-type']?.includes('application/json') ? request.postDataJSON() : null
      scenario.calls.push({ method, path, body })
      if (method === 'GET' && path === '/site/info') return json(route, {
        platform_name: 'TextMirror', platform_subtitle: '浏览器离线回归', favicon_url: '',
        guest_mode_enabled: 'on', quick_login_enabled: 'off',
      })
      if (method === 'GET' && path === '/auth/me') return json(route, {
        id: 1, employee_id: 'browser-test', username: '回归测试', role_code: scenario.admin ? 'super_admin' : 'user',
        permissions: scenario.admin ? ['admin:access', 'admin:settings:edit', 'admin:llm:edit']
          : ['proofread:text', 'proofread:document'], daily_quota: 100,
      })
      if (method === 'GET' && path === '/auth/feishu/config') return json(route, {
        app_id: '', redirect_uri: '', enabled: false,
      })
      if (scenario.admin) {
        if (method === 'GET' && path === '/admin/system-config/domain-prompts') return json(route, domainPrompts)
        if (method === 'PUT' && path === '/admin/system-config/domain-prompts') {
          expect(request.headers().authorization).toBe(`Bearer ${authTokens.access}`)
          domainPrompts = body as DomainPromptsConfig
          return json(route, domainPrompts)
        }
        if (method === 'GET' && path === '/admin/system-config/domain-prompts/defaults') return json(route, {
          general: '检查文字与语法。', official: '检查公文措辞。', legal: '检查法律术语。',
        } satisfies DomainPromptsConfig)
        if (method === 'GET' && path === '/admin/llm-config/providers') return json(route, [{
          code: 'qwen', name: '阿里百炼 (通义千问)', default_base: 'https://model.invalid/v1',
          default_model: 'qwen-test', models: ['qwen-test'],
        }] satisfies LLMProviderOption[])
        if (method === 'GET' && path === '/admin/llm-config') {
          const config: LLMConfigItem = { id: 1, name: '生产模型', provider: 'qwen',
            api_base: 'https://model.invalid/v1', api_key: '', api_key_masked: '未配置', model: 'qwen-test',
            temperature: 0.3, timeout: 60, max_retries: 0, is_active: true, is_enabled: true }
          return json(route, [config, { ...config, id: 2, name: '停用模型', model: 'archived-test',
            is_active: false, is_enabled: false }])
        }
      }
      if (method === 'GET' && path === '/polish/models') return json(route, { models: [] })
      if (method === 'GET' && path === '/history/usage') return json(route, { used_today: 0, daily_quota: 100 })
      if (method === 'POST' && path === '/document/upload') {
        expect(request.postDataBuffer()?.toString()).toContain('浏览器回归.txt')
        return json(route, scenario.uploadStatus === 200 ? {
          file_id: 'browser-file', filename: '浏览器回归.txt', file_size: 100, file_ext: '.txt',
          text_length: Array.from(scenario.review.original_text).length,
          text_preview: scenario.review.original_text.slice(0, 80),
        } : { detail: '文件超过允许大小' }, scenario.uploadStatus)
      }
      if (method === 'GET' && path === '/document/browser-file/extracted-text') {
        return json(route, { file_id: 'browser-file', extracted_text: scenario.review.original_text, extracted_html: '' })
      }
      if (method === 'POST' && path === '/document/proofread/async') {
        expect(body.file_id).toBe('browser-file')
        expect(request.headers()['idempotency-key']).toBeTruthy()
        return json(route, { task_id: 'browser-task', message: '已提交模拟任务' })
      }
      if (method === 'POST' && path === '/tasks/browser-task/cancel') {
        scenario.cancelled = true
        return json(route, { message: '已取消' })
      }
      if (method === 'GET' && ['/tasks/browser-task/stream', '/tasks/browser-task'].includes(path)) {
        const pending = scenario.stream === 'pending' && !scenario.cancelled
        const status = { task_id: 'browser-task', status: scenario.cancelled ? 'CANCELLED' : pending ? 'PROGRESS' : 'SUCCESS',
          progress: pending ? 50 : 100, message: pending ? '正在模拟审校' : '模拟任务完成',
          result: { ...scenario.review, file_id: 'browser-file', filename: '浏览器回归.txt', total_issues: scenario.review.issues.length } }
        if (path.endsWith('/stream')) {
          return route.fulfill({ contentType: 'text/event-stream', body: `data: ${JSON.stringify(
            scenario.stream === 'complete' ? status : { ...status, status: 'PROGRESS', progress: 50 },
          )}\n\n` })
        }
        return json(route, status)
      }
      if (method === 'GET' && path === '/history/7/review') return json(route, scenario.review)
      if (method === 'PUT' && path === '/history/7/review') {
        if (scenario.saveStatus !== 200) return json(route, { detail: '模拟保存失败' }, scenario.saveStatus)
        const payload = body as SaveReviewPayload
        expect(payload.revision).toBe(scenario.review.revision)
        scenario.review = { ...scenario.review, revision: scenario.review.revision + 1,
          issues: payload.issues, coverage: payload.coverage as ProofreadCoverage,
          modified_text: appliedText(scenario.review.original_text, payload.issues) }
        return json(route, scenario.review)
      }
      if (method === 'POST' && path === '/history/7/export') {
        expect(body).toEqual({ revision: scenario.review.revision, format: 'docx' })
        if (scenario.exportStatus !== 200) return json(route, { detail: '模拟导出失败' }, scenario.exportStatus)
        return route.fulfill({ contentType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', body: exportedBytes })
      }
      if (method === 'POST' && path === '/proofread/feedback') return json(route, { saved: body.items.length })
      unexpected.push(`${method} ${path}`)
      return json(route, { detail: 'No real API access is allowed in browser tests' }, 501)
    })
    await use(scenario)
    expect(unexpected, 'Unmocked requests must never reach backend or model APIs').toEqual([])
    expect(pageErrors, 'Uncaught browser errors').toEqual([])
  }, { auto: true }],
})
export { expect } from '@playwright/test'
