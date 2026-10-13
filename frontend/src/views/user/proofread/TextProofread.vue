<template>
  <div class="text-proofread-page">
    <CollaborationProgress
      v-if="collaboration || collaborationTaskId"
      :report="collaboration"
      :task-id="collaborationTaskId"
      :task-status="collaborationStatus"
      :message="collaborationMessage"
      :error="collaborationError"
      :monitoring="collaborationMonitoring"
      :cancelling="collaborationCancelling"
      :cancel-requested="collaborationCancelRequested"
      :view-only="!showResult"
      @reconnect="reconnectCollaboration"
      @cancel="cancelCollaboration"
      @rerun="rerunCollaboration"
    />
    <ProofreadEntry v-if="!showResult" />
    <!-- 输入区域 -->
    <div
      v-if="!showResult"
      class="input-section"
    >
      <el-card class="input-card">
        <template #header>
          <div class="card-header">
            <div>
              <span class="card-title">待审校文本</span><p class="editor-caption">
                专注内容，修改由你决定。
              </p>
            </div>
            <el-button
              v-if="!inputText"
              text
              :disabled="controlsLocked"
              @click="useExample"
            >
              试用示例文本
            </el-button>
            <el-button
              v-else
              text
              :disabled="controlsLocked"
              @click="clearInput"
            >
              清空文本
            </el-button>
          </div>
        </template>

        <!-- 文本输入 -->
        <div class="editor-wrapper">
          <el-input
            v-model="inputText"
            :disabled="controlsLocked"
            type="textarea"
            :rows="17"
            placeholder="在这里粘贴或写下你的内容…

