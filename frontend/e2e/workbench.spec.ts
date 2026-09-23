import { readFile } from 'node:fs/promises'
import { Buffer } from 'node:buffer'
import type { Locator, Page } from '@playwright/test'
import { test, expect, source, corrected, exportedBytes, sample } from './workbench.fixture'

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
