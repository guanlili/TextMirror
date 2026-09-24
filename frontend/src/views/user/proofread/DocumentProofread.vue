<template>
  <div class="document-proofread-page">
    <ProofreadEntry v-if="step === 'upload'" />
    <!-- 步骤一：上传文件 -->
    <div
      v-if="step === 'upload'"
      class="upload-section"
    >
      <el-card>
        <template #header>
          <div class="card-header">
            <span class="card-title">文档上传校对</span>
            <el-tag
              type="primary"
              effect="plain"
              size="small"
            >
              支持 Word / PDF / TXT，最大 20MB
            </el-tag>
          </div>
        </template>

        <el-upload
          ref="uploadRef"
          class="upload-dragger"
          drag
          :auto-upload="false"
          :limit="1"
          :on-change="handleFileChange"
          :on-remove="handleFileRemove"
          :on-exceed="() => ElMessage.warning('只能上传一个文件')"
          accept=".doc,.docx,.pdf,.txt"
        >
          <el-icon class="upload-icon">
            <UploadFilled />
          </el-icon>
          <div class="el-upload__text">
            将文件拖到此处，或 <em>点击上传</em>
          </div>
          <template #tip>
            <div class="upload-tip">
              支持 .doc / .docx / .pdf / .txt 格式，单文件最大 20MB，PDF 最多 100 页
            </div>
          </template>
        </el-upload>

        <!-- 校对设置 -->
        <div
          v-if="selectedFile"
          class="proofread-settings"
        >
          <div class="file-info">
            <el-icon><Document /></el-icon>
            <span>{{ selectedFile.name }}</span>
            <el-tag size="small">
              {{ formatSize(selectedFile.size) }}
            </el-tag>
          </div>
          <ProfessionalRules v-model="domain" />
          <div
            v-if="modelOptions.length > 1"
            class="setting-row"
          >
            <span class="setting-label">校对模型：</span>
            <el-select
              v-model="selectedModelId"
              size="default"
              style="max-width: 320px;"
              placeholder="默认当前模型"
              aria-describedby="document-model-help"
            >
              <el-option
                v-for="m in modelOptions"
                :key="m.id"
                :label="m.is_active ? `${m.name}（${m.model}）· 当前` : `${m.name}（${m.model}）`"
                :value="m.id"
              />
            </el-select>
            <p
              id="document-model-help"
              class="setting-help"
            >
              默认使用标记“当前”的模型；不同模型的速度、效果和用量不同。
            </p>
          </div>
          <el-button
            type="primary"
            size="large"
            :loading="uploading || proofreading"
            @click="handleStartProofread"
          >
            <el-icon><Edit /></el-icon>
            {{ statusText }}
          </el-button>
        </div>
      </el-card>
    </div>

    <!-- 步骤二：校对进度 -->
    <div
      v-else-if="step === 'processing'"
      class="processing-section"
    >
      <el-card>
        <div class="processing-content">
          <el-steps
            :active="stepIndex"
            align-center
            style="width: 480px; max-width: 100%; margin-bottom: 20px;"
          >
            <el-step
              title="上传提取"
              description="解析文档文本"
            />
            <el-step
              title="AI 校对"
              description="大模型逐片检查"
            />
            <el-step
              title="整理结果"
              description="准备人工审阅"
            />
            <el-step
              title="保存完成"
              description="写入历史记录"
            />
          </el-steps>
          <el-icon
            class="processing-icon"
            :size="40"
          >
            <Loading />
          </el-icon>
          <h3>{{ statusText }}</h3>
          <p class="processing-info">
            {{ processingInfo }}
          </p>
          <el-progress
            :percentage="progress"
            :stroke-width="8"
            style="width: 400px; max-width: 100%; margin-top: 16px;"
          />
          <el-button
            type="danger"
            plain
            size="small"
            style="margin-top: 16px;"
            :loading="cancelling"
            :disabled="cancelling"
            @click="handleCancel"
          >
            {{ currentTaskId ? '取消任务' : '取消上传' }}
          </el-button>
        </div>
      </el-card>
    </div>

    <!-- 步骤三：双栏对照结果 -->
    <div
      v-else-if="step === 'result'"
      class="result-section"
    >
      <div class="document-review-heading">
        <span>文档审校结果</span><h2>逐条确认，让文档准备就绪</h2><p>{{ resultFilename }} · {{ pendingCount }} 项待处理 · {{ acceptedCount }} 项已接受</p>
      </div>
      <details class="document-review-tools">
        <summary>草稿与版本管理</summary>
        <ReviewWorkspace
          :record-id="recordId"
          :source-text="sourceText"
          :issues="issues"
          :coverage="coverage"
          :domain="domain"
          :depth="depth"
          :config-id="selectedModelId"
          :saved-review="savedReview"
          @saved="handleReviewSaved"
          @restore="handleReviewRestore"
        />
      </details>
      <QualityFeedbackDialog
        ref="qualityFeedback"
        :record-id="recordId"
        :source-text="sourceText"
      />
      <details class="document-review-tools">
        <summary>检查覆盖范围与事实核查 · {{ coverage?.status === 'complete' ? '全文检查已完成' : coverage?.status === 'partial' ? '仍有未检查的内容' : '覆盖范围未确认' }}</summary>
        <FactCheckPanel
          :record-id="recordId"
          :source-text="sourceText"
          @started="router.replace({ query: { ...route.query, review: String($event) } })"
        />
        <ProofreadCoverage
          v-model:coverage="coverage"
          :source-text="sourceText"
          :domain="domain"
          :depth="depth"
          :config-id="selectedModelId"
          @issues="mergeIssues"
        />
      </details>
      <!-- 顶部操作栏 -->
      <div class="result-toolbar">
        <el-button @click="handleReupload">
          <el-icon><Back /></el-icon>重新上传
        </el-button>
        <div class="toolbar-info">
          <el-tag><el-icon><Document /></el-icon>&nbsp;{{ resultFilename }}</el-tag>
          <el-tag :type="isPartial ? 'warning' : 'success'">
            {{ isPartial ? '部分完成 · ' : '' }}共 {{ issues.length }} 个问题
          </el-tag>
          <el-tag type="info">
            {{ acceptedCount }} 已接受
          </el-tag>
          <el-tag type="warning">
            {{ pendingCount }} 待处理
          </el-tag>
        </div>
        <div class="toolbar-actions">
          <el-button
            type="warning"
            :disabled="pendingCount === 0"
            @click="handleAcceptAll"
          >
            一键修改全部
          </el-button>
          <el-button
            type="primary"
            :loading="exporting"
            @click="handleExportText"
          >
            <el-icon><Download /></el-icon>导出修订文本
          </el-button>
          <el-button
            v-if="sourceFileId || recordId !== null"
            :loading="exporting"
            @click="downloadCorrected"
          >
            {{ wordExportLabel }}
          </el-button>
          <el-button
            :loading="exporting"
            @click="handleExportReport"
          >
            导出问题报告
          </el-button>
        </div>
      </div>

      <!-- 双栏对照区域 -->
      <div class="result-columns">
        <el-card class="column-card original-column">
          <template #header>
            <div class="column-header">
              <span>文档审阅</span>
              <span class="text-count">{{ sourceCharacters.length }} 字</span>
            </div>
          </template>
          <ReviewPreview
            :source-text="sourceText"
            :current-text="currentText"
            :issues="issues"
            :patches="patches"
            :active-index="activeIssueIndex"
            :original-html="originalHtml"
          />
        </el-card>

        <!-- 右栏：问题列表（逐条审改） -->
        <el-card class="column-card issues-column">
          <template #header>
            <div class="issues-header">
              <span class="issues-title">
                <el-icon><Document /></el-icon>
                问题列表
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
            <div
              v-for="issue in filteredIssues"
              :key="reviewIssueKey(issue)"
              class="issue-item"
              :class="{
                'is-accepted': issue._accepted,
                'is-ignored': issue._ignored,
                'is-active': activeIssueIndex === getGlobalIndex(issue),
              }"
              tabindex="0"
              @focus="activeIssueIndex = getGlobalIndex(issue)"
              @click="activeIssueIndex = getGlobalIndex(issue)"
              @mouseenter="activeIssueIndex = getGlobalIndex(issue)"
              @mouseleave="activeIssueIndex = -1"
            >
              <div class="issue-header">
                <span class="issue-number">#{{ getGlobalIndex(issue) + 1 }}</span>
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
              </div>
              <div class="issue-body">
                <div class="issue-context">
                  {{ issueContext(issue) }}
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
              </div>
              <div
                v-if="!issue._accepted && !issue._ignored"
                class="issue-actions"
              >
                <el-button
                  v-if="issue.suggestion"
                  type="primary"
                  size="small"
                  @click="acceptIssue(issue)"
                >
                  <el-icon><Check /></el-icon>仅修改此处
                </el-button>
                <el-button
                  v-else-if="issue.type === 'sensitive' && issue.original"
                  type="warning"
                  size="small"
                  @click="deleteIssue(issue)"
                >
                  <el-icon><Delete /></el-icon>仅删除此处
                </el-button>
                <el-button
                  v-if="issue.suggestion || (issue.type === 'sensitive' && issue.original)"
                  size="small"
                  title="仅处理已报告且原文、建议相同的位置"
                  @click="acceptMatching(issue)"
                >
                  全文同类
                </el-button>
                <el-button
                  size="small"
                  @click="ignoreIssue(issue)"
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
                  :disabled="recordId === null"
                  @click="qualityFeedback?.open(issue)"
                >
                  补充原因（可选）
                </el-button>
                <el-button
                  text
                  size="small"
                  @click="undoIssue(issue)"
                >
                  撤销
                </el-button>
              </div>
            </div>
            <el-empty
              v-if="filteredIssues.length === 0"
              :description="emptyIssuesText"
            />
          </div>
        </el-card>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import ProfessionalRules from '@/components/ProfessionalRules.vue'