一封邮件、一篇文章，或一段需要仔细检查的文字。"
            aria-label="待审校文本"
            resize="vertical"
            :maxlength="collaborationMode ? undefined : 100000"
            :show-word-limit="false"
            @keydown="handleEditorKeydown"
          />
        </div>

        <!-- 操作按钮 -->
        <div class="action-bar">
          <el-button
            type="primary"
            size="large"
            :loading="loading || collaborationBusy"
            :disabled="!inputText.trim() || (compareMode && !canCompare) || (collaborationMode && (!!collaborationBlocked || !!collaborationTaskId))"
            @click="handleProofread"
          >
            <el-icon><Edit /></el-icon>
            {{ loading ? '审校中...' : collaborationMode ? (collaborationPending ? '重试提交（同一请求）' : '开始分工审校') : (compareMode ? '开始模型对比' : '开始审校') }}
          </el-button>

          <el-button
            v-if="collaborationTerminal && !showResult"
            @click="clearCollaborationTask"
          >
            返回编辑
          </el-button>
          <span class="text-count">{{ charCount.toLocaleString() }}{{ collaborationMode ? ' / 8,000' : ' / 100,000' }} 字<small>{{ inputText.trim() ? '⌘ / Ctrl + Enter 开始' : '输入文本后即可开始' }}</small></span>
        </div>
      </el-card>
      <!-- 校对设置 -->
      <aside
        class="proofread-settings"
        aria-label="审校设置"
      >
        <ProofreadOptions
          v-model:mode="proofreadMode"
          v-model:depth="depth"
          v-model:domain="domain"
          v-model:model-id="selectedModelId"
          v-model:compare-ids="compareModelIds"
          :models="modelOptions"
          :disabled="controlsLocked"
        />
        <p
          v-if="compareMode && unavailableCompareIds.length"
          class="collaboration-warning"
          role="alert"
        >
          所选模型已不可用或模型列表未能加载，请在更多检查方式中重新选择；不会静默减少参与模型。
        </p>
        <p
          v-if="collaborationMode && collaborationBlocked"
          class="collaboration-warning"
          role="alert"
        >
          {{ collaborationBlocked }}
        </p>
        <p
          v-if="collaborationPending"
          class="collaboration-warning"
          role="alert"
        >
          提交结果尚未确认。请保持此页，使用原参数重试同一请求，避免重复任务；取得任务编号后可通过地址刷新恢复。
        </p>
        <p
          v-if="collaborationError && !collaborationTaskId"
          class="collaboration-warning"
          role="alert"
        >
          {{ collaborationError }}
        </p>
      </aside>
    </div>

    <!-- 结果区域 -->
    <div
      v-else
      class="result-section"
    >
      <div class="review-heading">
        <div><span class="review-eyebrow">审校结果</span><h2>逐条确认，让内容更准确</h2><p>共 {{ compareResult ? summaryIssues.length : issues.length }} 项建议 · 已接受 {{ compareResult ? compareAcceptedCount : acceptedCount }} 项 · 已忽略 {{ compareResult ? compareIgnoredCount : ignoredCount }} 项</p></div>
      </div>
      <details class="review-tools">
        <summary>草稿与版本管理{{ hasUnsavedChanges ? ' · 有未保存的修改' : '' }}</summary>
        <ReviewWorkspace
          :record-id="recordId"
          :source-text="sourceText"
          :issues="issues"
          :coverage="coverage"
          :compare="compareSnapshot"
          :collaboration="collaboration"
          :domain="domain"
          :depth="depth"
          :config-id="compareResult ? null : selectedModelId"
          :saved-review="savedReview"
          @saved="markSaved"
          @restore="restoreVersion"
        />
      </details>
      <QualityFeedbackDialog
        ref="qualityFeedback"
        :record-id="recordId"
        :source-text="sourceText"
      />
      <details class="review-tools">
        <summary>检查覆盖范围与事实核查 · {{ compareResult ? compareCoverageLabel(compareResult) : collaboration ? collaborationCoverageLabel(collaboration) : coverage?.status === 'complete' ? '全文检查已完成' : coverage?.status === 'partial' ? '仍有未检查的内容' : '覆盖范围未确认' }}</summary>
        <FactCheckPanel
          :record-id="recordId"
          :source-text="sourceText"
          @started="router.replace({ query: { ...route.query, review: String($event) } })"
        />
        <ProofreadCoveragePanel
          v-if="!compareResult && !collaboration"
          v-model:coverage="coverage"
          :source-text="sourceText"
          :domain="domain"
          :depth="depth"
          :config-id="selectedModelId"
          @issues="mergeIssues"
        />
      </details>
      <!-- ===== 多模型对比视图 ===== -->
      <template v-if="compareResult">
        <div class="result-toolbar">
          <el-button @click="goBack">
            <el-icon><Back /></el-icon>返回编辑
          </el-button>
          <div class="toolbar-info">
            <el-tag type="info">
              合并后 {{ summaryIssues.length }} 项建议
            </el-tag>
            <el-tag type="info">
              领域：{{ domainLabel }}
            </el-tag>
            <el-tag type="warning">
              已接受 {{ compareAcceptedCount }} 条
            </el-tag>
          </div>
          <div class="toolbar-actions">
            <el-button
              type="warning"
              :disabled="comparePendingCount === 0"
              @click="handleAcceptAll"
            >
              一键接受全部
            </el-button>
            <el-button @click="handleCopy">
              复制结果
            </el-button>
            <el-dropdown @command="handleExport">
              <el-button type="primary">
                导出<el-icon class="el-icon--right">
                  <ArrowDown />
                </el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="text">
                    修改后全文（TXT）
                  </el-dropdown-item>
                  <el-dropdown-item command="report">
                    问题报告（TXT，含原文对照）
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </div>

        <!-- 全文预览按需展开，首屏优先处理统一建议清单。 -->
        <details class="review-tools">
          <summary>修改后全文（实时预览） · {{ currentText.length }} 字</summary>
          <div class="compare-text-preview">
            {{ currentText }}
          </div>
        </details>

        <!-- 各模型结果标签页 -->
        <el-card class="compare-card">
          <el-tabs v-model="activeCompareTab">
            <!-- 统一清单优先；各模型的独立说明保留在详情中。 -->
            <el-tab-pane name="__summary__">
              <template #label>
                <span class="summary-tab-label">综合建议 · {{ summaryIssues.length }}</span>
              </template>

              <el-alert
                v-if="compareIncomplete"
                type="warning"
                :closable="false"
                :title="compareIncompleteMessage"
              />
              <p class="summary-help">
                共同发现：所有参与模型均成功返回，并在同一位置给出相同建议；不代表建议已经证实正确。
              </p>
              <div
                class="summary-filters"
                role="group"
                aria-label="按模型发现范围筛选"
              >
                <button
                  v-for="option in summaryFilters"
                  :key="option.value"
                  type="button"
                  :aria-pressed="summaryFilter === option.value"
                  @click="summaryFilter = option.value"
                >
                  {{ option.label }}
                </button>
              </div>
              <p
                class="summary-count"
                role="status"
              >
                {{ filteredSummaryIssues.length }} 项建议
              </p>
              <div
                v-if="activeCompareTab === '__summary__'"
                class="compare-issues"
              >
                <IssueCard
                  v-for="item in visibleSummaryIssues"
                  :key="item.key"
                  :issue="item.issue"
                  :number="getCompareIndex(item.issue) + 1"
                  :active="activeIssueIndex === getCompareIndex(item.issue)"
                  :context="issueContext(item.issue)"
                  :provenance="compareSummaryProvenance(item)"
                  :feedback-disabled="recordId === null"
                  @activate="activeIssueIndex = getCompareIndex(item.issue)"
                  @deactivate="activeIssueIndex = -1"
                  @accept="acceptIssue(item.issue)"
                  @delete="deleteIssue(item.issue)"
                  @accept-matching="acceptMatching(item.issue)"
                  @ignore="ignoreIssue(item.issue)"
                  @undo="undoIssue(item.issue)"
                  @feedback="qualityFeedback?.open(item.issue)"
                />
                <div
                  v-if="filteredSummaryIssues.length > compareVisibleCount"
                  class="issues-more"
                >
                  <el-button
                    size="small"
                    @click="compareVisibleCount += ISSUES_PAGE_SIZE"
                  >
                    加载更多（剩余 {{ filteredSummaryIssues.length - compareVisibleCount }} 项）
                  </el-button>
                </div>
                <el-empty
                  v-if="filteredSummaryIssues.length === 0"
                  :description="summaryIssues.length ? '此筛选下暂无建议，可查看全部建议及模型详情' : '当前结果暂无问题，请同时确认各模型是否完整审校'"
                  :image-size="60"
                />
              </div>
            </el-tab-pane>

            <!-- ===== 各模型明细：次级标签保留独立报告与补查 ===== -->
            <el-tab-pane
              name="__models__"
              label="各模型详情"
            >
              <el-tabs v-model="activeCompareModel">
                <el-tab-pane
                  v-for="r in compareModels"
                  :key="r.config_id"
                  :name="String(r.config_id)"
                >
                  <template #label>
                    <span>{{ r.config_name }}</span>
                    <el-tag
                      :type="r.success ? (comparePendingCountOf(r) > 0 ? 'danger' : 'success') : 'info'"
                      size="small"
                      style="margin-left: 6px;"
                    >
                      {{ r.success ? comparePendingCountOf(r) : '失败' }}
                    </el-tag>
                  </template>

                  <div
                    v-if="!r.success"
                    class="compare-error"
                  >
                    <el-alert
                      type="error"
                      :closable="false"
                      show-icon
                      :title="`校对失败：${r.error || '未知错误'}`"
                    />
                  </div>
                  <template v-else>
                    <ProofreadCoveragePanel
                      :coverage="r.coverage"
                      :source-text="sourceText"
                      :domain="r.domain || domain"
                      :depth="r.depth || 'standard'"
                      :config-id="r.config_id"
                      @update:coverage="updateCompareCoverage(r.config_id, $event)"
                      @issues="mergeCompareRetry(r.config_id, $event)"
                    />
                    <div class="compare-meta">
                      <el-tag
                        type="info"
                        effect="plain"
                        size="small"
                      >
                        模型：{{ r.model }}
                      </el-tag>
                      <el-tag
                        type="info"
                        effect="plain"
                        size="small"
                      >
                        耗时 {{ (r.elapsed_ms / 1000).toFixed(1) }}s
                      </el-tag>
                    </div>
                    <div
                      v-if="activeCompareTab === '__models__' && activeCompareModel === String(r.config_id)"
                      class="compare-issues"
                    >
                      <IssueCard
                        v-for="issue in r.issues.slice(0, compareVisibleCount)"
                        :key="issueKey(issue)"
                        :issue="issue"
                        :number="getCompareIndex(issue) + 1"
                        :active="activeIssueIndex === getCompareIndex(issue)"
                        :context="issueContext(issue)"
                        :provenance="compareIssueProvenance(issue)"
                        :feedback-disabled="recordId === null"
                        @activate="activeIssueIndex = getCompareIndex(issue)"
                        @deactivate="activeIssueIndex = -1"
                        @accept="acceptIssue(issue)"
                        @delete="deleteIssue(issue)"
                        @accept-matching="acceptMatching(issue)"
                        @ignore="ignoreIssue(issue)"
                        @undo="undoIssue(issue)"
                        @feedback="qualityFeedback?.open(issue)"
                      />
                      <div
                        v-if="r.issues.length > compareVisibleCount"
                        class="issues-more"
                      >
                        <el-button
                          size="small"
                          @click="compareVisibleCount += ISSUES_PAGE_SIZE"
                        >
                          加载更多（剩余 {{ r.issues.length - compareVisibleCount }} 项）
                        </el-button>
                      </div>
                      <el-empty
                        v-if="r.issues.length === 0"
                        :description="r.coverage?.status === 'partial' ? '已完成范围暂无问题，仍有未审段落' : '该模型未发现问题'"
                        :image-size="60"
                      />
                    </div>
                  </template>
                </el-tab-pane>
              </el-tabs>
            </el-tab-pane>
          </el-tabs>
        </el-card>
      </template>

      <!-- ===== 普通单模型视图 ===== -->
      <template v-else>
        <!-- 顶部操作栏 -->
        <div class="result-toolbar">
          <el-button @click="goBack">
            <el-icon><Back /></el-icon>返回编辑
          </el-button>
          <div class="toolbar-info">
            <el-tag type="success">
              共发现 {{ issues.length }} 个问题
            </el-tag>
            <el-tag type="info">
              领域：{{ domainLabel }}
            </el-tag>
          </div>
          <div class="toolbar-actions">
            <el-button
              type="warning"
              :disabled="issues.length === 0"
              @click="handleAcceptAll"
            >
              一键修改全部
            </el-button>
            <el-button @click="handleCopy">
              复制结果
            </el-button>
            <el-dropdown @command="handleExport">
              <el-button type="primary">
                导出<el-icon class="el-icon--right">
                  <ArrowDown />
                </el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="text">
                    修改后全文（TXT）
                  </el-dropdown-item>
                  <el-dropdown-item command="report">
                    问题报告（TXT，含原文对照）
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </div>

        <!-- 双栏对照 -->
        <div class="result-columns">
          <!-- 左栏：原文展示 -->
          <el-card class="column-card original-column">
            <template #header>
              <div class="column-header">
                <span class="column-title">
                  <el-icon><Tickets /></el-icon>
                  文本与修订预览
                </span>
                <span class="text-count">{{ currentText.length }} 字</span>
              </div>
            </template>
            <ReviewPreview
              :source-text="sourceText"
              :current-text="currentText"
              :issues="issues"
              :patches="patches"
              :active-index="activeIssueIndex"
            />
          </el-card>

          <!-- 右栏：问题列表 -->
          <el-card class="column-card issues-column">
            <template #header>
              <div class="issues-header">
                <span class="issues-title">
                  <el-icon><Document /></el-icon>
                  修改建议
                  <el-tag
                    type="info"
                    effect="plain"
                    size="small"
                    round
                  >{{ filteredIssues.length }}</el-tag>
                </span>
                <el-select
                  v-model="filterType"
                  placeholder="全部类型"
                  clearable
                  size="default"
                  class="filter-select"
                >
                  <template #prefix>
                    <el-icon><Filter /></el-icon>
                  </template>
                  <el-option
                    label="全部类型"
                    value=""
                  >
                    <el-icon style="vertical-align:middle;margin-right:6px;">
                      <Menu />
                    </el-icon>全部类型
                  </el-option>
                  <el-option
                    label="错别字"
                    value="typo"
                  >
                    <el-icon style="vertical-align:middle;margin-right:6px;color:#f56c6c;">
                      <EditPen />
                    </el-icon>错别字
                  </el-option>
                  <el-option
                    label="语法错误"
                    value="grammar"
                  >
                    <el-icon style="vertical-align:middle;margin-right:6px;color:#e6a23c;">
                      <Reading />
                    </el-icon>语法错误
                  </el-option>
                  <el-option
                    label="标点符号"
                    value="punctuation"
                  >
                    <el-icon style="vertical-align:middle;margin-right:6px;color:#909399;">
                      <Operation />
                    </el-icon>标点符号
                  </el-option>
                  <el-option
                    label="表达优化"
                    value="style"
                  >
                    <el-icon style="vertical-align:middle;margin-right:6px;color:#409eff;">
                      <MagicStick />
                    </el-icon>表达优化
                  </el-option>
                  <el-option
                    label="敏感词"
                    value="sensitive"
                  >
                    <el-icon style="vertical-align:middle;margin-right:6px;color:#f56c6c;">
                      <Warning />
                    </el-icon>敏感词
                  </el-option>
                  <el-option
                    label="逻辑问题"
                    value="logic"
                  >
                    <el-icon style="vertical-align:middle;margin-right:6px;color:#67c23a;">
                      <Connection />
                    </el-icon>逻辑问题
                  </el-option>
                </el-select>
              </div>
            </template>
            <div class="issues-list">
              <IssueCard
                v-for="issue in visibleIssues"
                :key="issueKey(issue)"
                :issue="issue"
                :number="getGlobalIndex(issue) + 1"
                :active="activeIssueIndex === getGlobalIndex(issue)"
                :context="issueContext(issue)"
                :provenance="collaboration ? issueProvenance(issue) : ''"
                :feedback-disabled="recordId === null"
                @activate="activeIssueIndex = getGlobalIndex(issue)"
                @deactivate="activeIssueIndex = -1"
                @accept="acceptIssue(issue)"
                @delete="deleteIssue(issue)"
                @accept-matching="acceptMatching(issue)"
                @ignore="ignoreIssue(issue)"
                @undo="undoIssue(issue)"
                @feedback="qualityFeedback?.open(issue)"
              />
              <div
                v-if="filteredIssues.length > visibleCount"
                class="issues-more"
              >
                <el-button
                  size="small"
                  @click="visibleCount += ISSUES_PAGE_SIZE"
                >
                  加载更多（剩余 {{ filteredIssues.length - visibleCount }} 项）
                </el-button>
              </div>
              <el-empty
                v-if="filteredIssues.length === 0"
                :description="collaboration?.status === 'partial' ? '已发现范围暂无问题，分工流程尚未完整完成' : coverage?.status === 'partial' ? '已完成范围暂无问题，仍有未审段落' : (filterType ? '此类型暂无问题' : '没有发现问题，仍需人工复核')"
              />
            </div>
          </el-card>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import ProofreadOptions from '@/components/ProofreadOptions.vue'
