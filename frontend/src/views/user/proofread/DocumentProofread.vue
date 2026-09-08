<template>
  <div class="document-proofread-page">
    <!-- 步骤一：上传文件 -->
    <div v-if="step === 'upload'" class="upload-section">
      <el-card>
        <template #header>
          <div class="card-header">
            <span class="card-title">文档上传校对</span>
            <el-tag type="primary" effect="plain" size="small">支持 Word / PDF / TXT，最大 20MB</el-tag>
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
          <el-icon class="upload-icon"><UploadFilled /></el-icon>
          <div class="el-upload__text">将文件拖到此处，或 <em>点击上传</em></div>
          <template #tip>
            <div class="upload-tip">支持 .doc / .docx / .pdf / .txt 格式，单文件最大 20MB，PDF 最多 100 页</div>
          </template>
        </el-upload>

        <!-- 校对设置 -->
        <div class="proofread-settings" v-if="selectedFile">
          <div class="file-info">
            <el-icon><Document /></el-icon>
            <span>{{ selectedFile.name }}</span>
            <el-tag size="small">{{ formatSize(selectedFile.size) }}</el-tag>
          </div>
          <div class="setting-row">
            <span class="setting-label">领域选择：</span>
            <el-radio-group v-model="domain">
              <el-radio value="general">通用</el-radio>
              <el-radio value="official">公文</el-radio>
              <el-radio value="legal">法律</el-radio>
            </el-radio-group>
          </div>
          <div v-if="modelOptions.length > 1" class="setting-row">
            <span class="setting-label">校对模型：</span>
            <el-select
              v-model="selectedModelId"
              size="default"
              style="max-width: 320px;"
              placeholder="默认当前模型"
            >
              <el-option
                v-for="m in modelOptions"
                :key="m.id"
                :label="m.is_active ? `${m.name}（${m.model}）· 当前` : `${m.name}（${m.model}）`"
                :value="m.id"
              />
            </el-select>
          </div>
          <el-button type="primary" size="large" :loading="uploading || proofreading" @click="handleStartProofread">
            <el-icon><Edit /></el-icon>
            {{ statusText }}
          </el-button>
        </div>
      </el-card>
    </div>

    <!-- 步骤二：校对进度 -->
    <div v-else-if="step === 'processing'" class="processing-section">
      <el-card>
        <div class="processing-content">
          <el-steps :active="stepIndex" align-center style="width: 480px; max-width: 100%; margin-bottom: 20px;">
            <el-step title="上传提取" description="解析文档文本" />
            <el-step title="AI 校对" description="大模型逐片检查" />
            <el-step title="生成修订" description="产出修订文档" />
            <el-step title="保存完成" description="写入历史记录" />
          </el-steps>
          <el-icon class="processing-icon" :size="40"><Loading /></el-icon>
          <h3>{{ statusText }}</h3>
          <p class="processing-info">{{ processingInfo }}</p>
          <el-progress :percentage="progress" :stroke-width="8" style="width: 400px; max-width: 100%; margin-top: 16px;" />
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
    <div v-else-if="step === 'result'" class="result-section">
      <!-- 顶部操作栏 -->
      <div class="result-toolbar">
        <el-button @click="resetAll">
          <el-icon><Back /></el-icon>重新上传
        </el-button>
        <div class="toolbar-info">
          <el-tag><el-icon><Document /></el-icon>&nbsp;{{ resultFilename }}</el-tag>
          <el-tag type="success">共 {{ issues.length }} 个问题</el-tag>
          <el-tag type="info">{{ acceptedCount }} 已接受</el-tag>
          <el-tag type="warning">{{ pendingCount }} 待处理</el-tag>
        </div>
        <div class="toolbar-actions">
          <el-button type="warning" @click="handleAcceptAll" :disabled="pendingCount === 0">
            一键修改全部
          </el-button>
          <el-button type="primary" @click="handleExportText">
            <el-icon><Download /></el-icon>导出修订文本
          </el-button>
          <el-button
            v-if="correctedDownloadUrl"
            @click="downloadCorrected"
          >
            下载修订文档
          </el-button>
          <el-button @click="handleExportReport">导出问题报告</el-button>
        </div>
      </div>

      <!-- 双栏对照区域 -->
      <div class="result-columns">
        <!-- 左栏：原文（带高亮标注） -->
        <el-card class="column-card original-column">
          <template #header>
            <div class="column-header">
              <span>文档原文</span>
              <span class="text-count">{{ originalText.length }} 字</span>
            </div>
          </template>
          <div class="original-text" v-html="highlightedText"></div>
        </el-card>

        <!-- 右栏：问题列表（逐条审改） -->
        <el-card class="column-card issues-column">
          <template #header>
            <div class="issues-header">
              <span class="issues-title">
                <el-icon><Document /></el-icon>
                问题列表
                <el-tag type="info" effect="plain" size="small" round>{{ filteredIssues.length }}</el-tag>
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
                <el-option label="全部类型" value="">
                  <el-icon style="vertical-align:middle;margin-right:6px;"><Menu /></el-icon>全部类型
                </el-option>
                <el-option label="错别字" value="typo">
                  <el-icon style="vertical-align:middle;margin-right:6px;color:#f56c6c;"><EditPen /></el-icon>错别字
                </el-option>
                <el-option label="语法错误" value="grammar">
                  <el-icon style="vertical-align:middle;margin-right:6px;color:#e6a23c;"><Reading /></el-icon>语法错误
                </el-option>
                <el-option label="标点符号" value="punctuation">
                  <el-icon style="vertical-align:middle;margin-right:6px;color:#909399;"><Operation /></el-icon>标点符号
                </el-option>
                <el-option label="表达优化" value="style">
                  <el-icon style="vertical-align:middle;margin-right:6px;color:#409eff;"><MagicStick /></el-icon>表达优化
                </el-option>
                <el-option label="敏感词" value="sensitive">
                  <el-icon style="vertical-align:middle;margin-right:6px;color:#f56c6c;"><Warning /></el-icon>敏感词
                </el-option>
                <el-option label="逻辑问题" value="logic">
                  <el-icon style="vertical-align:middle;margin-right:6px;color:#67c23a;"><Connection /></el-icon>逻辑问题
                </el-option>
              </el-select>
            </div>
          </template>
          <div class="issues-list">
            <div
              v-for="(issue, index) in filteredIssues"
              :key="index"
              class="issue-item"
              :class="{
                'is-accepted': issue._accepted,
                'is-ignored': issue._ignored,
                'is-active': activeIssueIndex === getGlobalIndex(issue),
              }"
              @mouseenter="activeIssueIndex = getGlobalIndex(issue)"
              @mouseleave="activeIssueIndex = -1"
            >
              <div class="issue-header">
                <span class="issue-number">#{{ getGlobalIndex(issue) + 1 }}</span>
                <el-tag :type="severityColor(issue.severity)" size="small">
                  {{ typeLabel(issue.type) }}
                </el-tag>
                <el-tag :type="severityColor(issue.severity)" size="small" effect="plain">
                  {{ severityLabel(issue.severity) }}
                </el-tag>
              </div>
              <div class="issue-body">
                <div class="issue-diff">
                  <span class="text text-del" :title="issue.original">{{ issue.original }}</span>
                  <el-icon class="arrow-icon"><Right /></el-icon>
                  <span class="text text-add" :title="issue.suggestion">{{ issue.suggestion }}</span>
                </div>
                <div v-if="issue.explanation" class="issue-explanation">
                  <el-icon><InfoFilled /></el-icon>
                  <span>{{ issue.explanation }}</span>
                </div>
              </div>
              <div class="issue-actions" v-if="!issue._accepted && !issue._ignored">
                <el-button v-if="issue.suggestion" type="primary" size="small" @click="acceptIssue(issue)">
                  <el-icon><Check /></el-icon>接受修改
                </el-button>
                <el-button v-else-if="issue.type === 'sensitive' && issue.original" type="warning" size="small" @click="deleteIssue(issue)">
                  <el-icon><Delete /></el-icon>删除该词
                </el-button>
                <el-button size="small" @click="ignoreIssue(issue)">
                  <el-icon><Close /></el-icon>忽略
                </el-button>
              </div>
              <div class="issue-status" v-else>
                <el-tag v-if="issue._accepted" type="success" size="small">已接受</el-tag>
                <el-tag v-if="issue._ignored" type="info" size="small">已忽略</el-tag>
                <el-button text size="small" @click="undoIssue(issue)">撤销</el-button>
              </div>
            </div>
            <el-empty v-if="filteredIssues.length === 0" description="没有发现问题" />
          </div>
        </el-card>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { sanitizeDocumentHtml } from '@/utils/sanitize'
