<template>
  <section class="review-workspace" aria-label="审阅草稿与版本">
    <div class="workspace-toolbar">
      <div class="workspace-heading">
        <strong>审阅工作区</strong>
        <el-tag :type="dirty ? 'warning' : 'success'" size="small">{{ statusText }}</el-tag>
        <span class="workspace-meta">{{ domain }} · {{ collaboration ? '协作审校' : depth }}</span>
      </div>
      <div class="workspace-actions">
        <el-button :disabled="!canSave" :loading="busy === 'draft'" @click="save('draft')">保存草稿</el-button>
        <el-input v-model="versionLabel" class="version-label" placeholder="版本名称（可不填）" maxlength="80" :disabled="!!busy" aria-label="版本名称（可不填）" />
        <el-button :disabled="!canSave || versionLimitReached" :loading="busy === 'version'" @click="save('version')">保存版本</el-button>
        <el-button @click="drawerOpen = true">版本对比</el-button>
        <el-button v-if="busy" text @click="cancelRequest">取消请求</el-button>
      </div>
    </div>

    <p v-if="recordId === null" class="workspace-note">需登录保存草稿和版本；当前内容仅保留在本次页面内存中，不会写入本地存储。</p>
    <p v-else class="workspace-note">草稿手动保存，版本最多 20 个（当前 {{ versions.length }} 个）。恢复版本仅更新当前草稿，需另行保存。</p>
    <p v-if="versionLimitReached" class="workspace-note">已达到 20 个版本上限；仍可保存草稿。</p>
    <p v-if="compare" class="workspace-note">多模型审阅 · {{ compare.results.length }} 个模型 · {{ compareCoverageLabel(compare) }}；各模型结果和未审范围随草稿、版本一起保存。</p>
    <p v-else-if="collaboration" class="workspace-note">协作报告来自原始结果，只读且不随版本恢复改变。{{ collaboration.status === 'complete' ? '流程已完成，仍需人工复核。' : '流程未完整完成；保存或恢复版本不代表协作已完成。' }}</p>
    <p v-else-if="!coverage" class="workspace-note">本记录未保存审校覆盖范围，无法确认是否检查全文。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-alert v-if="needsLoad" title="已有保存草稿，继续审阅或以当前内容替换前需先明确加载。当前本地内容尚未被覆盖。" type="warning" :closable="false" show-icon />
    <div v-if="recordId !== null && (needsLoad || mustRefresh || conflict)" class="recovery-actions">
      <el-button v-if="needsLoad && !mustRefresh" :disabled="!!busy" @click="loadSavedDraft">加载已保存草稿</el-button>
      <el-button :disabled="!!busy" @click="loadRemote">重新读取已保存草稿</el-button>
    </div>
    <p v-if="notice" class="workspace-note" role="status">{{ notice }}</p>

    <el-drawer v-model="drawerOpen" title="版本对比" size="90%" append-to-body class="review-comparison-drawer">
      <p class="comparison-note">从左到右比较采纳决策。范围为原文 Unicode 字符的 0 起始半开区间 [start, end)，不比较逐字符排版差异。</p>
      <div class="comparison-columns">
        <section v-for="side in sides" :key="side" class="comparison-pane" :aria-label="side === 'left' ? '左侧版本' : '右侧版本'">
          <div class="comparison-controls">
            <el-select v-if="side === 'left'" v-model="leftChoice" aria-label="左侧版本">
              <el-option v-for="option in choices" :key="option.value" :label="option.label" :value="option.value" />
            </el-select>
            <el-select v-else v-model="rightChoice" aria-label="右侧版本">
              <el-option v-for="option in choices" :key="option.value" :label="option.label" :value="option.value" />
            </el-select>
            <el-button v-if="panes[side].version" :disabled="!canSave" @click="restoreVersion(panes[side].version!)">恢复此版本</el-button>
          </div>
          <p v-if="panes[side].version" class="comparison-note">{{ panes[side].version?.created_at }}</p>
          <p v-if="panes[side].coverage" class="comparison-note">
            {{ panes[side].coverage?.status === 'partial' ? '部分完成' : '已完成' }} ·
            {{ panes[side].coverage?.completed_chunks }}/{{ panes[side].coverage?.total_chunks }} 段
          </p>
          <p v-if="panes[side].compare" class="comparison-note">
            {{ compareCoverageLabel(panes[side].compare!) }} · {{ panes[side].compare?.results.map(model => model.config_name).join(' / ') }}
          </p>
          <el-alert v-if="panes[side].error" :title="panes[side].error" type="error" :closable="false" />
          <pre v-else class="comparison-text">{{ panes[side].text }}</pre>
        </section>
      </div>
      <section class="decision-changes" aria-label="采纳差异">
        <h3>采纳差异（{{ comparison.changes.length }}）</h3>
        <el-alert v-if="comparison.error" :title="comparison.error" type="error" :closable="false" />
        <p v-else-if="!comparison.changes.length" class="comparison-note">采纳决策无变化。忽略状态与未审范围仍随草稿或版本保存。</p>
        <ol v-else class="decision-list">
          <li v-for="change in comparison.changes" :key="`${change.start}:${change.end}`" class="decision-item">
            <el-tag :type="change.kind === 'added' ? 'success' : change.kind === 'removed' ? 'danger' : 'warning'" size="small">{{ changeLabels[change.kind] }}</el-tag>
            <span>[{{ change.start }}, {{ change.end }})</span>
            <span class="decision-original">{{ change.original }}</span>
            <span>{{ decisionLabel(change.before) }} → {{ decisionLabel(change.after) }}</span>
          </li>
        </ol>
      </section>
    </el-drawer>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElAlert, ElButton, ElDrawer, ElInput, ElMessageBox, ElOption, ElSelect, ElTag } from 'element-plus'