import ProofreadEntry from '@/components/ProofreadEntry.vue'
import IssueCard from '@/components/IssueCard.vue'
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { textProofreadApi, proofreadCompareApi, type ProofreadCompareResponse, type ProofreadCoverage, type ProofreadIssue } from '@/api/proofread'
import { getAvailableModelsCached, type AvailableModel } from '@/api/polish'
import { getReviewApi, type ReviewResponse, type ReviewRestorePayload } from '@/api/review'
import { severityLabel, typeLabel, downloadTextFile } from '@/utils/proofread'
import { useProofreadReview, type ReviewIssue } from '@/composables/useProofreadReview'
import { expandReviewIssues, reviewIssueKey } from '@/utils/review'
import { serializeReviewDraft } from '@/utils/reviewVersions'
import { compareCoverageLabel, modelReviewIssues, restoreCompareReview, serializeCompareReview } from '@/utils/compareReview'
import { buildCompareSummary, compareSummaryKey, compareSummaryProvenance } from '@/utils/compareSummary'
import ReviewPreview from '@/components/ReviewPreview.vue'
import ReviewWorkspace from '@/components/ReviewWorkspace.vue'
import ProofreadCoveragePanel from '@/components/ProofreadCoverage.vue'
import QualityFeedbackDialog from '@/components/QualityFeedbackDialog.vue'
import FactCheckPanel from '@/components/FactCheckPanel.vue'
import CollaborationProgress from '@/components/CollaborationProgress.vue'
import { useUserStore } from '@/stores/user'
import { useCollaboration } from '@/composables/useCollaboration'
import { MAX_COLLABORATION_CHARS } from '@/api/collaboration'
import { collaborationCoverageLabel, collaborationFindingMap, collaborationProvenance, findCollaborationFinding } from '@/utils/collaboration'