import {
  uploadDocumentApi,
  type DocumentProofreadResponse,
} from '@/api/document'
import { asyncDocumentProofreadApi, streamTaskStatus, cancelTaskApi, type TaskStatus } from '@/api/tasks'
import { submitIssueFeedbackApi } from '@/api/proofread'
import { getAvailableModelsApi, type AvailableModel } from '@/api/polish'

const recordId = ref<number | null>(null)

interface IssueWithStatus {
  original: string
  type: string
  suggestion: string
  explanation: string
  severity: string
  chunk_index: number
  _accepted: boolean
  _ignored: boolean
  _deletedText?: string    // 删除该词操作的实际删除内容（含标点），供撤销恢复
  _undoAnchor?: number     // 删除时的词首位置，撤销按此插回
}

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
const uploadRef = ref()
let activeRunId = 0

// 校对模型选择（默认当前活跃模型）
const modelOptions = ref<AvailableModel[]>([])
const selectedModelId = ref<number | null>(null)

onMounted(() => {
  void restoreTaskSnapshot()
  void loadModelOptions()
})

onUnmounted(() => {
  invalidateTaskRun()
})

// 设置
const domain = ref('general')

// 结果数据
const originalText = ref('')
const currentText = ref('')
const currentHtml = ref('')
const resultFilename = ref('')
const correctedDownloadUrl = ref('')
const issues = ref<IssueWithStatus[]>([])
const filterType = ref('')
const activeIssueIndex = ref(-1)

