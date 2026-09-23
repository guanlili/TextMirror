<template>
  <div class="quality-feedback-bar">
    <span>有遗漏或不合适的建议？反馈经人工确认后才会用于评测。</span>
    <el-button
      size="small"
      :disabled="recordId === null"
      @click="open()"
    >
      上报漏检
    </el-button>
    <span v-if="recordId === null">登录后生成的审校记录才能提交反馈。</span>
  </div>
  <el-dialog
    v-model="visible"
    :title="isMissed ? '上报漏检' : '补充忽略原因（可选）'"
    width="min(680px, 94vw)"
  >
    <p class="feedback-note">
      这不会改变审阅决定，也不代表已确认模型错误。请勿在补充说明中填写敏感信息。
    </p>
    <el-form
      label-position="top"
      :disabled="submitting || !!success"
      @submit.prevent="submit"
    >
      <template v-if="isMissed">
        <el-form-item label="在不可变原文中选中漏检片段">
          <textarea
            class="feedback-source"
            :value="sourceText"
            readonly
            aria-label="选择漏检原文"
            @select="captureSelection"
          />
        </el-form-item>
        <el-form-item label="目标片段（也可粘贴原文，最多 500 字）">
          <el-input
            v-model="original"
            placeholder="从上方原文选取，或粘贴需要反馈的片段"
          />
        </el-form-item>
        <el-form-item
          v-if="targets.length > 1"
          label="选择具体位置"
        >
          <el-select
            v-model="start"
            placeholder="同一片段出现多次，请选择位置"
            class="feedback-full-width"
          >
            <el-option
              v-for="target in targets"
              :key="target.start"
              :label="target.label"
              :value="target.start"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="问题类型">
          <el-select v-model="issueType">
            <el-option
              v-for="type in feedbackIssueTypes"
              :key="type"
              :value="type"
              :label="typeLabel(type)"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="建议改为（可选）">
          <el-input
            v-model="suggestion"
            placeholder="不确定时可以留空"
          />
        </el-form-item>
      </template>
      <template v-else>
        <div class="feedback-target">
          <span>{{ original }}</span><span aria-hidden="true"> → </span><span>{{ suggestion || '无替换建议' }}</span>
        </div>
        <el-form-item label="为什么保留原文？">
          <el-radio-group v-model="kind">
            <el-radio-button
              v-for="reason in reasons"
              :key="reason"
              :value="reason"
            >
              {{ feedbackKindLabels[reason] }}
            </el-radio-button>
          </el-radio-group>
        </el-form-item>
      </template>
      <p
        v-if="selectedTarget"
        class="feedback-location"
      >
        {{ selectedTarget.label }}
      </p>
      <el-form-item label="补充说明（可选）">
        <el-input
          v-model="note"
          type="textarea"
          :rows="3"
          placeholder="例如适用语境或判断依据，最多 1000 字"
        />
      </el-form-item>
      <el-alert
        v-if="validationError"
        type="info"
        :title="validationError"
        :closable="false"
      />
      <el-alert
        v-if="error"
        type="error"
        :title="error"
        :closable="false"
      />
      <el-alert
        v-if="success"
        type="success"
        :title="success"
        :closable="false"
      />
    </el-form>
    <template #footer>
      <el-button @click="visible = false">
        关闭
      </el-button>
      <el-button
        type="primary"
        :loading="submitting"
        :disabled="!!validationError || !!success"
        @click="submit"
      >
        提交反馈
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { feedbackKindLabels, submitQualityFeedbackApi, type FeedbackKind } from '@/api/qualityFeedback'
import { getReviewErrorDetail } from '@/api/review'
import type { ReviewIssue } from '@/composables/useProofreadReview'
import { feedbackIssueTypes, findFeedbackTargets } from '@/utils/qualityFeedback'
import { typeLabel } from '@/utils/proofread'

