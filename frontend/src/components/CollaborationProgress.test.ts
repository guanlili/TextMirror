/* eslint-disable vue/one-component-per-file -- Lightweight Element Plus test doubles, no browser dependency. */
import { describe, expect, it } from 'vitest'
import * as Vue from 'vue'
import { createSSRApp, defineComponent, h, type Component, type VNode } from 'vue'
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
    expect(html).toContain('语言与一致性并行审校')
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
    expect(html).toContain('重新运行完整协作（另计费）')
    expect(html).toContain('已发现问题（只读，不可采纳）')
    expect(html).toContain('来源：语言审校 · 存在争议：请人工核对')
    expect(html).not.toContain('请求取消')
  })
  it('remains visible on completion and states bounded budget and human decision', async () => {
    const html = await render({ report: { ...report('success'), status: 'complete', reviewed_count: 1 }, taskStatus: 'SUCCESS' })
    expect(html).toContain('协作流程已完成（不保证全文无误）')
    expect(html).toContain('一轮复核（最多 20 条）')
    expect(html).toContain('一次流程扣一次应用额度')
    expect(html).toContain('最多 3 次模型调用')
    expect(html).toContain('不会自动联网、无限辩论或自动采纳修改')
    expect(html).not.toContain('重新运行完整协作（另计费）')
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
  it('text page wires report outside result branch, excludes normal retry, and retains report on version restore', () => {
    expect(textPage.indexOf('<CollaborationProgress')).toBeLessThan(textPage.indexOf('v-if="!showResult"'))
    expect(textPage).toContain('v-if="!compareResult && !collaboration"')
    expect(textPage).toContain('v-if="proofreadMode === \'single\'" class="setting-row"')
    expect(textPage).toContain("collaboration.value = review.collaboration ?? null")
    const restore = textPage.slice(textPage.indexOf('function restoreVersion('), textPage.indexOf('async function confirmLeave('))
    expect(restore).not.toContain('collaboration.value =')
    expect(textPage).toContain('if (collaboration.value) lines.push(issueProvenance(issue))')
    expect(textPage).toContain('collaboration.value.status !== \'complete\'')
    expect(textPage).toContain('query: { collaboration_task: id }')
    expect(textPage).toContain('query: { review: String(result.record_id) }')
  })
})
