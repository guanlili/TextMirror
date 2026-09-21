<template>
  <section
    class="quality-admin"
    aria-label="质量反馈审阅台"
  >
    <header class="intro">
      <span class="eyebrow">人工审阅 / 目标评测</span>
      <h2>先确认样例，再评测模型</h2>
      <p>收集反馈不等于模型真错。忽略、保留原表达等个人偏好通常不纳入评测；请独立判断并脱敏。</p>
    </header>

    <div class="toolbar">
      <el-select
        :model-value="status"
        aria-label="反馈状态"
        :disabled="saving || runBusy"
        @update:model-value="changeStatus"
      >
        <el-option
          v-for="(label, value) in statusLabels"
          :key="value"
          :label="label"
          :value="value"
        />
      </el-select>
      <span class="muted">共 {{ total }} 条 · 每页 20 条</span>
      <el-button
        :disabled="loading || saving || runBusy"
        aria-label="重新读取反馈列表"
        @click="refreshList"
      >
        重新读取反馈
      </el-button>
    </div>
    <p
      v-if="loading"
      role="status"
      class="muted"
    >
      正在加载反馈…
    </p>
    <div
      v-else-if="listError"
      class="error-box"
      role="alert"
    >
      <p>{{ listError }}</p>
      <el-button @click="loadList">
        重试加载
      </el-button>
    </div>
    <el-empty
      v-else-if="items.length === 0"
      :description="`暂无${statusLabels[status]}的质量反馈`"
      :image-size="72"
    />

    <ul
      v-if="!loading && !listError"
      class="feedback-list"
      aria-label="反馈列表"
    >
      <li
        v-for="row in items"
        :key="row.id"
        class="feedback-card"
        :class="{ 'is-current': active?.id === row.id }"
      >
        <div class="card-heading">
          <div class="inline-group">
            <el-checkbox
              v-if="status === 'confirmed'"
              :model-value="selectedIds.includes(row.id)"
              :disabled="runBusy || saving || row.status !== 'confirmed' || !row.sample || (!selectedIds.includes(row.id) && selectedIds.length >= 10)"
              :aria-label="`选择反馈 ${row.id} 参与评测`"
              @change="toggleSelection(row, Boolean($event))"
            />
            <strong>{{ feedbackKindLabels[row.kind] }}</strong>
            <el-tag
              :type="statusTag(row.status)"
              size="small"
            >
              {{ statusLabels[row.status] }}
            </el-tag>
            <span class="muted">{{ typeLabel(row.issue_type) || '未指定问题类型' }} · #{{ row.id }} · revision {{ row.revision }}</span>
          </div>
          <el-button
            :disabled="saving || runBusy"
            :aria-label="`审核反馈 ${row.id}`"
            @click="openReview(row)"
          >
            {{ active?.id === row.id ? '正在审核' : '审核' }}
          </el-button>
        </div>
        <p class="fragment">
          <span class="field-caption">反馈片段</span>{{ row.original }}
        </p>
        <p
          v-if="row.suggestion"
          class="muted"
        >
          原建议：{{ row.suggestion }}
        </p>
        <p class="user-note">
          <span class="field-caption">用户备注</span>{{ row.note || '未填写' }}
        </p>
        <ul
          v-if="row.model_snapshot.length"
          class="snapshots"
          aria-label="逐模型反馈快照"
        >
          <li
            v-for="(snapshot, index) in row.model_snapshot"
            :key="index"
          >
            <span>{{ snapshot.config_name || '未命名配置' }} <span class="muted">{{ snapshot.model }}</span></span>
            <span class="muted">{{ feedbackSnapshotLabel(snapshot) }}</span>
          </li>
        </ul>
        <p
          v-else
          class="muted"
        >
          无逐模型快照，无法判定模型是否完成或报告目标。
        </p>
      </li>
    </ul>
    <el-pagination
      v-if="total > 0"
      :current-page="page"
      :page-size="20"
      :total="total"
      :disabled="saving || runBusy"
      layout="prev, pager, next"
      :pager-count="5"
      aria-label="反馈分页"
      @current-change="changePage"
    />

    <section
      v-if="active"
      ref="reviewPanel"
      class="review-panel"
      tabindex="-1"
      aria-label="反馈审核编辑器"
    >
      <div class="card-heading">
        <div><span class="eyebrow">审核 #{{ active.id }} / revision {{ active.revision }} · {{ statusLabels[active.status] }}</span><h3>制作独立评测样例</h3></div>
        <el-button
          :disabled="saving || runBusy"
          aria-label="收起反馈审核"
          @click="closeReview"
        >
          收起审核
        </el-button>
      </div>
      <p class="user-note">
        <span class="field-caption">{{ feedbackKindLabels[active.kind] }} · 用户备注</span>{{ active.note || '未填写' }}
      </p>
      <p class="field-caption">
        反馈上下文（只读）
      </p>
      <p
        class="proof-text"
        aria-label="原文上下文与反馈目标"
      >
        {{ contextParts.before }}<mark v-if="contextParts.target">{{ contextParts.target }}</mark>{{ contextParts.after }}
      </p>
      <p
        v-if="!contextParts.target"
        class="warning-text"
      >
        原反馈位置与上下文不匹配，请在下方重新定位有效目标。
      </p>
      <p class="muted">
        下方编辑不会改写用户原文。仅显式确认后保存，不会自动调用模型；切换反馈或收起将放弃未保存编辑。
      </p>
      <el-form
        label-position="top"
        :disabled="saving || runBusy"
      >
        <el-form-item
          label="脱敏样例"
          required
        >
          <el-input
            v-model="draft.text"
            type="textarea"
            :rows="5"
            aria-label="脱敏样例文本"
            placeholder="保留必要语境，移除姓名、证件号等敏感信息"
          />
          <span class="muted">{{ sampleLength }} / 4000 Unicode 字符</span>
        </el-form-item>
        <div class="form-grid">
          <el-form-item
            label="目标原文"
            required
          >
            <el-input
              v-model="draft.original"
              aria-label="目标原文"
              placeholder="须与样例片段完全一致"
            />
          </el-form-item>
          <el-form-item
            label="目标位置"
            required
          >
            <el-select
              v-model="targetStart"
              aria-label="目标出现位置"
              placeholder="请选择要评测的那一处"
              :disabled="positions.length === 0"
            >
              <el-option
                v-for="position in positions"
                :key="position.start"
                :label="position.label"
                :value="position.start"
              />
            </el-select>
          </el-form-item>
        </div>
        <p
          v-if="positions.length === 0"
          class="warning-text"
        >
          样例中找不到目标原文，不能确认纳入。
        </p>
        <p
          v-else-if="targetStart === null"
          class="warning-text"
        >
          存在多处相同片段，请从下拉列表选择目标位置。
        </p>
        <p
          class="proof-text sample-preview"
          aria-label="脱敏样例目标预览"
        >
          {{ sampleParts.before }}<mark v-if="sampleParts.target">{{ sampleParts.target }}</mark>{{ sampleParts.after }}
        </p>
        <div class="form-grid">
          <el-form-item
            label="样例领域"
            required
          >
            <el-select
              v-model="draft.domain"
              aria-label="样例领域"
            >
              <el-option
                label="通用"
                value="general"
              /><el-option
                label="公文"
                value="official"
              /><el-option
                label="法律"
                value="legal"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="目标问题类型">
            <el-select
              v-model="draft.issue_type"
              :empty-values="[null, undefined]"
              aria-label="目标问题类型"
            >
              <el-option
                label="任意类型"
                value=""
              />
              <el-option
                v-for="type in feedbackIssueTypes"
                :key="type"
                :label="typeLabel(type)"
                :value="type"
              />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item
          label="目标预期（须管理员确认）"
          required
        >
          <el-select
            v-model="draft.expectation"
            aria-label="目标预期"
          >
            <el-option
              label="应报告目标问题（report）"
              value="report"
            />
            <el-option
              label="不应报告目标问题（no_report）"
              value="no_report"
            />
          </el-select>
          <span class="muted">初次审核仅按反馈原因预填：漏检、建议不合适为「应报告」，其余为「不应报告」，并非自动裁定。</span>
        </el-form-item>
        <p
          v-if="active.kind === 'bad_suggestion'"
          class="warning-text"
        >
          建议不合适：首次审核已将原建议预填为禁止替换，请人工确认。纳入样例不等于质量已修复；须提供认可改法才能验证建议正确。
        </p>
        <div
          v-if="draft.expectation === 'report'"
          class="suggestion-editor"
        >
          <p class="muted">
            填写替换「目标原文」的精确文本，而非修改说明。每组最多 10 条、每条最多 500 个 Unicode 字符；保留空格和换行，空文本条目表示删除目标。
          </p>
          <div
            v-for="constraint in suggestionFields"
            :key="constraint.key"
            class="suggestion-group"
          >
            <div class="card-heading">
              <strong>{{ constraint.label }}</strong>
              <el-button
                :disabled="draft[constraint.key].length >= 10"
                :aria-label="`添加${constraint.label}`"
                @click="draft[constraint.key].push('')"
              >
                添加一条
              </el-button>
            </div>
            <div
              v-for="(_, index) in draft[constraint.key]"
              :key="index"
              class="suggestion-row"
            >
              <el-input
                v-model="draft[constraint.key][index]"
                type="textarea"
                :rows="2"
                :aria-label="`${constraint.label} ${index + 1}`"
                placeholder="空文本表示删除目标"
              />
              <el-button
                :aria-label="`移除${constraint.label} ${index + 1}`"
                @click="draft[constraint.key].splice(index, 1)"
              >
                移除
              </el-button>
            </div>
            <p
              v-if="!draft[constraint.key].length"
              class="muted"
            >
              未设置{{ constraint.label }}
            </p>
          </div>
          <p
            v-if="!draft.accepted_suggestions.length"
            class="warning-text"
          >
            未提供认可改法：只有禁止项时仅能判定命中坏建议，避开禁止项仍为未评估；两组均空时只评检出，不验证建议正确性。
          </p>
        </div>
        <p
          v-else
          class="muted"
        >
          不应报告：只评检出，本次保存不附带替换约束，建议为未评估。
        </p>
        <el-form-item label="审核备注（可选）">
          <el-input
            v-model="reviewNote"
            type="textarea"
            :rows="2"
            aria-label="审核备注"
          />
        </el-form-item>
        <el-checkbox
          v-model="expectationConfirmed"
          aria-label="确认脱敏样例与目标预期"
        >
          我已核对脱敏内容、目标位置、预期及认可/禁止替换；理解未设置认可改法不能证明建议正确
        </el-checkbox>
      </el-form>
      <p
        v-if="validationError"
        class="warning-text"
        role="status"
      >
        {{ validationError }}
      </p>
      <div
        v-if="reviewError"
        class="error-box"
        role="alert"
      >
        <p>{{ reviewError }}</p>
        <el-button
          v-if="conflict"
          :disabled="loading"
          @click="refreshList"
        >
          重新读取反馈（先确认放弃本地编辑）
        </el-button>
      </div>
      <p
        v-if="reviewNotice"
        class="success-text"
        role="status"
      >
        {{ reviewNotice }}
      </p>
      <div class="review-actions">
        <el-button
          type="primary"
          :disabled="!canConfirm"
          :loading="saving"
          @click="submitReview('confirmed')"
        >
          确认并纳入评测
        </el-button>
        <el-button
          :disabled="saving || runBusy || conflict"
          @click="submitReview('rejected')"
        >
          不纳入评测
        </el-button>
      </div>
    </section>

    <section
      v-if="status === 'confirmed'"
      class="evaluation-panel"
      aria-label="运行已确认样例评测"
    >
      <span class="eyebrow">小样本回归 / 按需调用</span>
      <h3>运行已确认样例</h3>
      <p class="muted">
        已选 {{ selectedIds.length }} / 10 条（仅当前页）。仅发送已保存的确认样例，本地未保存编辑不会发送。
      </p>
      <p
        v-if="modelsLoading"
        role="status"
      >
        正在加载可用模型…
      </p>
      <div
        v-else-if="modelsError"
        role="alert"
        class="error-box"
      >
        <p>{{ modelsError }}</p><el-button
          :disabled="runBusy"
          @click="loadModels"
        >
          重试加载模型
        </el-button>
      </div>
      <p
        v-else-if="models.length === 0"
        class="muted"
      >
        暂无可用模型，请由模型配置管理员检查已启用配置。
      </p>
      <el-select
        v-model="selectedModels"
        multiple
        :multiple-limit="4"
        :disabled="runBusy || modelsLoading || !!modelsError"
        aria-label="选择一至四个评测模型"
        placeholder="选择 1–4 个可用模型"
      >
        <el-option
          v-for="model in models"
          :key="model.id"
          :value="model.id"
          :label="`${model.name}${model.is_active ? '（当前）' : ''} · ${model.model}`"
        />
      </el-select>
      <el-button
        type="primary"
        :disabled="!canRun"
        :loading="runBusy"
        @click="runEvaluation"
      >
        运行选中样例评测
      </el-button>
      <p
        v-if="runBusy"
        role="status"
        class="muted"
      >
        {{ runPhase === 'confirm' ? '等待费用与发送内容确认…' : '评测中，最长等待 300 秒。关闭面板不保证取消已开始的模型调用，结果不会回填重新打开的面板。' }}
      </p>
      <p
        v-if="runError"
        class="error-box"
        role="alert"
      >
        {{ runError }}
      </p>
    </section>

    <section
      v-if="evaluation"
      class="results-panel"
      aria-label="目标评测报告"
    >
      <div class="card-heading">
        <div><span class="eyebrow">本次评测报告</span><h3>只衡量确认目标，不外推全文</h3></div>
        <el-button
          aria-label="下载可复现 JSON 报告"
          @click="downloadReport"
        >
          下载 JSON 报告
        </el-button>
      </div>
      <p class="muted">
        {{ evaluation.generated_at }} · 本报告不在服务端持久化，请及时下载。
      </p>
      <p class="scope-note">
        {{ evaluationScopeNotice }}
      </p>
      <article
        v-for="result in evaluation.results"
        :key="result.config_id"
        class="model-result"
      >
        <h4>{{ result.config_name }} <span class="muted">{{ result.model }}</span></h4>
        <dl class="metrics">
          <div><dt>目标误报</dt><dd>{{ result.false_positives }} / {{ result.no_report_evaluated }}</dd><small>false_positives / no_report_evaluated</small></div>
          <div><dt>目标漏检</dt><dd>{{ result.missed }} / {{ result.report_evaluated }}</dd><small>missed / report_evaluated</small></div>
          <div><dt>检出失败或未完成</dt><dd>{{ result.errors }}</dd><small>errors · 检出不可判定</small></div>
          <div><dt>建议通过</dt><dd>{{ result.suggestion_passed }} / {{ result.suggestion_evaluated }}</dd><small>suggestion_passed / suggestion_evaluated</small></div>
          <div><dt>建议不符</dt><dd>{{ result.suggestion_failed }} / {{ result.suggestion_evaluated }}</dd><small>suggestion_failed · 不计为漏检</small></div>
          <div><dt>建议未评估</dt><dd>{{ result.suggestion_not_evaluated }}</dd><small>not_evaluated · 不计为通过</small></div>
        </dl>
        <p class="muted">
          检出分母仅含定位可靠的病例；建议分母仅含可按人工约束判定通过或失败的病例。任一命中建议不符即失败；分母为 0 不代表通过。
        </p>
        <ul
          class="case-list"
          aria-label="病例评测详情"
        >
          <li
            v-for="entry in result.cases"
            :key="`${entry.feedback_id}:${entry.revision}`"
          >
            <div class="inline-group">
              <strong>#{{ entry.feedback_id }}</strong><span>revision {{ entry.revision }}</span>
              <span>{{ entry.expectation === 'report' ? '应报告' : '不应报告' }}（{{ entry.expectation }}）</span>
              <el-tag :type="entry.detection_status === 'pass' ? 'success' : entry.detection_status === 'fail' ? 'danger' : 'warning'">
                检出：{{ caseLabels[entry.detection_status] }} / {{ entry.detection_status }}
              </el-tag>
              <el-tag :type="entry.suggestion_status === 'pass' ? 'success' : entry.suggestion_status === 'fail' ? 'danger' : 'info'">
                建议：{{ caseLabels[entry.suggestion_status] }} / {{ entry.suggestion_status }}
              </el-tag>
            </div>
            <p class="muted">
              {{ entry.detected === null ? '目标检测不可判定' : entry.detected ? '检测到目标' : '未检测到目标' }} · {{ entry.elapsed_ms }} ms
            </p>
            <p class="muted">
              {{ suggestionReasons[entry.suggestion_reason] }}
            </p>
            <p
              v-if="entry.error"
              class="warning-text"
            >
              {{ entry.error }}
            </p>
          </li>
        </ul>
      </article>
      <details class="report-samples">
        <summary>查看本次实际评测样例与 revision（{{ evaluation.samples.length }} 条）</summary>
        <div
          v-for="entry in evaluation.samples"
          :key="entry.id"
          class="report-sample"
        >
          <p>#{{ entry.id }} · revision {{ entry.revision }} · {{ entry.sample.expectation }} · {{ entry.sample.domain }} · {{ entry.sample.issue_type || '任意类型' }}</p>
          <p>认可替换：{{ JSON.stringify(entry.sample.accepted_suggestions ?? []) }} · 禁止替换：{{ JSON.stringify(entry.sample.rejected_suggestions ?? []) }}</p>
          <p class="proof-text">
            {{ feedbackTargetParts(entry.sample.text, entry.sample.start, entry.sample.end, entry.sample.original).before }}<mark>{{ feedbackTargetParts(entry.sample.text, entry.sample.start, entry.sample.end, entry.sample.original).target }}</mark>{{ feedbackTargetParts(entry.sample.text, entry.sample.start, entry.sample.end, entry.sample.original).after }}
          </p>
        </div>
      </details>
      <p
        v-if="downloadError"
        class="error-box"
        role="alert"
      >
        {{ downloadError }}
      </p>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElButton, ElCheckbox, ElEmpty, ElForm, ElFormItem, ElInput, ElMessageBox, ElOption, ElPagination, ElSelect, ElTag } from 'element-plus'