const qualityFeedback = ref<InstanceType<typeof QualityFeedbackDialog> | null>(null)

// 状态
const inputText = ref('')
const loading = ref(false)
const showResult = ref(false)

// 审阅共享逻辑：问题列表 / 修订文本 / 接受·忽略·删除·撤销 / 反馈上报
const {
  issues,
  currentText,
  filterType,
  activeIssueIndex,
  recordId,
  filteredIssues,
  acceptedCount,
  getGlobalIndex,
  sourceText,
  patches,
  initialize,
  restore,
  mergeIssues,
  acceptMatching,
  acceptIssue,
  ignoreIssue,
  deleteIssue,
  undoIssue,
  handleAcceptAll,
} = useProofreadReview()

const charCount = computed(() => Array.from(inputText.value).length)
const ignoredCount = computed(() => issues.value.filter(issue => issue._ignored).length)
// sourceText 的字符数组缓存：issueContext 每条问题都要按位取上下文，避免每次渲染全量 Array.from
const sourceChars = computed(() => Array.from(sourceText.value))

// 问题列表渐进渲染：首批 100 条，「加载更多」递增——数百问题的长文不再一次性渲染全量卡片；
// 组件化后 hover/接受只重渲染单张卡片，不再触发整页 diff
const ISSUES_PAGE_SIZE = 100
const visibleCount = ref(ISSUES_PAGE_SIZE)
const visibleIssues = computed(() => filteredIssues.value.slice(0, visibleCount.value))
watch([filterType, issues], () => { visibleCount.value = ISSUES_PAGE_SIZE })

const route = useRoute()
const router = useRouter()
const coverage = ref<ProofreadCoverage | null>(null)
const savedReview = ref<ReviewResponse | null>(null)
const savedFingerprint = ref('')
const fingerprint = computed(() => serializeReviewDraft({
  sourceText: sourceText.value, issues: issues.value, coverage: coverage.value,
  compare: compareSnapshot.value, domain: domain.value, depth: depth.value,
  configId: compareResult.value ? null : selectedModelId.value,
}))
const hasUnsavedChanges = computed(() => showResult.value && savedFingerprint.value !== fingerprint.value)

async function syncReviewQuery(id: number | null): Promise<boolean> {
  if (route.query.review === (id === null ? undefined : String(id)) && route.query.collaboration_task === undefined) return true
  const query = { ...route.query }
  delete query.review
  delete query.collaboration_task
  if (id !== null) query.review = String(id)
  try {
    // 仅绑定地址；不重读草稿或重建 issues，保留请求期间的本地决策。
    const failure = await router.replace({ query })
    if (failure) throw failure
    return true
  } catch {
    ElMessage.warning('地址更新失败，有记录的结果可从校对历史继续审阅')
    return false
  }
}

function markSaved(review: ReviewResponse) {
  if (review.record_id !== recordId.value || review.original_text !== sourceText.value) return
  if (savedReview.value && review.revision < savedReview.value.revision) return
  savedReview.value = review
  if (review.collaboration) collaboration.value = review.collaboration
  savedFingerprint.value = serializeReviewDraft({
    sourceText: review.original_text, issues: review.issues, coverage: review.coverage,
    compare: review.compare, domain: review.domain, depth: review.depth, configId: review.config_id,
  })
  void syncReviewQuery(review.record_id)
}

function restoreVersion(snapshot: ReviewRestorePayload) {
  restore(sourceText.value, snapshot.issues)
  coverage.value = snapshot.coverage || null
  compareResult.value = snapshot.compare ? restoreCompareReview(sourceText.value, snapshot.compare) : null
  compareMode.value = !!compareResult.value
  compareModelIds.value = compareResult.value?.results.map(result => result.config_id) || []
  activeCompareTab.value = '__summary__'
}

async function confirmLeave() {
  if (collaborationPending.value) {
    try {
      await ElMessageBox.confirm('分工审校提交结果尚未确认，离开将丢失本次请求编号，重新提交可能重复计费。建议留在此页重试同一请求。', '提交尚未确认', { type: 'warning', confirmButtonText: '仍然离开', cancelButtonText: '留在此页' })
    } catch { return false }
  }
  if (!hasUnsavedChanges.value && (showResult.value || !inputText.value.trim())) return true
  try {
    await ElMessageBox.confirm(showResult.value ? '尚有未保存的审阅修改，离开后将丢失。请先保存草稿，或确认离开。' : '输入的文本尚未提交，离开后将丢失。确定离开吗？', '未保存的审阅', { confirmButtonText: '离开', cancelButtonText: '继续审阅', type: 'warning' })
    return true
  } catch { return false }
}
onBeforeRouteLeave(confirmLeave)
function handleBeforeUnload(event: { preventDefault(): void; returnValue: string }) {
  if (!hasUnsavedChanges.value && !collaborationPending.value && (showResult.value || !inputText.value.trim())) return
  event.preventDefault()
  event.returnValue = ''
}
onMounted(() => window.addEventListener('beforeunload', handleBeforeUnload))
onBeforeUnmount(() => window.removeEventListener('beforeunload', handleBeforeUnload))

function issueContext(issue: ReviewIssue) {
  if (issue.start == null || issue.start < 0) return '无法精确定位，请人工核对'
  const chars = sourceChars.value
  return `第 ${issue.start + 1} 字：${chars.slice(Math.max(0, issue.start - 12), Math.min(chars.length, (issue.end ?? issue.start) + 12)).join('')}`
}

const issueKey = reviewIssueKey

