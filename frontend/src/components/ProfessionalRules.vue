<template>
  <details class="professional-rules">
    <summary>专业规范（可选）<span>{{ label }}</span></summary>
    <p>普通内容使用通用检查；公文、合同等内容可选择对应的检查重点。</p>
    <el-radio-group
      :model-value="modelValue"
      :disabled="disabled"
      aria-label="专业规范"
      @update:model-value="$emit('update:modelValue', String($event))"
    >
      <el-radio value="general">
        通用检查
      </el-radio>
      <el-radio value="official">
        公文规范
      </el-radio>
      <el-radio value="legal">
        法律文书规范
      </el-radio>
      <el-radio
        v-if="modelValue === 'auto'"
        value="auto"
      >
        自动识别（原记录设置）
      </el-radio>
    </el-radio-group>
    <p v-if="modelValue === 'official'">
      侧重公文措辞、称谓与行文规范。
    </p>
    <p v-else-if="modelValue === 'legal'">
      侧重术语、主体称谓与条款表达，仍需专业复核。
    </p>
  </details>
</template>
<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ modelValue: string; disabled?: boolean }>()
defineEmits<{ 'update:modelValue': [value: string] }>()
const label = computed(() => ({ general: '通用检查', official: '已选公文规范', legal: '已选法律文书规范', auto: '自动识别' }[props.modelValue] || '通用检查'))
</script>
<style scoped>
.professional-rules { border: 1px solid var(--color-border); background: var(--surface); border-radius: 10px; padding: 14px; }
summary { cursor: pointer; font-size: 13px; font-weight: 500; line-height: 1.8; }
summary span { display: block; margin-left: 14px; font-size: 12px; color: var(--color-primary); font-weight: 400; }
p { font-size: 12px; line-height: 1.8; color: var(--color-text-secondary); margin: 12px 0 0; }
.el-radio-group { display: flex; flex-direction: column; align-items: flex-start; margin-top: 8px; }
</style>