// 计算属性
const statusText = computed(() => {
  if (cancelling.value) return '正在取消...'
  if (uploading.value) return '上传中...'
  if (proofreading.value) return 'AI 校对中...'
  return '开始校对'
})

const acceptedCount = computed(() => issues.value.filter(i => i._accepted).length)
const pendingCount = computed(() => issues.value.filter(i => !i._accepted && !i._ignored).length)

const filteredIssues = computed(() => {
  if (!filterType.value) return issues.value
  return issues.value.filter(i => i.type === filterType.value)
})

const highlightedText = computed(() => {
  // 优先使用格式化 HTML（保留 Word 排版），回退到纯文本
  let html = currentHtml.value
  if (!html) {
    html = escapeHtml(currentText.value).replace(/\n/g, '<br/>')
  }
  const activeIssues = issues.value.filter(i => !i._accepted && !i._ignored)
  for (const issue of activeIssues) {
    if (!issue.original) continue
    const globalIdx = issues.value.indexOf(issue)
    const isHover = activeIssueIndex.value === globalIdx
    const color = isHover ? '#fef3c7' : severityHighlight(issue.severity)
    const border = isHover ? 'box-shadow:0 0 0 2px #f59e0b;' : ''
    const markHtml = `<mark class="highlight-mark" style="background:${color};${border}padding:1px 3px;border-radius:2px;cursor:pointer;" title="[${typeLabel(issue.type)}] ${escapeHtml(issue.suggestion)}">${escapeHtml(issue.original)}</mark>`
    html = replaceTextInHtml(html, issue.original, markHtml)
  }
  return sanitizeDocumentHtml(html)
})