// 初始化：历史审阅或协作任务刷新恢复；不把协作原文写入浏览器存储。
let pageAlive = true
const restoreController = new AbortController()
onBeforeUnmount(() => { pageAlive = false; restoreController.abort() })
onMounted(async () => {
  const reviewId = Number(route.query.review)
  const queuedTask = typeof route.query.collaboration_task === 'string' ? route.query.collaboration_task : ''
  if (queuedTask) {
    proofreadMode.value = 'collaboration'
    await collaborationFlow.resume(queuedTask)
  } else if (Number.isInteger(reviewId) && reviewId > 0) {
    loading.value = true
    try {
      const review = await getReviewApi(reviewId, { signal: restoreController.signal })
      if (!pageAlive) return
      inputText.value = review.original_text
      sourceText.value = review.original_text
      collaboration.value = review.collaboration ?? null
      if (collaboration.value) proofreadMode.value = 'collaboration'
      restoreVersion({ ...review, coverage: review.coverage || null })
      recordId.value = review.record_id
      savedReview.value = review
      domain.value = review.domain
      depth.value = review.depth || 'standard'
      selectedModelId.value = review.config_id ?? null
      showResult.value = true
      savedFingerprint.value = fingerprint.value
    } catch {
      if (pageAlive) ElMessage.error('无法恢复这份审阅记录，请从校对历史重新打开')
    } finally { if (pageAlive) loading.value = false }
  } else {
    const rerunText = sessionStorage.getItem('tm_rerun_text')
    if (rerunText) {
      inputText.value = rerunText
      sessionStorage.removeItem('tm_rerun_text')
    }
  }
  if (!pageAlive) return
  try {
    const res = await getAvailableModelsCached()
    if (!pageAlive) return
    modelOptions.value = res.models
    const active = res.models.find(m => m.is_active)
    if (selectedModelId.value === null && !queuedTask && !collaborationPending.value && !collaborationTaskId.value && !showResult.value) selectedModelId.value = active ? active.id : (res.models[0]?.id ?? null)
  } catch { /* 模型列表加载失败时用默认活跃模型 */ }
})

// 设置
const domain = ref('general')
const depth = ref('standard')

function useExample() {
  inputText.value = '关于举办读书分享活动的通知\n\n为了丰富大家的业余生活，提高阅读兴去，我们计划于本周五下午举办读书分享活动。欢迎各位同事踊跃参加，分享自己最喜爱的一本书。\n\n请参加活动的同事提前做好准备，并准时按时到达会议室。让我们一起在阅读中收获知识，在交流中共同进步。'
}
async function clearInput() {
  if (inputText.value) {
    try { await ElMessageBox.confirm('清空后无法恢复尚未提交的文本。', '清空文本', { confirmButtonText: '清空', cancelButtonText: '保留' }) } catch { return }
  }
  inputText.value = ''
}
function handleEditorKeydown(event: { preventDefault(): void; metaKey?: boolean; ctrlKey?: boolean; key?: string; isComposing?: boolean }) {
  if ((event.metaKey || event.ctrlKey) && event.key === 'Enter' && !event.isComposing) {
    event.preventDefault()
    if (!controlsLocked.value) void handleProofread()
  }
}

// 校对模型选择（默认当前活跃模型）
const modelOptions = ref<AvailableModel[]>([])
const selectedModelId = ref<number | null>(null)

// 显式选择，保留现有多模型对比视图。
const proofreadMode = ref<'single' | 'compare' | 'collaboration'>('single')
const compareMode = computed({
  get: () => proofreadMode.value === 'compare',
  set: value => { if (value) proofreadMode.value = 'compare'; else if (proofreadMode.value === 'compare') proofreadMode.value = 'single' },
})
const collaborationMode = computed(() => proofreadMode.value === 'collaboration')
const compareModelIds = ref<number[]>([])
const compareResult = ref<ProofreadCompareResponse | null>(null)
const activeCompareTab = ref('__summary__')
const activeCompareModel = ref('')
const unavailableCompareIds = computed(() => compareModelIds.value.filter(id => !modelOptions.value.some(model => model.id === id)))
const canCompare = computed(() => compareModelIds.value.length >= 2 && compareModelIds.value.length <= 4
  && new Set(compareModelIds.value).size === compareModelIds.value.length && unavailableCompareIds.value.length === 0)
const compareSnapshot = computed(() => serializeCompareReview(compareResult.value))
const compareModels = computed(() => compareResult.value?.results.map(result => ({
  ...result, issues: modelReviewIssues(result.issues, issues.value),
})) || [])
// 模型详情保留独立说明，因此不是 canonical 对象；预建索引避免每张卡片全表查找。
const compareIssueIndices = computed(() => new Map(issues.value.map((issue, index) => [issueKey(issue), index])))
function getCompareIndex(issue: ReviewIssue): number {
  return compareIssueIndices.value.get(issueKey(issue)) ?? -1
}
const compareIncomplete = computed(() => compareResult.value
  ? compareResult.value.results.some(result => !result.success || result.coverage?.status !== 'complete')
  : false)
const compareIncompleteMessage = computed(() => {
  if (!compareResult.value) return ''
  if (compareResult.value.results.some(result => !result.success)) {
    return '部分模型审校失败，当前建议仅来自成功返回的模型，不能视为所有模型一致。请查看各模型详情。'
  }
  return `${compareCoverageLabel(compareResult.value)}；共同发现只针对已报告的位置，不代表全文均已检查。可在各模型详情中查看覆盖范围与补查。`
})
watch(compareResult, result => { activeCompareModel.value = String(result?.results[0]?.config_id ?? '') })