const props = defineProps<{ recordId: number | null; sourceText: string }>()
const visible = ref(false)
const kind = ref<FeedbackKind>('missed')
const original = ref('')
const suggestion = ref('')
const issueType = ref('typo')
const start = ref<number | null>(null)
const note = ref('')
const error = ref('')
const success = ref('')
const submitting = ref(false)
const reasons: Exclude<FeedbackKind, 'missed'>[] = ['false_positive', 'bad_suggestion', 'preference', 'other']
let generation = 0
const isMissed = computed(() => kind.value === 'missed')
const targets = computed(() => findFeedbackTargets(props.sourceText, original.value))
const selectedTarget = computed(() => targets.value.find(target => target.start === start.value))
const validationError = computed(() => {
  if (props.recordId === null) return '请登录并完成一次审校后提交反馈。'
  if (!original.value.trim()) return '请先选择需要反馈的原文片段。'
  if (Array.from(original.value).length > 500) return '目标片段最多 500 字，请缩小选区。'
  if (!selectedTarget.value) return isMissed.value ? '请选择原文中的具体位置，不要使用修改后的文本。' : '这条建议尚未准确定位，暂不能提交质量反馈。'
  if (Array.from(suggestion.value).length > 500) return '修改建议最多 500 字。'
  if (Array.from(note.value).length > 1000) return '补充说明最多 1000 字。'
  return ''
})

watch(original, () => {
  if (!isMissed.value) return
  start.value = targets.value.length === 1 ? targets.value[0]!.start : null
}, { flush: 'sync' })
watch(visible, value => { if (!value) invalidate() })
watch(() => [props.recordId, props.sourceText], () => { visible.value = false; invalidate() })
onBeforeUnmount(invalidate)

function invalidate() {
  generation++
  submitting.value = false
}

function open(issue?: ReviewIssue) {
  invalidate()
  kind.value = issue ? 'preference' : 'missed'
  original.value = issue?.original || ''
  suggestion.value = issue?.suggestion || ''
  issueType.value = issue?.type || 'typo'
  start.value = issue?.start ?? null
  if (issue && issue.end !== selectedTarget.value?.end) start.value = null
  note.value = ''
  error.value = ''
  success.value = ''
  visible.value = true
}

function captureSelection(event: { target: unknown }) {
  if (submitting.value || success.value) return
  const input = event.target as { selectionStart: number; selectionEnd: number }
  if (input.selectionStart === input.selectionEnd) return
  original.value = props.sourceText.slice(input.selectionStart, input.selectionEnd)
  start.value = Array.from(props.sourceText.slice(0, input.selectionStart)).length
}

async function submit() {
  if (submitting.value || validationError.value || success.value || props.recordId === null || !selectedTarget.value) return
  const run = ++generation
  submitting.value = true
  error.value = ''
  try {
    const result = await submitQualityFeedbackApi({
      record_id: props.recordId, kind: kind.value, original: original.value, suggestion: suggestion.value,
      issue_type: issueType.value, start: selectedTarget.value.start, end: selectedTarget.value.end, note: note.value,
    })
    if (run !== generation) return
    success.value = result.status === 'pending' ? '反馈已记录，等待人工确认；重复提交不会新增样例。' : '该反馈已记录并经过审核，不会重复新增或覆盖审核结果。'
  } catch (cause) {
    if (run === generation) error.value = getReviewErrorDetail(cause)
  } finally {
    if (run === generation) submitting.value = false
  }
}

defineExpose({ open })
</script>

<style scoped>
.quality-feedback-bar { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; padding: 12px 0; color: var(--el-text-color-secondary); font-size: 13px; }
.feedback-note { margin: 0 0 20px; color: var(--el-text-color-secondary); line-height: 1.7; }
.feedback-source { box-sizing: border-box; width: 100%; min-height: 160px; max-height: 320px; resize: vertical; padding: 14px; border: 1px solid var(--el-border-color); border-radius: 4px; background: var(--el-fill-color-light); color: var(--el-text-color-primary); font: inherit; line-height: 1.9; }
.feedback-source:focus { outline: 2px solid var(--el-color-primary-light-5); }
.feedback-full-width { width: 100%; }
.feedback-target, .feedback-location { overflow-wrap: anywhere; white-space: pre-wrap; line-height: 1.8; padding: 12px; background: var(--el-fill-color-light); border-left: 3px solid var(--el-color-primary); margin-bottom: 20px; }
.feedback-location { color: var(--el-text-color-secondary); font-size: 13px; }
.el-alert + .el-alert { margin-top: 12px; }
</style>