// HTML 工具函数：仅在文本节点中替换，跳过 HTML 标签
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function replaceTextInHtml(html: string, searchPlain: string, replacementHtml: string): string {
  const search = escapeHtml(searchPlain)
  let replaced = false
  return html.replace(/(<[^>]*>)|([^<]+)/g, (match: string, tag: string, text: string) => {
    if (tag || replaced) return match
    if (text && text.includes(search)) {
      replaced = true
      return text.replace(search, replacementHtml)
    }
    return match
  })
}

// 辅助函数
function getGlobalIndex(issue: IssueWithStatus): number {
  return issues.value.indexOf(issue)
}

function severityHighlight(severity: string): string {
  switch (severity) {
    case 'error': return '#fee2e2'  // 柔和红
    case 'warning': return '#fef3c7' // 柔和琥珀
    default: return '#dbeafe'        // 柔和蓝
  }
}

function severityColor(severity: string): 'danger' | 'warning' | 'info' {
  switch (severity) {
    case 'error': return 'danger'
    case 'warning': return 'warning'
    default: return 'info'
  }
}

function severityLabel(severity: string): string {
  switch (severity) {
    case 'error': return '错误'
    case 'warning': return '警告'
    default: return '建议'
  }
}

function typeLabel(type: string): string {
  const map: Record<string, string> = {
    typo: '错别字', grammar: '语法', punctuation: '标点',
    style: '表达', sensitive: '敏感词', logic: '逻辑',
  }
  return map[type] || type
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / 1024 / 1024).toFixed(1) + ' MB'
}