import { feedbackKindLabels, listQualityFeedbackApi, reviewQualityFeedbackApi, evaluateQualityFeedbackApi } from '@/api/qualityFeedback'
import type { FeedbackEvaluation, FeedbackSample, FeedbackStatus, QualityFeedback } from '@/api/qualityFeedback'
import { getAvailableModelsApi, type AvailableModel } from '@/api/polish'
import { getReviewErrorDetail } from '@/api/review'
import { typeLabel } from '@/utils/proofread'
import { defaultFeedbackModels, evaluationScopeNotice, feedbackIssueTypes, feedbackSampleError, feedbackSnapshotLabel, feedbackTargetParts, findFeedbackTargets, initialFeedbackSample, serializeFeedbackReport } from '@/utils/qualityFeedback'

const statusLabels: Record<FeedbackStatus, string> = { pending: '待确认', confirmed: '已纳入', rejected: '未纳入' }
const caseLabels = { pass: '通过', fail: '未通过', error: '失败或未完成', not_evaluated: '未评估' }
const suggestionFields = [
  { key: 'accepted_suggestions', label: '认可替换' },
  { key: 'rejected_suggestions', label: '禁止替换' },
] as const
const suggestionReasons = {
  detection_error: '检出结果不可判定，未评估建议。', no_report: '不应报告样例不评估替换建议。',
  no_constraints: '无人工替换约束，仅评检出，不代表建议通过。', not_detected: '未检出目标，没有建议可评估。',
  invalid_suggestion: '命中项缺少有效替换文本，未评估建议。',
  incomparable_context: '命中项改变了目标外上下文，无法可靠比较，未评估建议。',
  rejected: '至少一个命中项符合禁止替换。', not_accepted: '至少一个命中项不符合任何认可替换。',
  no_accepted_golden: '未命中禁止替换，但没有认可改法，不能证明建议正确。',
  all_accepted: '所有目标命中项均符合人工认可替换。',
}
const statusTag = (value: FeedbackStatus) => value === 'confirmed' ? 'success' : value === 'pending' ? 'warning' : 'info'
const status = ref<FeedbackStatus>('pending')
const page = ref(1)
const total = ref(0)
const items = ref<QualityFeedback[]>([])
const loading = ref(true)
const listError = ref('')
const reviewPanel = ref<HTMLElement | null>(null)
const active = ref<QualityFeedback | null>(null)
const draft = ref<Required<FeedbackSample>>({ text: '', original: '', start: -1, end: -1, domain: 'general', expectation: 'no_report', issue_type: '', accepted_suggestions: [], rejected_suggestions: [] })
const targetStart = ref<number | null>(null)
const reviewNote = ref('')
const expectationConfirmed = ref(false)
const reviewError = ref('')
const reviewNotice = ref('')
const conflict = ref(false)
const saving = ref(false)
const selectedIds = ref<number[]>([])
const models = ref<AvailableModel[]>([])
const selectedModels = ref<number[]>([])
const modelsLoading = ref(false)
const modelsError = ref('')
const runPhase = ref<'' | 'confirm' | 'running'>('')
const runBusy = computed(() => runPhase.value !== '')
const runError = ref('')
const evaluation = ref<FeedbackEvaluation | null>(null)
const downloadError = ref('')
let alive = true
let listSequence = 0
let editorSequence = 0
let modelSequence = 0
let runSequence = 0

