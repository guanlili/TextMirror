import { readFile } from 'node:fs/promises'
import type { Locator, Page } from '@playwright/test'
import type { AuditLogDetail, AuditLogItem, AuditLogListResponse, AuditStats } from '../src/api/audit'
import type { DictionaryItem } from '../src/api/dictionary'
import type { PaginatedResponse, WhitelistItem } from '../src/api/whitelist'
import { test, expect, source, corrected, exportedBytes } from './workbench.fixture'

async function within(target: Locator, container?: Locator, vertical = false) {
  await expect(target).toBeVisible()
  await expect.poll(async () => {
    const box = await target.boundingBox()
    const viewport = target.page().viewportSize()!
    const parent = container ? await container.boundingBox() : { x: 0, y: 0, ...viewport }
    if (!box || !parent) return ['missing bounds']
    const overflow = {
      left: Math.max(0, parent.x) - box.x,
      right: box.x + box.width - Math.min(viewport.width, parent.x + parent.width),
      ...(vertical ? {
        top: Math.max(0, parent.y) - box.y,
        bottom: box.y + box.height - Math.min(viewport.height, parent.y + parent.height),
      } : {}),
    }
    return Object.entries(overflow).filter(([, pixels]) => pixels > 1)
  }, { message: `${target} must fit ${container || 'the viewport'}` }).toEqual([])
}

async function reachable(control: Locator, container?: Locator) {
  await control.scrollIntoViewIfNeeded()
  await within(control, container, true)
  await control.click({ trial: true })
}

async function dialogControls(overlay: Locator, nativeWidth: number) {
  const dialog = overlay.locator('.el-dialog')
  await within(dialog)
  await expect.poll(async () => (await dialog.boundingBox())?.width).toBeCloseTo(
    Math.min(nativeWidth, dialog.page().viewportSize()!.width - 32), 0,
  )
  await reachable(dialog.locator('.el-dialog__headerbtn'), dialog)
  for (const button of await dialog.locator('button:visible, [role="button"]:visible').all()) {
    await reachable(button, dialog)
  }
  const footer = dialog.locator('.el-dialog__footer')
  await footer.scrollIntoViewIfNeeded()
  await within(footer, dialog, true)
  for (const button of await footer.getByRole('button').all()) await within(button, footer, true)
}

async function paginationBounds(page: Page) {
  const pagination = page.locator('.audit-page .el-pagination')
  const card = page.locator('.audit-page > .el-card').filter({ has: page.locator('.el-pagination') })
  await pagination.scrollIntoViewIfNeeded()
  await within(pagination, page.locator('.admin-main'), true)
  await within(pagination, card, true)
  const controls = pagination.locator(':scope > *, .el-pager > li, button, [role="combobox"], .el-select__wrapper')
  for (const control of await controls.all()) {
    if (await control.isVisible()) await within(control, pagination, true)
  }
}

async function mockGet(page: Page, baseURL: string | undefined, path: string, response: (url: URL) => unknown) {
  const endpoint = new URL(`/api/v1${path}`, baseURL)
  const calls: URL[] = []
  await page.route(url => url.origin === endpoint.origin && url.pathname === endpoint.pathname, async route => {
    if (route.request().method() !== 'GET') return route.fallback()
    const url = new URL(route.request().url())
    calls.push(url)
    await route.fulfill({ json: response(url) })
  })
  return calls
}

async function mockWordLists(page: Page, baseURL: string | undefined) {
  const dictionary = await mockGet(page, baseURL, '/dictionary', () => [{
    id: 11, name: '离线词库', description: '响应式回归数据', is_active: true, entry_count: 2,
  }] satisfies DictionaryItem[])
  const whitelist = await mockGet(page, baseURL, '/whitelist', () => ({
    items: [{ id: 12, word: 'TextMirror', type: 'permanent', remark: '产品名称' }],
    total: 1, page: 1, page_size: 50,
  } satisfies PaginatedResponse<WhitelistItem>))
  return { dictionary, whitelist }
}

