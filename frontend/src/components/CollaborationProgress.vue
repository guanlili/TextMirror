<template>
  <section
    class="collaboration-panel"
    aria-label="分工审校进度"
    data-testid="collaboration-progress"
  >
    <header>
      <strong>分工审校</strong>
      <el-tag
        role="status"
        :type="failed ? 'danger' : cancelRequested && !terminal ? 'warning' : report?.status === 'complete' ? 'success' : report?.status === 'partial' ? 'warning' : 'info'"
      >
        {{ statusLabel }}
      </el-tag>
    </header>
    <p class="pipeline">
      规则检查 → 文字表达与全文一致性 → 疑点复核
    </p>
    <ul
      v-if="report"
      class="steps"
      aria-label="各项检查状态"
    >
      <li
        v-for="role in report.roles"
        :key="role.id"
        :data-role="role.id"
        :data-status="role.status"
      >
        <span>{{ collaborationRoleLabel(role.id, role.name) }}</span>
        <el-tag
          size="small"
          :type="collaborationStatusTypes[role.status]"
        >
          {{ collaborationStatusLabels[role.status] }}
        </el-tag>
      </li>
    </ul>
    <p v-if="report">
      已发现 {{ report.findings.length }} 条 · 已复核 {{ report.reviewed_count }} 条。修改由你决定。
    </p>
    <div
      v-if="reviewSummary.unreviewed || reviewSummary.disputed || reviewSummary.overBudget"
      class="warning"
      role="alert"
    >
      <p v-if="reviewSummary.unreviewed">
        仍有 {{ reviewSummary.unreviewed }} 条未复核，不能视为已确认。
      </p>
      <p v-if="reviewSummary.overBudget">
        其中 {{ reviewSummary.overBudget }} 条超出本轮 {{ reviewLimit }} 条复核预算，需人工核对。
      </p>
      <p v-if="reviewSummary.disputed">
        有 {{ reviewSummary.disputed }} 条仍存在疑点，需人工核对。
      </p>
    </div>
    <p
      v-if="report && report.findings.length > reviewLimit && !reviewSummary.overBudget"
      class="warning"
      role="status"
    >
      本轮疑点复核最多 {{ reviewLimit }} 条，不代表全部发现已复核。
    </p>
    <p
      v-for="role in interruptedRoles"
      :key="role.id"
      class="warning"
      role="alert"
    >
      {{ collaborationRoleLabel(role.id, role.name) }}{{ role.id === 'reviewer' ? '未完成' : collaborationStatusLabels[role.status] }}：{{ collaborationDisplayMessage(role.message) }}
    </p>
    <p
      v-if="reviewer?.status === 'skipped'"
      class="muted"
    >
      疑点复核已跳过：{{ collaborationDisplayMessage(reviewer.message) }}
    </p>
    <p
      v-if="report?.status === 'partial' || failed"
      class="warning"
      role="alert"
    >
      流程未完整完成；保留此前发现，仅供参考。普通补查不能补全分工审校。可手动重新运行完整流程（另计额度与费用）。
    </p>
    <p
      v-if="cancelRequested && !terminal"
      class="warning"
      role="status"
    >
      取消请求已发送，等待服务端确认；正在执行的请求可能先完成并产生费用。
    </p>
    <div
      v-if="error"
      class="warning"
      role="alert"
    >
      状态连接中断：{{ collaborationDisplayMessage(error) }}。重连只读取原任务，不会重新提交或新增计费调用。
    </div>
    <div class="actions">
      <el-button
        v-if="taskId && !monitoring"
        size="small"
        @click="$emit('reconnect')"
      >
        重连监控（不重新提交）
      </el-button>
      <el-button
        v-if="taskId && !terminal"
        size="small"
        :loading="cancelling"
        :disabled="cancelling || cancelRequested"
        @click="$emit('cancel')"
      >
        请求取消
      </el-button>
      <el-button
        v-if="(report?.status === 'partial' || failed) && (terminal || !taskId)"
        size="small"
        type="warning"
        plain
        @click="$emit('rerun')"
      >
        重新运行完整分工审校（另计费）
      </el-button>
    </div>
    <p
      v-if="taskId && !terminal"
      class="muted"
    >
      取消后，已产生的用量仍计费；重连只读取原任务，不会重新提交或新增计费调用。
    </p>
    <details
      :key="`${taskId}-${finished}`"
      class="check-details"
    >
      <summary>查看检查详情与用量</summary>
      <p class="muted">
        规则检查不调用模型；文字表达与全文一致性由同一模型按不同职责并行检查，再对疑点复核，不是多模型对比。
      </p>
      <p class="muted">
        一次流程扣一次应用额度；最多 3 次模型调用，另可能有服务商传输重试，实际供应商费用按 Token 计。一轮疑点复核最多 {{ reviewLimit }} 条，不会自动联网、无限辩论或自动采纳修改。
      </p>
      <p
        v-if="report"
        class="muted"
      >
        共享模型：{{ report.model_name || '未报告' }}
      </p>
      <p
        v-if="message"
        role="status"
      >
        {{ collaborationDisplayMessage(message) }}
      </p>
      <div
        v-if="report"
        class="roles"
      >
        <article
          v-for="role in report.roles"
          :key="role.id"
          :data-status="role.status"
        >
          <div class="role-heading">
            <strong>{{ collaborationRoleLabel(role.id, role.name) }}</strong>
            <el-tag
              size="small"
              :type="collaborationStatusTypes[role.status]"
            >
              {{ collaborationStatusLabels[role.status] }}
            </el-tag>
          </div>
          <p>{{ collaborationDisplayMessage(role.message) }}</p>
          <p class="muted">
            {{ role.id === 'reviewer' ? '已复核' : '发现' }} {{ role.issue_count }} 条 · 已报告耗时 {{ (role.elapsed_ms / 1000).toFixed(1) }} 秒
          </p>
          <p class="muted">
            {{ usageLabel(role.usage) }}
          </p>
        </article>
      </div>
    </details>
    <details
      v-if="report?.findings.length && viewOnly"
      class="findings"
    >
      <summary>已发现问题（只读，不可采纳）</summary>
      <ol>
        <li
          v-for="(finding, index) in report.findings"
          :key="`${finding.start ?? ''}-${finding.end ?? ''}-${finding.type}-${index}`"
        >
          <p>{{ finding.original }} → {{ finding.suggestion || '需人工核对' }}</p>
          <p class="muted">
            {{ collaborationProvenance(finding, report) }}
          </p>
          <p
            v-if="finding.explanation"
            class="muted"
          >
            {{ finding.explanation }}
          </p>
        </li>
      </ol>
    </details>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { ElButton, ElTag } from 'element-plus'