const positions = computed(() => findFeedbackTargets(draft.value.text, draft.value.original))
const sampleLength = computed(() => Array.from(draft.value.text).length)
const sample = computed<FeedbackSample>(() => ({
  ...draft.value, start: targetStart.value ?? -1,
  end: targetStart.value === null ? -1 : targetStart.value + Array.from(draft.value.original).length,
  accepted_suggestions: draft.value.expectation === 'report' ? [...draft.value.accepted_suggestions] : [],
  rejected_suggestions: draft.value.expectation === 'report' ? [...draft.value.rejected_suggestions] : [],
}))
const validationError = computed(() => feedbackSampleError(sample.value))
const canConfirm = computed(() => !!active.value && !saving.value && !runBusy.value && !conflict.value && !validationError.value && expectationConfirmed.value)
const contextParts = computed(() => active.value
  ? feedbackTargetParts(active.value.context_text, active.value.start - active.value.context_start, active.value.end - active.value.context_start, active.value.original)
  : { before: '', target: '', after: '' })
const sampleParts = computed(() => feedbackTargetParts(sample.value.text, sample.value.start, sample.value.end, sample.value.original))
const canRun = computed(() => status.value === 'confirmed' && !loading.value && !listError.value && !saving.value && !conflict.value && !runBusy.value && !modelsLoading.value && !modelsError.value
  && selectedIds.value.length >= 1 && selectedIds.value.length <= 10
  && new Set(selectedIds.value).size === selectedIds.value.length
  && selectedIds.value.every(id => items.value.some(row => row.id === id && row.status === 'confirmed' && row.sample))
  && selectedModels.value.length >= 1 && selectedModels.value.length <= 4
  && new Set(selectedModels.value).size === selectedModels.value.length
  && selectedModels.value.every(id => models.value.some(model => model.id === id)))

