<template>
  <div class="review-preview">
    <div class="preview-toolbar">
      <el-radio-group v-model="view" size="small" aria-label="预览内容">
        <el-radio-button value="modified">修订预览</el-radio-button>
        <el-radio-button value="original">{{ originalHtml ? '原始排版' : '原文' }}</el-radio-button>
      </el-radio-group>
      <span>{{ Array.from(view === 'modified' ? currentText : sourceText).length }} 字</span>
    </div>
    <p v-if="view === 'original'" class="preview-tip">此处保留原文；已采纳的修改请查看修订预览。</p>
    <div v-if="view === 'original' && originalHtml" class="formatted-original" v-html="safeOriginalHtml"></div>
    <div v-else ref="previewRef" class="preview-text" v-html="previewHtml"></div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch, nextTick } from 'vue'
import type { ReviewIssue } from '@/composables/useProofreadReview'
import { buildReviewPreview } from '@/utils/reviewPreview'
import { escapeHtml } from '@/utils/proofread'
import { sanitizeDocumentHtml } from '@/utils/sanitize'

const props = defineProps<{
  sourceText: string
  currentText: string
  issues: ReviewIssue[]
  patches: { start: number; end: number; replacement: string }[]
  activeIndex?: number
  originalHtml?: string
}>()
const view = ref('modified')
const previewRef = ref<HTMLElement>()
const safeOriginalHtml = computed(() => sanitizeDocumentHtml(props.originalHtml || ''))
const previewHtml = computed(() => view.value === 'original'
  ? escapeHtml(props.sourceText)
  : buildReviewPreview(props.currentText, props.issues, props.patches))
watch(() => [props.activeIndex, previewHtml.value], async () => {
  await nextTick()
  const root = previewRef.value
  if (!root) return
  root.querySelectorAll('mark.is-hover').forEach(el => el.classList.remove('is-hover'))
  if (props.activeIndex != null && props.activeIndex >= 0) {
    root.querySelectorAll(`mark[data-issue-idx="${props.activeIndex}"]`).forEach(el => el.classList.add('is-hover'))
  }
})
</script>

<style scoped>
.preview-toolbar { display: flex; gap: 12px; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.preview-toolbar span, .preview-tip { font-size: 12px; color: var(--color-text-secondary); }
.preview-text { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 15px; line-height: 2; }
.formatted-original { overflow-wrap: anywhere; overflow-x: auto; }
.preview-text :deep(mark) { padding: 2px 1px; border-radius: 3px; color: inherit; }
.preview-text :deep(.hl-error) { background: #fee2e2; color: #7f1d1d; }
.preview-text :deep(.hl-warning) { background: #fef3c7; color: #78350f; }
.preview-text :deep(.hl-info) { background: #dbeafe; color: #1e3a8a; }
.preview-text :deep(.is-hover) { outline: 2px solid #f59e0b; font-weight: 600; }
</style>