const user = useUserStore()
const collaborationBlocked = computed(() => {
  if (!user.isLoggedIn) return '请先登录后使用分工审校。'
  if (!user.hasPermission('proofread:text')) return '当前账号没有文本审校权限（proofread:text）。'
  if (Array.from(inputText.value).length > MAX_COLLABORATION_CHARS) return '分工审校最多支持 8000 字，请缩短文本或改用日常审校；不会截断提交。'
  if (typeof globalThis.crypto?.getRandomValues !== 'function') return '浏览器不支持安全随机数，请更换浏览器。'
  return ''
})
const collaborationFlow = useCollaboration({
  canStart: () => !collaborationBlocked.value,
  onQueued: id => router.replace({ query: { collaboration_task: id } }),
  onSnapshot: snapshot => {
    inputText.value = snapshot.text
    domain.value = snapshot.domain
    selectedModelId.value = snapshot.config_id
    proofreadMode.value = 'collaboration'
  },
  onSuccess: async (result, snapshot) => {
    initialize(snapshot.text, result.issues)
    recordId.value = result.record_id ?? null
    coverage.value = result.coverage ?? null
    domain.value = result.domain
    depth.value = result.depth || 'standard'
    selectedModelId.value = result.config_id ?? snapshot.config_id
    compareResult.value = null
    savedReview.value = null
    showResult.value = true
    savedFingerprint.value = fingerprint.value
    await router.replace({ query: { review: String(result.record_id) } })
  },
})
const {
  taskId: collaborationTaskId, report: collaboration, status: collaborationStatus,
  message: collaborationMessage, error: collaborationError, busy: collaborationBusy,
  pending: collaborationPending, monitoring: collaborationMonitoring, cancelling: collaborationCancelling,
  cancelRequested: collaborationCancelRequested, terminal: collaborationTerminal,
  reconnect: reconnectCollaboration, cancel: cancelCollaboration,
} = collaborationFlow
const controlsLocked = computed(() => loading.value || collaborationFlow.locked.value || (!!collaborationTaskId.value && !showResult.value))
const collaborationFindings = computed(() => collaborationFindingMap(collaboration.value))
function issueProvenance(issue: ReviewIssue) {
  const finding = findCollaborationFinding(issue, collaborationFindings.value)
  return finding && collaboration.value ? collaborationProvenance(finding, collaboration.value) : '来源：未匹配原始发现 · 未复核'
}
async function clearCollaborationTask() {
  collaborationFlow.reset()
  await router.replace({ query: {} })
}
async function rerunCollaboration() {
  if (collaborationTaskId.value && !collaborationTerminal.value) return
  if (!await confirmLeave()) return
  try {
    await ElMessageBox.confirm('将以原始文本重新运行完整分工审校，另扣一次应用额度并按实际 Token 产生供应商费用；不会自动采纳任何建议。', '重新运行分工审校（另计费）', { type: 'warning', confirmButtonText: '重新运行', cancelButtonText: '保留当前结果' })
  } catch { return }
  const original = showResult.value ? sourceText.value : collaborationFlow.snapshot.value?.text || inputText.value
  if (collaborationBlocked.value) { ElMessage.warning(collaborationBlocked.value); return }
  await clearCollaborationTask()
  inputText.value = original
  proofreadMode.value = 'collaboration'
  showResult.value = false
  compareResult.value = null
  savedReview.value = null
  recordId.value = null
  await handleProofread()
}

function updateCompareCoverage(configId: number, value: ProofreadCoverage | null) {
  const result = compareResult.value?.results.find(item => item.config_id === configId)
  if (result) result.coverage = value
}

function mergeCompareRetry(configId: number, additional: ProofreadIssue[]) {
  const result = compareResult.value?.results.find(r => r.config_id === configId)
  if (!result) return
  mergeIssues(additional)
  result.issues = expandReviewIssues(sourceText.value, [...result.issues, ...additional])
  result.total_issues = result.issues.length
}

/** 汇总仅为展示投影，原始模型报告与审阅决策仍按现有结构保存。 */
const summaryIssues = computed(() => buildCompareSummary(compareModels.value))
const summaryFilters = [
  { value: 'all', label: '全部建议' },
  { value: 'common', label: '共同发现' },
  { value: 'partial', label: '仅部分模型发现' },
] as const
const summaryFilter = ref<typeof summaryFilters[number]['value']>('all')
const filteredSummaryIssues = computed(() => summaryIssues.value.filter(item =>
  summaryFilter.value === 'all' || (summaryFilter.value === 'common' ? item.isConsensus : !item.isConsensus)
))
const compareVisibleCount = ref(ISSUES_PAGE_SIZE)
const visibleSummaryIssues = computed(() => filteredSummaryIssues.value.slice(0, compareVisibleCount.value))
watch([summaryFilter, activeCompareTab, activeCompareModel, issues], () => {
  compareVisibleCount.value = ISSUES_PAGE_SIZE
})
watch(compareResult, () => { summaryFilter.value = 'all' })
const summaryByKey = computed(() => new Map(summaryIssues.value.map(item => [item.key, item])))
function compareIssueProvenance(issue: ReviewIssue): string {
  const item = summaryByKey.value.get(compareSummaryKey(issue))
  return item ? compareSummaryProvenance(item) : '来源未确认，请查看原始模型报告'
}
const compareAcceptedCount = computed(() => summaryIssues.value.filter(item => item.issue._accepted).length)
const compareIgnoredCount = computed(() => summaryIssues.value.filter(item => item.issue._ignored).length)
const comparePendingCount = computed(() => issues.value.filter(i =>
  !i._accepted && !i._ignored && (i.suggestion || i.type === 'sensitive')
).length)
function comparePendingCountOf(r: { issues: ReviewIssue[] }): number {
  return r.issues.filter(i => !i._accepted && !i._ignored).length
}

// 领域标签
const domainLabel = computed(() => {
  const map: Record<string, string> = {
    general: '通用', official: '公文', legal: '法律',
  }
  return map[domain.value] || '通用'
})

// 开始校对
async function handleProofread() {
  if (loading.value || !inputText.value.trim()) return
  const submittedText = inputText.value
  if (collaborationMode.value) {
    if (collaborationBlocked.value) return ElMessage.warning(collaborationBlocked.value)
    await collaborationFlow.submit({ text: submittedText, domain: domain.value, config_id: selectedModelId.value ?? undefined })
    return
  }
  if (collaborationFlow.locked.value) return
  if (compareMode.value && !canCompare.value) return ElMessage.warning('请选择 2–4 个当前可用的不同模型')
  collaborationFlow.reset()
  loading.value = true
  showResult.value = false
  compareResult.value = null
  savedReview.value = null
  recordId.value = null
  // 新任务先解除旧 review；失败或匿名结果也不会刷新回另一份记录。
  if (!await syncReviewQuery(null)) { loading.value = false; return }
  if (!pageAlive) return
  // 多模型对比模式
  if (compareMode.value) {
    try {
      const response = await proofreadCompareApi({
        text: submittedText,
        domain: domain.value,
        config_ids: compareModelIds.value,
      })
      if (!pageAlive) return
      compareResult.value = restoreCompareReview(submittedText, response)
      initialize(submittedText, compareResult.value.results.flatMap(r => r.success ? r.issues : []))
      recordId.value = response.record_id ?? null
      const firstSuccess = compareResult.value.results.find(result => result.success)
      domain.value = firstSuccess?.domain || domain.value
      depth.value = firstSuccess?.depth || 'standard'
      coverage.value = null
      savedReview.value = null
      activeCompareTab.value = '__summary__'
      showResult.value = true
      savedFingerprint.value = fingerprint.value
      await syncReviewQuery(recordId.value)
      const okCount = compareResult.value.results.filter(r => r.success && r.coverage?.status === 'complete').length
      ElMessage.info(`${okCount}/${compareResult.value.results.length} 个模型已完整校对，请查看各模型状态`)
    } catch {
      // 错误已在拦截器中处理
    } finally {
      loading.value = false
    }
    return
  }
  loading.value = true
  try {
    const res = await textProofreadApi({
      text: submittedText,
      domain: domain.value,
      depth: depth.value,
      config_id: selectedModelId.value ?? undefined,
    })
    if (!pageAlive) return
    initialize(submittedText, res.issues)
    recordId.value = res.record_id ?? null
    coverage.value = res.coverage || null
    domain.value = res.domain
    depth.value = res.depth || 'standard'
    selectedModelId.value = res.config_id ?? selectedModelId.value
    savedReview.value = null
    showResult.value = true
    savedFingerprint.value = fingerprint.value
    await syncReviewQuery(recordId.value)
    if (coverage.value?.status === 'partial') {
      ElMessage.warning('部分段落尚未审完，请补查失败段')
    } else if (issues.value.length === 0) {
      ElMessage.success('文本没有发现问题，仍建议人工复核')
    } else {
      ElMessage.info(`共发现 ${issues.value.length} 处问题`)
    }
  } catch {
    // 错误已在拦截器中处理
  } finally {
    loading.value = false
  }
}