import type { ProofreadCoverage } from '@/api/proofread'
import type { CollaborationReport } from '@/api/collaboration'
import type { ReviewIssue } from '@/composables/useProofreadReview'
import {
  createReviewVersionApi,
  getReviewApi,
  getReviewErrorDetail,
  saveReviewApi,
  type ReviewCompareState,
  type ReviewResponse,
  type ReviewRestorePayload,
  type ReviewSnapshot,
  type ReviewVersion,
} from '@/api/review'
import { serializeReviewIssues } from '@/utils/review'
import { compareCoverageLabel } from '@/utils/compareReview'
import {
  compareReviewVersions,
  renderReviewVersionText,
  serializeReviewDraft,
  type AcceptedReviewDecision,
  type ReviewDecisionChange,
} from '@/utils/reviewVersions'

const props = withDefaults(defineProps<{
  recordId: number | null
  sourceText: string
  issues: ReviewIssue[]
  coverage?: ProofreadCoverage | null
  compare?: ReviewCompareState | null
  collaboration?: CollaborationReport | null
  domain: string
  depth: string
  configId?: number | null
  savedReview?: ReviewResponse | null
}>(), { coverage: null, compare: null, collaboration: null, configId: null, savedReview: null })

const emit = defineEmits<{
  saved: [review: ReviewResponse]
  restore: [snapshot: ReviewRestorePayload]
}>()

const remote = ref<ReviewResponse | null>(null)
const baseline = ref<string | null>(null)
const busy = ref<'load' | 'draft' | 'version' | null>(null)
const needsLoad = ref(false)
const mustRefresh = ref(false)
const conflict = ref(false)
const error = ref('')
const notice = ref('')
const versionLabel = ref('')
const drawerOpen = ref(false)
const leftChoice = ref('original')
const rightChoice = ref('current')
let controller: AbortController | null = null
let requestToken = 0
let documentToken = 0