function auditLog(id: number): AuditLogItem {
  return {
    id, action_type: 'proofread_text', user_id: 1, username: `回归用户${id}`, employee_id: 'browser-test',
    is_guest: false, client_ip: '127.0.0.1', device_type: 'desktop', input_preview: source,
    output_preview: corrected, file_name: null, status: 'success', duration_ms: 120,
    created_at: '2026-06-01T00:00:00Z',
  }
}

for (const width of [320, 390, 768, 1440, 2560]) {
  test.describe(`确认弹窗 ${width}px`, () => {
    test.use({ viewport: { width, height: 900 } })

    test('未保存审阅保持紧凑、居中，取消与离开均可操作', async ({ page, scenario }, testInfo) => {
      await page.goto('/proofread/text?review=7')
      const preview = page.locator('.review-preview .preview-text')
      await expect(preview).toHaveText(source)
      await page.getByRole('button', { name: '仅修改此处', exact: true }).first().click()
      await expect(preview).toHaveText(corrected)
      const back = page.getByRole('button', { name: '返回编辑', exact: true })
      await back.click()
      const dialog = page.getByRole('dialog', { name: '未保存的审阅', exact: true }).locator('.el-message-box')
      await within(dialog, undefined, true)
      const expectedWidth = Math.min(420, width - 32)
      await expect.poll(async () => (await dialog.boundingBox())?.width).toBeCloseTo(expectedWidth, 0)
      await expect.poll(async () => (await dialog.boundingBox())?.x).toBeCloseTo((width - expectedWidth) / 2, 0)
      await within(dialog.locator('.el-message-box__content'), dialog, true)
      for (const button of await dialog.getByRole('button').all()) await reachable(button, dialog)
      if ([390, 1440].includes(width)) {
        await testInfo.attach('confirmation', { body: await page.screenshot(), contentType: 'image/png' })
      }
      await dialog.getByRole('button', { name: '继续审阅', exact: true }).click()
      await expect(dialog).not.toBeVisible()
      await expect(preview).toHaveText(corrected)
      await back.click()
      await dialog.locator('.el-message-box__headerbtn').click()
      await expect(dialog).not.toBeVisible()
      await expect(preview).toHaveText(corrected)
      await back.click()
      await dialog.getByRole('button', { name: '离开', exact: true }).click()
      await expect(dialog).not.toBeVisible()
      await expect(page.getByRole('textbox', { name: '待审校文本', exact: true })).toHaveValue(corrected)
      expect(scenario.calls.some(call => call.method === 'PUT')).toBe(false)
    })
  })
}