function handleFileChange(file: any) {
  selectedFile.value = file.raw
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
  configId?: number
  accessToken?: string
  originalText: string
  currentHtml: string
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

function isAbortError(error: any): boolean {
  return error?.name === 'AbortError' || error?.code === 'ERR_CANCELED'
}

function createIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

async function loadModelOptions() {
  try {
    const res = await getAvailableModelsApi()
    modelOptions.value = res.models
    if (selectedModelId.value === null) {
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
  if (status.message) processingInfo.value = status.message
}

function completeTask(taskResult: TaskStatus, fallbackFilename: string, runId: number) {
  if (!isCurrentRun(runId)) return

  uploading.value = false
  proofreading.value = false
  cancelling.value = false
  clearSnapshot()
  currentTaskId.value = ''
  accessToken.value = ''

  if (taskResult.status !== 'SUCCESS') {
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

  const result = taskResult.result as any
  if (!result || !result.issues) {
    step.value = 'upload'
    stepKey.value = 'upload'
    progress.value = 0
    processingInfo.value = ''
    invalidateTaskRun()
    ElMessage.error('校对结果格式异常，请重试')
    return
  }

  const proofreadRes: DocumentProofreadResponse = {
    filename: result.filename || fallbackFilename,
    issues: result.issues,
    total_issues: result.total_issues ?? result.issues.length,
    corrected_download_url: result.corrected_download_url || '',
    record_id: result.record_id ?? undefined,
  } as DocumentProofreadResponse

  recordId.value = proofreadRes.record_id ?? null
  resultFilename.value = proofreadRes.filename
  correctedDownloadUrl.value = proofreadRes.corrected_download_url || ''
  issues.value = proofreadRes.issues.map((issue: any) => ({
    ...issue,
    _accepted: false,
    _ignored: false,
  }))
  progress.value = 100
  stepKey.value = 'save'
  step.value = 'result'
  invalidateTaskRun()

  if (proofreadRes.total_issues === 0) {
    ElMessage.success('文档没有发现任何问题')
  } else {
    ElMessage.info(`共发现 ${proofreadRes.total_issues} 个问题，请逐条审阅`)
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
    completeTask(taskResult, snapshot.filename, runId)
  } catch (error: any) {
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

  originalText.value = snapshot.originalText || ''
  currentText.value = snapshot.originalText || ''
  currentHtml.value = snapshot.currentHtml || ''
  domain.value = snapshot.domain || 'general'
  if (snapshot.configId !== undefined) selectedModelId.value = snapshot.configId
  resultFilename.value = snapshot.filename || ''
  accessToken.value = snapshot.accessToken || ''
  currentTaskId.value = snapshot.taskId || ''
  step.value = 'processing'
  stepKey.value = 'proofread'
  proofreading.value = true
  progress.value = 50

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
  } catch (error: any) {
    if (!isCurrentRun(runId) || isAbortError(error)) return
    processingInfo.value = '任务提交状态暂未确认，刷新页面后将自动重试'
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

    const uploadRes = await uploadDocumentApi(selectedFile.value, abortController.value?.signal)
    if (!isCurrentRun(runId)) return

    uploading.value = false
    proofreading.value = true
    progress.value = 50
    stepKey.value = 'proofread'
    processingInfo.value = `文本提取完成，共 ${uploadRes.text_length} 字，正在提交校对任务...`
    originalText.value = uploadRes.extracted_text
    currentText.value = uploadRes.extracted_text
    currentHtml.value = uploadRes.extracted_html || ''

    snapshot = {
      idempotencyKey: createIdempotencyKey(),
      fileId: uploadRes.file_id,
      filename: uploadRes.filename,
      domain: domain.value,
      configId: selectedModelId.value ?? undefined,
      originalText: uploadRes.extracted_text,
      currentHtml: uploadRes.extracted_html || '',
    }
    saveSnapshot(snapshot)
    await submitTaskSnapshot(snapshot, runId)
  } catch (error: any) {
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

// 审校建议反馈上报（fire-and-forget：失败不打扰用户）
function reportFeedback(items: IssueWithStatus[], action: 'accept' | 'ignore') {
  if (!items.length) return
  submitIssueFeedbackApi({
    record_id: recordId.value ?? undefined,
    items: items.map(i => ({
      original: i.original,
      suggestion: i.suggestion || undefined,
      issue_type: i.type || undefined,
      action,
    })),
  }).catch(() => {})
}

// 接受单条修改
function acceptIssue(issue: IssueWithStatus) {
  if (issue.original && issue.suggestion) {
    currentText.value = currentText.value.replace(issue.original, issue.suggestion)
    if (currentHtml.value) {
      currentHtml.value = replaceTextInHtml(currentHtml.value, issue.original, escapeHtml(issue.suggestion))
    }
  }
  issue._accepted = true
  reportFeedback([issue], 'accept')
}

// 忽略
function ignoreIssue(issue: IssueWithStatus) {
  issue._ignored = true
  reportFeedback([issue], 'ignore')
}

// 删除敏感词（连同紧邻标点）
function deleteIssue(issue: IssueWithStatus) {
  const word = issue.original
  const wordIdx = currentText.value.indexOf(word)
  if (wordIdx < 0) return
  const nextChar = currentText.value[wordIdx + word.length]
  const punct = '，。！？；、,'
  const target = nextChar && punct.includes(nextChar) ? word + nextChar : word
  currentText.value = currentText.value.replace(target, '')
  if (currentHtml.value) {
    currentHtml.value = currentHtml.value.replace(target, '')
  }
  issue._accepted = true
  issue._deletedText = target
  issue._undoAnchor = wordIdx
  reportFeedback([issue], 'accept')
}

// 撤销
function undoIssue(issue: IssueWithStatus) {
  if (issue._accepted && issue.original && issue.suggestion) {
    currentText.value = currentText.value.replace(issue.suggestion, issue.original)
    if (currentHtml.value) {
      currentHtml.value = replaceTextInHtml(currentHtml.value, issue.suggestion, escapeHtml(issue.original))
    }
  } else if (issue._accepted && (issue as any)._deletedText !== undefined) {
    const deleted = (issue as any)._deletedText as string
    const anchor = Math.min((issue as any)._undoAnchor ?? 0, currentText.value.length)
    currentText.value = currentText.value.slice(0, anchor) + deleted + currentText.value.slice(anchor)
    ;(issue as any)._deletedText = undefined
    ;(issue as any)._undoAnchor = undefined
  }
  issue._accepted = false
  issue._ignored = false
}

// 一键修改全部
async function handleAcceptAll() {
  // 与单条操作语义一致：有建议的替换 + 敏感词删除
  const actionable = issues.value.filter(i =>
    !i._accepted && !i._ignored && i.original && (i.suggestion || i.type === 'sensitive')
  )
  try {
    await ElMessageBox.confirm(
      `确认接受全部 ${actionable.length} 条修改建议？`,
      '一键修改',
      { confirmButtonText: '确认', cancelButtonText: '取消', type: 'warning' }
    )
    const accepted: IssueWithStatus[] = []
    for (const issue of actionable) {
      if (issue.suggestion) {
        currentText.value = currentText.value.replace(issue.original, issue.suggestion)
        if (currentHtml.value) {
          currentHtml.value = replaceTextInHtml(currentHtml.value, issue.original, escapeHtml(issue.suggestion))
        }
      } else {
        const wordIdx = currentText.value.indexOf(issue.original)
        if (wordIdx < 0) continue
        const nextChar = currentText.value[wordIdx + issue.original.length]
        const punct = '，。！？；、,'
        const target = nextChar && punct.includes(nextChar) ? issue.original + nextChar : issue.original
        currentText.value = currentText.value.replace(target, '')
        if (currentHtml.value) {
          currentHtml.value = currentHtml.value.replace(target, '')
        }
        issue._deletedText = target
        issue._undoAnchor = wordIdx
      }
      issue._accepted = true
      accepted.push(issue)
    }
    reportFeedback(accepted, 'accept')
    ElMessage.success('已接受所有修改')
  } catch {
    // 取消
  }
}

// 下载修订文档
function downloadCorrected() {
  if (correctedDownloadUrl.value) {
    window.open(correctedDownloadUrl.value, '_blank')
  }
}

// 导出修订文本
function handleExportText() {
  const blob = new Blob([currentText.value], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `修订_${resultFilename.value || 'document'}.txt`
  a.click()
  URL.revokeObjectURL(url)
  ElMessage.success('修订文本已导出')
}

// 导出问题报告
function handleExportReport() {
  if (issues.value.length === 0) return
  const lines = [
    `文档校对报告 - ${resultFilename.value}`,
    `共发现 ${issues.value.length} 个问题`,
    `已接受: ${acceptedCount.value}  已忽略: ${issues.value.filter(i => i._ignored).length}  待处理: ${pendingCount.value}`,
    '',
  ]
  issues.value.forEach((issue, i) => {
    const status = issue._accepted ? '[已接受]' : issue._ignored ? '[已忽略]' : '[待处理]'
    lines.push(`${i + 1}. ${status} [${typeLabel(issue.type)}] ${severityLabel(issue.severity)}`)
    lines.push(`   原文: ${issue.original}`)
    lines.push(`   建议: ${issue.suggestion}`)
    if (issue.explanation) lines.push(`   说明: ${issue.explanation}`)
    lines.push('')
  })
  const blob = new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `校对报告_${resultFilename.value || 'document'}.txt`
  a.click()
  URL.revokeObjectURL(url)
  ElMessage.success('报告已导出')
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
  currentText.value = ''
  currentHtml.value = ''
  resultFilename.value = ''
  correctedDownloadUrl.value = ''
  issues.value = []
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

.original-text {
  font-size: 14px;
  line-height: 1.8;
  color: var(--color-text);
  word-break: break-all;

  :deep(p) {
    margin: 0.3em 0;
  }

  :deep(h1), :deep(h2), :deep(h3), :deep(h4), :deep(h5), :deep(h6) {
    margin: 0.5em 0 0.3em;
    font-weight: 600;
  }

  :deep(h1) { font-size: 22pt; }
  :deep(h2) { font-size: 18pt; }
  :deep(h3) { font-size: 14pt; }
  :deep(h4) { font-size: 12pt; }

  :deep(table) {
    border-collapse: collapse;
    width: 100%;
    margin: 8px 0;
  }

  :deep(td), :deep(th) {
    border: 1px solid #ccc;
    padding: 6px 8px;
  }

  :deep(strong) { font-weight: 700; }
  :deep(em) { font-style: italic; }
  :deep(u) { text-decoration: underline; }
  :deep(s) { text-decoration: line-through; }
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
</style>
