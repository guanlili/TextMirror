<template>
  <section
    class="proofread-options"
    aria-label="审校设置"
  >
    <div
      class="options-current"
      aria-live="polite"
    >
      <span class="options-eyebrow">当前检查方案</span>
      <strong>{{ currentLabel }}</strong>
      <p>{{ currentDescription }}</p>
      <span class="options-summary">{{ domainLabel }} · {{ modelSummary }}</span>
    </div>
    <details class="options-disclosure">
      <summary>更多检查方式<span>按需调整</span></summary>
      <div class="options-content">
        <fieldset
          v-if="allowModes"
          class="mode-choices"
          :disabled="disabled"
        >
          <legend>选择检查方式</legend>
          <label
            v-for="option in modes"
            :key="option.value"
            class="mode-choice"
            :class="{ 'is-selected': mode === option.value, 'is-disabled': option.value === 'compare' && models.length < 2 }"
          >
            <input
              type="radio"
              :name="`${id}-mode`"
              :value="option.value"
              :checked="mode === option.value"
              :disabled="option.value === 'compare' && models.length < 2"
              @change="selectMode(option.value)"
            >
            <span><strong>{{ option.label }}</strong><small>{{ option.description }}</small></span>
          </label>
          <p
            v-if="models.length < 2"
            class="options-help"
          >
            多模型对比需要至少两个可用模型。
          </p>
        </fieldset>
        <div
          v-if="mode === 'compare'"
          class="model-selection"
        >
          <label :for="`${id}-compare-models`">参与对比的模型</label>
          <el-select
            :id="`${id}-compare-models`"
            :model-value="compareIds"
            multiple
            :multiple-limit="4"
            collapse-tags
            collapse-tags-tooltip
            :disabled="disabled"
            placeholder="选择 2–4 个模型"
            @update:model-value="emit('update:compareIds', $event)"
          >
            <el-option
              v-for="model in models"
              :key="model.id"
              :label="model.name"
              :value="model.id"
            />
          </el-select>
          <p class="options-help">
            各模型分别检查，不互相复核。相同建议也不代表已证实正确。
          </p>
        </div>
        <p
          v-if="mode === 'collaboration'"
          class="options-help"
        >
          同一个模型按职责分别检查。最多 8000 字，最多复核 20 项疑点；不联网、不自动修改。
        </p>
        <ProfessionalRules
          :model-value="domain"
          :disabled="disabled"
          @update:model-value="emit('update:domain', $event)"
        />
        <details
          v-if="mode !== 'compare'"
          class="expert-options"
        >
          <summary>{{ mode === 'single' ? '检查强度与模型' : '模型设置' }}</summary>
          <div
            v-if="mode === 'single'"
            class="model-selection"
          >
            <label :for="`${id}-depth`">检查强度</label>
            <el-select
              :id="`${id}-depth`"
              :model-value="depth"
              :disabled="disabled"
              @update:model-value="emit('update:depth', $event)"
            >
              <el-option
                label="日常审校（默认）"
                value="standard"
              />
              <el-option
                label="更细检查"
                value="deep"
              />
              <el-option
                label="规则初筛（不调用 AI）"
                value="quick"
              />
            </el-select>
            <p class="options-help">
              {{ proofreadDepthHints[depth] }}
            </p>
          </div>
          <div
            v-if="models.length && (depth !== 'quick' || mode === 'collaboration')"
            class="model-selection"
          >
            <label :for="`${id}-model`">使用模型</label>
            <el-select
              :id="`${id}-model`"
              :model-value="modelId"
              :disabled="disabled"
              placeholder="当前默认模型"
              @update:model-value="emit('update:modelId', $event)"
            >
              <el-option
                v-for="model in models"
                :key="model.id"
                :label="`${model.name}${model.is_active ? ' · 默认' : ''}`"
                :value="model.id"
              />
            </el-select>
            <p class="options-help">
              不确定时保持默认即可。不同模型的速度与用量可能不同。
            </p>
          </div>
        </details>
      </div>
    </details>
    <p
      v-if="mode === 'compare'"
      class="options-cost"
      role="status"
    >
      本次 {{ compareIds.length }} 个模型分别审校，按 {{ compareIds.length }} 次应用额度计，模型用量另计；点击开始后才会调用。
    </p>
    <p
      v-else-if="mode === 'collaboration'"
      class="options-cost"
      role="status"
    >
      本次采用分工流程，通常比日常审校更久；扣 1 次应用额度，模型用量另计。
    </p>
    <p class="options-footnote">
      疑似问题会说明依据，修改由你决定。
    </p>
  </section>
</template>

<script setup lang="ts">
import { computed, useId } from 'vue'
import type { AvailableModel } from '@/api/polish'
import { proofreadDepthHints } from '@/utils/proofread'
import ProfessionalRules from './ProfessionalRules.vue'