import ProofreadEntry from '@/components/ProofreadEntry.vue'
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox, type UploadFile, type UploadInstance } from 'element-plus'
import { uploadDocumentApi, fetchExtractedTextApi, exportRevisedTextApi, exportReportApi, type DocumentProofreadResponse } from '@/api/document'
import { asyncDocumentProofreadApi, streamTaskStatus, cancelTaskApi, type TaskStatus } from '@/api/tasks'
import { getAvailableModelsCached, type AvailableModel } from '@/api/polish'
import type { ProofreadCoverage as Coverage } from '@/api/proofread'
import {
  getReviewApi,
  saveReviewApi,
  exportReviewApi,
  exportDocumentReviewApi,
  getReviewErrorDetail,
  type ReviewResponse,
  type ReviewRestorePayload,
} from '@/api/review'
import { severityColor, severityLabel, typeLabel } from '@/utils/proofread'
import { reviewStateFingerprint, reviewIssueKey } from '@/utils/review'
import { formatSize } from '@/utils/format'
import { useProofreadReview, type ReviewIssue } from '@/composables/useProofreadReview'
import ReviewPreview from '@/components/ReviewPreview.vue'
import ProofreadCoverage from '@/components/ProofreadCoverage.vue'
import ReviewWorkspace from '@/components/ReviewWorkspace.vue'
import QualityFeedbackDialog from '@/components/QualityFeedbackDialog.vue'
import FactCheckPanel from '@/components/FactCheckPanel.vue'