// 复制结果
async function handleCopy() {
  try {
    await navigator.clipboard.writeText(currentText.value)
    ElMessage.success('已复制到剪贴板')
  } catch {
    const textarea = document.createElement('textarea')
    textarea.value = currentText.value
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.select()
    try {
      document.execCommand('copy')
      ElMessage.success('已复制到剪贴板')
    } catch {
      ElMessage.error('复制失败，请手动选择复制')
    }
    document.body.removeChild(textarea)
  }
}

// 导出：text=修改后全文；report=问题报告
async function handleExport(kind: string) {
  const incomplete = collaboration.value
    ? collaboration.value.status !== 'complete'
    : compareResult.value ? compareIncomplete.value : coverage.value?.status === 'partial'
  if (incomplete && kind === 'text') {
    try {
      await ElMessageBox.confirm('仍有未完成的审校范围，导出的正文仅包含当前已采纳修改，不能视为全文审校完成。', '审校尚未完成', { confirmButtonText: '仍然导出', cancelButtonText: '继续审阅', type: 'warning' })
    } catch { return }
  }
  const dateStr = new Date().toLocaleDateString()
  let content: string
  let filename: string

  if (kind === 'report') {
    const accepted = issues.value.filter(i => i._accepted).length
    const ignored = issues.value.filter(i => i._ignored).length
    const lines: string[] = [
      'TextMirror 校对问题报告',
      `导出时间：${new Date().toLocaleString('zh-CN')}`,
      `领域：${domainLabel.value}`,
      `审校范围：${collaboration.value ? collaborationCoverageLabel(collaboration.value) : compareResult.value ? compareCoverageLabel(compareResult.value) : coverage.value?.status === 'partial' ? '未完成全文，请补查失败段' : coverage.value ? '已完成' : '未记录覆盖范围，无法确认全文完成'}`,
      `问题总数：${issues.value.length}（已采纳 ${accepted} / 已忽略 ${ignored} / 待处理 ${issues.value.length - accepted - ignored}）`,
      '',
      '='.repeat(50),
      '',
    ]
    issues.value.forEach((issue, idx) => {
      const status = issue._accepted ? '已采纳' : issue._ignored ? '已忽略' : '待处理'
      lines.push(`【${idx + 1}】${typeLabel(issue.type)}｜${severityLabel(issue.severity)}｜${status}`)
      lines.push(`原文：${issue.original}`)
      lines.push(`建议：${issue.suggestion || (issue.type === 'sensitive' ? '（删除该词）' : '（需人工核对）')}`)
      if (issue.explanation) lines.push(`说明：${issue.explanation}`)
      if (collaboration.value) lines.push(issueProvenance(issue))
      if (compareResult.value) lines.push(compareIssueProvenance(issue))
      lines.push('')
    })
    lines.push('='.repeat(50), '', '【修改后全文】', currentText.value)
    content = lines.join('\n')
    filename = `校对报告_${dateStr}.txt`
  } else {
    content = currentText.value
    filename = `校对结果_${dateStr}.txt`
  }

  downloadTextFile(content, filename)
  ElMessage.success('导出成功')
}

// 返回编辑
async function goBack() {
  if (!await confirmLeave()) return
  inputText.value = currentText.value
  collaborationFlow.reset()
  showResult.value = false
  compareResult.value = null
  savedReview.value = null
  recordId.value = null
  await router.replace({ query: {} })
}
</script>

<style scoped lang="scss">
.collaboration-warning { font-size: 13px; color: var(--el-color-warning-dark-2); line-height: 1.7; overflow-wrap: anywhere; }
.text-proofread-page {
  max-width: 1400px;
  margin: 0 auto;
}

.input-card {
  border-radius: 16px;

  .card-header {
    display: flex;
    align-items: center;
    gap: 12px;

    .card-title {
      color: var(--color-text);
      font-size: 17px;
      font-weight: 650;
    }
  }
}

.editor-wrapper {
  margin-bottom: 14px;

  :deep(.el-textarea__inner) {
    padding: 16px 18px;
    border-radius: 12px;
    background: var(--surface-soft);
    font-size: 14px;
    line-height: 1.8;
    font-family: var(--font-family);
    box-shadow: 0 0 0 1px #dfe6ef inset;

    &:focus {
      background: var(--surface);
      box-shadow: 0 0 0 1px #4e86db inset, 0 0 0 3px rgba(45, 115, 221, .08);
    }
  }
}

.action-bar {
  display: flex;
  align-items: center;
  gap: 12px;

  .text-count {
    margin-left: auto;
    color: var(--color-text-secondary);
    font-size: 13px;
  }

  :deep(.el-button--large) { min-width: 126px; border-radius: 10px; }
}

// 结果区域
.result-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
  padding: 12px 16px;
  background: var(--surface);
  border-radius: var(--border-radius-md);
  box-shadow: var(--shadow-sm);

  .toolbar-info {
    display: flex;
    gap: 8px;
  }

  .toolbar-actions {
    margin-left: auto;
    display: flex;
    gap: 8px;
  }
}

.result-columns {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  height: calc(100vh - 220px);

  .column-card {
    height: 100%;
    overflow: hidden;
    display: flex;
    flex-direction: column;

    :deep(.el-card__body) {
      flex: 1;
      overflow-y: auto;
      padding: 16px;
    }
  }
}

.column-header {
  display: flex;
  align-items: center;
  justify-content: space-between;

  .column-title {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-size: 15px;
    font-weight: 600;
    color: var(--color-text);
  }

  .text-count {
    font-size: 12px;
    color: var(--color-text-secondary);
  }
}

