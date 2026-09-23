import { readFile } from 'node:fs/promises'
import { Buffer } from 'node:buffer'
import type { Locator, Page } from '@playwright/test'
import { test, expect, authTokens, source, corrected, exportedBytes, sample } from './workbench.fixture'

async function readTokens(page: Page) {
  return page.evaluate(() => ({ access: localStorage.getItem('access_token'), refresh: localStorage.getItem('refresh_token') }))
}
async function loggedOut(page: Page) {
  await expect(page).toHaveURL(/\/login$/)
  await expect(page.locator('.login-page')).toBeVisible()
  await expect.poll(() => readTokens(page)).toEqual({ access: null, refresh: null })
}

async function upload(page: Page) {
  await page.goto('/proofread/document')
  await page.locator('input[type=file]').setInputFiles({ name: '浏览器回归.txt', mimeType: 'text/plain', buffer: Buffer.from(source) })
  await page.getByRole('button', { name: '开始校对', exact: true }).click()
}
async function ready(page: Page) {
  await expect(page.locator('.review-preview .preview-text')).toBeVisible()
  await expect(page).toHaveURL(/review=7/)
}
async function workspace(page: Page) {
  await page.getByText('草稿与版本管理', { exact: true }).click()
  await expect(page.getByRole('button', { name: '保存草稿', exact: true })).toBeEnabled()
}

const preview = (page: Page) => page.locator('.review-preview .preview-text')

for (const theme of ['dark', 'light']) {
  test(`恢复持久化 ${theme} 主题且不触发脚本安全策略错误`, async ({ page, context, baseURL }) => {
    await context.addInitScript(({ value, origin }) => {
      if (window.location.origin === origin) localStorage.setItem('tm_theme', value)
    }, { value: theme, origin: new URL(baseURL!).origin })
    await page.goto('/proofread/document?review=7')
    await ready(page)
    await expect(page.locator('html')).toHaveClass(theme === 'dark' ? /\bdark\b/ : /^(?!.*\bdark\b).*$/)
  })
}

async function measureUpdate(button: Locator, expected: string): Promise<number> {
  return button.evaluate((element, text) => new Promise<number>((resolve, reject) => {
    const root = document.querySelector('.review-preview .preview-text')!
    const start = performance.now()
    const timer = window.setTimeout(() => { observer.disconnect(); reject(new Error('Preview update exceeded 5 seconds')) }, 5_000)
    const observer = new window.MutationObserver(() => {
      if (root.textContent !== text) return
      observer.disconnect()
      window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
        window.clearTimeout(timer)
        resolve(performance.now() - start)
      }))
    })
    observer.observe(root, { subtree: true, childList: true, characterData: true })
    ;(element as HTMLElement).click()
  }), expected)
}

