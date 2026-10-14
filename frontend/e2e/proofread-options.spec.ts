import type { Page } from '@playwright/test'
import type { ProofreadCompareResponse } from '../src/api/proofread'
import type { CollaborationReport } from '../src/api/collaboration'
import { test, expect } from './workbench.fixture'

const models = [
  { id: 11, name: '日常模型', model: 'model-a', is_active: true, provider: 'test' },
  { id: 22, name: '交叉模型', model: 'model-b', is_active: false, provider: 'test' },
  { id: 33, name: '补充模型', model: 'model-c', is_active: false, provider: 'test' },
]
const complete = { status: 'complete' as const, total_chunks: 1, completed_chunks: 1, failed_chunks: [] }
const text = '工做安排。6600小时。'
const typo = { original: '工做', suggestion: '工作', type: 'typo', severity: 'warning', start: 0, end: 2, explanation: '用字错误' }
const manual = { original: '6600小时', suggestion: '', type: 'logic', severity: 'warning', start: 5, end: 11, explanation: '请核实统计口径' }
async function setup(page: Page) {
  const submissions: { path: string; body: Record<string, unknown> }[] = []
  page.on('request', request => {
    const path = new URL(request.url()).pathname
    if (request.method() === 'POST' && /\/(proofread\/(text|compare|collaborate)|document\/proofread\/async)$/.test(path)) {
      submissions.push({ path, body: request.postDataJSON() })
    }
  })
  await page.route('**/api/v1/polish/models', route => route.fulfill({ json: { models } }))
  return submissions
}
async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
}

