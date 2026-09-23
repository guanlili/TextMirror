<template>
  <div class="history-page">
    <el-card>
      <template #header>
        <div class="card-header">
          <span class="card-title">校对历史</span>
          <div class="header-filters">
            <el-select
              v-model="filterType"
              placeholder="全部类型"
              clearable
              size="small"
              style="width: 120px;"
              @change="fetchList"
            >
              <el-option
                label="全部"
                value=""
              />
              <el-option
                label="AI润色"
                value="polish"
              />
              <el-option
                label="文本校对"
                value="text"
              />
              <el-option
                label="文档校对"
                value="document"
              />
            </el-select>
            <el-select
              v-model="filterDomain"
              placeholder="全部领域"
              clearable
              size="small"
              style="width: 120px;"
              @change="fetchList"
            >
              <el-option
                label="全部"
                value=""
              />
              <el-option
                label="通用"
                value="general"
              />
              <el-option
                label="公文"
                value="official"
              />
              <el-option
                label="法律"
                value="legal"
              />
            </el-select>
          </div>
        </div>
      </template>

      <el-table
        v-loading="loading"
        :data="items"
        stripe
        @row-click="openDetail"
      >
        <el-table-column
          label="类型"
          width="100"
          align="center"
        >
          <template #default="{ row }">
            <el-tag
              :type="recordTypeTag(row.type)"
              size="small"
            >
              {{ recordTypeLabel(row.type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          label="内容预览"
          min-width="300"
        >
          <template #default="{ row }">
            <div class="preview-text">
              {{ row.text_preview }}
            </div>
            <div
              v-if="row.source_filename"
              class="filename-tag"
            >
              <el-tag
                size="small"
                type="info"
              >
                {{ row.source_filename }}
              </el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column
          prop="domain"
          label="领域"
          width="80"
          align="center"
        >
          <template #default="{ row }">
            {{ domainLabel(row.domain) }}
          </template>
        </el-table-column>
        <el-table-column
          label="审阅概况"
          min-width="240"
        >
          <template #default="{ row }">
            <span
              v-if="row.type === 'polish'"
              class="summary-text"
            >—</span>
            <div
              v-else
              class="review-summary"
            >
              <div class="summary-tags">
                <el-tag
                  type="info"
                  size="small"
                >
                  {{ modeLabel(row.mode) }}
                </el-tag>
                <el-tag
                  :type="coverageTag(row.coverage_status)"
                  size="small"
                  effect="plain"
                >
                  {{ coverageLabel(row.coverage_status) }}
                </el-tag>
              </div>
              <div class="summary-text">
                已发现 {{ row.review_summary.total }} 项 · {{ decisionSummary(row as HistoryItem) }}
              </div>
              <div
                v-if="row.review_summary.failed_models"
                class="summary-warning"
              >
                {{ row.review_summary.failed_models }} 个模型失败，不能视为零问题
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column
          label="Token"
          width="100"
          align="center"
        >
          <template #default="{ row }">
            <span style="font-size: 12px; color: var(--color-text-secondary);">{{ row.token_usage?.total_tokens || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column
          label="时间"
          width="170"
        >
          <template #default="{ row }">
            <span style="font-size: 12px; color: var(--color-text-secondary);">{{ formatTime(row.created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column
          label="操作"
          width="160"
          align="center"
        >
          <template #default="{ row }">
            <el-button
              v-if="row.type === 'text' || row.type === 'document'"
              type="primary"
              link
              size="small"
              @click.stop="continueReview(row as HistoryItem)"
            >
              继续审阅
            </el-button>
            <el-popconfirm
              title="确定删除此记录？"
              @confirm.stop="handleDelete(row.id)"
            >
              <template #reference>
                <el-button
                  type="danger"
                  link
                  size="small"
                  @click.stop
                >
                  删除
                </el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>

      <el-empty
        v-if="!loading && items.length === 0"
        description="暂无校对历史"
      />

      <div
        v-if="total > pageSize"
        class="pagination-wrap"
      >
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="total, prev, pager, next"
          @current-change="fetchList"
        />
      </div>
    </el-card>

    <!-- 详情抽屉 -->
    <el-drawer
      v-model="showDetail"
      :title="detail?.type === 'polish' ? '润色详情' : '校对详情'"
      size="min(600px, 100vw)"
      direction="rtl"
    >
      <template v-if="detail">
        <div class="detail-meta">
          <el-tag :type="recordTypeTag(detail.type)">
            {{ recordTypeLabel(detail.type) }}
          </el-tag>
          <el-tag type="info">
            {{ detail.type === 'polish' ? polishStyleLabel(detail.domain) : domainLabel(detail.domain) }}
          </el-tag>
          <template v-if="detail.type !== 'polish'">
            <el-tag type="info">
              {{ modeLabel(detail.mode) }}
            </el-tag>
            <el-tag
              :type="coverageTag(detail.coverage_status)"
              effect="plain"
            >
              {{ coverageLabel(detail.coverage_status) }}
            </el-tag>
          </template>
          <span
            v-if="detail.source_filename"
            style="font-size: 13px; color: var(--color-text-secondary);"
          >{{ detail.source_filename }}</span>
        </div>

        <div
          v-if="detail.type !== 'polish'"
          class="review-summary"
        >
          <div class="summary-text">
            已发现 {{ detail.review_summary.total }} 项 · {{ decisionSummary(detail) }}
          </div>
          <div class="summary-text">
            按已保存审阅统计；未保存操作不计入。
          </div>
          <div
            v-if="detail.review_summary.failed_models"
            class="summary-warning"
          >
            {{ detail.review_summary.failed_models }} 个模型失败，不能视为零问题。
          </div>
        </div>

        <el-divider content-position="left">
          原文
        </el-divider>
        <div class="detail-text">
          {{ detail.original_text }}
        </div>

        <div class="rerun-bar">
          <el-button
            v-if="detail.type === 'text' || detail.type === 'document'"
            type="primary"
            size="small"
            @click="continueReview(detail)"
          >
            继续审阅
          </el-button>
          <el-button
            v-if="detail.type === 'polish'"
            type="primary"
            size="small"
            @click="rerun('polish')"
          >
            <el-icon><MagicStick /></el-icon>再次润色
          </el-button>
          <el-button
            v-else
            type="primary"
            size="small"
            @click="rerun('proofread')"
          >
            <el-icon><Edit /></el-icon>重新校对
          </el-button>
        </div>

        <!-- AI润色结果 -->
        <template v-if="detail.type === 'polish'">
          <el-divider content-position="left">
            润色结果
          </el-divider>
          <div class="polish-versions">
            <div
              v-for="(ver, i) in (detail.result?.versions || [])"
              :key="`${ver.label}-${i}`"
              class="polish-version-item"
            >
              <div class="version-label">
                <el-tag size="small">
                  {{ ver.label }}
                </el-tag>
              </div>
              <div class="version-content">
                {{ ver.content }}
              </div>
            </div>
            <div
              v-if="!detail.result?.versions?.length && detail.modified_text"
              class="detail-text"
              style="white-space: pre-wrap;"
            >
              {{ detail.modified_text }}
            </div>
          </div>
        </template>

        <!-- 校对问题列表 -->
        <template v-else>
          <el-divider content-position="left">
            问题列表 ({{ detail.issues?.length || 0 }})
          </el-divider>
          <div class="detail-issues">
            <div
              v-for="(issue, i) in (detail.issues || [])"
              :key="`${issue.start ?? ''}-${issue.end ?? ''}-${issue.type}-${i}`"
              class="issue-item"
            >
              <div class="issue-head">
                <el-tag
                  :type="severityColor(issue.severity)"
                  size="small"
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
                <el-tag
                  type="info"
                  size="small"
                  effect="plain"
                >
                  {{ issue._accepted ? '已采纳' : issue._ignored ? '已忽略' : '待处理' }}
                </el-tag>
              </div>
              <div class="issue-body">
                <div><span class="label">原文：</span><span class="text-del">{{ issue.original }}</span></div>
                <div><span class="label">建议：</span><span class="text-add">{{ issue.suggestion }}</span></div>
                <div v-if="issue.explanation">
                  <span class="label">说明：</span><span class="text-muted">{{ issue.explanation }}</span>
                </div>
              </div>
            </div>
            <el-empty
              v-if="!detail.issues?.length"
              :description="emptyIssuesLabel(detail.coverage_status)"
              :image-size="60"
            />
          </div>
        </template>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  listHistoryApi, getHistoryDetailApi, deleteHistoryApi,
  type HistoryItem, type HistoryDetail, type HistoryReviewMetadata,
} from '@/api/history'
import { typeLabel, severityColor, severityLabel } from '@/utils/proofread'

const router = useRouter()
const loading = ref(false)
const items = ref<HistoryItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const filterType = ref('')
const filterDomain = ref('')

const showDetail = ref(false)
const detail = ref<HistoryDetail | null>(null)

onMounted(() => fetchList())

function modeLabel(mode: HistoryReviewMetadata['mode']) {
  return mode === 'compare' ? '对比' : mode === 'collaboration' ? '协作' : mode === 'single' ? '单模型' : '未记录模式'
}
function coverageLabel(status: HistoryReviewMetadata['coverage_status']) {
  return status === 'complete' ? '覆盖完整' : status === 'partial' ? '部分完成' : '覆盖未知'
}
function coverageTag(status: HistoryReviewMetadata['coverage_status']) {
  return status === 'complete' ? 'success' : status === 'partial' ? 'warning' : 'info'
}
function decisionSummary(record: HistoryReviewMetadata) {
  const summary = record.review_summary
  return `已采纳 ${summary.accepted} / 已忽略 ${summary.ignored} / 待处理 ${summary.pending}`
}
function emptyIssuesLabel(status: HistoryReviewMetadata['coverage_status']) {
  if (status === 'partial') return '已完成范围暂无问题，仍有未完成审校'
  if (status !== 'complete') return '暂无问题报告，未记录覆盖范围，无法确认全文完成'
  return '没有发现问题，仍建议人工复核'
}

/** 只读取保存的审阅，不提取旧签名 URL，也不重新调用校对。 */
function continueReview(record: Pick<HistoryItem, 'id' | 'type'>) {
  if (record.type !== 'text' && record.type !== 'document') return
  void router.push({
    path: record.type === 'document' ? '/proofread/document' : '/proofread/text',
    query: { review: String(record.id) },
  })
}

/** 从历史记录复跑：原文带去润色页或文本校对页 */
function rerun(kind: 'polish' | 'proofread') {
  if (!detail.value?.original_text) return ElMessage.warning('该记录没有可复用的原文')
  sessionStorage.setItem('tm_rerun_text', detail.value.original_text)
  router.push(kind === 'polish' ? '/polish' : '/proofread/text')
}

async function fetchList() {
  loading.value = true
  try {
    const res = await listHistoryApi({
      page: page.value,
      page_size: pageSize,
      type: filterType.value || undefined,
      domain: filterDomain.value || undefined,
    })
    items.value = res.items
    total.value = res.total
  } catch {
    // 拦截器已处理
  }
  loading.value = false
}

async function openDetail(row: HistoryItem) {
  try {
    detail.value = await getHistoryDetailApi(row.id)
    showDetail.value = true
  } catch {
    // 拦截器已处理
  }
}

async function handleDelete(id: number) {
  try {
    await deleteHistoryApi(id)
    ElMessage.success('记录已删除')
    await fetchList()
  } catch {
    // 拦截器已处理
  }
}

import { formatTime } from '@/utils/format'

function domainLabel(d: string): string {
  // 保留旧领域映射：历史记录可能存在收敛前（power 等）的数据
  const m: Record<string, string> = { general: '通用', official: '公文', legal: '法律', power: '电力', new_energy: '新能源', meter: '电能表' }
  return m[d] || d
}

function recordTypeLabel(t: string): string {
  switch (t) { case 'polish': return 'AI润色'; case 'text': return '文本校对'; case 'document': return '文档校对'; default: return t }
}

function recordTypeTag(t: string): 'primary' | 'success' | 'warning' | 'info' | 'danger' {
  switch (t) { case 'polish': return 'warning'; case 'text': return 'primary'; case 'document': return 'success'; default: return 'info' }
}

function polishStyleLabel(style: string): string {
  const m: Record<string, string> = {
    formal: '正式规范', friendly: '亲切友好', plain: '通俗易懂', concise: '精简凝练',
    evidence: '论证严谨', strategic: '战略性', practical: '务实实用',
    firm: '坚定有力', gentle: '柔和委婉', action: '行动号召'
  }
  return m[style] || style
}
</script>

<style scoped lang="scss">
.history-page { max-width: 1200px; margin: 0 auto; }

.card-header {
  display: flex; align-items: center; justify-content: space-between;
  .card-title { font-size: 18px; font-weight: 600; }
  .header-filters { display: flex; gap: 8px; }
}

.preview-text {
  font-size: 13px; color: var(--color-text); line-height: 1.5;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 400px;
}
.filename-tag { margin-top: 4px; }
.review-summary { display: grid; gap: 4px; }
.summary-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.summary-text { font-size: 12px; color: var(--color-text-secondary); line-height: 1.7; overflow-wrap: anywhere; }
.summary-warning { font-size: 12px; color: var(--el-color-warning-dark-2); }

.pagination-wrap { margin-top: 16px; display: flex; justify-content: flex-end; }

.detail-meta { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
.detail-text {
  background: #f9f9f9; padding: 12px; border-radius: 6px;
  font-size: 13px; line-height: 1.8; white-space: pre-wrap; max-height: 200px; overflow-y: auto;
}

.detail-issues {
  .issue-item {
    padding: 10px; border: 1px solid #eee; border-radius: 6px; margin-bottom: 8px;
    &:hover { box-shadow: 0 1px 4px rgba(0,0,0,0.06); }
  }
  .issue-head { display: flex; gap: 6px; margin-bottom: 6px; }
  .issue-body {
    font-size: 13px; line-height: 1.7;
    .label { color: var(--color-text-secondary); font-weight: 500; }
    .text-del { color: #f56c6c; text-decoration: line-through; }
    .text-add { color: #67c23a; font-weight: 500; }
    .text-muted { color: var(--color-text-secondary); font-size: 12px; }
  }
}

.polish-versions {
  .polish-version-item {
    margin-bottom: 16px;
    padding: 12px;
    background: #f9fafb;
    border: 1px solid #e5e7eb;
    border-radius: 8px;

    .version-label {
      margin-bottom: 8px;
    }

    .version-content {
      font-size: 13px;
      line-height: 1.8;
      color: var(--color-text);
      white-space: pre-wrap;
    }
  }
}

@media (max-width: 768px) {
  .card-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
  .header-filters {
    width: 100%;
  }
  .preview-text {
    max-width: 200px;
  }
}

.rerun-bar {
  margin: 10px 0 4px;
  display: flex;
  gap: 8px;
}
</style>