watch(() => [draft.value.text, draft.value.original], () => {
  targetStart.value = positions.value.length === 1 ? positions.value[0].start : null
}, { flush: 'sync' })
watch(() => [draft.value, targetStart.value], () => {
  expectationConfirmed.value = false
  reviewNotice.value = ''
}, { flush: 'sync', deep: true })

function resetEditor() {
  editorSequence++
  active.value = null
  saving.value = false
  conflict.value = false
  reviewError.value = ''
  reviewNotice.value = ''
  expectationConfirmed.value = false
}
function openReview(row: QualityFeedback) {
  if (saving.value || runBusy.value || active.value?.id === row.id) return
  resetEditor()
  active.value = { ...row }
  const initial = initialFeedbackSample(row)
  draft.value = initial
  targetStart.value = feedbackTargetParts(initial.text, initial.start, initial.end, initial.original).target ? initial.start : null
  reviewNote.value = row.review_note || ''
  const sequence = editorSequence
  void nextTick(() => {
    if (!alive || sequence !== editorSequence) return
    reviewPanel.value?.scrollIntoView?.({ block: 'start' })
    reviewPanel.value?.focus?.({ preventScroll: true })
  })
}
function closeReview() {
  if (!saving.value && !runBusy.value) resetEditor()
}
function changeStatus(value: FeedbackStatus) {
  if (saving.value || runBusy.value || status.value === value) return
  status.value = value
  page.value = 1
  resetEditor()
  void loadList()
}
function changePage(value: number) {
  if (saving.value || runBusy.value || page.value === value) return
  page.value = value
  resetEditor()
  void loadList()
}
async function loadList() {
  if (!alive) return
  const sequence = ++listSequence
  const query = { status: status.value, page: page.value, page_size: 20 }
  loading.value = true
  listError.value = ''
  selectedIds.value = []
  items.value = []
  total.value = 0
  try {
    const response = await listQualityFeedbackApi(query)
    if (!alive || sequence !== listSequence) return
    total.value = response.total
    const lastPage = Math.max(1, Math.ceil(response.total / 20))
    if (page.value > lastPage) {
      page.value = lastPage
      await loadList()
      return
    }
    items.value = response.items
  } catch (error) {
    if (alive && sequence === listSequence) listError.value = getReviewErrorDetail(error)
  } finally {
    if (alive && sequence === listSequence) loading.value = false
  }
}
async function refreshList() {
  if (saving.value || runBusy.value) return
  const sequence = editorSequence
  if (active.value) {
    try {
      await ElMessageBox.confirm('重新读取将放弃当前本地编辑。请先保留需要的脱敏内容，再从最新反馈列表重新选择并审核。', '重新读取反馈', { confirmButtonText: '放弃编辑并重读', cancelButtonText: '保留本地编辑', type: 'warning' })
    } catch { return }
  }
  if (!alive || sequence !== editorSequence || saving.value || runBusy.value) return
  resetEditor()
  await loadList()
}
async function submitReview(nextStatus: 'confirmed' | 'rejected') {
  if (!alive || !active.value || saving.value || runBusy.value || conflict.value) return
  if (nextStatus === 'confirmed' && !canConfirm.value) return
  const sequence = editorSequence
  const id = active.value.id
  const payload = { revision: active.value.revision, status: nextStatus, sample: nextStatus === 'confirmed' ? { ...sample.value } : null, review_note: reviewNote.value }
  saving.value = true
  reviewError.value = ''
  reviewNotice.value = ''
  try {
    const response = await reviewQualityFeedbackApi(id, payload)
    if (!alive || sequence !== editorSequence) return
    active.value = response
    draft.value = initialFeedbackSample(response)
    targetStart.value = feedbackTargetParts(draft.value.text, draft.value.start, draft.value.end, draft.value.original).target ? draft.value.start : null
    reviewNote.value = response.review_note
    expectationConfirmed.value = false
    reviewNotice.value = nextStatus === 'confirmed' ? '已确认并纳入评测。修改后需再次确认；也可撤回为不纳入。' : '已设为不纳入评测。'
    await loadList()
  } catch (error) {
    if (!alive || sequence !== editorSequence) return
    conflict.value = (error as { response?: { status?: number } })?.response?.status === 409
    reviewError.value = conflict.value
      ? `409：${getReviewErrorDetail(error)}。本地编辑已保留，请重新读取反馈后再审核；不会自动覆盖新 revision。`
      : getReviewErrorDetail(error)
  } finally {
    if (alive && sequence === editorSequence) saving.value = false
  }
}
async function loadModels() {
  const sequence = ++modelSequence
  modelsLoading.value = true
  modelsError.value = ''
  try {
    const response = await getAvailableModelsApi()
    if (!alive || sequence !== modelSequence) return
    models.value = response.models
    selectedModels.value = defaultFeedbackModels(response.models)
  } catch (error) {
    if (alive && sequence === modelSequence) modelsError.value = getReviewErrorDetail(error)
  } finally {
    if (alive && sequence === modelSequence) modelsLoading.value = false
  }
}
function toggleSelection(row: QualityFeedback, checked: boolean) {
  if (runBusy.value || saving.value || status.value !== 'confirmed' || row.status !== 'confirmed' || !row.sample) return
  selectedIds.value = selectedIds.value.filter(id => id !== row.id)
  if (checked && selectedIds.value.length < 10) selectedIds.value.push(row.id)
}
async function runEvaluation() {
  if (!alive || !canRun.value) return
  const sequence = ++runSequence
  const querySequence = listSequence
  const payload = { feedback_ids: [...selectedIds.value], config_ids: [...selectedModels.value] }
  const names = models.value.filter(model => payload.config_ids.includes(model.id)).map(model => model.name).join('、')
  runPhase.value = 'confirm'
  runError.value = ''
  try {
    try {
      await ElMessageBox.confirm(`将向所选模型（${names}）发送 ${payload.feedback_ids.length} 条管理员已确认、脱敏并保存的样例，产生正常模型调用费用，但不消耗用户审校次数。本地未保存编辑不会发送。是否运行？`, '确认运行目标评测', { confirmButtonText: '确认发送并运行', cancelButtonText: '取消', type: 'warning' })
    } catch { return }
    if (!alive || sequence !== runSequence || querySequence !== listSequence) return
    runPhase.value = 'running'
    evaluation.value = null
    downloadError.value = ''
    const response = await evaluateQualityFeedbackApi(payload)
    if (alive && sequence === runSequence && querySequence === listSequence) evaluation.value = response
  } catch (error) {
    if (alive && sequence === runSequence && querySequence === listSequence) runError.value = getReviewErrorDetail(error)
  } finally {
    if (alive && sequence === runSequence) runPhase.value = ''
  }
}
function downloadReport() {
  if (!evaluation.value) return
  downloadError.value = ''
  let url: string | null = null
  try {
    url = URL.createObjectURL(new Blob([serializeFeedbackReport(evaluation.value)], { type: 'application/json;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = `quality-feedback-${evaluation.value.generated_at.replace(/[^0-9A-Za-z-]/g, '-')}.json`
    link.click()
  } catch (error) {
    downloadError.value = getReviewErrorDetail(error)
  } finally {
    if (url) URL.revokeObjectURL(url)
  }
}
onMounted(() => { void loadList(); void loadModels() })
onBeforeUnmount(() => { alive = false; listSequence++; editorSequence++; modelSequence++; runSequence++ })
</script>

<style scoped>
.quality-admin { color: var(--el-text-color-primary); font-size: 14px; line-height: 1.65; }
.intro { padding: 0 0 20px; border-bottom: 1px solid var(--el-border-color); margin-bottom: 20px; }
.eyebrow { color: var(--el-text-color-secondary); font-size: 12px; letter-spacing: .08em; }
h2 { font-size: 22px; margin: 5px 0 8px; font-weight: 600; }
h3 { font-size: 17px; margin: 4px 0 10px; }
h4 { margin: 0 0 12px; font-size: 15px; }
p { margin: 8px 0; overflow-wrap: anywhere; }
.muted, .field-caption { color: var(--el-text-color-secondary); font-size: 12px; }
.field-caption { display: block; margin-bottom: 3px; }
.toolbar, .card-heading, .inline-group, .review-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
.toolbar { margin-bottom: 14px; }
.toolbar .el-select { width: 140px; }
.card-heading { justify-content: space-between; }
.feedback-list, .snapshots, .case-list { list-style: none; padding: 0; margin: 0; }
.feedback-card { border: 1px solid var(--el-border-color-lighter); border-radius: 6px; padding: 16px; margin-bottom: 12px; }
.feedback-card.is-current { border-color: var(--el-color-primary); }
.fragment { font-size: 16px; white-space: pre-wrap; }
.user-note { white-space: pre-wrap; }
.snapshots { border-top: 1px solid var(--el-border-color-extra-light); margin-top: 12px; padding-top: 8px; font-size: 12px; }
.snapshots li { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 4px 12px; padding: 2px 0; overflow-wrap: anywhere; }
.review-panel, .evaluation-panel, .results-panel { margin-top: 24px; padding: 20px; border: 1px solid var(--el-border-color); border-radius: 6px; }
.review-panel { border-top: 3px solid var(--el-color-primary); }
.proof-text { white-space: pre-wrap; overflow-wrap: anywhere; padding: 14px 16px; background: var(--el-fill-color-light); border-left: 2px solid var(--el-border-color); line-height: 1.9; max-height: 320px; overflow-y: auto; }
mark { background: var(--el-color-warning-light-7); color: var(--el-text-color-primary); border-bottom: 2px solid var(--el-color-warning); padding: 2px 0; }
.sample-preview { margin-top: 0; margin-bottom: 18px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 16px; }
.form-grid > *, .el-select { min-width: 0; }
.review-actions { margin-top: 16px; }
.suggestion-editor { border-top: 1px solid var(--el-border-color-lighter); margin-bottom: 18px; }
.suggestion-group { margin: 12px 0; }
.suggestion-row { display: flex; align-items: flex-start; gap: 10px; margin-top: 8px; }
.suggestion-row .el-input { flex: 1; min-width: 0; }
.quality-admin :deep(.el-checkbox) { height: auto; white-space: normal; align-items: flex-start; }
.quality-admin :deep(.el-checkbox__input) { margin-top: 4px; }
.quality-admin :deep(.el-checkbox__label) { white-space: normal; line-height: 1.65; }
.error-box { color: var(--el-color-danger); background: var(--el-color-danger-light-9); padding: 10px 14px; border-radius: 4px; }
.warning-text { color: var(--el-color-warning-dark-2); font-size: 13px; }
.success-text { color: var(--el-color-success-dark-2); }
.evaluation-panel > .el-select { width: 100%; margin: 8px 0 14px; }
.scope-note { padding: 10px 12px; background: var(--el-fill-color-light); font-size: 13px; }
.model-result { padding: 18px 0; border-bottom: 1px solid var(--el-border-color-lighter); }
.metrics { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; margin: 0; }
.metrics dd { font-size: 24px; font-variant-numeric: tabular-nums; margin: 4px 0 0; }
.metrics dt { font-size: 13px; }
.metrics small { color: var(--el-text-color-secondary); font-size: 10px; overflow-wrap: anywhere; }
.case-list li { border-top: 1px dashed var(--el-border-color-lighter); padding: 10px 0; font-size: 12px; }
.report-samples { margin-top: 16px; }
.report-samples summary { cursor: pointer; }
.report-sample { font-size: 12px; }
@media (max-width: 640px) {
  .review-panel, .evaluation-panel, .results-panel, .feedback-card { padding: 12px; }
  .form-grid, .metrics { grid-template-columns: 1fr; }
  .metrics { gap: 14px; }
  .metrics dd { font-size: 21px; }
  .review-actions .el-button { margin-left: 0; white-space: normal; height: auto; min-height: 32px; }
  h2 { font-size: 20px; }
  .toolbar { align-items: flex-start; }
}
</style>
