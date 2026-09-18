<template>
  <section class="collaboration-panel" aria-label="协作审校进度" data-testid="collaboration-progress">
    <header>
      <strong>协作审校</strong>
      <el-tag :type="failed ? 'danger' : report?.status === 'complete' ? 'success' : report?.status === 'partial' ? 'warning' : 'info'">
        {{ failed ? taskStatus === 'FAILURE' ? '任务失败 · 仅供查看' : '任务已取消 · 仅供查看' : report ? collaborationCoverageLabel(report) : '等待任务状态' }}
      </el-tag>
      <span v-if="report?.model_name" class="muted">共享模型：{{ report.model_name }}</span>
    </header>
    <p class="pipeline">规则检查 → 语言与一致性并行审校 → 一轮复核（最多 20 条）→ 人工决定</p>
    <p class="muted">规则检查不是 AI Agent。一次流程扣一次应用额度；最多 3 次模型调用，另可能有服务商传输重试，实际供应商费用按 Token 计。不会自动联网、无限辩论或自动采纳修改。</p>
    <p v-if="message" role="status">{{ message }}</p>
    <div v-if="report" class="roles">
      <article v-for="role in report.roles" :key="role.id" :data-role="role.id" :data-status="role.status">
        <div class="role-heading"><strong>{{ role.id === 'rules' ? '规则检查' : role.name }}</strong><el-tag size="small" :type="collaborationStatusTypes[role.status]">{{ collaborationStatusLabels[role.status] }}</el-tag></div>
        <p>{{ role.message }}</p>
        <p class="muted">发现 {{ role.issue_count }} 条 · 已报告耗时 {{ (role.elapsed_ms / 1000).toFixed(1) }} 秒</p>
        <p class="muted">{{ usageLabel(role.usage) }}</p>
      </article>
    </div>
    <p v-if="report" class="muted">已发现 {{ report.findings.length }} 条 · 已复核 {{ report.reviewed_count }} / 最多 {{ Math.min(20, report.review_limit) }} 条。未复核不等于已确认；争议建议请人工把关。</p>
    <p v-if="report?.status === 'partial' || failed" class="warning" role="alert">流程未完整完成；保留此前发现，仅供参考。不能通过普通补查宣称协作完成。可手动重新运行完整流程（另计额度与费用）。</p>
    <p v-if="cancelRequested && !terminal" class="warning" role="status">取消请求已发送，等待服务端确认；正在执行的请求可能先完成并产生费用。</p>
    <div v-if="error" class="warning" role="alert">状态连接中断：{{ error }}。重连只读取原任务，不会重新提交。</div>
    <div class="actions">
      <el-button v-if="taskId && !monitoring" size="small" @click="$emit('reconnect')">重连监控（不重新提交）</el-button>
      <el-button v-if="taskId && !terminal" size="small" :loading="cancelling" :disabled="cancelling || cancelRequested" @click="$emit('cancel')">请求取消</el-button>
      <el-button v-if="(report?.status === 'partial' || failed) && (terminal || !taskId)" size="small" type="warning" plain @click="$emit('rerun')">重新运行完整协作（另计费）</el-button>
    </div>
    <details v-if="report?.findings.length && viewOnly" class="findings">
      <summary>已发现问题（只读，不可采纳）</summary>
      <ol><li v-for="(finding, index) in report.findings" :key="`${finding.start ?? ''}-${finding.end ?? ''}-${finding.type}-${index}`">
        <p>{{ finding.original }} → {{ finding.suggestion || '需人工核对' }}</p>
        <p class="muted">{{ collaborationProvenance(finding, report) }}</p>
        <p v-if="finding.explanation" class="muted">{{ finding.explanation }}</p>
      </li></ol>
    </details>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { ElButton, ElTag } from 'element-plus'
import type { CollaborationReport } from '@/api/collaboration'
import { collaborationCoverageLabel, collaborationProvenance, collaborationStatusLabels, collaborationStatusTypes } from '@/utils/collaboration'
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
function usageLabel(usage: Readonly<Record<string, number>>) {
  return Object.keys(usage).length ? `Token 用量：${Object.entries(usage).map(([key, value]) => `${key} ${value}`).join(' / ')}` : 'Token 用量：未报告'
}
</script>

<style scoped>
.collaboration-panel { margin-bottom: 16px; padding: 16px; border: 1px solid var(--el-border-color); border-radius: 12px; background: var(--el-bg-color); color: var(--el-text-color-primary); font-size: 14px; line-height: 1.7; }
header, .role-heading, .actions { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
p { margin: 8px 0; overflow-wrap: anywhere; }
.pipeline { font-weight: 500; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
.warning { color: var(--el-color-warning-dark-2); margin: 12px 0; }
.roles { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 14px 0; }
article { padding: 12px; border: 1px solid var(--el-border-color-lighter); border-radius: 8px; background: var(--el-fill-color-light); }
article[data-status="failed"] { border-color: var(--el-color-danger-light-5); }
article[data-status="running"] { border-color: var(--el-color-primary-light-5); }
.findings { margin-top: 12px; }
summary { cursor: pointer; }
li { border-bottom: 1px solid var(--el-border-color-lighter); padding: 6px 0; }
@media (max-width: 1000px) { .roles { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 540px) { .roles { grid-template-columns: minmax(0, 1fr); } }
</style>
