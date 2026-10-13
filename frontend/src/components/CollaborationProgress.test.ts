/* eslint-disable vue/one-component-per-file -- Lightweight Element Plus test doubles, no browser dependency. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as Vue from 'vue'
import { createRenderer, createSSRApp, defineComponent, h, nextTick, reactive, type Component, type VNode } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc'
import { ModuleKind, ScriptTarget, transpileModule } from 'typescript'
import type { CollaborationReport, CollaborationRoleStatus } from '@/api/collaboration'
import * as collaborationUtils from '@/utils/collaboration'
import source from './CollaborationProgress.vue?raw'
import textPage from '@/views/user/proofread/TextProofread.vue?raw'

const elements = {
  ElTag: defineComponent({ props: { type: { type: String, default: '' } }, setup: (props, { slots }) => () => h('span', { 'data-type': props.type }, slots.default?.()) }),
  ElButton: defineComponent({ setup: (_props, { slots }) => () => h('button', slots.default?.()) }),
}
const { descriptor } = parse(source)
const script = compileScript(descriptor, { id: 'collaboration-test' })
const template = compileTemplate({ source: descriptor.template!.content, filename: 'CollaborationProgress.vue', id: 'collaboration-test', compilerOptions: { bindingMetadata: script.bindings } })
if (template.errors.length) throw new Error(String(template.errors[0]))
const modules: Record<string, unknown> = { vue: Vue, 'element-plus': elements, '@/utils/collaboration': collaborationUtils }
const compiled = { exports: {} as { default: Component; render: () => VNode } }
const code = transpileModule(`${script.content}\n${template.code}`, { compilerOptions: { module: ModuleKind.CommonJS, target: ScriptTarget.ES2020 } }).outputText
new Function('require', 'module', 'exports', code)((id: string) => {
  if (!(id in modules)) throw new Error(`Unexpected module ${id}`)
  return modules[id]
}, compiled, compiled.exports)
const Panel = Object.assign(compiled.exports.default, { render: compiled.exports.render })
const report = (status: CollaborationRoleStatus = 'running'): CollaborationReport => ({
  status: 'running', config_id: 2, model_name: 'shared-model', reviewed_count: 0, review_limit: 20,
  roles: ['rules', 'language', 'consistency', 'reviewer'].map((id, index) => ({ id: id as CollaborationReport['roles'][number]['id'], name: ['规则检查', '语言审校', '一致性审校', '复核'][index], status, message: '真实进度消息', issue_count: 3, elapsed_ms: 1234, usage: { prompt_tokens: 100, completion_tokens: 20, total_tokens: 120 } })),
  findings: [{ original: '帐号', suggestion: '账号', start: 0, end: 2, type: 'typo', severity: 'warning', found_by: ['language'], review_status: 'disputed', review_note: '请人工核对' }],
})
const render = (props: Record<string, unknown>) => renderToString(createSSRApp(Panel, props))
const mainPanel = (html: string) => html.slice(0, html.indexOf('<details'))

interface HostNode { tag: string; text: string; props: Record<string, unknown>; parent: HostNode | null; children: HostNode[] }
const node = (text = '', tag = ''): HostNode => ({ tag, text, props: {}, parent: null, children: [] })
function remove(child: HostNode) {
  const index = child.parent?.children.indexOf(child) ?? -1
  if (index >= 0) child.parent!.children.splice(index, 1)
  child.parent = null
}
function insert(child: HostNode, parent: HostNode, anchor: HostNode | null = null) {
  remove(child)
  const index = anchor ? parent.children.indexOf(anchor) : -1
  parent.children.splice(index < 0 ? parent.children.length : index, 0, child)
  child.parent = parent
}
const renderer = createRenderer<HostNode, HostNode>({
  createElement: tag => node('', tag), createText: text => node(text), createComment: () => node(),
  setText: (target, text) => { target.text = text },
  setElementText: (target, text) => { target.text = text; target.children = [] },
  patchProp: (target, key, _old, value) => { target.props[key] = value },
  parentNode: target => target.parent,
  nextSibling: target => target.parent?.children[target.parent.children.indexOf(target) + 1] ?? null,
  insert, remove,
  insertStaticContent(content, parent, anchor) { const target = node(content); insert(target, parent, anchor); return [target, target] },
})
function descendants(root: HostNode): HostNode[] { return [root, ...root.children.flatMap(descendants)] }
function text(root: HostNode): string { return root.text + root.children.map(text).join('') }
const cleanups: (() => void)[] = []
function mount(initial: Record<string, unknown>) {
  const props = reactive(initial)
  const events = { onReconnect: vi.fn(), onCancel: vi.fn(), onRerun: vi.fn() }
  const app = renderer.createApp(defineComponent({ setup: () => () => h(Panel, { ...props, ...events }) }))
  const root = node()
  app.mount(root)
  cleanups.push(() => app.unmount())
  return { props, root, events }
}
afterEach(() => cleanups.splice(0).forEach(cleanup => cleanup()))

describe('CollaborationProgress', () => {
  it.each([
    ['pending', '等待执行', 'info'], ['running', '执行中', 'primary'], ['success', '已完成', 'success'],
    ['failed', '失败', 'danger'], ['skipped', '已跳过', 'warning'], ['cancelled', '已取消', 'warning'],
  ] as const)('renders real %s role status, not a fabricated successful agent', async (status, label, color) => {
    const html = await render({ report: report(status) })
    expect(html).toContain(`data-status="${status}"`)
    expect(html).toContain(`data-type="${color}"`)
    expect(html).toContain(label)
    expect(html).toContain('规则检查')
    expect(html).toContain('规则检查 → 文字表达与全文一致性 → 疑点复核')
    expect(html).toContain('真实进度消息')
    expect(html).toContain('发现 3 条')
    expect(html).toContain('1.2 秒')
    expect(html).toContain('prompt_tokens 100')
    expect(html).toContain('total_tokens 120')
  })
  it('shows partial findings readonly, source and disputed verdict, with paid rerun warning', async () => {
    const html = await render({ report: { ...report('failed'), status: 'partial' }, taskStatus: 'FAILURE', taskId: 'task', viewOnly: true })
    expect(html).toContain('任务失败 · 仅供查看')
    expect(html).toContain('流程未完整完成')
    expect(html).toContain('重新运行完整分工审校（另计费）')
    expect(html).toContain('已发现问题（只读，不可采纳）')
    expect(html).toContain('来源：文字表达 · 存在疑点：请人工核对')
    expect(html).not.toContain('请求取消')
  })
  it('remains visible on completion and states bounded budget and human decision', async () => {
    const html = await render({ report: { ...report('success'), status: 'complete', reviewed_count: 1 }, taskStatus: 'SUCCESS' })
    expect(html).toContain('流程已完成（不保证全文无误）')
    expect(html).toContain('一轮疑点复核最多 20 条')
    expect(html).toContain('一次流程扣一次应用额度')
    expect(html).toContain('最多 3 次模型调用')
    expect(html).toContain('不会自动联网、无限辩论或自动采纳修改')
    expect(html).not.toContain('重新运行完整分工审校（另计费）')
  })
  it('distinguishes cancellation request from terminal state and offers reconnect without resubmit', async () => {
    const html = await render({ report: report(), taskId: 'task', taskStatus: 'RUNNING', cancelRequested: true, error: 'offline' })
    expect(html).toContain('等待服务端确认')
    expect(html).toContain('可能先完成并产生费用')
    expect(html).toContain('重连监控（不重新提交）')
    expect(html).not.toContain('任务已取消 · 仅供查看')
  })
  it('does not label missing usage as zero cost', async () => {
    const data = report('pending')
    const html = await render({ report: { ...data, roles: data.roles.map(role => ({ ...role, usage: {} })) } })
    expect(html).toContain('Token 用量：未报告')
  })
  it('keeps the first screen concise and moves model, usage and detailed progress into closed details', async () => {
    const html = await render({ report: report(), message: '语言审校与一致性审校正在并行执行' })
    const main = mainPanel(html)
    expect(main).toContain('<strong>分工审校</strong>')
    expect(main).toContain('检查进行中')
    for (const label of ['规则检查', '文字表达', '全文一致性', '疑点复核']) expect(main).toContain(label)
    for (const jargon of ['共享模型', 'Token', '模型调用', 'Agent', '真实进度消息', '已报告耗时']) expect(main).not.toContain(jargon)
    expect(html).toMatch(/<details[^>]*class="check-details"[^>]*><summary>查看检查详情与用量<\/summary>/)
    expect(html).not.toMatch(/<details[^>]*\sopen(?:[\s=>])/)
    expect(html).toContain('由同一模型按不同职责并行检查')
    expect(html).toContain('不是多模型对比')
    expect(html).toContain('共享模型：shared-model')
    expect(html).toContain('文字表达与全文一致性正在并行执行')
    expect(html).not.toMatch(/协作审校|语言审校|一致性审校|争议复核/)
  })
  it('shows unreviewed items and doubts outside collapsed details even for a complete flow', async () => {
    const data = report('success')
    const html = await render({ report: { ...data, status: 'complete', reviewed_count: 1, findings: [data.findings[0], { ...data.findings[0], review_status: 'not_reviewed', review_note: '' }] }, taskStatus: 'SUCCESS' })
    const main = mainPanel(html)
    expect(main).toContain('流程已完成（不保证全文无误）')
    expect(main).toContain('已发现 2 条 · 已复核 1 条')
    expect(main).toContain('仍有 1 条未复核，不能视为已确认')
    expect(main).toContain('有 1 条仍存在疑点，需人工核对')
    expect(main).toContain('role="alert"')
    expect(main).not.toContain('真实进度消息')
  })
  it.each([20, 50])('keeps the 20-item budget overflow warning visible with reported limit %s', async limit => {
    const data = report('success')
    const findings = Array.from({ length: 25 }, (_, index) => ({ ...data.findings[0], review_status: index < 20 ? 'confirmed' as const : 'not_reviewed' as const, review_note: index < 20 ? '' : '超出本轮20条复核预算，未复核，需人工核对' }))
    const main = mainPanel(await render({ report: { ...data, status: 'partial', review_limit: limit, reviewed_count: 20, findings }, taskStatus: 'SUCCESS' }))
    expect(main).toContain('已发现 25 条 · 已复核 20 条')
    expect(main).toContain('仍有 5 条未复核')
    expect(main).toContain('其中 5 条超出本轮 20 条复核预算')
    expect(main).toContain('流程部分完成')
    expect(main).toContain('重新运行完整分工审校（另计费）')
  })
  it('does not claim all findings are review candidates, or that a skipped review verified them', async () => {
    const data = report('success')
    const findings = Array.from({ length: 25 }, () => ({ ...data.findings[0], review_status: 'not_reviewed' as const, review_note: '' }))
    const roles = data.roles.map(role => role.id === 'reviewer' ? { ...role, status: 'skipped', message: '无冲突或疑点，明确建议未逐条复核' } : role)
    const main = mainPanel(await render({ report: { ...data, roles, findings, status: 'complete', review_limit: 10 }, taskStatus: 'SUCCESS' }))
    expect(main).toContain('本轮疑点复核最多 10 条，不代表全部发现已复核')
    expect(main).toContain('仍有 25 条未复核')
    expect(main).toContain('疑点复核已跳过：无冲突或疑点，明确建议未逐条复核')
    expect(main).not.toContain('条超出本轮')
  })
  it('does not invent an incomplete review when no findings need review', async () => {
    const data = report('success')
    const roles = data.roles.map(role => role.id === 'reviewer' ? { ...role, status: 'skipped', message: '没有发现问题，无需复核' } : role)
    const main = mainPanel(await render({ report: { ...data, roles, findings: [], status: 'complete' }, taskStatus: 'SUCCESS' }))
    expect(main).toContain('疑点复核已跳过：没有发现问题，无需复核')
    expect(main).not.toContain('条未复核')
    expect(main).not.toContain('复核未完成')
  })
  it.each(['FAILURE', 'REVOKED', 'CANCELLED'])('keeps %s explicit even without a report', async taskStatus => {
    const main = mainPanel(await render({ report: null, taskStatus, taskId: 'task' }))
    expect(main).toContain(taskStatus === 'FAILURE' ? '任务失败 · 仅供查看' : '任务已取消 · 仅供查看')
    expect(main).toContain('流程未完整完成')
    expect(main).toContain('另计额度与费用')
    expect(main).not.toContain('请求取消')
  })
  it('shows failed review reason before the disclosure instead of hiding it with normal role details', async () => {
    const data = report('success')
    const roles = data.roles.map(role => role.id === 'reviewer' ? { ...role, status: 'failed', message: '复核失败；保留原建议，需人工核对' } : role)
    const main = mainPanel(await render({ report: { ...data, roles, status: 'partial' }, taskStatus: 'SUCCESS' }))
    expect(main).toContain('疑点复核未完成：复核失败；保留原建议，需人工核对')
    expect(main).toContain('data-status="failed"')
    expect(main).toContain('流程未完整完成')
  })
  it('shows waiting rather than inventing progress when a report is unavailable or not started', async () => {
    expect(mainPanel(await render({ report: null }))).toContain('等待任务状态')
    expect(mainPanel(await render({ report: null, taskStatus: 'PENDING' }))).toContain('等待执行')
    expect(mainPanel(await render({ report: report('pending') }))).not.toContain('检查进行中')
    expect(mainPanel(await render({ report: null, taskStatus: 'SUCCESS' }))).toContain('任务已结束 · 等待报告')
    expect(mainPanel(await render({ report: report(), taskStatus: 'SUCCESS' }))).toContain('任务已结束 · 等待完整报告')
  })
  it.each(['complete', 'partial', 'cancelled'] as const)('folds opened progress details on %s and does not emit actions on updates', async outcome => {
    const { root, props, events } = mount({ report: report(), taskId: 'task', taskStatus: 'RUNNING' })
    const details = () => descendants(root).find(item => item.tag === 'details' && item.props.class === 'check-details')!
    const before = details()
    before.props.open = true // Native details toggling does not change Vue state.
    props.report = report()
    await nextTick()
    expect(details()).toBe(before)
    expect(details().props.open).toBe(true)
    props.report = { ...report('success'), status: outcome === 'cancelled' ? 'running' : outcome }
    props.taskStatus = outcome === 'cancelled' ? 'CANCELLED' : 'SUCCESS'
    await nextTick()
    expect(details()).not.toBe(before)
    expect(details().props.open).toBeUndefined()
    details().props.open = true
    props.report = { ...report('success'), status: 'complete' }
    await nextTick()
    expect(details().props.open).toBe(true) // Finishing updates must not keep forcing it closed.
    for (const event of Object.values(events)) expect(event).not.toHaveBeenCalled()
  })
  it('emits only the requested action and preserves cancellation and paid rerun guards', async () => {
    const { root, props, events } = mount({ report: report(), taskId: 'task', taskStatus: 'RUNNING', monitoring: false, cancelling: false, cancelRequested: false })
    const buttons = () => descendants(root).filter(item => item.tag === 'button')
    const button = (label: string) => buttons().find(item => text(item).includes(label))!
    for (const event of Object.values(events)) expect(event).not.toHaveBeenCalled()
    ;(button('重连监控').props.onClick as () => void)()
    expect(events.onReconnect).toHaveBeenCalledExactlyOnceWith()
    expect(events.onCancel).not.toHaveBeenCalled()
    expect(events.onRerun).not.toHaveBeenCalled()
    ;(button('请求取消').props.onClick as () => void)()
    expect(events.onCancel).toHaveBeenCalledExactlyOnceWith()
    props.cancelling = true
    await nextTick()
    expect(button('请求取消').props.disabled).toBe(true)
    props.cancelling = false
    props.cancelRequested = true
    await nextTick()
    expect(button('请求取消').props.disabled).toBe(true)
    expect(text(root)).toContain('等待服务端确认')
    expect(text(root)).toContain('已产生的用量仍计费')
    props.taskStatus = 'CANCELLED'
    props.monitoring = true
    await nextTick()
    expect(button('请求取消')).toBeUndefined()
    expect(button('重连监控')).toBeUndefined()
    ;(button('另计费').props.onClick as () => void)()
    expect(events.onRerun).toHaveBeenCalledExactlyOnceWith()
    expect(events.onReconnect).toHaveBeenCalledTimes(1)
    expect(events.onCancel).toHaveBeenCalledTimes(1)
  })
  it('text page wires report outside result branch, excludes normal retry, and retains report on version restore', () => {
    expect(textPage.indexOf('<CollaborationProgress')).toBeLessThan(textPage.indexOf('v-if="!showResult"'))
    expect(textPage).toContain('v-if="!compareResult && !collaboration"')
    expect(textPage).toMatch(/<ProofreadOptions\s+v-model:mode="proofreadMode"/)
    expect(textPage).toContain("collaboration.value = review.collaboration ?? null")
    const restore = textPage.slice(textPage.indexOf('function restoreVersion('), textPage.indexOf('async function confirmLeave('))
    expect(restore).not.toContain('collaboration.value =')
    expect(textPage).toContain('if (collaboration.value) lines.push(issueProvenance(issue))')
    expect(textPage).toContain('collaboration.value.status !== \'complete\'')
    expect(textPage).toContain('query: { collaboration_task: id }')
    expect(textPage).toContain('query: { review: String(result.record_id) }')
  })
})
