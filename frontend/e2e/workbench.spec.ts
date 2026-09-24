import { readFile } from 'node:fs/promises'
import { Buffer } from 'node:buffer'
import type { Locator, Page } from '@playwright/test'
import { test, expect, authTokens, source, corrected, exportedBytes, sample, factRun, holdApi } from './workbench.fixture'

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
  await expect(page.locator('.issue-item')).toHaveCount(100)
  metrics.initial_render_ms = performance.now() - start
  const loadMore = page.getByRole('button', { name: /加载更多/ })
  await expect(loadMore).toContainText('剩余 200 项')
  await loadMore.click()
  await expect(page.locator('.issue-item')).toHaveCount(200)
  await expect(loadMore).toContainText('剩余 100 项')
  await loadMore.click()
  await expect(page.locator('.issue-item')).toHaveCount(300)
  await expect(loadMore).toHaveCount(0)
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

test('润色首段输出前离开页面，不发起同步回退请求', async ({ page, scenario }) => {
  let releaseStream!: () => void
  let notifyStarted!: () => void
  const released = new Promise<void>(resolve => { releaseStream = resolve })
  const started = new Promise<void>(resolve => { notifyStarted = resolve })
  await page.route('**/api/v1/polish/text/stream', async route => {
    notifyStarted()
    await released
    await route.abort('aborted')
  })
  try {
    await page.goto('/polish')
    await page.getByPlaceholder('请在此粘贴或输入需要润色的文本内容（10-5000字）...').fill(source)
    await page.getByRole('button', { name: '一键润色', exact: true }).click()
    await started
    const cancelled = page.waitForEvent('requestfailed', request => request.url().endsWith('/polish/text/stream'))
    await page.getByRole('menuitem', { name: '新建审校', exact: true }).click()
    await expect(page).toHaveURL(/\/proofread\/text$/)
    await cancelled
    expect(scenario.calls.filter(call => call.path === '/polish/text')).toEqual([])
  } finally {
    releaseStream()
  }
})