const qualityFeedback = ref<InstanceType<typeof QualityFeedbackDialog> | null>(null)
const route = useRoute()
const router = useRouter()

// 步骤状态
const step = ref<'upload' | 'processing' | 'result'>('upload')
// 进度步骤映射：后端 step 字段 → el-steps 序号（0-3）
const stepKey = ref('upload')
const stepIndex = computed(() => {
  const map: Record<string, number> = { upload: 0, proofread: 1, generate: 2, save: 3 }
  return map[stepKey.value] ?? 1
})
const selectedFile = ref<File | null>(null)
const uploading = ref(false)
const proofreading = ref(false)
const cancelling = ref(false)
const progress = ref(0)
const processingInfo = ref('')
const accessToken = ref('')
const currentTaskId = ref('')
const abortController = ref<AbortController | null>(null)
const uploadRef = ref<UploadInstance>()
let activeRunId = 0

// 校对模型选择（默认当前活跃模型）
const modelOptions = ref<AvailableModel[]>([])
const selectedModelId = ref<number | null>(null)

onMounted(async () => {
  window.addEventListener('beforeunload', handleBeforeUnload)
  const id = reviewQueryId()
  if (id !== null) await restoreSavedReview(id)
  else void restoreTaskSnapshot()
  void loadModelOptions()
})

onUnmounted(() => {
  window.removeEventListener('beforeunload', handleBeforeUnload)
  invalidateTaskRun()
})

// 设置
const domain = ref('general')
const depth = ref('standard')

// 原文及原始排版只在上传/恢复时设置，采纳决策不修改它们。
const originalText = ref('')
const originalHtml = ref('')
const resultFilename = ref('')
const sourceFileId = ref('')
const coverage = ref<Coverage | null>(null)
const savedReview = ref<ReviewResponse | null>(null)
const savedFingerprint = ref('')
const exporting = ref(false)

const review = useProofreadReview()
const {
  sourceText,
  issues,
  currentText,
  patches,
  filterType,
  activeIssueIndex,
  recordId,
  filteredIssues,
  acceptedCount,
  pendingCount,
  getGlobalIndex,
  initialize,
  mergeIssues,
  serializeIssues,
  acceptIssue,
  acceptMatching,
  ignoreIssue,
  deleteIssue,
  undoIssue,
  handleAcceptAll,
} = review

// 计算属性
const statusText = computed(() => {
  if (cancelling.value) return '正在取消...'
  if (uploading.value) return '上传中...'
  if (proofreading.value) return 'AI 校对中...'
  if (step.value === 'processing') return '正在恢复审阅...'
  return '开始校对'
})

const sourceCharacters = computed(() => Array.from(sourceText.value))
const isPartial = computed(() => coverage.value?.status === 'partial')
const sourceFingerprint = computed(() => reviewStateFingerprint(issues.value, coverage.value))
const hasUnsavedChanges = computed(() => step.value === 'result'
  && sourceFingerprint.value !== savedFingerprint.value)
const wordExportLabel = computed(() => sourceFileId.value && /\.docx$/i.test(resultFilename.value)
  ? '导出已采纳 Word（保留排版）'
  : '导出 Word（纯文本）')
const emptyIssuesText = computed(() => {
  if (isPartial.value) return '审校尚未完成，当前范围暂无此类问题，不代表全文无误'
  return filterType.value ? '没有符合筛选条件的问题' : '没有发现问题'
})

function issueContext(issue: ReviewIssue): string {
  const { start, end } = issue
  if (start == null || end == null || start < 0 || end <= start) return '位置未确定，请人工核对'
  const chars = sourceCharacters.value
  const before = chars.slice(Math.max(0, start - 12), start).join('')
  const after = chars.slice(end, end + 12).join('')
  return `第 ${start + 1} 字 · ${start > 12 ? '…' : ''}${before}【${issue.original}】${after}${end + 12 < chars.length ? '…' : ''}`
}

