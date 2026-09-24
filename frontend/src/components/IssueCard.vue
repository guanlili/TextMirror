<template>
  <div
    class="issue-item"
    :class="{
      'is-accepted': issue._accepted,
      'is-ignored': issue._ignored,
      'is-active': active,
    }"
    tabindex="0"
    @focus="emit('activate')"
    @click="emit('activate')"
    @mouseenter="emit('activate')"
    @mouseleave="emit('deactivate')"
  >
    <div class="issue-header">
      <span class="issue-number">#{{ number }}</span>
      <el-tag
        :type="severityColor(issue.severity)"
        size="small"
        effect="dark"
      >
        {{ typeLabel(issue.type) }}
      </el-tag>
      <el-tag
        :type="severityColor(issue.severity)"
        size="small"
        effect="plain"
      >
        {{ severityLabel(issue.severity) }}
      </el-tag>
    </div>
    <div class="issue-body">
      <div class="issue-context">
        {{ context }}
      </div>
      <div class="issue-diff">
        <span
          class="text text-del"
          :title="issue.original"
        >{{ issue.original }}</span>
        <el-icon class="arrow-icon">
          <Right />
        </el-icon>
        <span
          class="text text-add"
          :title="issue.suggestion"
        >{{ issue.suggestion }}</span>
      </div>
      <div
        v-if="issue.explanation"
        class="issue-explanation"
      >
        <el-icon><InfoFilled /></el-icon>
        <span>{{ issue.explanation }}</span>
      </div>
      <p
        v-if="provenance"
        class="collaboration-note"
        data-testid="collaboration-provenance"
      >
        {{ provenance }}
      </p>
    </div>
    <div
      v-if="!issue._accepted && !issue._ignored"
      class="issue-actions"
    >
      <el-button
        v-if="issue.suggestion"
        type="primary"
        size="small"
        @click="emit('accept')"
      >
        <el-icon><Check /></el-icon>仅修改此处
      </el-button>
      <el-button
        v-else-if="issue.type === 'sensitive' && issue.original"
        type="warning"
        size="small"
        @click="emit('delete')"
      >
        <el-icon><Delete /></el-icon>{{ deleteLabel }}
      </el-button>
      <el-button
        v-if="issue.suggestion || (issue.type === 'sensitive' && issue.original)"
        size="small"
        :title="matchingHint"
        @click="emit('accept-matching')"
      >
        全文同类
      </el-button>
      <el-button
        size="small"
        @click="emit('ignore')"
      >
        <el-icon><Close /></el-icon>忽略
      </el-button>
    </div>
    <div
      v-else
      class="issue-status"
    >
      <el-tag
        v-if="issue._accepted"
        type="success"
        size="small"
      >
        已接受
      </el-tag>
      <el-tag
        v-if="issue._ignored"
        type="info"
        size="small"
      >
        已忽略
      </el-tag>
      <el-button
        v-if="issue._ignored"
        text
        size="small"
        :disabled="feedbackDisabled"
        @click="emit('feedback')"
      >
        补充原因（可选）
      </el-button>
      <el-button
        text
        size="small"
        @click="emit('undo')"
      >
        撤销
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { severityColor, severityLabel, typeLabel } from '@/utils/proofread'
import type { ReviewIssue } from '@/utils/review'

withDefaults(defineProps<{
  issue: ReviewIssue
  /** 展示序号（全局索引 + 1） */
  number: number
  active?: boolean
  /** 定位上下文（两页格式不同，由页面计算传入） */
  context: string
  /** 协作审校来源说明（仅文本审校协作模式） */
  provenance?: string
  deleteLabel?: string
  matchingHint?: string
  feedbackDisabled?: boolean
}>(), {
  active: false,
  provenance: '',
  deleteLabel: '删除该词',
  matchingHint: '',
  feedbackDisabled: false,
})

const emit = defineEmits<{
  activate: []
  deactivate: []
  accept: []
  delete: []
  'accept-matching': []
  ignore: []
  undo: []
  feedback: []
}>()
</script>

<style scoped lang="scss">
.issue-item {
  padding: 14px 14px 12px;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  margin-bottom: 12px;
  background: var(--surface);
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  cursor: pointer;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
  scroll-margin-top: 20px;

  &:hover {
    box-shadow: 0 4px 16px rgba(99, 102, 241, 0.08), 0 2px 4px rgba(0, 0, 0, 0.04);
    border-color: #c7d2fe;
    transform: translateY(-1px);
  }

  &:focus-visible {
    outline: 2px solid var(--color-primary);
  }

  &.is-active {
    border-color: #fbbf24;
    box-shadow: 0 0 0 3px rgba(251, 191, 36, 0.12), 0 4px 12px rgba(251, 191, 36, 0.15);
    background: linear-gradient(135deg, #fffbeb 0%, #ffffff 100%);
  }

  &.is-accepted {
    opacity: 0.65;
    background: linear-gradient(135deg, #f0fdf4 0%, #ffffff 100%);
    border-color: #bbf7d0;
  }

  &.is-ignored {
    opacity: 0.5;
    background: var(--surface-soft);
    border-color: #e5e7eb;
  }
}

.issue-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;

  .issue-number {
    font-size: 11px;
    font-weight: 600;
    color: #6b7280;
    min-width: 28px;
    background: linear-gradient(135deg, #f3f4f6 0%, #e5e7eb 100%);
    padding: 3px 7px;
    border-radius: 6px;
    letter-spacing: 0.02em;
  }
}

.issue-body {
  font-size: 14px;
  line-height: 1.6;

  // 原文 → 建议 单行高亮对比
  .issue-diff {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    padding: 12px 14px;
    background: linear-gradient(135deg, #fef2f2 0%, #fafafa 50%, #f0fdf4 100%);
    border-radius: 8px;
    margin-bottom: 10px;
    border: 1px solid #f3f4f6;

    .text {
      font-size: 15px;
      font-weight: 600;
      max-width: 100%;
      word-break: break-all;
      line-height: 1.5;
    }

    .text-del {
      color: #dc2626;
      text-decoration: line-through;
      text-decoration-thickness: 2px;
      text-decoration-color: #fca5a5;
    }

    .text-add {
      color: #059669;
    }

    .arrow-icon {
      font-size: 20px;
      color: #f59e0b;
      flex-shrink: 0;
      font-weight: bold;
    }
  }

  .issue-explanation {
    display: flex;
    align-items: flex-start;
    gap: 6px;
    color: #6b7280;
    font-size: 13px;
    padding: 6px 8px;
    background: #f9fafb;
    border-radius: 6px;
    border-left: 2px solid #e5e7eb;

    .el-icon {
      margin-top: 2px;
      color: #9ca3af;
      flex-shrink: 0;
    }
  }
}

.issue-context {
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--color-text-secondary);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.collaboration-note {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.7;
  overflow-wrap: anywhere;
}

.issue-actions, .issue-status {
  margin-top: 10px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;

  .el-button .el-icon {
    margin-right: 4px;
  }
}
</style>