.original-text {
  font-size: 15px;
  line-height: 2;
  color: var(--color-text);
  white-space: pre-wrap;
  word-break: break-all;

  :deep(mark.hl-mark) {
    padding: 2px 3px;
    border-radius: 3px;
    cursor: pointer;
    transition: all 0.2s;
  }

  :deep(mark.hl-error) { background: #fee2e2; }
  :deep(mark.hl-warning) { background: #fef3c7; }
  :deep(mark.hl-info) { background: #dbeafe; }

  :deep(mark.is-hover) {
    background: #fde68a;
    box-shadow: 0 0 0 2px #f59e0b;
    font-weight: 600;
  }
}

.issues-header {
  display: flex;
  align-items: center;
  justify-content: space-between;

  .issues-title {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-size: 15px;
    font-weight: 600;
    color: var(--color-text);
  }
  .filter-select {
    width: 180px;
    :deep(.el-input__wrapper) {
      padding-left: 8px;
      border-radius: 8px;
    }
    :deep(.el-input__prefix) {
      color: var(--color-primary);
    }
  }
}

.issues-more { display: flex; justify-content: center; padding: 4px 0 8px; }

/* ===== 移动端响应式 ===== */
@media (max-width: 768px) {
  .result-columns {
    grid-template-columns: 1fr;
    height: auto;
  }

  .result-toolbar {
    flex-wrap: wrap;

    .toolbar-actions {
      margin-left: 0;
      width: 100%;
      flex-wrap: wrap;

      :deep(.el-button + .el-button) { margin-left: 0; }
    }
  }
}

/* ===== 多模型对比视图 ===== */
.compare-card {
  margin-top: 14px;
  min-width: 0;
  box-shadow: none;
  border-color: var(--color-border);

  :deep(.el-tabs__item) { max-width: 260px; overflow: hidden; }
}

.summary-tab-label { font-weight: 600; }
.summary-help, .summary-count {
  margin: 12px 0;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.8;
  overflow-wrap: anywhere;
}
.summary-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;

  button {
    min-height: 36px;
    padding: 8px 12px;
    border: 1px solid var(--color-border);
    border-radius: var(--review-option-radius);
    background: var(--surface);
    color: var(--color-text-secondary);
    font: inherit;
    font-size: 13px;
    cursor: pointer;

    &[aria-pressed='true'] {
      border-color: var(--color-primary);
      color: var(--color-primary);
      background: var(--review-option-selected);
    }
    &:focus-visible {
      outline: 2px solid var(--review-option-ring);
      outline-offset: 2px;
    }
  }
}

.compare-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
  overflow-wrap: anywhere;

  :deep(.el-tag) { height: auto; white-space: normal; }
}

.compare-issues {
  max-height: 620px;
  overflow-y: auto;
  padding: 3px;
}

.compare-issues :deep(.issue-item) {
  border-color: var(--color-border);
  box-shadow: none;
  transform: none;

  &.is-active {
    background: var(--review-option-selected);
    border-color: var(--color-primary);
    box-shadow: 0 0 0 2px var(--review-option-ring);
  }
}
.compare-issues :deep(.issue-actions .el-button + .el-button),
.compare-issues :deep(.issue-status .el-button + .el-button) { margin-left: 0; }

.compare-text-preview {
  max-height: 220px;
  overflow-y: auto;
  font-size: 13px;
  line-height: 1.8;
  color: var(--color-text, #374151);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  padding: 4px 16px;
}

.compare-error {
  padding: 8px 0;
}
/* Content-first editing workspace. */
.input-section { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 22px; align-items: start; }
.input-card { border-radius: 12px; box-shadow: none; border: 1px solid var(--color-border); }
.input-card .card-header { justify-content: space-between; }.editor-caption { margin-top: 6px; color: var(--color-text-secondary); font-size: 12px; }
.input-card :deep(.el-card__body) { padding: 0; }
.editor-wrapper { margin: 0; }.editor-wrapper :deep(.el-textarea__inner) { background: var(--surface); border-radius: 0; box-shadow: none; padding: 28px; font-size: 16px; line-height: 1.9; min-height: 430px !important; resize: vertical; }
.editor-wrapper :deep(.el-textarea__inner:focus) { box-shadow: inset 0 0 0 2px var(--el-color-primary-light-5); }
.action-bar { border-top: 1px solid var(--color-border); padding: 16px 22px; position: sticky; bottom: 0; background: var(--surface); z-index: 5; flex-direction: row-reverse; border-radius: 0 0 12px 12px; }
.action-bar .text-count { margin-left: 0; margin-right: auto; font-size: 12px; }.action-bar small { display: block; margin-top: 5px; color: var(--color-text-secondary); font-size: 11px; }
.proofread-settings { min-width: 0; background: transparent; padding: 4px 0; display: flex; flex-direction: column; gap: 18px; }
.review-heading { margin: 4px 0 24px; }.review-eyebrow { font-size: 12px; color: var(--color-primary); }.review-heading h2 { font-size: 24px; font-weight: 600; margin: 10px 0; }.review-heading p { color: var(--color-text-secondary); font-size: 13px; }
.review-tools { border: 1px solid var(--color-border); background: var(--surface); border-radius: 8px; margin-bottom: 10px; }.review-tools summary { cursor: pointer; padding: 13px 16px; font-size: 13px; color: var(--color-text-secondary); }.review-tools[open] { padding-bottom: 14px; }
.result-toolbar { box-shadow: none; border: 1px solid var(--color-border); margin-top: 16px; flex-wrap: wrap; }.result-columns { grid-template-columns: minmax(0, 1.35fr) minmax(340px, 1fr); height: max(600px, calc(100vh - 230px)); }.column-card { box-shadow: none; }
@media(max-width:1150px) { .input-section { grid-template-columns: minmax(0, 1fr) 270px; gap: 16px; }.result-columns { grid-template-columns: 1fr; height: auto; }.result-columns .column-card { max-height: 650px; } }
@media(max-width:700px) { .input-section { grid-template-columns: 1fr; }.proofread-settings { padding: 8px 0; }.editor-wrapper :deep(.el-textarea__inner) { min-height: 280px !important; padding: 20px; }.action-bar { padding: 14px; flex-wrap: wrap; }.action-bar :deep(.el-button--large) { min-width: 110px; }.result-toolbar .toolbar-info { flex-wrap: wrap; }.review-heading h2 { font-size: 21px; } }

@media(max-width:700px) { .input-section { padding-bottom: 84px; }.input-section .action-bar { position: fixed; left: 0; right: 0; bottom: 0; padding: 12px 18px max(12px, env(safe-area-inset-bottom)); border-radius: 0; box-shadow: 0 -4px 18px rgba(0,0,0,.04); }.editor-wrapper :deep(.el-textarea__inner) { height: 330px; } }
</style>