function reviewQueryId(): number | null {
  const raw = route.query.review
  if (typeof raw !== 'string' || !/^\d+$/.test(raw)) return null
  const id = Number(raw)
  return Number.isSafeInteger(id) && id > 0 ? id : null
}

async function restoreSavedReview(id: number) {
  const runId = startTaskRun()
  step.value = 'processing'
  processingInfo.value = '正在读取已保存审阅，不会重新调用 AI 校对'
  try {
    const response = await getReviewApi(id, { signal: abortController.value?.signal })
    if (!isCurrentRun(runId)) return
    if (response.record_id !== id) throw new Error('返回的审阅记录不匹配')
    originalText.value = response.original_text
    originalHtml.value = ''
    recordId.value = response.record_id
    sourceFileId.value = response.source_file_id || ''
    resultFilename.value = response.source_filename || 'document'
    domain.value = response.domain
    depth.value = response.depth || 'standard'
    selectedModelId.value = response.config_id ?? null
    coverage.value = response.coverage ?? null
    savedReview.value = response
    review.restore(response.original_text, response.issues)
    savedFingerprint.value = reviewStateFingerprint(response.issues, response.coverage)
    progress.value = 100
    stepKey.value = 'save'
    step.value = 'result'
    invalidateTaskRun()
  } catch (error) {
    if (!isCurrentRun(runId) || isAbortError(error)) return
    resetAll()
    ElMessage.error(`恢复审阅失败：${getReviewErrorDetail(error)}`)
  }
}

async function handleReviewSaved(response: ReviewResponse) {
  if (response.record_id !== recordId.value || response.original_text !== sourceText.value) return
  if (savedReview.value && response.revision < savedReview.value.revision) return
  savedReview.value = response
  // 基线来自服务端响应，保存请求期间新增的操作仍视为未保存。
  savedFingerprint.value = reviewStateFingerprint(response.issues, response.coverage)
  await syncReviewQuery(response.record_id)
}

async function syncReviewQuery(id: number | null): Promise<boolean> {
  if (route.query.review === (id === null ? undefined : String(id))) return true
  const query = { ...route.query }
  delete query.review
  if (id !== null) query.review = String(id)
  try {
    // 地址同步不触发恢复，不覆盖更新地址期间的采纳/忽略操作。
    const failure = await router.replace({ query })
    if (failure) throw failure
    return true
  } catch {
    ElMessage.warning('地址更新失败，有记录的结果可从校对历史继续审阅')
    return false
  }
}

function handleReviewRestore(snapshot: ReviewRestorePayload) {
  review.restore(sourceText.value, snapshot.issues)
  coverage.value = snapshot.coverage
}

async function confirmDiscardChanges(): Promise<boolean> {
  if (!hasUnsavedChanges.value) return true
  try {
    await ElMessageBox.confirm('当前有未保存的审阅修改，离开后将丢失。是否继续？', '未保存修改', {
      type: 'warning', confirmButtonText: '放弃修改', cancelButtonText: '继续审阅',
    })
    return true
  } catch {
    return false
  }
}

onBeforeRouteLeave(() => confirmDiscardChanges())

function handleBeforeUnload(event: { preventDefault(): void; returnValue: string }) {
  if (!hasUnsavedChanges.value) return
  event.preventDefault()
  event.returnValue = ''
}

async function handleReupload() {
  if (!await confirmDiscardChanges()) return
  resetAll()
  const query = { ...route.query }
  delete query.review
  await router.replace({ query })
}

const MAX_FILE_SIZE = 20 * 1024 * 1024

function handleFileChange(file: UploadFile) {
  if (file.raw) {
    if (file.raw.size > MAX_FILE_SIZE) {
      ElMessage.warning('文件大小不能超过 20MB')
      selectedFile.value = null
      uploadRef.value?.clearFiles()
      return
    }
    selectedFile.value = file.raw
  }
}

function handleFileRemove() {
  selectedFile.value = null
}

// sessionStorage 快照：刷新后恢复轮询
const SESSION_KEY = 'textmirror_task_snapshot'

interface TaskSnapshot {
  taskId?: string
  idempotencyKey?: string
  fileId: string
  filename: string
  domain: string
  depth?: string
  configId?: number
  accessToken?: string
}

function saveSnapshot(snap: TaskSnapshot) {
  try { sessionStorage.setItem(SESSION_KEY, JSON.stringify(snap)) } catch { /* quota */ }
}

function clearSnapshot() {
  try { sessionStorage.removeItem(SESSION_KEY) } catch { /* ignore */ }
}

function loadSnapshot(): TaskSnapshot | null {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY)
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}

function startTaskRun(): number {
  abortController.value?.abort()
  activeRunId += 1
  abortController.value = new AbortController()
  return activeRunId
}

function invalidateTaskRun() {
  activeRunId += 1
  abortController.value?.abort()
  abortController.value = null
}

function isCurrentRun(runId: number): boolean {
  return runId === activeRunId
}

function isAbortError(error: unknown): boolean {
  return (error as { name?: string })?.name === 'AbortError' || (error as { code?: string })?.code === 'ERR_CANCELED'
}

function createIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

async function loadModelOptions() {
  try {
    const res = await getAvailableModelsCached()
    modelOptions.value = res.models
    if (selectedModelId.value === null && step.value === 'upload') {
      const active = res.models.find(m => m.is_active)
      selectedModelId.value = active ? active.id : (res.models[0]?.id ?? null)
    }
  } catch { /* 模型列表加载失败时用默认活跃模型 */ }
}

function applyTaskProgress(status: TaskStatus, runId: number) {
  if (!isCurrentRun(runId)) return
  if (status.step) stepKey.value = status.step
  if (typeof status.progress === 'number') {
    progress.value = Math.min(50 + Math.floor(status.progress * 0.5), 99)
  }
  if (status.message) {
    processingInfo.value = status.partial_total
      ? `${status.message}（已发现 ${status.partial_total} 处问题）`
      : status.message
  }
}

async function completeTask(taskResult: TaskStatus, fallbackFilename: string, runId: number) {
  if (!isCurrentRun(runId)) return

  uploading.value = false
  proofreading.value = false
  cancelling.value = false
  currentTaskId.value = ''
  accessToken.value = ''

  if (taskResult.status !== 'SUCCESS') {
    clearSnapshot()
    step.value = 'upload'
    stepKey.value = 'upload'
    progress.value = 0
    processingInfo.value = ''
    invalidateTaskRun()
    if (taskResult.status === 'CANCELLED') {
      ElMessage.info('任务已取消')
    } else {
      ElMessage.error(taskResult.error || taskResult.message || '校对任务执行失败，请重试')
    }
    return
  }

  const result = taskResult.result as Partial<DocumentProofreadResponse> | undefined
  if (!result || !Array.isArray(result.issues)) {
    clearSnapshot()
    step.value = 'upload'
    stepKey.value = 'upload'
    progress.value = 0
    processingInfo.value = ''
    invalidateTaskRun()
    ElMessage.error('校对结果格式异常，请重试')
    return
  }

  recordId.value = result.record_id ?? null
  resultFilename.value = result.filename || fallbackFilename
  sourceFileId.value = result.file_id || sourceFileId.value
  domain.value = result.domain || domain.value
  depth.value = result.depth || 'standard'
  if (result.config_id !== undefined) selectedModelId.value = result.config_id
  coverage.value = result.coverage ?? null
  savedReview.value = null
  initialize(originalText.value, result.issues)
  savedFingerprint.value = sourceFingerprint.value
  progress.value = 100
  stepKey.value = 'save'
  step.value = 'result'
  const bound = await syncReviewQuery(recordId.value)
  if (!isCurrentRun(runId)) return
  // 有记录但导航失败时仍可从任务快照恢复；匿名结果不生成虚假的 review id。
  if (bound || recordId.value === null) clearSnapshot()
  invalidateTaskRun()

  if (isPartial.value) {
    ElMessage.warning(`审校尚未完成，已发现 ${issues.value.length} 个问题；未审范围请补查，不能视为全文无误`)
  } else if (issues.value.length === 0) {
    ElMessage.success('文档没有发现任何问题')
  } else {
    ElMessage.info(`共发现 ${issues.value.length} 个问题，请逐条审阅`)
  }
}

async function trackTask(snapshot: TaskSnapshot, runId: number) {
  if (!snapshot.taskId || !isCurrentRun(runId)) return

  try {
    const taskResult = await streamTaskStatus(snapshot.taskId, (status) => {
      applyTaskProgress(status, runId)
    }, {
      accessToken: snapshot.accessToken,
      signal: abortController.value?.signal,
    })
    await completeTask(taskResult, snapshot.filename, runId)
  } catch (error: unknown) {
    if (!isCurrentRun(runId) || isAbortError(error)) return
    uploading.value = false
    proofreading.value = true
    processingInfo.value = '连接中断，刷新页面后会继续恢复任务状态'
    ElMessage.warning('任务状态连接中断，请稍后刷新页面继续恢复')
  }
}

async function submitTaskSnapshot(snapshot: TaskSnapshot, runId: number) {
  const submitRes = await asyncDocumentProofreadApi({
    file_id: snapshot.fileId,
    domain: snapshot.domain,
    depth: snapshot.depth || 'standard',
    config_id: snapshot.configId,
  }, {
    idempotencyKey: snapshot.idempotencyKey,
    signal: abortController.value?.signal,
  })
  if (!isCurrentRun(runId)) return

  snapshot.taskId = submitRes.task_id
  snapshot.accessToken = submitRes.access_token
  saveSnapshot(snapshot)
  accessToken.value = submitRes.access_token || ''
  currentTaskId.value = submitRes.task_id
  await trackTask(snapshot, runId)
}