const serialized = computed(() => serializeReviewDraft({
  sourceText: props.sourceText,
  issues: props.issues,
  coverage: props.coverage,
  compare: props.compare,
  domain: props.domain,
  depth: props.depth,
  configId: props.configId,
}))
const dirty = computed(() => baseline.value === null || baseline.value !== serialized.value)
const versions = computed(() => remote.value?.versions ?? [])
const versionLimitReached = computed(() => versions.value.length >= 20)
const canSave = computed(() => props.recordId !== null && remote.value !== null
  && !busy.value && !needsLoad.value && !mustRefresh.value && !conflict.value)
const statusText = computed(() => {
  if (props.recordId === null) return '需登录保存'
  if (busy.value === 'load') return '读取中'
  if (busy.value) return '保存中'
  if (conflict.value) return '保存冲突'
  if (needsLoad.value || mustRefresh.value) return '待加载草稿'
  return dirty.value ? '有未保存改动' : '已保存'
})

function copy<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function responseSnapshot(review: ReviewResponse): string {
  return serializeReviewDraft({
    sourceText: review.original_text,
    issues: review.issues,
    coverage: review.coverage,
    compare: review.compare,
    domain: review.domain,
    depth: review.depth,
    configId: review.config_id,
  })
}

function matchesDocument(review: ReviewResponse): boolean {
  return review.record_id === props.recordId && review.original_text === props.sourceText
}

function invalidateRequest() {
  requestToken++
  controller?.abort()
  controller = null
  busy.value = null
}

function resetDocument() {
  documentToken++
  invalidateRequest()
  remote.value = null
  baseline.value = null
  needsLoad.value = false
  mustRefresh.value = false
  conflict.value = false
  error.value = ''
  notice.value = ''
  versionLabel.value = ''
  drawerOpen.value = false
  leftChoice.value = 'original'
  rightChoice.value = 'current'
  if (props.recordId === null) return
  if (props.savedReview && matchesDocument(props.savedReview)) {
    remote.value = copy(props.savedReview)
    baseline.value = responseSnapshot(props.savedReview)
  } else {
    void loadRemote()
  }
}

watch(() => [props.recordId, props.sourceText] as const, resetDocument, { immediate: true, flush: 'sync' })
watch(() => props.savedReview, review => {
  if (!review || !matchesDocument(review)) return
  // 父页导出也会保存；仅以响应内容为基线，不吞掉请求期间的本地修改。
  if (remote.value && review.revision <= remote.value.revision) return
  invalidateRequest()
  remote.value = copy(review)
  mustRefresh.value = false
  baseline.value = responseSnapshot(review)
  needsLoad.value = false
  conflict.value = false
}, { flush: 'sync' })

onBeforeUnmount(() => {
  documentToken++
  invalidateRequest()
})

async function loadRemote() {
  if (props.recordId === null || busy.value) return
  const id = props.recordId
  const token = ++requestToken
  controller = new AbortController()
  busy.value = 'load'
  mustRefresh.value = true
  error.value = ''
  notice.value = ''
  try {
    const review = await getReviewApi(id, { signal: controller.signal })
    if (token !== requestToken) return
    if (!matchesDocument(review)) throw new Error('记录原文与当前文档不一致，禁止保存。请重新打开对应记录。')
    remote.value = copy(review)
    baseline.value = responseSnapshot(review)
    needsLoad.value = review.revision > 0 || conflict.value
    mustRefresh.value = false
    // 读取只同步元数据；已有草稿必须点击加载，绝不静默恢复或推进并发保存。
  } catch (cause) {
    if (token === requestToken) error.value = `读取草稿失败：${getReviewErrorDetail(cause)}`
  } finally {
    if (token === requestToken) {
      controller = null
      busy.value = null
    }
  }
}

function cancelRequest() {
  const wasSaving = busy.value === 'draft' || busy.value === 'version'
  invalidateRequest()
  mustRefresh.value = true
  notice.value = wasSaving
    ? '保存请求已取消，服务端可能已收到请求；本地改动仍保留，请重新读取并明确加载后再保存。'
    : '读取已取消；本地改动仍保留，请重新读取后再保存。'
}

async function confirmRestore(message: string): Promise<boolean> {
  try {
    await ElMessageBox.confirm(message, '恢复审阅内容', {
      type: 'warning', confirmButtonText: '确认加载', cancelButtonText: '取消',
    })
    return true
  } catch {
    return false
  }
}