type Mode = 'single' | 'compare' | 'collaboration'
const props = withDefaults(defineProps<{
  mode?: Mode
  depth: string
  domain: string
  modelId: number | null
  compareIds?: number[]
  models: AvailableModel[]
  disabled?: boolean
  allowModes?: boolean
}>(), { mode: 'single', compareIds: () => [], disabled: false, allowModes: true })
const emit = defineEmits<{
  'update:mode': [Mode]
  'update:depth': [string]
  'update:domain': [string]
  'update:modelId': [number | null]
  'update:compareIds': [number[]]
}>()
const id = useId()
const modes: { value: Mode; label: string; description: string }[] = [
  { value: 'single', label: '日常审校', description: '检查错字、语病及文内一致性，兼顾速度。' },
  { value: 'collaboration', label: '分工审校', description: '分别检查文字表达和全文一致性，再复核疑点。' },
  { value: 'compare', label: '多模型对比', description: '不同模型分别检查，比较共同发现和不同建议。' },
]
const currentLabel = computed(() => props.mode === 'single'
  ? ({ standard: '日常审校', deep: '更细检查', quick: '规则初筛' }[props.depth] || '日常审校')
  : modes.find(option => option.value === props.mode)!.label)
const currentDescription = computed(() => props.mode === 'single'
  ? props.depth === 'quick' ? '只查词库、格式和已有规则，不调用 AI。'
    : props.depth === 'deep' ? '增加思考与补查，耗时更长；不保证建议一定更准确。'
      : '检查错字、语病及文内一致性，输入后即可开始。'
  : modes.find(option => option.value === props.mode)!.description)
const domainLabel = computed(() => ({ general: '通用规范', official: '公文规范', legal: '法律文书规范', auto: '自动识别规范' }[props.domain] || '通用规范'))
const modelSummary = computed(() => {
  if (props.mode === 'single' && props.depth === 'quick') return '不调用 AI'
  if (props.mode === 'compare') return props.compareIds.map(id => props.models.find(model => model.id === id)?.name || `模型 ${id}`).join('、') || '请选择两个模型'
  const model = props.models.find(model => model.id === props.modelId)
  return !model || model.is_active ? '默认模型' : `自选模型：${model.name}`
})
function selectMode(mode: Mode) {
  if (props.disabled) return
  if (mode === 'single') emit('update:depth', 'standard')
  if (mode === 'compare' && props.compareIds.length === 0) {
    const preferred = [...props.models].sort((a, b) => Number(b.is_active) - Number(a.is_active))
    emit('update:compareIds', preferred.slice(0, 2).map(model => model.id))
  }
  emit('update:mode', mode)
}
</script>

<style scoped>
.proofread-options { color: var(--color-text); min-width: 0; }
.options-current { display: grid; gap: 8px; padding-bottom: var(--spacing-md); }
.options-eyebrow { color: var(--color-text-secondary); font-size: 12px; letter-spacing: .08em; }
.options-current strong { font-size: 19px; font-weight: 600; letter-spacing: -.03em; }
.options-current p { font-size: 13px; line-height: 1.8; color: var(--color-text-secondary); }
.options-summary { font-size: 12px; line-height: 1.7; color: var(--color-primary); overflow-wrap: anywhere; }
.options-disclosure { border-top: 1px solid var(--color-border); }
summary { cursor: pointer; padding: 14px 0; font-size: 13px; font-weight: 500; }
summary span { float: right; font-size: 12px; font-weight: 400; color: var(--color-text-secondary); }
summary:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 3px; border-radius: 4px; }
.options-content { display: grid; gap: var(--spacing-md); padding-bottom: 12px; }
.mode-choices { display: grid; gap: 8px; border: 0; min-width: 0; }
legend { font-size: 12px; color: var(--color-text-secondary); padding-bottom: 8px; }
.mode-choice { display: flex; gap: 10px; align-items: flex-start; border: 1px solid var(--color-border); border-radius: var(--review-option-radius); padding: 12px; cursor: pointer; background: var(--surface); transition: border-color .15s ease; }
.mode-choice.is-selected { border-color: var(--color-primary); background: var(--review-option-selected); }
.mode-choice:focus-within { outline: 2px solid var(--review-option-ring); }
.mode-choice input { flex: 0 0 auto; margin-top: 3px; accent-color: var(--color-primary); }
.mode-choice strong { display: block; font-size: 13px; font-weight: 500; }
.mode-choice small { display: block; font-size: 12px; line-height: 1.7; margin-top: 5px; color: var(--color-text-secondary); }
.mode-choice.is-disabled, fieldset:disabled .mode-choice { opacity: .6; cursor: not-allowed; }
.model-selection { display: grid; gap: 8px; min-width: 0; }
.model-selection label { font-size: 13px; }
.model-selection .el-select { width: 100%; min-width: 0; }
.expert-options { border-top: 1px solid var(--color-border); }
.expert-options .model-selection + .model-selection { margin-top: 16px; }
.options-help, .options-cost, .options-footnote { font-size: 12px; line-height: 1.8; color: var(--color-text-secondary); overflow-wrap: anywhere; }
.options-cost { padding: 12px; border-radius: var(--review-option-radius); background: var(--surface-soft); margin-bottom: 12px; }
.options-footnote { border-top: 1px solid var(--color-border); padding-top: 12px; }
@media (prefers-reduced-motion: reduce) { .mode-choice { transition: none; } }
</style>