async function restoreTaskSnapshot() {
  const snapshot = loadSnapshot()
  if (!snapshot) return

  sourceFileId.value = snapshot.fileId
  domain.value = snapshot.domain || 'general'
  depth.value = snapshot.depth || 'standard'
  selectedModelId.value = snapshot.configId ?? null
  resultFilename.value = snapshot.filename || ''
  accessToken.value = snapshot.accessToken || ''
  currentTaskId.value = snapshot.taskId || ''
  step.value = 'processing'
  stepKey.value = 'proofread'
  proofreading.value = true
  progress.value = 50
  processingInfo.value = '正在恢复文档内容...'

  try {
    const textRes = await fetchExtractedTextApi(snapshot.fileId)
    originalText.value = textRes.extracted_text
    initialize(originalText.value, [])
  } catch {
    processingInfo.value = '文档内容恢复失败，但任务仍可继续'
  }

  const runId = startTaskRun()
  if (snapshot.taskId) {
    processingInfo.value = '恢复连接中...'
    await trackTask(snapshot, runId)
    return
  }
  if (!snapshot.idempotencyKey) {
    clearSnapshot()
    resetAll()
    ElMessage.warning('未完成任务缺少恢复信息，请重新提交')
    return
  }

  processingInfo.value = '正在恢复任务提交...'
  try {
    await submitTaskSnapshot(snapshot, runId)
  } catch (error: unknown) {
    if (!isCurrentRun(runId) || isAbortError(error)) return
    processingInfo.value = '任务提交状态暂未确认，刷新页面后会自动重试'
    ElMessage.warning('任务提交状态暂未确认，请稍后刷新页面继续恢复')
  }
}

// 开始校对
async function handleStartProofread() {
  if (!selectedFile.value) return

  const runId = startTaskRun()
  let snapshot: TaskSnapshot | null = null
  try {
    step.value = 'processing'
    stepKey.value = 'upload'
    uploading.value = true
    proofreading.value = false
    cancelling.value = false
    progress.value = 20
    processingInfo.value = '正在上传文件并提取文本...'
    recordId.value = null
    savedReview.value = null
    if (!await syncReviewQuery(null)) {
      if (!isCurrentRun(runId)) return
      uploading.value = false
      step.value = 'upload'
      progress.value = 0
      processingInfo.value = ''
      invalidateTaskRun()
      return
    }
    if (!isCurrentRun(runId)) return

    const uploadRes = await uploadDocumentApi(selectedFile.value, abortController.value?.signal)
    if (!isCurrentRun(runId)) return

    const textRes = await fetchExtractedTextApi(uploadRes.file_id)
    if (!isCurrentRun(runId)) return

    uploading.value = false
    proofreading.value = true
    progress.value = 50
    stepKey.value = 'proofread'
    processingInfo.value = `文本提取完成，共 ${uploadRes.text_length} 字，正在提交校对任务...`
    originalText.value = textRes.extracted_text
    initialize(originalText.value, [])
    originalHtml.value = textRes.extracted_html || ''
    sourceFileId.value = uploadRes.file_id
    resultFilename.value = uploadRes.filename

    snapshot = {
      idempotencyKey: createIdempotencyKey(),
      fileId: uploadRes.file_id,
      filename: uploadRes.filename,
      domain: domain.value,
      depth: depth.value,
      configId: selectedModelId.value ?? undefined,
    }
    saveSnapshot(snapshot)
    await submitTaskSnapshot(snapshot, runId)
  } catch (error: unknown) {
    if (!isCurrentRun(runId) || isAbortError(error)) return
    uploading.value = false
    if (snapshot) {
      proofreading.value = true
      processingInfo.value = '任务提交状态暂未确认，刷新页面后将自动重试'
      ElMessage.warning('任务提交状态暂未确认，请稍后刷新页面继续恢复')
      return
    }
    proofreading.value = false
    step.value = 'upload'
    stepKey.value = 'upload'
    progress.value = 0
    processingInfo.value = ''
    ElMessage.error('文件上传失败，请重试')
  }
}

// 取消任务
async function handleCancel() {
  if (!currentTaskId.value) {
    resetAll()
    ElMessage.info('已取消上传')
    return
  }
  if (cancelling.value) return

  const taskId = currentTaskId.value
  const runId = activeRunId
  cancelling.value = true
  try {
    await cancelTaskApi(taskId, accessToken.value || undefined)
    if (!isCurrentRun(runId) || currentTaskId.value !== taskId) return
    processingInfo.value = '取消请求已提交，正在等待任务停止...'
    ElMessage.info('取消请求已提交，正在等待任务停止')
  } catch {
    if (isCurrentRun(runId) && currentTaskId.value === taskId) {
      cancelling.value = false
      ElMessage.warning('取消请求发送失败')
    }
  }
}

async function confirmPartialExport(): Promise<boolean> {
  if (!isPartial.value) return true
  try {
    await ElMessageBox.confirm('审校仅部分完成，仍有未检查范围。本次只导出已采纳修改，不代表全文已校对。是否继续？', '部分完成导出', {
      type: 'warning', confirmButtonText: '导出已采纳部分', cancelButtonText: '继续审阅',
    })
    return true
  } catch {
    return false
  }
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  try {
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
  } finally {
    link.remove()
    URL.revokeObjectURL(url)
  }
}