import type { CollaborationReport } from '@/api/collaboration'
import { collaborationCoverageLabel, collaborationDisplayMessage, collaborationProvenance, collaborationRoleLabel, collaborationStatusLabels, collaborationStatusTypes } from '@/utils/collaboration'
const props = withDefaults(defineProps<{
  report: CollaborationReport | null
  taskId?: string
  taskStatus?: string
  message?: string
  error?: string
  monitoring?: boolean
  cancelling?: boolean
  cancelRequested?: boolean
  viewOnly?: boolean
}>(), { taskId: '', taskStatus: '', message: '', error: '', monitoring: false, cancelling: false, cancelRequested: false, viewOnly: false })
defineEmits<{ reconnect: []; cancel: []; rerun: [] }>()
const failed = computed(() => ['FAILURE', 'REVOKED', 'CANCELLED'].includes(props.taskStatus))
const terminal = computed(() => failed.value || props.taskStatus === 'SUCCESS')
// Native details stays closed initially and resets on completion (even if opened during progress).
const finished = computed(() => terminal.value || !!props.report && props.report.status !== 'running')
const statusLabel = computed(() => {
  if (failed.value) return props.taskStatus === 'FAILURE' ? '任务失败 · 仅供查看' : '任务已取消 · 仅供查看'
  if (props.cancelRequested && !terminal.value) return '正在请求取消 · 等待确认'
  if (props.report) {
    if (props.report.status === 'running') {
      if (terminal.value) return '任务已结束 · 等待完整报告'
      if (props.report.roles.length && props.report.roles.every(role => role.status === 'pending')) return '等待执行'
    }
    return collaborationCoverageLabel(props.report)
  }
  if (props.taskStatus === 'PENDING') return '等待执行'
  return props.taskStatus === 'SUCCESS' ? '任务已结束 · 等待报告' : '等待任务状态'
})
const reviewLimit = computed(() => Math.max(0, Math.min(20, props.report?.review_limit ?? 20)))
const reviewer = computed(() => props.report?.roles.find(role => role.id === 'reviewer'))
const interruptedRoles = computed(() => props.report?.roles.filter(role => ['failed', 'cancelled'].includes(role.status)) ?? [])
const reviewSummary = computed(() => {
  const summary = { unreviewed: 0, disputed: 0, overBudget: 0 }
  for (const finding of props.report?.findings ?? []) {
    if (finding.review_status === 'not_reviewed') {
      summary.unreviewed++
      // The backend marks only budget-excluded candidates; not every finding needs this round of review.
      if (/超出.*复核预算/.test(finding.review_note)) summary.overBudget++
    }
    if (finding.review_status === 'disputed') summary.disputed++
  }
  return summary
})
function usageLabel(usage: Readonly<Record<string, number>>) {
  return Object.keys(usage).length ? `Token 用量：${Object.entries(usage).map(([key, value]) => `${key} ${value}`).join(' / ')}` : 'Token 用量：未报告'
}
</script>