function emitRestore(snapshot: ReviewSnapshot) {
  emit('restore', {
    issues: serializeReviewIssues(snapshot.issues), coverage: copy(snapshot.coverage ?? null),
    compare: copy(snapshot.compare ?? null),
  })
}

async function loadSavedDraft() {
  const review = remote.value
  if (!review || busy.value || mustRefresh.value) return
  const token = documentToken
  if (!await confirmRestore('加载已保存草稿将替换当前本地审阅决策和未审范围。未保存改动不会写入服务端，是否继续？')) return
  if (token !== documentToken || busy.value || remote.value !== review || mustRefresh.value) return
  baseline.value = responseSnapshot(review)
  needsLoad.value = false
  conflict.value = false
  error.value = ''
  notice.value = '已加载保存草稿；后续修改请手动保存。'
  emitRestore(review)
  emit('saved', review)
}

async function save(kind: 'draft' | 'version') {
  if (!canSave.value || !remote.value || props.recordId === null || (kind === 'version' && versionLimitReached.value)) return
  const id = props.recordId
  // 与实际请求体一起捕获，不在 await 之后重新读取响应式 issues。
  const captured = serialized.value
  const payload = {
    revision: remote.value.revision,
    issues: serializeReviewIssues(props.issues),
    coverage: copy(props.coverage),
    compare: copy(props.compare),
    depth: props.depth,
    config_id: props.configId,
  }
  const label = versionLabel.value.trim()
  const token = ++requestToken
  controller = new AbortController()
  busy.value = kind
  error.value = ''
  notice.value = ''
  try {
    const review = kind === 'version'
      ? await createReviewVersionApi(id, { ...payload, ...(label ? { label } : {}) }, { signal: controller.signal })
      : await saveReviewApi(id, payload, { signal: controller.signal })
    if (token !== requestToken) return
    if (!matchesDocument(review)) {
      mustRefresh.value = true
      throw new Error('返回的草稿与当前文档不一致，请重新读取')
    }
    remote.value = copy(review)
    baseline.value = captured
    if (kind === 'version') versionLabel.value = ''
    notice.value = `${kind === 'version' ? '版本' : '草稿'}已保存（请求发起时的内容）；后续改动需再次保存。`
    emit('saved', review)
  } catch (cause) {
    if (token !== requestToken) return
    const status = (cause as { response?: { status?: number } } | null)?.response?.status
    if (status === 409) {
      conflict.value = true
      error.value = '保存冲突（409）：其他会话已更新草稿。本地改动仍保留，不会自动覆盖；请重新读取并明确加载最新草稿。'
    } else {
      error.value = `保存失败：${getReviewErrorDetail(cause)}`
    }
  } finally {
    if (token === requestToken) {
      controller = null
      busy.value = null
    }
  }
}

async function restoreVersion(version: ReviewVersion) {
  if (!canSave.value) return
  if (props.compare && !version.compare) {
    notice.value = '该旧版本未保存逐模型结果，无法恢复为多模型审阅；仍可查看采纳差异。'
    return
  }
  const token = documentToken
  const revision = remote.value?.revision
  if (!await confirmRestore(`将版本「${version.label}」恢复到当前本地草稿，替换未保存改动。此操作不会覆盖服务端，恢复后仍需点击保存。是否继续？`)) return
  if (token !== documentToken || !canSave.value || revision !== remote.value?.revision) return
  emitRestore(version)
  notice.value = `已恢复版本「${version.label}」到当前草稿，尚未保存。请点击保存草稿或保存版本。`
}

const sides = ['left', 'right'] as const
const changeLabels = { added: '新增采纳', removed: '撤销采纳', changed: '改变采纳' } as const
const choices = computed(() => [
  { value: 'original', label: '原文' },
  { value: 'current', label: '当前草稿' },
  ...versions.value.map(version => ({ value: `version:${version.id}`, label: version.label })),
])