// 导出请求发起时的已采纳快照，绝不下载任务生成的“全采纳”静态文件。
async function downloadCorrected() {
  if (exporting.value || (!sourceFileId.value && recordId.value === null)) return
  const runId = activeRunId
  exporting.value = true
  let stage = '保存'
  try {
    if (!await confirmPartialExport() || !isCurrentRun(runId)) return
    const id = recordId.value
    const fileId = sourceFileId.value
    const capturedIssues = serializeIssues()
    const capturedCoverage: Coverage | null = coverage.value ? JSON.parse(JSON.stringify(coverage.value)) : null
    const filename = `${isPartial.value ? '部分审校_' : ''}已采纳_${(resultFilename.value || 'document').replace(/\.[^.]+$/, '')}.docx`
    let blob: Blob
    if (id !== null) {
      const response = await saveReviewApi(id, {
        revision: savedReview.value?.revision ?? 0,
        issues: capturedIssues,
        coverage: capturedCoverage,
        depth: depth.value,
        config_id: selectedModelId.value,
      })
      if (!isCurrentRun(runId)) return
      if (response.record_id !== id || response.original_text !== sourceText.value) {
        throw new Error('返回的审阅与当前文档不一致，已停止导出')
      }
      await handleReviewSaved(response)
      if (!isCurrentRun(runId)) return
      stage = '导出'
      blob = await exportReviewApi(id, { revision: response.revision, format: 'docx' })
    } else {
      stage = '导出'
      blob = await exportDocumentReviewApi(fileId, { issues: capturedIssues, format: 'docx' })
    }
    if (!isCurrentRun(runId)) return
    downloadBlob(blob, filename)
    ElMessage.success('已导出请求发起时的已采纳修改；后续操作需再次导出')
  } catch (error) {
    if (!isCurrentRun(runId)) return
    ElMessage.error(`${stage}失败，未导出：${getReviewErrorDetail(error)}`)
  } finally {
    if (isCurrentRun(runId)) exporting.value = false
  }
}

// 导出修订文本为 Word
async function handleExportText() {
  if (exporting.value) return
  const runId = activeRunId
  if (!await confirmPartialExport() || !isCurrentRun(runId)) return
  if (!sourceFileId.value) {
    ElMessage.error('缺少文档标识，无法导出')
    return
  }
  exporting.value = true
  try {
    const baseName = (resultFilename.value || 'document').replace(/\.[^.]+$/, '')
    const filename = `${isPartial.value ? '部分审校_' : ''}修订_${baseName}`
    const blob = await exportRevisedTextApi(sourceFileId.value, { text: currentText.value, filename })
    downloadBlob(blob, `${filename}.docx`)
    ElMessage.success('修订文本已导出为 Word')
  } catch (error) {
    ElMessage.error(`导出失败：${getReviewErrorDetail(error)}`)
  } finally {
    exporting.value = false
  }
}

// 导出问题报告为 Word
async function handleExportReport() {
  if (exporting.value) return
  if (!sourceFileId.value) {
    ElMessage.error('缺少文档标识，无法导出')
    return
  }
  exporting.value = true
  try {
    const baseName = (resultFilename.value || 'document').replace(/\.[^.]+$/, '')
    const status = isPartial.value ? 'partial' : coverage.value ? 'completed' : 'unknown'
    const reportIssues = issues.value.map(issue => ({
      type: issue.type,
      severity: issue.severity,
      original: issue.original,
      suggestion: issue.suggestion,
      explanation: issue.explanation || '',
      context: issueContext(issue),
      status: issue._accepted ? 'accepted' : issue._ignored ? 'ignored' : 'pending',
    }))
    const blob = await exportReportApi(sourceFileId.value, {
      filename: `校对报告_${baseName}`,
      status,
      total_issues: issues.value.length,
      accepted_count: acceptedCount.value,
      ignored_count: issues.value.filter(i => i._ignored).length,
      pending_count: pendingCount.value,
      coverage: coverage.value ? {
        total_chunks: coverage.value.total_chunks,
        completed_chunks: coverage.value.completed_chunks,
        failed_chunks: coverage.value.failed_chunks.map(c => ({ start: c.start, end: c.end, error_code: c.error_code })),
      } : null,
      issues: reportIssues,
    })
    downloadBlob(blob, `校对报告_${baseName}.docx`)
    ElMessage.success('报告已导出为 Word')
  } catch (error) {
    ElMessage.error(`导出失败：${getReviewErrorDetail(error)}`)
  } finally {
    exporting.value = false
  }
}

// 重置
function resetAll() {
  invalidateTaskRun()
  step.value = 'upload'
  stepKey.value = 'upload'
  selectedFile.value = null
  uploading.value = false
  proofreading.value = false
  cancelling.value = false
  originalText.value = ''
  initialize('', [])
  originalHtml.value = ''
  resultFilename.value = ''
  sourceFileId.value = ''
  coverage.value = null
  depth.value = 'standard'
  savedReview.value = null
  savedFingerprint.value = ''
  exporting.value = false
  progress.value = 0
  processingInfo.value = ''
  filterType.value = ''
  recordId.value = null
  accessToken.value = ''
  currentTaskId.value = ''
  clearSnapshot()
  uploadRef.value?.clearFiles()
}
</script>

