import type { Page } from '@playwright/test'
import type { FactCheckEvidence, FactCheckRun } from '../src/api/factCheck'
import { test, expect, factRun, source } from './workbench.fixture'

const submit = (page: Page) => page.getByRole('button', { name: '开始事实核查', exact: true })

async function consent(page: Page) {
  const checkbox = page.getByRole('checkbox', { name: '同意材料外发', exact: true })
  await page.locator('label').filter({ has: checkbox }).click()
  await expect(checkbox).toBeChecked()
}

async function noOverflow(page: Page) {
  await expect.poll(() => page.locator('.user-main').evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
  for (const selector of ['.intake', '.run-overview', '.claims-pane', '.evidence-pane']) {
    const panel = page.locator(selector)
    if (!await panel.isVisible()) continue
    const bounds = (await panel.boundingBox())!
    expect(bounds.x).toBeGreaterThanOrEqual(0)
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(page.viewportSize()!.width + 1)
  }
}

function citedReport(): FactCheckRun {
  const run = factRun()
  const quote = '参考资料中的逐字引文。'
  const evidence: FactCheckEvidence = {
    id: 'E1', title: '原始资料', url: 'https://example.com/reference', quote,
    published_at: '2026-06-01T00:00:00Z', retrieved_at: '2026-06-02T00:00:00Z',
    publisher: '测试来源', stance: 'refutes', body_text: quote,
    body_sha256: 'a'.repeat(64), body_hash_scope: 'normalized_model_visible_text_utf8',
    body_text_length: quote.length, quote_start: 0, quote_end: quote.length,
    checks: {
      subject: { status: 'match', reason: '同一主体' },
      event_time: { status: 'match', reason: '同一事件' },
      scope_unit: { status: 'not_applicable', reason: '没有数值单位' },
    },
  }
  run.result!.claims[0] = {
    ...run.result!.claims[0], verdict: 'refuted', reason: '原始资料与这项陈述不符。',
    suggestion: '依据资料修正这项陈述。', evidence: [evidence],
    search_rounds: [{ kind: 'initial', query: source, status: 'complete', pages_fetched: 1, error_codes: [],
      sources: [{ url: evidence.url, title: evidence.title, origin: 'search', status: 'fetched',
        reason: '已读取并引用正文', error_code: null, evidence_id: evidence.id }] }],
  }
  run.result!.claims.push({
    ...run.result!.claims[0], id: 'C2', statement: '第二条需要继续核实的陈述',
    checked: false, selected: false, verdict: 'insufficient', reason: '超出本次核查范围。',
    suggestion: null, evidence: [], search_rounds: [],
  })
  run.result!.coverage = { extracted: 2, checked: 1, unverified: 1, status: 'partial', reason: '有一条事实未纳入核查。' }
  run.result!.usage = { prompt_tokens: 10, completion_tokens: 10, total_tokens: 20, search_queries: 2, pages_fetched: 1 }
  return run
}

async function respondWith(page: Page, report: FactCheckRun) {
  await page.route('**/api/v1/fact-check/runs/41', route => route.request().method() === 'GET'
    ? route.fulfill({ json: report }) : route.fallback())
}

for (const width of [320, 375, 1440]) {
  test.describe(`精简事实核查 ${width}px`, () => {
    test.use({ viewport: { width, height: 900 }, isMobile: width < 768, hasTouch: width < 768 })

    test('默认只需材料和联网同意，更多选项与记录收起', async ({ page, scenario }) => {
      scenario.factCheck = true
      await page.goto('/fact-check')
      // 页面标题由布局顶栏提供：桌面为 page-context h1，移动端为顶栏标题
      if (width < 768) {
        await expect(page.locator('.mobile-page-title')).toHaveText('事实核查')
      } else {
        await expect(page.getByRole('heading', { name: '事实核查', exact: true })).toBeVisible()
      }
      await expect(submit(page)).toBeDisabled()
      await expect(page.getByRole('checkbox', { name: '同意材料外发' })).not.toBeChecked()
      await expect(page.locator('.advanced-options')).not.toHaveAttribute('open', '')
      await expect(page.getByRole('button', { name: '先确认事实项', exact: true })).toBeHidden()
      await expect(page.locator('.history-tools')).toBeHidden()
      await expect(page.locator('.history-item')).toBeHidden()
      const input = page.getByRole('textbox', { name: '待核查文本', exact: true })
      await input.fill('测'.repeat(20001))
      await consent(page)
      await expect(submit(page)).toBeDisabled()
      await input.fill(source)
      await expect(submit(page)).toBeEnabled()
      await noOverflow(page)
      await submit(page).click()
      await expect(page).toHaveURL(/\/fact-check\/42$/)
      await expect(page.locator('.claim-item')).toHaveCount(1)
      await expect(page.locator('.evidence-pane')).toBeVisible()
      await expect(page.getByRole('button', { name: '下载打印版', exact: true })).toBeVisible()
      for (const name of ['导出 JSON', '清理材料与证据', '保存复核意见']) {
        await expect(page.getByRole('button', { name, exact: true })).toBeHidden()
      }
      expect(scenario.calls.filter(call => call.method === 'POST' && call.path === '/fact-check/runs')).toHaveLength(1)
      expect(scenario.calls.find(call => call.method === 'POST' && call.path === '/fact-check/runs')?.body).toMatchObject({
        text: source, mode: 'web', source_ids: [], confirm_claims: false, allow_external_search: true,
      })
      await noOverflow(page)
    })

    test('结果先展示理由和原始引文，技术信息按需展开', async ({ page, scenario }) => {
      scenario.factCheck = true
      const report = citedReport()
      await respondWith(page, report)
      await page.goto('/fact-check/41')
      const evidence = page.locator('.evidence-pane')
      await expect(page.locator('.claim-item').first()).toContainText('证据反驳')
      await expect(page.locator('.claim-item').nth(1)).toContainText('未核查')
      await expect(evidence).toContainText('原始资料与这项陈述不符。')
      await expect(evidence.locator('.evidence-card blockquote')).toHaveText('参考资料中的逐字引文。')
      await expect(evidence.getByRole('link', { name: '原始资料', exact: true })).toHaveAttribute('href', 'https://example.com/reference')
      await expect(page.getByTestId('fact-check-search-trace')).toBeHidden()
      await expect(page.locator('.check-list')).toBeHidden()
      await expect(page.getByRole('textbox', { name: '复核说明', exact: true })).toBeHidden()
      await noOverflow(page)
      await page.locator('.search-details > summary').click()
      await expect(page.getByTestId('fact-check-search-trace')).toBeVisible()
      await page.locator('.report-details > summary').click()
      await expect(page.getByRole('button', { name: '导出 JSON', exact: true })).toBeVisible()
      await page.locator('.review-options > summary').click()
      await expect(page.getByRole('textbox', { name: '复核说明', exact: true })).toBeVisible()
      await page.getByRole('button', { name: '新建核查', exact: true }).click()
      await expect(page).toHaveURL(/\/fact-check$/)
      await page.locator('details.history > summary').click()
      await page.locator('.history-item').filter({ hasText: '模拟核查 41' }).click()
      await expect(page).toHaveURL(/\/fact-check\/41$/)
      await expect(page.getByTestId('fact-check-search-trace')).toBeHidden()
      await expect(page.getByRole('textbox', { name: '复核说明', exact: true })).toBeHidden()
      await expect(page.getByRole('button', { name: '导出 JSON', exact: true })).toBeHidden()
      await page.locator('.claim-item').nth(1).click()
      await expect(evidence).toContainText('第二条需要继续核实的陈述')
      await expect(evidence.locator('.evidence-card')).toHaveCount(0)
      await noOverflow(page)
      expect(scenario.calls.every(call => call.method === 'GET')).toBe(true)
    })

    test('暗色模式来源链接仍清晰可读', async ({ page, scenario, context, baseURL }) => {
      scenario.factCheck = true
      await context.addInitScript(origin => {
        if (location.origin === origin) localStorage.setItem('tm_theme', 'dark')
      }, new URL(baseURL!).origin)
      await respondWith(page, citedReport())
      await page.goto('/fact-check/41')
      await expect(page.locator('html')).toHaveClass(/dark/)
      const link = page.locator('.evidence-card a')
      await link.scrollIntoViewIfNeeded()
      const ratio = await link.evaluate(element => {
        const luminance = (value: string) => {
          const channels = value.match(/[\d.]+/g)!.slice(0, 3).map(Number).map(channel => {
            const unit = channel / 255
            return unit <= 0.04045 ? unit / 12.92 : ((unit + 0.055) / 1.055) ** 2.4
          })
          return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722
        }
        const foreground = luminance(getComputedStyle(element).color)
        const background = luminance(getComputedStyle(element.closest('.paper')!).backgroundColor)
        return (Math.max(foreground, background) + 0.05) / (Math.min(foreground, background) + 0.05)
      })
      expect(ratio).toBeGreaterThanOrEqual(4.5)
      await noOverflow(page)
    })

    test('取证受阻不当作核查通过，完整错误仍可追溯', async ({ page, scenario }) => {
      scenario.factCheck = true
      const report = factRun()
      report.result!.coverage.status = 'partial'
      report.result!.coverage.reason = '技术原因：UNSAFE_ADDRESS。'
      report.result!.claims[0].reason = '技术原因导致检索或安全抓取不完整，无法作出可靠判定：UNSAFE_ADDRESS。'
      report.result!.claims[0].search_rounds = [{ kind: 'initial', query: source, status: 'partial',
        pages_fetched: 0, error_codes: ['UNSAFE_ADDRESS'], sources: [] }]
      await respondWith(page, report)
      await page.goto('/fact-check/41')
      await expect(page.locator('.run-overview')).toContainText('核查受阻')
      await expect(page.locator('.claim-item')).toContainText('核查受阻')
      await expect(page.locator('.claim-item')).toContainText('尚不能判断')
      await expect(page.locator('.claim-item')).not.toContainText('UNSAFE_ADDRESS')
      await expect(page.getByTestId('fact-check-search-trace')).toBeHidden()
      await expect(page.locator('.evidence-card')).toHaveCount(0)
      const visible = await page.getByTestId('fact-workbench').innerText()
      expect(visible).not.toContain('UNSAFE_ADDRESS')
      expect(visible).not.toContain('已证实')
      await page.locator('.search-details > summary').click()
      await expect(page.getByTestId('fact-check-search-trace')).toContainText('UNSAFE_ADDRESS')
      await noOverflow(page)
    })
  })
}

test('更多选项保留可信信源和先确认流程，折叠后不隐瞒非默认模式', async ({ page, scenario }) => {
  scenario.factCheck = true
  await page.route('**/api/v1/fact-check/options', route => route.fulfill({ json: {
    available: true, unavailable_reason: '', provider: 'model', model_name: '离线模型', max_claims: 10,
    max_text_chars: 20000, daily_limit: 20, sources: [{ id: 's1', name: '指定来源', domain: 'example.com', path_prefix: '/', is_enabled: true }],
  } }))
  await page.goto('/fact-check')
  await page.locator('.advanced-options > summary').click()
  await page.locator('.configuration .el-select').first().click()
  await page.getByRole('option', { name: '指定可信信源', exact: true }).click()
  await page.locator('.advanced-options > summary').click()
  await expect(page.getByTestId('fact-workbench')).toContainText('指定可信信源')
  await page.getByRole('textbox', { name: '待核查文本', exact: true }).fill(source)
  await consent(page)
  await page.locator('.advanced-options > summary').click()
  await page.getByRole('button', { name: '先确认事实项', exact: true }).click()
  await expect(page).toHaveURL(/\/fact-check\/42$/)
  expect(scenario.calls.find(call => call.method === 'POST' && call.path === '/fact-check/runs')?.body).toMatchObject({
    mode: 'trusted', source_ids: ['s1'], confirm_claims: true, allow_external_search: true,
  })
})

test('任务执行中可取消，等待确认时可以继续核查', async ({ page, scenario }) => {
  scenario.factCheck = true
  const report = factRun()
  report.status = 'WAITING_CONFIRMATION'
  report.stage = 'extract'
  report.confirm_claims = true
  report.result!.claims[0].checked = false
  await respondWith(page, report)
  let executeBody: unknown
  await page.route('**/api/v1/fact-check/runs/41/execute', async route => {
    executeBody = route.request().postDataJSON()
    report.status = 'RUNNING'
    report.stage = 'check'
    await route.fulfill({ json: report })
  })
  await page.route('**/api/v1/fact-check/runs/41/cancel', async route => {
    report.status = 'CANCELLED'
    await route.fulfill({ json: report })
  })
  await page.goto('/fact-check/41')
  await expect(page.locator('.confirmation')).toBeVisible()
  await page.getByRole('textbox', { name: '待核查陈述 C1', exact: true }).fill('需要确认的独立陈述。')
  await page.locator('.confirmation').getByRole('button').click()
  expect(executeBody).toMatchObject({ claims: [{ id: 'C1', statement: '需要确认的独立陈述。' }] })
  await expect(page.locator('.confirmation')).toHaveCount(0)
  await page.getByRole('button', { name: '取消核查', exact: true }).click()
  await expect(page.locator('.run-overview')).toContainText('已取消')
  await expect(page.getByRole('button', { name: '取消核查', exact: true })).toHaveCount(0)
})

for (const status of ['CANCELLED', 'FAILURE', 'SUCCESS'] as const) {
  test(`空报告 ${status} 不显示通过或默认证据`, async ({ page, scenario }) => {
    scenario.factCheck = true
    const report = factRun()
    report.status = status
    report.result!.claims = []
    report.result!.coverage = { extracted: 0, checked: 0, unverified: 0, status: 'complete', reason: '' }
    await respondWith(page, report)
    await page.goto('/fact-check/41')
    await expect(page.locator('.claims-pane .empty')).toBeVisible()
    await expect(page.locator('.claims-pane .empty')).toContainText(/不代表|不能据此/)
    await expect(page.locator('.evidence-card')).toHaveCount(0)
    expect(scenario.calls.every(call => call.method === 'GET')).toBe(true)
  })
}