<style scoped>
.collaboration-panel { margin-bottom: 16px; padding: 16px; border: 1px solid var(--el-border-color); border-radius: 12px; background: var(--el-bg-color); color: var(--el-text-color-primary); font-size: 14px; line-height: 1.7; overflow-wrap: anywhere; }
header, .role-heading, .actions { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
header > strong { flex-shrink: 0; }
header .el-tag { max-width: 100%; height: auto; white-space: normal; padding-block: 3px; line-height: 1.5; }
p { margin: 8px 0; }
.pipeline { font-weight: 500; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
.warning { color: var(--el-color-warning-dark-2); margin: 12px 0; padding-left: 10px; border-left: 2px solid var(--el-color-warning); }
.steps { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px 16px; margin: 12px 0; padding: 0; list-style: none; }
.steps li { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 4px; min-width: 0; }
.roles { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin: 12px 0; }
article { min-width: 0; padding: 12px; border: 1px solid var(--el-border-color-lighter); border-radius: 8px; background: var(--el-fill-color-light); }
article[data-status="failed"] { border-color: var(--el-color-danger-light-5); }
article[data-status="running"] { border-color: var(--el-color-primary-light-5); }
.actions:empty { display: none; }
.actions .el-button + .el-button { margin-left: 0; }
.check-details, .findings { margin-top: 12px; border-top: 1px solid var(--el-border-color-lighter); }
summary { cursor: pointer; padding: 10px 0; color: var(--el-text-color-regular); }
summary:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; border-radius: 4px; }
.findings ol { padding-left: 24px; }
.findings li { border-bottom: 1px solid var(--el-border-color-lighter); padding: 6px 0; }
@media (max-width: 1000px) { .steps { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 540px) {
  .collaboration-panel { padding: 12px; }
  .roles { grid-template-columns: minmax(0, 1fr); }
  .actions .el-button { max-width: 100%; height: auto; min-height: 32px; white-space: normal; line-height: 1.5; }
}
</style>