<style scoped lang="scss">
.setting-help {
  flex-basis: 100%;
  margin: 0;
  padding-left: 80px;
  font-size: 12px;
  line-height: 1.65;
  color: var(--color-text-secondary);
  overflow-wrap: anywhere;
}
@media (max-width: 768px) {
  .setting-help { flex-basis: auto; padding-left: 0; }
}
.document-proofread-page {
  max-width: 1400px;
  margin: 0 auto;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 12px;
  .card-title { color: var(--color-text); font-size: 17px; font-weight: 650; }
}

.upload-dragger {
  width: 100%;
  margin-bottom: 20px;
  :deep(.el-upload-dragger) {
    padding: 58px 20px;
    border: 1.5px dashed #cfd9e7;
    border-radius: 14px;
    background: linear-gradient(145deg, #fbfdff, #f6f9fd);
    transition: all .2s ease;

    &:hover {
      border-color: #4b83d9;
      background: #f4f8ff;
      box-shadow: 0 8px 24px rgba(45, 115, 221, .08);
    }
  }
  .upload-icon {
    font-size: 50px;
    color: #4a7fce;
    margin-bottom: 14px;
  }
}

.upload-tip {
  color: var(--color-text-secondary);
  font-size: 12px;
  margin-top: 8px;
}

.proofread-settings {
  padding: 16px;
  background: var(--surface-soft);
  border: 1px solid var(--surface-border);
  border-radius: 12px;

  .file-info {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 16px;
    padding: 8px 12px;
    background: var(--surface);
    border-radius: var(--border-radius-sm);
    border: 1px solid var(--color-border);
  }

  .setting-row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    row-gap: 6px;
    margin-bottom: 12px;
    .setting-label {
      width: 80px;
      font-weight: 500;
      flex-shrink: 0;
    }
  }
}

.processing-section {
  .processing-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 80px 20px;
    .processing-icon { color: var(--color-primary); margin-bottom: 16px; animation: spin 1.5s linear infinite; }
    h3 { margin-bottom: 8px; }
    .processing-info { color: var(--color-text-secondary); font-size: 14px; }
  }
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* 结果区域 */
.result-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
  padding: 12px 16px;
  background: var(--surface);
  border-radius: var(--border-radius-md);
  box-shadow: var(--shadow-sm);
  flex-wrap: wrap;

  .toolbar-info {
    display: flex;
    gap: 8px;
    align-items: center;
  }

  .toolbar-actions {
    margin-left: auto;
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
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

  .text-count {
    font-size: 12px;
    color: var(--color-text-secondary);
  }
}

.result-section > .review-workspace {
  margin-bottom: 16px;
}

.issue-context {
  margin-bottom: 8px;
  color: var(--color-text-secondary);
  font-size: 12px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
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

.issues-list {
  .issue-item {
    padding: 14px 14px 12px;
    border: 1px solid #e5e7eb;
    border-radius: 10px;
    margin-bottom: 12px;
    background: var(--surface);
    transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    cursor: pointer;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);

    &:hover {
      box-shadow: 0 4px 16px rgba(99, 102, 241, 0.08), 0 2px 4px rgba(0, 0, 0, 0.04);
      border-color: #c7d2fe;
      transform: translateY(-1px);
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
    gap: 6px;
    margin-bottom: 10px;
    align-items: center;

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
}

/* ===== 移动端响应式 ===== */
@media (max-width: 768px) {
  .proofread-settings {
    .setting-row {
      flex-direction: column;
      align-items: flex-start;
      gap: 6px;
    }
  }

  .result-columns {
    grid-template-columns: 1fr;
    height: auto;
  }

  .result-toolbar {
    flex-wrap: wrap;

    .toolbar-actions {
      margin-left: 0;
      width: 100%;
    }
  }
}

.upload-section :deep(.el-card) { box-shadow: none; border-radius: 12px; }
.upload-section :deep(.el-upload-dragger) { padding: 60px 24px; background: var(--surface-soft); border-radius: 12px; }
.document-review-heading { margin: 4px 0 24px; }.document-review-heading > span { color: var(--color-primary); font-size: 12px; }.document-review-heading h2 { margin: 10px 0; font-size: 24px; font-weight: 600; }.document-review-heading p { color: var(--color-text-secondary); font-size: 13px; }
.document-review-tools { border: 1px solid var(--color-border); background: var(--surface); border-radius: 8px; margin-bottom: 10px; }.document-review-tools summary { padding: 13px 16px; cursor: pointer; color: var(--color-text-secondary); font-size: 13px; }
.result-toolbar { margin-top: 16px; box-shadow: none; border: 1px solid var(--color-border); flex-wrap: wrap; }.result-columns { grid-template-columns: minmax(0,1.35fr) minmax(340px,1fr); }.column-card { box-shadow: none; }
@media(max-width:1150px) { .result-columns { grid-template-columns: 1fr; height: auto; }.result-columns .column-card { max-height: 650px; } }
@media(max-width:700px) { .document-review-heading h2 { font-size: 21px; }.upload-section .card-header { flex-wrap: wrap; gap: 12px; } }
</style>