interface ComparisonPane {
  issues: ReviewIssue[]
  text: string
  coverage?: ProofreadCoverage | null
  compare?: ReviewCompareState | null
  version?: ReviewVersion
  error?: string
}

function pane(choice: string): ComparisonPane {
  if (choice === 'original') return { issues: [], text: props.sourceText }
  if (choice === 'current') {
    try {
      return { issues: props.issues, text: renderReviewVersionText(props.sourceText, props.issues), coverage: props.coverage, compare: props.compare }
    } catch (cause) {
      return { issues: props.issues, text: '', coverage: props.coverage, error: getReviewErrorDetail(cause) }
    }
  }
  const version = versions.value.find(item => `version:${item.id}` === choice)
  return version
    ? { issues: version.issues, text: version.modified_text, coverage: version.coverage, compare: version.compare, version }
    : { issues: [], text: '', error: '该版本已不可用，请重新选择。' }
}

const panes = computed(() => ({ left: pane(leftChoice.value), right: pane(rightChoice.value) }))
const comparison = computed<{ changes: ReviewDecisionChange[]; error?: string }>(() => {
  try {
    if (panes.value.left.error || panes.value.right.error) throw new Error('所选内容无法安全对比，请先处理范围错误。')
    return { changes: compareReviewVersions(props.sourceText, panes.value.left.issues, panes.value.right.issues) }
  } catch (cause) {
    return { changes: [], error: getReviewErrorDetail(cause) }
  }
})

function decisionLabel(decision: AcceptedReviewDecision | null): string {
  if (!decision) return '未采纳'
  if (!decision.replacement) return decision.patchEnd > decision.end ? '删除（含紧邻标点）' : '删除'
  return `采纳为「${decision.replacement}」`
}
</script>

<style scoped lang="scss">
.review-workspace {
  padding: var(--spacing-md);
  border: 1px solid var(--surface-border);
  border-radius: var(--border-radius-md);
  background: var(--surface);
  color: var(--color-text);
}
.workspace-toolbar, .workspace-heading, .workspace-actions, .recovery-actions, .comparison-controls {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--spacing-sm);
}
.workspace-toolbar { justify-content: space-between; }
.workspace-meta, .workspace-note, .comparison-note {
  color: var(--color-text-secondary);
  font-size: var(--font-size-small);
  line-height: 1.7;
}
.workspace-note, .comparison-note { margin: var(--spacing-sm) 0; }
.version-label { width: 180px; }
.recovery-actions { margin-top: var(--spacing-sm); }
.workspace-actions :deep(.el-button + .el-button), .recovery-actions :deep(.el-button + .el-button) { margin-left: 0; }
.review-workspace :deep(.el-alert + .el-alert) { margin-top: var(--spacing-sm); }
.comparison-columns { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: var(--spacing-md); }
.comparison-pane { min-width: 0; }
.comparison-controls :deep(.el-select) { flex: 1; min-width: 160px; }
.comparison-text {
  margin-top: var(--spacing-sm);
  padding: var(--spacing-md);
  height: 45vh;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: inherit;
  line-height: 1.9;
  color: var(--color-text);
  background: var(--surface-soft);
  border: 1px solid var(--surface-border);
  border-radius: var(--border-radius-sm);
}
.decision-changes { margin-top: var(--spacing-lg); }
.decision-changes h3 { font-size: var(--font-size-large); color: var(--color-text); }
.decision-list { list-style: none; padding: 0; }
.decision-item {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--spacing-sm);
  padding: var(--spacing-sm) 0;
  border-bottom: 1px solid var(--surface-border);
  color: var(--color-text);
  overflow-wrap: anywhere;
}
.decision-original { white-space: pre-wrap; font-weight: 600; }
@media (max-width: 760px) {
  .comparison-columns { grid-template-columns: minmax(0, 1fr); }
  .comparison-text { height: 30vh; }
  .workspace-actions { width: 100%; }
  .version-label { flex: 1; min-width: 150px; }
}
</style>