for (const width of [320, 390, 768, 769, 900, 1080, 1081, 1440]) {
  test.describe(`润色布局 ${width}px`, () => {
    test.use({ viewport: { width, height: 900 } })

    test('输入和生成结果完整可达，窄窗口上下排列', async ({ page, baseURL }, testInfo) => {
      await mockGet(page, baseURL, '/polish/styles', () => ({
        styles: ['正式规范', '简洁明了', '生动形象', '学术严谨', '商务专业', '文学优美', '通俗易懂', '新闻客观', '公文规范', '创意表达']
          .map((name, index) => ({ key: index === 0 ? 'formal' : `style-${index}`, name, description: '清晰准确地表达原意' })),
      }))
      const versions = ['light', 'standard', 'deep'].map(level => ({ level, content: `这是${level}润色结果，保持原意并优化表达。` }))
      await page.route('**/api/v1/polish/text/stream', route => route.fulfill({
        contentType: 'text/event-stream',
        body: [
          { event: 'meta', style: 'formal', style_name: '正式规范' },
          ...versions.flatMap(version => [{ event: 'delta', ...version }, { event: 'done', ...version }]),
          { event: 'end' },
        ].map(event => `data: ${JSON.stringify(event)}\n\n`).join(''),
      }))
      await page.goto('/polish')
      const left = page.locator('.panel-left')
      const right = page.locator('.panel-right')
      const checkLayout = async (stacked: boolean) => {
        await within(left, page.locator('.polish-layout'))
        await within(right, page.locator('.polish-layout'))
        await expect.poll(async () => {
          const a = await left.boundingBox(), b = await right.boundingBox()
          return !!a && !!b && (stacked
            ? b.y >= a.y + a.height && Math.abs(a.width - b.width) < 1
            : b.x >= a.x + a.width && Math.abs(a.y - b.y) < 1)
        }).toBe(true)
        await expect.poll(async () => (await right.boundingBox())?.width || 0).toBeGreaterThan(260)
      }
      await checkLayout(width <= 1080)
      await reachable(right.getByRole('heading', { name: '润色结果将在这里展示', exact: true }), right)
      const input = page.getByPlaceholder('请在此粘贴或输入需要润色的文本内容（10-5000字）...')
      const submit = page.getByRole('button', { name: '一键润色', exact: true })
      await expect(submit).toBeDisabled()
      await input.fill(source)
      await reachable(submit, left)
      await submit.click()
      const cards = right.locator('.result-card')
      await expect(cards).toHaveCount(3)
      for (const card of await cards.all()) {
        await within(card, right)
        await within(card.locator('.card-body'), card)
        await reachable(card.getByRole('button', { name: '复制', exact: true }), card)
      }
      await reachable(page.getByRole('button', { name: '重新生成', exact: true }), right)
      await checkLayout(width <= 1080)
      if ([769, 1440].includes(width)) {
        await testInfo.attach('polish-layout', { body: await page.screenshot(), contentType: 'image/png' })
      }
      if (width === 1440) {
        await page.setViewportSize({ width: 900, height: 900 })
        await checkLayout(true)
        await page.setViewportSize({ width, height: 900 })
        await checkLayout(false)
        await expect(cards).toHaveCount(3)
      }
    })

    test('长模型名称换行且下拉层不越界，选择取消正常', async ({ page, baseURL }, testInfo) => {
      const models = [
        { id: 1, name: '火山方舟（豆包）生产审校模型', model: 'doubao-seed-2-0-mini-260428' },
        { id: 2, name: '阿里百炼（通义千问）', model: 'qwen3.8-flash' },
        { id: 3, name: '长标识模型', model: 'long-model-identifier-without-spaces'.repeat(4) },
      ]
      await mockGet(page, baseURL, '/polish/models', () => ({ models }))
      await page.goto('/polish')
      await page.getByPlaceholder('请在此粘贴或输入需要润色的文本内容（10-5000字）...').fill(source)
      await page.locator('.compare-section .el-checkbox').click()
      const select = page.locator('.compare-section .el-select__wrapper')
      await reachable(select, page.locator('.panel-left'))
      await select.click()
      const popper = page.locator('.el-popper.polish-model-popper:visible')
      const options = popper.getByRole('option')
      await expect(options).toHaveCount(3)
      await within(popper, undefined, true)
      await expect.poll(async () => (await popper.boundingBox())?.width || 0).toBeLessThanOrEqual(width - 32)
      for (const option of await options.all()) {
        await reachable(option, popper)
        await expect(option).toHaveCSS('white-space', 'normal')
        expect(await option.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true)
      }
      await options.first().click()
      await options.nth(1).click()
      await expect(options.first()).toHaveAttribute('aria-selected', 'true')
      await expect(options.nth(1)).toHaveAttribute('aria-selected', 'true')
      const compare = page.getByRole('button', { name: '开始对比', exact: true })
      await expect(compare).toBeEnabled()
      await options.nth(1).click()
      await expect(compare).toBeDisabled()
      if (width === 390) {
        await testInfo.attach('polish-model-dropdown', { body: await page.screenshot(), contentType: 'image/png' })
      }
      await options.nth(1).click()
      await page.getByRole('combobox').press('Escape')
      await expect(popper).not.toBeVisible()
      await reachable(compare, page.locator('.panel-left'))
    })
  })
}

