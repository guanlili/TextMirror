<template>
  <section
    v-if="coverage?.status === 'partial'"
    class="coverage-panel"
    aria-label="未完成的审校范围"
  >
    <el-alert
      type="warning"
      :closable="false"
      show-icon
      title="审校尚未完成，请勿把当前结果视为全文无误"
    >
      <p>已完成 {{ coverage.completed_chunks }}/{{ coverage.total_chunks }} 段；下列范围的 AI 检查未完成，已发现的问题和已采纳的修改会保留。</p>
      <p>补查仅发送失败段（包含少量前文），每段按一次普通审校计入额度。</p>
    </el-alert>
    <div class="coverage-actions">
      <el-button
        type="warning"
        plain
        :loading="retrying"
        @click="retryChunks()"
      >
        补查全部失败段
      </el-button>
      <span v-if="retrying">正在补查，请勿离开页面</span>
    </div>
    <details
      v-for="chunk in coverage.failed_chunks"
      :key="`${chunk.start}:${chunk.end}`"
    >
      <summary>第 {{ chunk.start + 1 }}–{{ chunk.end }} 字（含上下文）</summary>
      <pre>{{ chunk.text }}</pre>
      <el-button
        size="small"
        :disabled="retrying"
        @click="retryChunks(chunk)"
      >
        仅补查这一段
      </el-button>
    </details>
  </section>
</template>

<script setup lang="ts">
import { ref, onBeforeUnmount, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { textProofreadApi, type ProofreadCoverage, type ProofreadIssue, type FailedProofreadChunk } from '@/api/proofread'
import { applyChunkRetry } from '@/utils/proofreadCoverage'

const props = defineProps<{
  coverage?: ProofreadCoverage | null
  sourceText: string
  domain: string
  depth?: string
  configId?: number | null
}>()
const emit = defineEmits<{
  'update:coverage': [ProofreadCoverage]
  issues: [ProofreadIssue[]]
}>()
const retrying = ref(false)
let active = true
onBeforeUnmount(() => { active = false })

async function retryChunks(only?: FailedProofreadChunk) {
  if (!props.coverage || retrying.value) return
  const targets = only ? [only] : [...props.coverage.failed_chunks]
  const source = props.sourceText
  let coverage = props.coverage
  retrying.value = true
  let failures = 0
  try {
    for (const chunk of targets) {
      if (!active || props.sourceText !== source) break
      if (Array.from(source).slice(chunk.start, chunk.end).join('') !== chunk.text) {
        ElMessage.error('原文已变化，不能继续补查，请重新校对')
        break
      }
      const previousCoverage: ProofreadCoverage | null | undefined = props.coverage
      try {
        const response = await textProofreadApi({
          text: chunk.text,
          domain: props.domain,
          depth: props.depth || 'standard',
          config_id: props.configId ?? undefined,
        })
        if (!active || props.sourceText !== source || props.coverage !== previousCoverage) break
        const next = applyChunkRetry(coverage, chunk, response)
        coverage = next.coverage
        emit('issues', next.issues)
        emit('update:coverage', coverage)
        await nextTick()
      } catch {
        failures += 1
        break
      }
    }
    if (active && props.sourceText === source) {
      if (coverage.status === 'complete') ElMessage.success('失败段已全部补查完成，请继续审阅新增问题')
      else if (failures) ElMessage.warning('补查未完成，已保留原结果，可稍后重试')
    }
  } finally {
    retrying.value = false
  }
}
</script>

<style scoped>
.coverage-panel { margin-bottom: 16px; border: 1px solid var(--el-color-warning-light-5); border-radius: 10px; padding: 12px; background: var(--surface); }
.coverage-panel p { margin: 4px 0; }
.coverage-actions { display: flex; gap: 12px; align-items: center; margin: 12px 0; font-size: 13px; }
.coverage-panel details { padding: 8px 0; }
.coverage-panel summary { cursor: pointer; font-size: 13px; }
.coverage-panel pre { max-height: 180px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; font-size: 13px; }
</style>