for (const [device, viewport, isMobile] of [
  ['桌面', { width: 1440, height: 1000 }, false],
  ['手机', { width: 390, height: 844 }, true],
] as const) {
  test.describe(`简化审校 ${device}`, () => {
    test.use({ viewport, isMobile, hasTouch: isMobile })

    test('默认无需选择，只调用一次标准审校（模拟接口）', async ({ page, scenario }, info) => {
      const submissions = await setup(page)
      await page.route('**/api/v1/proofread/text', async route => {
        const body = route.request().postDataJSON()
        scenario.review = { ...scenario.review, original_text: body.text, modified_text: body.text, issues: [], config_id: 11 }
        await route.fulfill({ json: { ...scenario.review, issues: [], total_issues: 0, chunks_count: 1, usage: {}, check_types: [] } })
      })
      await page.goto('/proofread/text')
      const options = page.getByRole('region', { name: '审校设置' })
      await expect(options.locator('.options-current')).toContainText('日常审校')
      await expect(options.locator('.options-disclosure')).not.toHaveAttribute('open')
      await expect(options.getByRole('radio')).toHaveCount(0)
      await expect(options.getByRole('combobox')).toHaveCount(0)
      await expect(page.getByRole('button', { name: '开始审校', exact: true })).toBeDisabled()
      await page.getByRole('textbox', { name: '待审校文本', exact: true }).fill('这是一篇需要检查的文章。')
      await noOverflow(page)
      await page.screenshot({ path: info.outputPath(`default-${device}.png`), fullPage: true, animations: 'disabled' })
      expect(submissions).toHaveLength(0)
      await page.getByRole('button', { name: '开始审校', exact: true }).click()
      await expect(page).toHaveURL(/review=7/)
      expect(submissions).toHaveLength(1)
      expect(submissions[0]).toMatchObject({ path: '/api/v1/proofread/text', body: { depth: 'standard', domain: 'general', config_id: 11 } })
    })

    test('更多方式按需展开，返回日常恢复标准，切换不发请求', async ({ page }, info) => {
      const submissions = await setup(page)
      await page.goto('/proofread/text')
      const options = page.getByRole('region', { name: '审校设置' })
      await options.locator('summary').filter({ hasText: '更多检查方式' }).click()
      await options.locator('.expert-options > summary').click()
      await options.locator('.expert-options .model-selection').first().locator('.el-select__wrapper').click()
      await page.getByRole('option', { name: '更细检查', exact: true }).click()
      await expect(options.locator('.options-current')).toContainText('更细检查')
      await options.getByRole('radio', { name: /分工审校/ }).check()
      await expect(options.getByLabel('检查强度', { exact: true })).toHaveCount(0)
      await expect(options.locator('.options-current')).toContainText('分工审校')
      await options.getByRole('radio', { name: /多模型对比/ }).check()
      await expect(options.locator('.options-cost')).toContainText('2 次应用额度')
      await expect(options.locator('.options-current')).toContainText('日常模型、交叉模型')
      await options.locator('summary').filter({ hasText: '更多检查方式' }).click()
      await expect(options.locator('.options-current')).toContainText('多模型对比')
      await noOverflow(page)
      await page.screenshot({ path: info.outputPath(`selected-compare-${device}.png`), fullPage: true, animations: 'disabled' })
      await options.locator('summary').filter({ hasText: '更多检查方式' }).click()
      await options.getByRole('radio', { name: /日常审校/ }).check()
      await expect(options.locator('.options-current')).toContainText('日常审校')
      expect(submissions).toHaveLength(0)
    })

    test('文档上传直接使用标准模式，更多选项不创建额外任务', async ({ page, scenario }) => {
      const submissions = await setup(page)
      await page.goto('/proofread/document')
      await page.locator('input[type="file"]').setInputFiles({ name: '浏览器回归.txt', mimeType: 'text/plain', buffer: Buffer.from(text) })
      await expect(page.getByRole('button', { name: '开始审校', exact: true })).toBeEnabled()
      const options = page.getByRole('region', { name: '审校设置' })
      await expect(options.locator('.options-current')).toContainText('日常审校')
      await expect(options.getByRole('combobox')).toHaveCount(0)
      expect(submissions).toHaveLength(0)
      await page.getByRole('button', { name: '开始审校', exact: true }).click()
      await expect(page.getByRole('heading', { name: '逐条确认，让文档准备就绪' })).toBeVisible()
      expect(submissions).toHaveLength(1)
      expect(submissions[0]).toMatchObject({ path: '/api/v1/document/proofread/async', body: { depth: 'standard', domain: 'general' } })
      expect(scenario.calls.filter(call => call.path === '/document/upload')).toHaveLength(1)
    })

    test('两模型对比用合并清单，不改人工核对数字，详情与保存仍可用', async ({ page, scenario }, info) => {
      const submissions = await setup(page)
      const response: ProofreadCompareResponse = { record_id: 7, consensus_originals: ['工做'], only_in: {}, results: models.slice(0, 2).map((model, index) => ({
        config_id: model.id, config_name: model.name, model: model.model, success: true, elapsed_ms: 100,
        domain: 'general', depth: 'standard', coverage: complete, total_issues: index ? 1 : 2, issues: index ? [typo] : [typo, manual],
      })) }
      scenario.review = { ...scenario.review, original_text: text, modified_text: text, issues: [typo, manual], compare: { results: response.results.map(item => ({ ...item, domain: 'general', depth: 'standard', error: item.error ?? null, coverage: item.coverage ?? null })) } }
      await page.route('**/api/v1/proofread/compare', route => route.fulfill({ json: response }))
      await page.goto('/proofread/text')
      const options = page.getByRole('region', { name: '审校设置' })
      await options.locator('summary').filter({ hasText: '更多检查方式' }).click()
      await options.getByRole('radio', { name: /多模型对比/ }).check()
      await page.getByRole('textbox', { name: '待审校文本', exact: true }).fill(text)
      await page.getByRole('button', { name: '开始模型对比', exact: true }).click()
      await expect(page).toHaveURL(/review=7/)
      expect(submissions).toHaveLength(1)
      expect(submissions[0].body).toMatchObject({ config_ids: [11, 22], domain: 'general' })
      expect(submissions[0].body).not.toHaveProperty('depth')
      const cards = page.locator('.issue-item:visible')
      await expect(cards).toHaveCount(2)
      await expect(page.locator('.summary-help')).toContainText('不代表建议已经证实正确')
      await page.getByRole('button', { name: /^共同发现/ }).click()
      await expect(cards).toHaveCount(1)
      await expect(cards.first()).toContainText('工做')
      await page.getByRole('button', { name: /^仅部分模型发现/ }).click()
      await expect(cards).toHaveCount(1)
      await expect(cards.first()).toContainText('需人工核对')
      await expect(cards.first().getByRole('button', { name: '仅修改此处' })).toHaveCount(0)
      await page.getByRole('button', { name: /^全部建议/ }).click()
      await cards.filter({ has: page.locator('.issue-diff [title="工做"]') }).getByRole('button', { name: '仅修改此处', exact: true }).click()
      await expect(cards.filter({ has: page.locator('.issue-diff [title="工做"]') })).toHaveClass(/is-accepted/)
      await noOverflow(page)
      await page.screenshot({ path: info.outputPath(`compare-result-${device}.png`), fullPage: true, animations: 'disabled' })
      await page.locator('summary').filter({ hasText: '草稿与版本管理' }).click()
      await page.getByRole('button', { name: '保存草稿', exact: true }).click()
      await expect(page.locator('.review-workspace [role="status"]')).toContainText('草稿已保存')
      expect(scenario.review.modified_text).toBe('工作安排。6600小时。')
      await page.reload()
      await expect(page.locator('.issue-item:visible').filter({ has: page.locator('.issue-diff [title="工做"]') })).toHaveClass(/is-accepted/)
      expect(submissions).toHaveLength(1)
    })

    test('历史模型已停用时阻止静默缩减参与模型', async ({ page, scenario }) => {
      const submissions = await setup(page)
      await page.route('**/api/v1/polish/models', route => route.fulfill({ json: { models: models.slice(0, 2) } }))
      scenario.review = { ...scenario.review, original_text: text, modified_text: text, issues: [typo], compare: {
        results: models.map(model => ({ config_id: model.id, config_name: model.name, model: model.model,
          success: true, error: null, elapsed_ms: 1, domain: 'general', depth: 'standard', coverage: complete, issues: [typo] })),
      } }
      await page.goto('/proofread/text?review=7')
      await page.getByRole('button', { name: '返回编辑', exact: true }).click()
      await expect(page.getByRole('alert')).toContainText('不会静默减少参与模型')
      await expect(page.getByRole('button', { name: '开始模型对比', exact: true })).toBeDisabled()
      await page.getByRole('textbox', { name: '待审校文本', exact: true }).press('Control+Enter')
      expect(submissions).toHaveLength(0)
    })

    test('暗色界面保留对比摘要与清晰的更多选项', async ({ page }, info) => {
      const submissions = await setup(page)
      await page.addInitScript(() => localStorage.setItem('tm_theme', 'dark'))
      await page.goto('/proofread/text')
      await expect(page.locator('html')).toHaveClass(/dark/)
      const options = page.getByRole('region', { name: '审校设置' })
      await options.locator('summary').filter({ hasText: '更多检查方式' }).click()
      await options.getByRole('radio', { name: /多模型对比/ }).check()
      await expect(options.locator('.options-current')).toContainText('日常模型、交叉模型')
      await noOverflow(page)
      await page.screenshot({ path: info.outputPath(`options-dark-${device}.png`), fullPage: true, animations: 'disabled' })
      expect(submissions).toHaveLength(0)
    })

    test('三模型中仅两模型发现不算共同，失败模型保持可见', async ({ page, scenario }) => {
      const submissions = await setup(page)
      const results = models.map((model, index) => ({
        config_id: model.id, config_name: model.name, model: model.model,
        success: index !== 2, error: index === 2 ? '模拟超时' : null, elapsed_ms: 100,
        domain: 'general', depth: 'standard', coverage: index === 2 ? null : complete,
        issues: index === 2 ? [] : [typo],
      }))
      scenario.review = { ...scenario.review, original_text: text, modified_text: text, issues: [typo], compare: { results } }
      await page.goto('/proofread/text?review=7')
      await expect(page.locator('.summary-help')).toBeVisible()
      await expect(page.getByText('部分模型审校失败', { exact: false })).toBeVisible()
      await expect(page.locator('.issue-item:visible')).toContainText('仅部分模型发现')
      await expect(page.locator('.issue-item:visible')).toContainText('2/3')
      await page.getByRole('button', { name: /^共同发现/ }).click()
      await expect(page.locator('.issue-item:visible')).toHaveCount(0)
      await page.getByRole('tab', { name: '各模型详情', exact: true }).click()
      await page.getByRole('tab', { name: /补充模型/ }).click()
      await expect(page.getByText('校对失败：模拟超时', { exact: true })).toBeVisible()
      expect(submissions).toHaveLength(0)
    })

    test('分工审校只提交固定流程，显示职责与未复核状态', async ({ page, scenario }, info) => {
      const submissions = await setup(page)
      const report: CollaborationReport = {
        status: 'complete', config_id: 11, model_name: '日常模型', reviewed_count: 0, review_limit: 20,
        roles: ['rules', 'language', 'consistency', 'reviewer'].map((id, index) => ({ id: id as CollaborationReport['roles'][number]['id'], name: ['规则检查', '语言审校', '一致性审校', '争议复核'][index], status: index === 3 ? 'skipped' : 'success', message: index === 3 ? '无冲突或疑点' : '已完成', issue_count: index === 1 ? 1 : 0, elapsed_ms: 100, usage: {} })),
        findings: [{ ...typo, severity: 'error', found_by: ['语言审校'], review_status: 'not_reviewed', review_note: '' }],
      }
      scenario.review = { ...scenario.review, original_text: text, modified_text: text, issues: [typo], collaboration: report }
      await page.route('**/api/v1/proofread/collaborate', route => route.fulfill({ json: { task_id: 'division-task', message: '已提交' } }))
      await page.route('**/api/v1/tasks/division-task', route => route.fulfill({ json: { task_id: 'division-task', status: 'SUCCESS', progress: 100,
        result: { ...scenario.review, total_issues: 1, chunks_count: 1, usage: {}, check_types: [], collaboration: report } } }))
      await page.goto('/proofread/text')
      const options = page.getByRole('region', { name: '审校设置' })
      await options.locator('summary').filter({ hasText: '更多检查方式' }).click()
      await options.getByRole('radio', { name: /分工审校/ }).check()
      await page.getByRole('textbox', { name: '待审校文本', exact: true }).fill(text)
      await page.getByRole('button', { name: '开始分工审校', exact: true }).click()
      await expect(page).toHaveURL(/review=7/)
      expect(submissions).toHaveLength(1)
      expect(submissions[0].path).toBe('/api/v1/proofread/collaborate')
      expect(submissions[0].body).not.toHaveProperty('depth')
      expect(submissions[0].body).not.toHaveProperty('config_ids')
      const panel = page.getByTestId('collaboration-progress')
      await expect(panel).toContainText('分工审校')
      await expect(panel).toContainText('文字表达')
      await expect(panel).toContainText('全文一致性')
      await expect(page.locator('.issue-item')).toContainText('未复核')
      await noOverflow(page)
      await page.screenshot({ path: info.outputPath(`division-result-${device}.png`), fullPage: true, animations: 'disabled' })
    })
  })
}