test('上传→审校→单处修改→撤销→保存→刷新→导出下载（模拟接口）', async ({ page, scenario }) => {
  await upload(page)
  await ready(page)
  await expect(preview(page)).toHaveText(source)
  const first = page.locator('.issue-item').first()
  await first.getByRole('button', { name: '仅修改此处' }).click()
  await expect(preview(page)).toHaveText(corrected)
  await first.getByRole('button', { name: '撤销', exact: true }).click()
  await expect(preview(page)).toHaveText(source)
  await first.getByRole('button', { name: '仅修改此处' }).click()
  await workspace(page)
  await page.getByRole('button', { name: '保存草稿', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('草稿已保存')
  expect(scenario.review.modified_text).toBe(corrected)
  expect(scenario.review.issues[0]._accepted).toBe(true)
  expect(scenario.review.issues[1]._accepted).not.toBe(true)
  await page.reload()
  await ready(page)
  await expect(preview(page)).toHaveText(corrected)
  expect(scenario.calls.filter(call => call.path === '/document/proofread/async')).toHaveLength(1)
  const downloadEvent = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出 Word（纯文本）', exact: true }).click()
  const download = await downloadEvent
  expect(download.suggestedFilename()).toBe('已采纳_浏览器回归.docx')
  expect(await download.failure()).toBeNull()
  expect(await readFile((await download.path())!)).toEqual(exportedBytes)
  expect(scenario.review.modified_text).toBe(corrected)
})

test('保存冲突保留本地决策，并阻止继续覆盖服务端', async ({ page, scenario }) => {
  await upload(page)
  await ready(page)
  await page.getByRole('button', { name: '仅修改此处' }).first().click()
  await workspace(page)
  scenario.saveStatus = 409
  await page.getByRole('button', { name: '保存草稿', exact: true }).click()
  await expect(page.getByRole('alert').filter({ hasText: '保存冲突（409）' })).toBeVisible()
  await expect(preview(page)).toHaveText(corrected)
  await expect(page.getByRole('button', { name: '保存草稿', exact: true })).toBeDisabled()
  expect(scenario.review.modified_text).toBe(source)
})

test('非终态 SSE 断流后轮询恢复，不重复提交审校', async ({ page, scenario }) => {
  scenario.stream = 'disconnect'
  await upload(page)
  await ready(page)
  await expect(preview(page)).toHaveText(source)
  expect(scenario.calls.some(call => call.path === '/tasks/browser-task')).toBe(true)
  expect(scenario.calls.filter(call => call.path === '/document/proofread/async')).toHaveLength(1)
})

test('取消等待中的任务后返回上传页，不显示成功结果', async ({ page, scenario }) => {
  scenario.stream = 'pending'
  await upload(page)
  await page.getByRole('button', { name: '取消任务', exact: true }).click()
  await expect(page.getByText('文档上传校对', { exact: true })).toBeVisible()
  await expect(page.locator('.review-preview')).toHaveCount(0)
  expect(scenario.cancelled).toBe(true)
  expect(scenario.calls.filter(call => call.path === '/document/proofread/async')).toHaveLength(1)
})

test('上传被拒后不提交模型任务', async ({ page, scenario }) => {
  scenario.uploadStatus = 413
  await upload(page)
  await expect(page.getByText('文件上传失败，请重试', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '开始校对', exact: true })).toBeEnabled()
  expect(scenario.calls.some(call => call.path === '/document/proofread/async')).toBe(false)
})

test('导出失败显示错误，不生成错误响应文件', async ({ page, scenario }) => {
  await upload(page)
  await ready(page)
  scenario.exportStatus = 503
  const downloads: string[] = []
  page.on('download', download => downloads.push(download.suggestedFilename()))
  await page.getByRole('button', { name: '导出 Word（纯文本）', exact: true }).click()
  await expect(page.getByText('导出失败，未导出：模拟导出失败', { exact: true })).toBeVisible()
  expect(downloads).toEqual([])
  await expect(preview(page)).toHaveText(source)
})

test('部分完成的零问题报告不得当作全文无误，取消导出不发送保存请求', async ({ page, scenario }) => {
  scenario.review.issues = []
  scenario.review.coverage = { status: 'partial', total_chunks: 2, completed_chunks: 1,
    failed_chunks: [{ chunk_index: 1, start: 10, end: Array.from(source).length,
      text: Array.from(source).slice(10).join(''), error_code: 'timeout' }] }
  await upload(page)
  await ready(page)
  await expect(page.getByText('审校尚未完成，当前范围暂无此类问题，不代表全文无误', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '导出 Word（纯文本）', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '部分完成导出' })
  await expect(dialog).toContainText('不代表全文已校对')
  await dialog.getByRole('button', { name: '继续审阅' }).click()
  await expect(dialog).not.toBeVisible()
  expect(scenario.calls.some(call => call.method === 'PUT' || call.path === '/history/7/export')).toBe(false)
})

test('万字文章与300条问题的渲染、单处修改、撤销性能基准', async ({ page, scenario }, testInfo) => {
  scenario.review = sample(true)
  const metrics: Record<string, number | string> = {
    scope: 'mocked API; initial wall-clock load plus browser DOM-click-to-paint timings, not production P95',
    characters: Array.from(scenario.review.original_text).length, issues: scenario.review.issues.length,
  }
  expect(metrics.characters).toBeGreaterThan(10_000)
  const start = performance.now()
  await page.goto('/proofread/document?review=7')
  await ready(page)
  await expect(page.locator('.issue-item')).toHaveCount(300)
  metrics.initial_render_ms = performance.now() - start
  await expect(preview(page)).toHaveText(scenario.review.original_text)
  const item = page.locator('.issue-item').first()
  metrics.accept_ms = await measureUpdate(item.getByRole('button', { name: '仅修改此处' }),
    scenario.review.original_text.replace('第1项工做', '第1项工作'))
  metrics.undo_ms = await measureUpdate(item.getByRole('button', { name: '撤销', exact: true }), scenario.review.original_text)
  await expect(preview(page)).toHaveText(scenario.review.original_text)
  await testInfo.attach('browser-performance.json', { body: JSON.stringify(metrics, null, 2), contentType: 'application/json' })
  expect(metrics.initial_render_ms).toBeLessThan(10_000)
  expect(metrics.accept_ms).toBeLessThan(3_000)
  expect(metrics.undo_ms).toBeLessThan(3_000)
})

test('管理员取消退出保留未保存规则和令牌，保存后再次退出须确认离开', async ({ page, scenario }) => {
  scenario.admin = true
  const path = '/admin/system-config/domain-prompts'
  const saves = () => scenario.calls.filter(call => call.method === 'PUT' && call.path === path)
  await page.goto('/admin/domain-rules')
  await expect(page.locator('.account-button')).toContainText('回归测试')
  const editor = page.getByRole('textbox', { name: '自定义审校规则' })
  const save = page.getByRole('button', { name: '保存并生效', exact: true })
  const draft = '检查专有术语，保留经人工确认的例外。'
  await editor.fill(draft)
  await expect(save).toBeEnabled()
  expect(await readTokens(page)).toEqual(authTokens)

  await page.locator('.account-button').click()
  await page.getByRole('menuitem', { name: '退出登录', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '离开规则编辑', exact: true })
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('尚有未保存的规则，离开后将丢失修改。')
  await expect(page).toHaveURL(/\/admin\/domain-rules$/)
  expect(await readTokens(page)).toEqual(authTokens)
  expect(saves()).toHaveLength(0)
  await dialog.getByRole('button', { name: '继续编辑', exact: true }).click()
  await expect(dialog).not.toBeVisible()
  await expect(page).toHaveURL(/\/admin\/domain-rules$/)
  await expect(editor).toHaveValue(draft)
  expect(await readTokens(page)).toEqual(authTokens)

  await save.click()
  await expect(page.getByText('规则已保存，后续审校使用新规则', { exact: true })).toBeVisible()
  await expect(save).toBeDisabled()
  expect(saves()).toEqual([{ method: 'PUT', path, body: { general: draft, official: '', legal: '' } }])
  expect(await readTokens(page)).toEqual(authTokens)

  await editor.fill(`${draft}\n再次修改，确认离开时不保存。`)
  await expect(save).toBeEnabled()
  await page.locator('.account-button').click()
  await page.getByRole('menuitem', { name: '退出登录', exact: true }).click()
  await expect(dialog).toBeVisible()
  expect(await readTokens(page)).toEqual(authTokens)
  await dialog.getByRole('button', { name: '离开', exact: true }).click()
  await loggedOut(page)
  expect(saves()).toHaveLength(1)
})

test('管理员模型搜索支持供应商显示名、内部代码、模型与名称，并保留状态筛选', async ({ page, scenario }) => {
  scenario.admin = true
  await page.goto('/admin/llm')
  const cards = page.locator('.config-card')
  const names = cards.locator('.name-text')
  const query = page.getByRole('textbox', { name: '搜索模型服务' })
  const status = page.locator('.el-select').filter({ has: page.getByRole('combobox', { name: '模型服务状态' }) })
  const empty = page.getByText('没有匹配的模型服务', { exact: true })
  await expect(names).toHaveText(['生产模型', '停用模型'])
  await expect(cards.first().getByText('阿里百炼 (通义千问)', { exact: true })).toBeVisible()

  for (const keyword of ['阿里百炼', '通义千问', 'qwen']) {
    await query.fill(keyword)
    await expect(names).toHaveText(['生产模型', '停用模型'])
  }
  for (const keyword of ['qwen-test', '生产模型']) {
    await query.fill(keyword)
    await expect(names).toHaveText(['生产模型'])
  }
  await query.fill('不存在的模型服务')
  await expect(cards).toHaveCount(0)
  await expect(empty).toBeVisible()
  await query.clear()
  await expect(names).toHaveText(['生产模型', '停用模型'])
  await expect(empty).not.toBeVisible()

  await status.click()
  await page.getByRole('option', { name: '已停用', exact: true }).click()
  await expect(names).toHaveText(['停用模型'])
  await query.fill('阿里百炼')
  await expect(names).toHaveText(['停用模型'])
  await query.fill('qwen-test')
  await expect(cards).toHaveCount(0)
  await expect(empty).toBeVisible()
  await status.click()
  await page.getByRole('option', { name: '已启用', exact: true }).click()
  await expect(names).toHaveText(['生产模型'])
  await query.clear()
  await expect(names).toHaveText(['生产模型'])
  await status.click()
  await page.getByRole('option', { name: '全部服务', exact: true }).click()
  await expect(names).toHaveText(['生产模型', '停用模型'])

  expect(scenario.calls.map(call => `${call.method} ${call.path}`).sort()).toEqual([
    'GET /admin/llm-config', 'GET /admin/llm-config/providers', 'GET /auth/me', 'GET /site/info',
  ])
})

test('普通用户从文档审校工作区退出后清除双令牌和退出查询参数', async ({ page, scenario }) => {
  await page.goto('/proofread/document?review=7')
  await ready(page)
  await expect(page.locator('.user-info')).toContainText('回归测试')
  expect(await readTokens(page)).toEqual(authTokens)
  await page.locator('.user-info').click()
  await page.getByRole('menuitem', { name: '退出登录', exact: true }).click()
  await loggedOut(page)
  expect(scenario.calls.every(call => call.method === 'GET')).toBe(true)
})