test('润色正常流式生成三个版本，不调用同步回退', async ({ page, scenario }) => {
  const versions = ['light', 'standard', 'deep'].map(level => ({ level, content: `已生成${level}版本正文。` }))
  await page.route('**/api/v1/polish/text/stream', route => route.fulfill({
    contentType: 'text/event-stream',
    body: [
      { event: 'meta', style: 'formal', style_name: '正式规范' },
      ...versions.flatMap(version => [{ event: 'delta', ...version }, { event: 'done', ...version }]),
      { event: 'end' },
    ].map(event => `data: ${JSON.stringify(event)}\n\n`).join(''),
  }))
  await page.goto('/polish')
  await page.getByPlaceholder('请在此粘贴或输入需要润色的文本内容（10-5000字）...').fill(source)
  await page.getByRole('button', { name: '一键润色', exact: true }).click()
  for (const version of versions) await expect(page.getByText(version.content, { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '一键润色', exact: true })).toBeEnabled()
  expect(scenario.calls.filter(call => call.path === '/polish/text')).toEqual([])
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

const factInput = (page: Page) => page.getByRole('textbox', { name: '待核查文本', exact: true })
const factSubmit = (page: Page) => page.getByRole('button', { name: /^(开始事实核查|重试提交（同一请求）)$/ })
const factError = (page: Page) => page.getByTestId('fact-workbench').getByRole('alert')

async function factText(page: Page, text: string) {
  const radio = page.getByRole('radio', { name: '粘贴文本', exact: true })
  await page.locator('label').filter({ has: radio }).click()
  await expect(radio).toBeChecked()
  await expect(factInput(page)).toBeEnabled()
  await factInput(page).fill(text)
  const consent = page.getByRole('checkbox', { name: '同意材料外发', exact: true })
  await page.locator('label').filter({ has: consent }).click()
  await expect(consent).toBeChecked()
  await expect(factSubmit(page)).toBeEnabled()
}
async function factUpload(page: Page) {
  const radio = page.getByRole('radio', { name: '上传文档', exact: true })
  await page.locator('label').filter({ has: radio }).click()
  await expect(radio).toBeChecked()
  await page.locator('#fact-file').setInputFiles({ name: '浏览器回归.txt', mimeType: 'text/plain', buffer: Buffer.from(source) })
}
async function factHistory(page: Page) {
  // SPA navigation reuses the workbench instance; page.goto would hide the epoch regression.
  await page.locator('.history-item').filter({ hasText: '模拟核查 41' }).click()
  await expect(page).toHaveURL(/\/fact-check\/41$/)
  await expect(page.getByRole('heading', { name: '模拟核查 41', exact: true })).toBeVisible()
  for (const name of ['刷新状态', '导出 JSON', '清理材料与证据', '保存复核意见']) {
    await expect(page.getByRole('button', { name, exact: true })).toBeEnabled()
  }
}

// These cases only use the fixture's allowlisted APIs; no search or LLM is contacted.
test('事实核查：上传并提取文本后可提交文档任务', async ({ page, scenario }) => {
  scenario.factCheck = true
  await page.goto('/fact-check')
  await factUpload(page)
  await expect(page.getByText(`浏览器回归.txt · 已提取 ${Array.from(source).length} 字符`, { exact: true })).toBeVisible()
  await expect(page.locator('#fact-file')).toBeEnabled()
  const consent = page.getByRole('checkbox', { name: '同意材料外发', exact: true })
  await page.locator('label').filter({ has: consent }).click()
  await expect(consent).toBeChecked()
  await expect(factSubmit(page)).toBeEnabled()
  await factSubmit(page).click()
  await expect(page).toHaveURL(/\/fact-check\/42$/)
  await expect(page.getByRole('button', { name: '刷新状态', exact: true })).toBeEnabled()
  const creates = scenario.calls.filter(call => call.method === 'POST' && call.path === '/fact-check/runs')
  expect(creates).toHaveLength(1)
  expect(creates[0].body).toMatchObject({ file_id: 'browser-file', allow_external_search: true, confirm_claims: false })
  expect(scenario.calls.some(call => call.path === '/document/browser-file/extracted-text')).toBe(true)
  expect(scenario.calls.some(call => call.path === '/document/proofread/async')).toBe(false)
})

for (const phase of ['upload', 'extract'] as const) {
  test(`事实核查：${phase}失败后恢复操作且不提交未提取的文档`, async ({ page, scenario }) => {
    scenario.factCheck = true
    const failed = await holdApi(page, phase === 'upload' ? '/document/upload' : '/document/browser-file/extracted-text', phase === 'upload' ? 'POST' : 'GET')
    try {
      await page.goto('/fact-check')
      await factUpload(page)
      await failed.started
      await expect(page.locator('#fact-file')).toBeDisabled()
      await failed.respond({ detail: '材料处理失败' }, 503)
      await expect(factError(page)).toContainText('材料处理失败')
      await expect(page.locator('#fact-file')).toBeEnabled()
      const consent = page.getByRole('checkbox', { name: '同意材料外发', exact: true })
      await page.locator('label').filter({ has: consent }).click()
      await expect(consent).toBeChecked()
      await expect(factSubmit(page)).toBeDisabled()
      expect(scenario.calls.some(call => call.method === 'POST' && call.path === '/fact-check/runs')).toBe(false)
      await page.locator('label').filter({ has: consent }).click()
      await factText(page, '改用文本输入，重新提交需要核查的材料。')
      await factSubmit(page).click()
      await expect(page).toHaveURL(/\/fact-check\/42$/)
      await expect(page.getByRole('button', { name: '刷新状态', exact: true })).toBeEnabled()
    } finally {
      await failed.dispose()
    }
  })

  for (const outcome of ['resolve', 'reject'] as const) {
    test(`事实核查：${phase}挂起时切历史→详情→新建，旧请求${outcome}不影响新提交`, async ({ page, scenario }) => {
      scenario.factCheck = true
      const path = phase === 'upload' ? '/document/upload' : '/document/browser-file/extracted-text'
      const old = await holdApi(page, path, phase === 'upload' ? 'POST' : 'GET')
      const current = await holdApi(page, '/fact-check/runs')
      try {
        await page.goto('/fact-check')
        await factUpload(page)
        const oldRoute = await old.started
        await expect(page.locator('#fact-file')).toBeDisabled()
        await expect(factSubmit(page)).toBeDisabled()
        const aborted = phase === 'upload' ? page.waitForEvent('requestfailed', request => request === oldRoute.request()) : Promise.resolve()
        await factHistory(page)
        await aborted
        await page.getByRole('button', { name: '新建核查', exact: true }).click()
        const fresh = '这是新页面独立输入的事实，不能被旧文档提取结果替换。'
        await factText(page, fresh)
        await factSubmit(page).click()
        await current.started
        await expect(factInput(page)).toBeDisabled()

        const response = phase === 'upload' ? { file_id: 'stale-file', filename: '旧文档.txt' }
          : { file_id: 'browser-file', extracted_text: '过期提取结果', extracted_html: '' }
        await old.respond(outcome === 'resolve' ? response : { detail: '旧请求模拟失败' }, outcome === 'resolve' ? 200 : 503, phase === 'upload')
        await expect(page).toHaveURL(/\/fact-check$/)
        await expect(factInput(page)).toHaveValue(fresh)
        await expect(factInput(page)).toBeDisabled()
        await expect(factSubmit(page)).toBeDisabled()
        await expect(factError(page)).toHaveCount(0)
        expect((await current.started).request().postDataJSON()).toMatchObject({ text: fresh })
        expect(scenario.calls.some(call => call.path === '/document/stale-file/extracted-text')).toBe(false)

        await current.respond(factRun(42))
        await expect(page).toHaveURL(/\/fact-check\/42$/)
        await expect(page.getByRole('button', { name: '刷新状态', exact: true })).toBeEnabled()
      } finally {
        await old.dispose()
        await current.dispose()
      }
    })
  }
}

for (const outcome of ['resolve', 'reject'] as const) {
  test(`事实核查：旧创建请求${outcome}不导航、不清除新创建的busy或重试编号`, async ({ page, scenario }) => {
    scenario.factCheck = true
    const old = await holdApi(page, '/fact-check/runs')
    let current: Awaited<ReturnType<typeof holdApi>> | undefined
    try {
      await page.goto('/fact-check')
      await factText(page, '旧页面提交材料。')
      await factSubmit(page).click()
      const oldRequest = (await old.started).request().postDataJSON()
      await factHistory(page)
      await page.getByRole('button', { name: '新建核查', exact: true }).click()
      const fresh = '新页面提交材料，必须保留其请求编号。'
      await factText(page, fresh)
      current = await holdApi(page, '/fact-check/runs')
      await factSubmit(page).click()
      const currentRequest = (await current.started).request().postDataJSON()
      expect(currentRequest.request_id).not.toBe(oldRequest.request_id)
      await old.respond(outcome === 'resolve' ? factRun(99) : { detail: '旧创建失败' }, outcome === 'resolve' ? 200 : 409)
      await expect(page).toHaveURL(/\/fact-check$/)
      await expect(factInput(page)).toHaveValue(fresh)
      await expect(factInput(page)).toBeDisabled()
      const retry = page.getByRole('button', { name: '重试提交（同一请求）', exact: true })
      await expect(retry).toBeDisabled()
      await expect(factError(page)).toHaveCount(0)

      await current.respond({ detail: '新请求结果未知' }, 503)
      await expect(retry).toBeEnabled()
      const retryRequest = await holdApi(page, '/fact-check/runs')
      try {
        await retry.click()
        expect((await retryRequest.started).request().postDataJSON()).toEqual(currentRequest)
        await retryRequest.respond(factRun(42))
        await expect(page).toHaveURL(/\/fact-check\/42$/)
        await expect(page.getByRole('button', { name: '刷新状态', exact: true })).toBeEnabled()
      } finally {
        await retryRequest.dispose()
      }
    } finally {
      await old.dispose()
      await current?.dispose()
    }
  })
}

for (const kind of ['reviews', 'deepen'] as const) {
  for (const outcome of ['resolve', 'reject'] as const) {
    test(`事实核查：返回同一任务后旧${kind}请求${outcome}不得覆盖数据、导航或新操作busy`, async ({ page, scenario }) => {
      scenario.factCheck = true
      const old = await holdApi(page, `/fact-check/runs/41/${kind}`)
      let current: Awaited<ReturnType<typeof holdApi>> | undefined
      try {
        await page.goto('/fact-check')
        await factHistory(page)
        const editor = page.getByRole('textbox', { name: '复核说明', exact: true })
        const save = page.getByRole('button', { name: '保存复核意见', exact: true })
        if (kind === 'reviews') {
          await editor.fill('旧页面复核')
          await save.click()
        } else {
          const consent = page.getByRole('checkbox', { name: '确认可再次对外检索', exact: true })
          await page.locator('label').filter({ has: consent }).click()
          await expect(consent).toBeChecked()
          await page.getByRole('button', { name: '发起单条深查', exact: true }).click()
        }
        await old.started
        await expect(page.getByRole('button', { name: '刷新状态', exact: true })).toBeDisabled()
        await page.getByRole('button', { name: '新建核查', exact: true }).click()
        await factHistory(page)
        await editor.fill('新页面尚未完成的复核')
        current = await holdApi(page, '/fact-check/runs/41/reviews')
        await save.click()
        const payload = (await current.started).request().postDataJSON()
        const review = { id: 1, run_id: 41, user_id: 1, claim_id: 'C1', decision: 'agree', note: '旧页面复核', request_id: 'old-review', created_at: '2026-06-01T00:02:00Z' }
        await old.respond(outcome === 'resolve' ? (kind === 'reviews' ? review : factRun(99)) : { detail: '旧操作失败' }, outcome === 'resolve' ? 200 : 503)
        await expect(page).toHaveURL(/\/fact-check\/41$/)
        await expect(editor).toHaveValue('新页面尚未完成的复核')
        await expect(editor).toBeDisabled()
        await expect(save).toBeDisabled()
        await expect(page.getByRole('button', { name: '刷新状态', exact: true })).toBeDisabled()
        await expect(page.locator('.review-entry')).toHaveCount(0)
        await expect(factError(page)).toHaveCount(0)

        await current.respond({ ...review, ...payload, id: 2 })
        await expect(save).toBeEnabled()
        await expect(editor).toHaveValue('')
        await expect(page.locator('.review-entry')).toHaveCount(1)
        await expect(page.locator('.review-entry')).toContainText('新页面尚未完成的复核')
      } finally {
        await old.dispose()
        await current?.dispose()
      }
    })
  }
}