for (const width of [320, 375, 390, 430, 768, 1440]) {
  const mobile = width < 768
  test.describe(`响应式 ${width}px ${mobile ? '手机' : '桌面'}`, () => {
    test.use({ viewport: { width, height: 900 }, isMobile: mobile, hasTouch: mobile })
    const press = (control: Locator) => mobile ? control.tap() : control.click()

    test('模型卡片、搜索筛选与添加弹窗保持可用', async ({ page, scenario }) => {
      scenario.admin = true
      await page.goto('/admin/llm')
      await expect.poll(() => page.evaluate(() => window.innerWidth)).toBe(width)
      await expect(page.locator('meta[name="viewport"]')).not.toHaveAttribute('content', /user-scalable=no|maximum-scale=1(?:\D|$)/)
      const main = page.locator('.admin-main')
      const cards = page.locator('.config-card')
      const names = cards.locator('.name-text')
      await expect(names).toHaveText(['生产模型', '停用模型'])
      await within(page.locator('.config-grid'), main)
      for (const card of await cards.all()) {
        await within(card, main)
        await reachable(card.getByRole('button', { name: '测试连接', exact: true }), card)
      }

      const query = page.getByRole('textbox', { name: '搜索模型服务' })
      const status = page.locator('.el-select').filter({ has: page.getByRole('combobox', { name: '模型服务状态' }) })
      await reachable(query, main)
      await query.fill('qwen-test')
      await expect(names).toHaveText(['生产模型'])
      await query.fill('通义千问')
      await expect(names).toHaveText(['生产模型', '停用模型'])
      await reachable(status, main)
      await status.click()
      await page.getByRole('option', { name: '已停用', exact: true }).click()
      await expect(names).toHaveText(['停用模型'])
      await query.fill('不存在的服务')
      await expect(cards).toHaveCount(0)
      await expect(page.getByText('没有匹配的模型服务', { exact: true })).toBeVisible()
      await query.clear()
      await expect(names).toHaveText(['停用模型'])
      await status.click()
      await page.getByRole('option', { name: '全部服务', exact: true }).click()
      await expect(names).toHaveText(['生产模型', '停用模型'])

      const add = page.getByRole('button', { name: '添加模型', exact: true })
      await reachable(add, main)
      await add.click()
      const dialog = page.getByRole('dialog', { name: '添加模型配置', exact: true })
      await within(dialog)
      const close = dialog.locator('.el-dialog__headerbtn')
      await reachable(close, dialog)
      await close.click()
      await expect(dialog).not.toBeVisible()
      await add.click()
      await dialog.locator('.el-select').first().click()
      await page.getByRole('option', { name: '阿里百炼 (通义千问)', exact: true }).click()
      const key = dialog.getByPlaceholder('sk-...')
      const name = dialog.getByPlaceholder('留空自动使用供应商名')
      await reachable(key, dialog)
      await key.fill('browser-test-not-a-real-key')
      await reachable(name, dialog)
      await name.fill('响应式离线草稿')
      await expect(name).toHaveValue('响应式离线草稿')
      await dialogControls(dialog, 600)
      if (width === 1440) {
        await page.setViewportSize({ width: 375, height: 812 })
        await dialogControls(dialog, 600)
        await page.setViewportSize({ width, height: 900 })
        await dialogControls(dialog, 600)
      }
      await dialog.getByRole('button', { name: '取消', exact: true }).click()
      await expect(dialog).not.toBeVisible()
      await expect(names).toHaveText(['生产模型', '停用模型'])
      expect(scenario.calls.every(call => call.method === 'GET')).toBe(true)
    })

    test('审计分页的七个页码及省略号完整可达，详情抽屉不越界', async ({ page, scenario, baseURL }) => {
      scenario.admin = true
      const total = 2500
      const listCalls = await mockGet(page, baseURL, '/admin/audit/logs', url => {
        const current = Number(url.searchParams.get('page'))
        const size = Number(url.searchParams.get('page_size'))
        return {
          items: Array.from({ length: size }, (_, index) => auditLog((current - 1) * size + index + 1)),
          total, page: current, page_size: size,
        } satisfies AuditLogListResponse
      })
      const statsCalls = await mockGet(page, baseURL, '/admin/audit/stats', () => ({
        today_count: 20, today_guest_count: 0, today_failed_count: 0, total_count: total,
        type_distribution: { proofread_text: total },
      } satisfies AuditStats))
      const actionCalls = await mockGet(page, baseURL, '/admin/audit/action-types', () => ({
        items: [{ action: 'proofread_text', count: total }],
      }))
      const detailCalls = await mockGet(page, baseURL, '/admin/audit/logs/1', () => ({
        ...auditLog(1), user_agent: 'TextMirror offline responsive browser', input_text: source,
        input_length: Array.from(source).length, output_text: corrected, output_length: Array.from(corrected).length,
        extra_params: { domain: 'general' }, file_id: null, file_path: null, file_size: null,
        error_message: null, token_usage: { total_tokens: 0 },
      } satisfies AuditLogDetail))
      await page.goto('/admin/audit')
      const rows = page.locator('.audit-page .el-table__body-wrapper .el-table__row')
      const pagination = page.locator('.audit-page .el-pagination')
      await expect(rows).toHaveCount(20)
      await expect(pagination.locator('.el-pager .number')).toHaveCount(7)
      await expect(pagination.locator('.el-pager .more')).toHaveCount(1)
      await paginationBounds(page)

      const details = rows.first().getByRole('button', { name: '详情', exact: true })
      await reachable(details, page.locator('.audit-page .el-table'))
      await details.click()
      const drawer = page.getByRole('dialog', { name: '日志详情', exact: true })
      await within(drawer, undefined, true)
      await expect.poll(async () => (await drawer.boundingBox())?.width).toBeCloseTo(Math.min(560, width), 0)
      const output = drawer.locator('.text-block').filter({ hasText: corrected })
      await output.scrollIntoViewIfNeeded()
      await within(output, drawer, true)
      const close = drawer.locator('.el-drawer__close-btn')
      await reachable(close, drawer)
      await close.click()
      await expect(drawer).not.toBeVisible()

      await reachable(pagination.locator('.btn-next'), pagination)
      await pagination.locator('.btn-next').click()
      await expect(pagination.locator('.number.is-active')).toHaveText('2')
      await expect(rows.first()).toContainText('回归用户21')
      const sixth = pagination.locator('.el-pager .number').filter({ hasText: /^6$/ })
      await reachable(sixth, pagination)
      await sixth.click()
      await expect(pagination.locator('.number.is-active')).toHaveText('6')
      await expect(rows.first()).toContainText('回归用户101')
      await expect(pagination.locator('.el-pager .number')).toHaveCount(7)
      await expect(pagination.locator('.el-pager .more')).toHaveCount(2)
      await paginationBounds(page)

      const sizes = pagination.locator('.el-select')
      await reachable(sizes, pagination)
      await sizes.click()
      const fifty = page.getByRole('option', { name: /50\s*条\/页/ })
      await reachable(fifty)
      await fifty.click()
      await expect(rows).toHaveCount(50)
      await expect(rows.first()).toContainText('回归用户251')
      await expect(pagination.locator('.number.is-active')).toHaveText('6')
      await expect(sizes).toContainText('50')
      await paginationBounds(page)
      expect(listCalls.map(url => [url.searchParams.get('page'), url.searchParams.get('page_size')])).toEqual([
        ['1', '20'], ['2', '20'], ['6', '20'], ['6', '50'],
      ])
      expect(statsCalls).toHaveLength(1)
      expect(actionCalls).toHaveLength(1)
      expect(detailCalls).toHaveLength(1)
      expect(scenario.calls.every(call => call.method === 'GET')).toBe(true)
    })

    if ([320, 375, 1440].includes(width)) {
      test('系统设置标签与输入自适应，图标和保存操作不被裁切', async ({ page, scenario, baseURL }) => {
        scenario.admin = true
        await mockGet(page, baseURL, '/admin/settings/site', () => ({ platform_name: 'TextMirror' }))
        await mockGet(page, baseURL, '/admin/system-config/basic', () => ({
          version: '1.0.0', debug: false, allow_register: false, maintenance_mode: false,
        }))
        await mockGet(page, baseURL, '/admin/system-config/security', () => ({ default_password: '******' }))
        await mockGet(page, baseURL, '/admin/fact-check/settings', () => ({
          enabled: false, provider: 'model', model_config_id: null, max_claims: 10, sources: [],
          api_key_configured: false, model_name: '离线模型', model_search_supported: true, model_search_reason: '',
        }))
        await page.goto('/admin/settings')
        const brand = page.locator('.admin-settings > .el-card').first()
        const name = brand.getByPlaceholder('请输入平台名称', { exact: true })
        await expect(name).toHaveValue('TextMirror')
        await reachable(name, brand)
        await name.fill('自适应布局测试')
        const label = brand.locator('.el-form-item__label').filter({ hasText: /^平台名称$/ })
        const inputBox = (await name.boundingBox())!
        const labelBox = (await label.boundingBox())!
        if (mobile) {
          expect(labelBox.y + labelBox.height).toBeLessThanOrEqual(inputBox.y)
          expect(inputBox.width).toBeGreaterThan(150)
        } else {
          expect(labelBox.x + labelBox.width).toBeLessThanOrEqual(inputBox.x)
        }
        await reachable(brand.locator('.icon-upload button'), brand)
        await within(brand.locator('.favicon-config'), brand)
        await reachable(brand.getByRole('button', { name: '保存品牌设置', exact: true }), brand)
        expect(scenario.calls.every(call => call.method === 'GET')).toBe(true)
      })
    }

    if ([375, 430, 1440].includes(width)) {
      test('新建词库与批量导入放行词可填写和取消，关闭与底部按钮可达', async ({ page, scenario, baseURL }) => {
        const calls = await mockWordLists(page, baseURL)
        await page.goto('/dictionary')
        await expect(page.locator('.dict-list-card .el-table__row')).toHaveCount(1)
        await page.getByRole('button', { name: '新建词库', exact: true }).click()
        const dictionary = page.getByRole('dialog', { name: '新建词库', exact: true })
        await within(dictionary)
        const name = dictionary.getByPlaceholder('词库名称')
        const description = dictionary.getByPlaceholder('词库描述（可选）')
        await reachable(name, dictionary)
        await name.fill('手机端测试词库')
        await reachable(description, dictionary)
        await description.fill('仅验证表单，不提交真实数据。')
        await expect(name).toHaveValue('手机端测试词库')
        await expect(description).toHaveValue('仅验证表单，不提交真实数据。')
        await dialogControls(dictionary, 420)
        await dictionary.getByRole('button', { name: '取消', exact: true }).click()
        await expect(dictionary).not.toBeVisible()
        await page.getByRole('button', { name: '新建词库', exact: true }).click()
        await expect(name).toHaveValue('')
        await reachable(dictionary.locator('.el-dialog__headerbtn'), dictionary)
        await dictionary.locator('.el-dialog__headerbtn').click()
        await expect(dictionary).not.toBeVisible()

        await page.goto('/whitelist')
        await expect(page.locator('.whitelist-page .el-table__row')).toHaveCount(1)
        await page.getByRole('button', { name: '批量导入', exact: true }).click()
        const whitelist = page.getByRole('dialog', { name: '批量导入放行词', exact: true })
        await within(whitelist)
        const words = whitelist.getByRole('textbox')
        await reachable(words, whitelist)
        await words.fill('TextMirror,产品名称\n离线术语')
        await expect(words).toHaveValue('TextMirror,产品名称\n离线术语')
        await dialogControls(whitelist, 480)
        await whitelist.getByRole('button', { name: '取消', exact: true }).click()
        await expect(whitelist).not.toBeVisible()
        expect(calls.dictionary).toHaveLength(1)
        expect(calls.whitelist).toHaveLength(1)
        expect(scenario.calls.every(call => call.method === 'GET')).toBe(true)
      })
    }

    if ([375, 1440].includes(width)) {
      test('恢复文档后修改、撤销、导出下载，并通过用户导航进入词库', async ({ page, scenario, baseURL }) => {
        const calls = await mockWordLists(page, baseURL)
        await page.goto('/proofread/document?review=7')
        const preview = page.locator('.review-preview .preview-text')
        await expect(preview).toHaveText(source)
        const issue = page.locator('.issue-item').first()
        const accept = issue.getByRole('button', { name: '仅修改此处', exact: true })
        await reachable(accept, issue)
        await press(accept)
        await expect(preview).toHaveText(corrected)
        const undo = issue.getByRole('button', { name: '撤销', exact: true })
        await reachable(undo, issue)
        await press(undo)
        await expect(preview).toHaveText(source)
        await reachable(accept, issue)
        await press(accept)
        await expect(preview).toHaveText(corrected)
        const exportButton = page.getByRole('button', { name: '导出 Word（纯文本）', exact: true })
        await reachable(exportButton, page.locator('.result-toolbar'))
        const downloading = page.waitForEvent('download')
        await press(exportButton)
        const download = await downloading
        expect(download.suggestedFilename()).toBe('已采纳_浏览器回归.docx')
        expect(await download.failure()).toBeNull()
        expect(await readFile((await download.path())!)).toEqual(exportedBytes)
        await expect(exportButton).toBeEnabled()
        expect(scenario.review.modified_text).toBe(corrected)
        expect(scenario.review.issues[0]._accepted).toBe(true)
        expect(scenario.review.issues[1]._accepted).not.toBe(true)
        expect(scenario.calls.filter(call => call.method !== 'GET' && call.path !== '/proofread/feedback')
          .map(call => `${call.method} ${call.path}`)).toEqual([
          'PUT /history/7/review', 'POST /history/7/export',
        ])
        expect(scenario.calls.some(call => call.path === '/document/proofread/async')).toBe(false)
        // 触屏模拟仍保留鼠标指针，悬停提示会暂停其自动关闭计时。
        await page.mouse.move(width - 1, page.viewportSize()!.height - 1)
        await expect(page.locator('.el-message')).toHaveCount(0)

        const navigation = page.getByRole('button', { name: '打开导航菜单', exact: true })
        for (const [label, path] of [['个性化词库', '/dictionary'], ['放行词管理', '/whitelist']]) {
          if (mobile) {
            await reachable(navigation)
            await navigation.tap()
            const drawer = page.locator('.mobile-drawer')
            await within(drawer, undefined, true)
            const item = drawer.getByRole('menuitem', { name: label, exact: true })
            await reachable(item, drawer)
            await item.tap()
            await expect(drawer).not.toBeVisible()
          } else {
            await expect(navigation).toBeHidden()
            const item = page.locator('.workspace-sidebar').getByRole('menuitem', { name: label, exact: true })
            await reachable(item)
            await item.click()
          }
          await expect(page).toHaveURL(new RegExp(`${path}$`))
          await expect(page.locator('.user-main .el-table__row')).toHaveCount(1)
        }
        expect(calls.dictionary).toHaveLength(1)
        expect(calls.whitelist).toHaveLength(1)
      })
    }
  })
}
