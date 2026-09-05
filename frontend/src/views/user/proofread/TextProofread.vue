<template>
  <div class="text-proofread-page">
    <!-- 输入区域 -->
    <div v-if="!showResult" class="input-section">
      <el-card class="input-card">
        <template #header>
          <div class="card-header">
            <span class="card-title">文本在线校对</span>
            <el-tag type="primary" effect="plain" size="small">粘贴或输入文本，AI 智能审校</el-tag>
          </div>
        </template>

        <!-- 文本输入 -->
        <div class="editor-wrapper">
          <el-input
            v-model="inputText"
            type="textarea"
            :rows="10"
            placeholder="请在此粘贴或输入需要校对的文本内容..."
            resize="vertical"
            maxlength="100000"
            show-word-limit
          />
        </div>

        <!-- 校对设置 -->
        <div class="proofread-settings">
          <div class="setting-row">
            <span class="setting-label">领域选择：</span>
            <el-radio-group v-model="domain" class="setting-value">
              <el-radio value="general">通用</el-radio>
              <el-radio value="official">公文</el-radio>
              <el-radio value="legal">法律</el-radio>
              <el-radio value="power">电力</el-radio>
              <el-radio value="new_energy">新能源</el-radio>
              <el-radio value="meter">电能表</el-radio>
            </el-radio-group>
          </div>
          <div v-if="modelOptions.length > 1" class="setting-row">
            <span class="setting-label">校对模型：</span>
            <el-checkbox v-model="compareMode" size="small" style="margin-right: 10px;">多模型对比</el-checkbox>
            <template v-if="compareMode">
              <el-select
                v-model="compareModelIds"
                multiple
                collapse-tags
                size="default"
                class="setting-value"
                style="max-width: 420px;"
                placeholder="选择 2-4 个模型并发校对"
              >
                <el-option
                  v-for="m in modelOptions"
                  :key="m.id"
                  :label="`${m.name}（${m.model}）`"
                  :value="m.id"
                />
              </el-select>
            </template>
            <el-select
              v-else
              v-model="selectedModelId"
              size="default"
              class="setting-value"
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
        </div>

        <!-- 操作按钮 -->
        <div class="action-bar">
          <el-button
            type="primary"
            size="large"
            :loading="loading"
            :disabled="!inputText.trim() || (compareMode && !canCompare)"
            @click="handleProofread"
          >
            <el-icon><Edit /></el-icon>
            {{ loading ? '校对中...' : (compareMode ? '开始对比校对' : '开始校对') }}
          </el-button>
          <el-button size="large" @click="inputText = ''">清空</el-button>
          <span class="text-count">{{ inputText.length }} 字</span>
        </div>
      </el-card>
    </div>

    <!-- 结果区域 -->
    <div v-else class="result-section">
      <!-- ===== 多模型对比视图 ===== -->
      <template v-if="compareResult">
        <div class="result-toolbar">
          <el-button @click="goBack">
            <el-icon><Back /></el-icon>返回编辑
          </el-button>
          <div class="toolbar-info">
            <el-tag type="success">共识问题 {{ compareResult.consensus_originals.length }} 个</el-tag>
            <el-tag type="info">领域：{{ domainLabel }}</el-tag>
            <el-tag type="warning">已接受 {{ compareAcceptedCount }} 条</el-tag>
          </div>
          <div class="toolbar-actions">
            <el-button type="warning" @click="handleCompareAcceptAll" :disabled="comparePendingCount === 0">
              一键接受全部
            </el-button>
            <el-button @click="handleCopy">复制结果</el-button>
            <el-dropdown @command="handleExport">
              <el-button type="primary">
                导出<el-icon class="el-icon--right"><ArrowDown /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="text">修改后全文（TXT）</el-dropdown-item>
                  <el-dropdown-item command="report">问题报告（TXT，含原文对照）</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </div>

        <!-- 修改结果预览 -->
        <el-card class="compare-card" style="margin-bottom: 14px;">
          <template #header>
            <div class="column-header">
              <span class="column-title"><el-icon><Tickets /></el-icon>修改后全文（实时预览）</span>
              <span class="text-count">{{ currentText.length }} 字</span>
            </div>
          </template>
          <div class="compare-text-preview">{{ currentText }}</div>
        </el-card>

        <!-- 各模型结果标签页 -->
        <el-card class="compare-card">
          <el-tabs v-model="activeCompareTab">
            <!-- ===== 汇总建议（综合多模型意见） ===== -->
            <el-tab-pane name="__summary__">
              <template #label>
                <span style="font-weight: 600;">综合建议</span>
                <el-tag type="danger" size="small" style="margin-left: 6px;">{{ summaryStats.total }}</el-tag>
              </template>

              <!-- 统计卡 -->
              <div class="summary-stats">
                <div class="stat-item">
                  <div class="stat-num is-consensus">{{ summaryStats.consensus }}</div>
                  <div class="stat-label">多模型一致</div>
                </div>
                <div class="stat-item">
                  <div class="stat-num is-unique">{{ summaryStats.unique }}</div>
                  <div class="stat-label">单模型发现（待把关）</div>
                </div>
                <div class="stat-item">
                  <div class="stat-num is-accepted">{{ compareAcceptedCount }}</div>
                  <div class="stat-label">已接受</div>
                </div>
                <div class="stat-item" v-for="r in summaryStats.models" :key="r.id">
                  <div class="stat-num">{{ r.count }}</div>
                  <div class="stat-label">{{ r.name }}</div>
                </div>
              </div>

              <!-- 分级接受策略 -->
              <div class="summary-strategy">
                <span class="strategy-label">一键应用：</span>
                <el-button size="small" type="success" plain @click="handleAcceptByStrategy('consensus')" :disabled="summaryStats.consensusPending === 0">
                  只接受多模型一致（{{ summaryStats.consensusPending }} 条）
                </el-button>
                <el-button size="small" type="warning" plain @click="handleAcceptByStrategy('high')" :disabled="summaryStats.highPending === 0">
                  一致 + 高严重度独有（{{ summaryStats.highPending }} 条）
                </el-button>
                <el-button size="small" type="danger" plain @click="handleAcceptByStrategy('all')" :disabled="comparePendingCount === 0">
                  全部（{{ comparePendingCount }} 条）
                </el-button>
              </div>

              <!-- 汇总问题列表：共识在前，独有按模型数×严重度排序 -->
              <div class="compare-issues">
                <div
                  v-for="(item, i) in summaryIssues"
                  :key="i"
                  class="compare-issue-item"
                  :class="{ 'is-consensus': item.isConsensus, 'is-accepted': item.issue._accepted, 'is-ignored': item.issue._ignored }"
                >
                  <div class="issue-head">
                    <el-tag :type="severityColor(item.issue.severity)" size="small">{{ typeLabel(item.issue.type) }}</el-tag>
                    <el-tag :type="item.isConsensus ? 'success' : 'warning'" size="small" effect="plain">
                      {{ item.isConsensus ? `${item.modelCount} 个模型一致` : '仅 1 个模型发现' }}
                    </el-tag>
                    <span class="issue-source">{{ item.sources }}</span>
                  </div>
                  <div class="issue-body">
                    <div><span class="label">原文：</span><span class="text-del">{{ item.issue.original }}</span></div>
                    <div><span class="label">建议：</span><span class="text-add">{{ item.issue.suggestion }}</span></div>
                    <div v-if="item.issue.explanation"><span class="label">说明：</span><span class="text-muted">{{ item.issue.explanation }}</span></div>
                  </div>
                  <div class="issue-actions" v-if="!item.issue._accepted && !item.issue._ignored">
                    <el-button type="primary" size="small" @click="acceptCompareIssue(item.issue)">
                      <el-icon><Check /></el-icon>接受修改
                    </el-button>
                    <el-button size="small" @click="item.issue._ignored = true; syncCompareIssueState(item.issue)">
                      <el-icon><Close /></el-icon>忽略
                    </el-button>
                  </div>
                  <div class="issue-status" v-else>
                    <el-tag v-if="item.issue._accepted" type="success" size="small">已接受</el-tag>
                    <el-tag v-if="item.issue._ignored" type="info" size="small">已忽略</el-tag>
                    <el-button text size="small" @click="undoCompareIssue(item.issue)">撤销</el-button>
                  </div>
                </div>
                <el-empty v-if="summaryIssues.length === 0" description="没有发现任何问题" :image-size="60" />
              </div>
            </el-tab-pane>

            <!-- ===== 各模型明细 ===== -->
            <el-tab-pane
              v-for="r in compareResult.results"
              :key="r.config_id"
              :name="String(r.config_id)"
            >
              <template #label>
                <span>{{ r.config_name }}</span>
                <el-tag
                  :type="r.success ? (comparePendingCountOf(r) > 0 ? 'danger' : 'success') : 'info'"
                  size="small"
                  style="margin-left: 6px;"
                >{{ r.success ? comparePendingCountOf(r) : '失败' }}</el-tag>
              </template>

              <div v-if="!r.success" class="compare-error">
                <el-alert type="error" :closable="false" show-icon :title="`校对失败：${r.error || '未知错误'}`" />
              </div>
              <template v-else>
                <div class="compare-meta">
                  <el-tag type="info" effect="plain" size="small">模型：{{ r.model }}</el-tag>
                  <el-tag type="info" effect="plain" size="small">耗时 {{ (r.elapsed_ms / 1000).toFixed(1) }}s</el-tag>
                  <el-tag type="success" effect="plain" size="small">
                    独有 {{ (compareResult.only_in[String(r.config_id)] || []).length }} 个
                  </el-tag>
                </div>
                <div class="compare-issues">
                  <div
                    v-for="(issue, i) in r.issues"
                    :key="i"
                    class="compare-issue-item"
                    :class="{ 'is-consensus': compareResult.consensus_originals.includes(issue.original), 'is-accepted': issue._accepted, 'is-ignored': issue._ignored }"
                  >
                    <div class="issue-head">
                      <el-tag :type="severityColor(issue.severity)" size="small">{{ typeLabel(issue.type) }}</el-tag>
                      <el-tag
                        :type="compareResult.consensus_originals.includes(issue.original) ? 'success' : 'warning'"
                        size="small" effect="plain"
                      >
                        {{ compareResult.consensus_originals.includes(issue.original) ? '共识' : '独有' }}
                      </el-tag>
                    </div>
                    <div class="issue-body">
                      <div><span class="label">原文：</span><span class="text-del">{{ issue.original }}</span></div>
                      <div><span class="label">建议：</span><span class="text-add">{{ issue.suggestion }}</span></div>
                      <div v-if="issue.explanation"><span class="label">说明：</span><span class="text-muted">{{ issue.explanation }}</span></div>
                    </div>
                    <div class="issue-actions" v-if="!issue._accepted && !issue._ignored">
                      <el-button type="primary" size="small" @click="acceptCompareIssue(issue)">
                        <el-icon><Check /></el-icon>接受修改
                      </el-button>
                      <el-button size="small" @click="issue._ignored = true">
                        <el-icon><Close /></el-icon>忽略
                      </el-button>
                    </div>
                    <div class="issue-status" v-else>
                      <el-tag v-if="issue._accepted" type="success" size="small">已接受</el-tag>
                      <el-tag v-if="issue._ignored" type="info" size="small">已忽略</el-tag>
                      <el-button text size="small" @click="undoCompareIssue(issue)">撤销</el-button>
                    </div>
                  </div>
                  <el-empty v-if="r.issues.length === 0" description="该模型未发现问题" :image-size="60" />
                </div>
              </template>
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
          <el-tag type="success">共发现 {{ issues.length }} 个问题</el-tag>
          <el-tag type="info">领域：{{ domainLabel }}</el-tag>
        </div>
        <div class="toolbar-actions">
          <el-button type="warning" @click="handleAcceptAll" :disabled="issues.length === 0">
            一键修改全部
          </el-button>
          <el-button @click="handleCopy">复制结果</el-button>
          <el-dropdown @command="handleExport">
            <el-button type="primary">
              导出<el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="text">修改后全文（TXT）</el-dropdown-item>
                <el-dropdown-item command="report">问题报告（TXT，含原文对照）</el-dropdown-item>
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
                原文对照
              </span>
              <span class="text-count">{{ currentText.length }} 字</span>
            </div>
          </template>
          <div class="original-text" v-html="highlightedText"></div>
        </el-card>

        <!-- 右栏：问题列表 -->
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
                <el-tag :type="severityColor(issue.severity)" size="small" effect="dark">
                  {{ typeLabel(issue.type) }}
                </el-tag>
                <el-tag :type="severityTagType(issue.severity)" size="small" effect="plain">
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
                <el-button type="primary" size="small" @click="acceptIssue(index)">
                  <el-icon><Check /></el-icon>接受修改
                </el-button>
                <el-button size="small" @click="ignoreIssue(index)">
                  <el-icon><Close /></el-icon>忽略
                </el-button>
              </div>
              <div class="issue-status" v-else>
                <el-tag v-if="issue._accepted" type="success" size="small">已接受</el-tag>
                <el-tag v-if="issue._ignored" type="info" size="small">已忽略</el-tag>
                <el-button text size="small" @click="undoIssue(index)">撤销</el-button>
              </div>
            </div>
            <el-empty v-if="filteredIssues.length === 0" description="没有发现问题" />
          </div>
        </el-card>
      </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { textProofreadApi, proofreadCompareApi, type ProofreadIssue, type ProofreadCompareResponse } from '@/api/proofread'
import { getAvailableModelsApi, type AvailableModel } from '@/api/polish'

interface IssueWithStatus extends ProofreadIssue {
  _accepted?: boolean
  _ignored?: boolean
}

// 状态
const inputText = ref('')
const loading = ref(false)
const showResult = ref(false)
const issues = ref<IssueWithStatus[]>([])
const currentText = ref('')
const filterType = ref('')
const activeIssueIndex = ref(-1)

// 从校对历史「重新校对」带入的原文
onMounted(() => {
  const rerunText = sessionStorage.getItem('tm_rerun_text')
  if (rerunText) {
    inputText.value = rerunText
    sessionStorage.removeItem('tm_rerun_text')
  }
})

// 设置
const domain = ref('general')

// 校对模型选择（默认当前活跃模型）
const modelOptions = ref<AvailableModel[]>([])
const selectedModelId = ref<number | null>(null)

// 多模型对比
const compareMode = ref(false)
const compareModelIds = ref<number[]>([])
const compareResult = ref<ProofreadCompareResponse | null>(null)
const activeCompareTab = ref('')
const canCompare = computed(() => compareModelIds.value.length >= 2)

/** 对比视图：所有模型问题的扁平列表（用于统计与批量操作；共识问题只计一次） */
const compareAllIssues = computed(() => {
  if (!compareResult.value) return []
  const seen = new Set<string>()
  const list: any[] = []
  for (const r of compareResult.value.results) {
    if (!r.success) continue
    for (const issue of r.issues) {
      const key = (issue.original || '').trim()
      // 同一原文多个模型都报：操作绑定第一个出现的对象，其余跟随其状态
      if (seen.has(key)) continue
      seen.add(key)
      list.push(issue)
    }
  }
  return list
})

/** 汇总建议：每个问题聚合各模型意见（发现该问题的模型名列表 + 排序） */
const summaryIssues = computed(() => {
  if (!compareResult.value) return []
  const okModels = compareResult.value.results.filter(r => r.success)
  const map = new Map<string, { issue: any; models: string[]; isConsensus: boolean }>()
  for (const r of okModels) {
    for (const issue of r.issues) {
      const key = (issue.original || '').trim()
      if (!key) continue
      if (!map.has(key)) {
        map.set(key, { issue, models: [], isConsensus: false })
      }
      const entry = map.get(key)!
      if (!entry.models.includes(r.config_name)) entry.models.push(r.config_name)
    }
  }
  const total = okModels.length
  const severityOrder: Record<string, number> = { error: 3, warning: 2, info: 1 }
  const items = [...map.values()].map(e => ({
    issue: e.issue,
    modelCount: e.models.length,
    isConsensus: total >= 2 && e.models.length >= 2,
    sources: e.models.join(' / '),
  }))
  // 排序：共识在前；同组内按 模型数 desc → 严重度 desc
  items.sort((a, b) => {
    if (a.isConsensus !== b.isConsensus) return a.isConsensus ? -1 : 1
    if (a.modelCount !== b.modelCount) return b.modelCount - a.modelCount
    return (severityOrder[b.issue.severity] || 0) - (severityOrder[a.issue.severity] || 0)
  })
  return items
})

const summaryStats = computed(() => {
  const items = summaryIssues.value
  const consensus = items.filter(i => i.isConsensus)
  const unique = items.filter(i => !i.isConsensus)
  const highUnique = unique.filter(i => i.issue.severity === 'error')
  const models: { id: number; name: string; count: number }[] = []
  if (compareResult.value) {
    for (const r of compareResult.value.results) {
      if (r.success) models.push({ id: r.config_id, name: r.config_name, count: r.total_issues })
    }
  }
  return {
    total: items.length,
    consensus: consensus.length,
    unique: unique.length,
    consensusPending: consensus.filter(i => !i.issue._accepted && !i.issue._ignored).length,
    highPending: [...consensus, ...highUnique].filter(i => !i.issue._accepted && !i.issue._ignored).length,
    models,
  }
})

/** 分级一键接受：consensus=仅共识；high=共识+高严重度独有；all=全部 */
async function handleAcceptByStrategy(level: 'consensus' | 'high' | 'all') {
  let targets = summaryIssues.value.filter(i => !i.issue._accepted && !i.issue._ignored && i.issue.original && i.issue.suggestion)
  if (level === 'consensus') targets = targets.filter(i => i.isConsensus)
  else if (level === 'high') targets = targets.filter(i => i.isConsensus || i.issue.severity === 'error')
  if (targets.length === 0) return

  const labels = { consensus: '多模型一致', high: '一致 + 高严重度独有', all: '全部' }
  try {
    await ElMessageBox.confirm(
      `确认接受 ${labels[level]}的 ${targets.length} 条修改建议？`,
      '一键应用',
      { confirmButtonText: '确认', cancelButtonText: '取消', type: 'warning' }
    )
    for (const item of targets) {
      currentText.value = currentText.value.replace(item.issue.original, item.issue.suggestion)
      item.issue._accepted = true
      syncCompareIssueState(item.issue)
    }
    ElMessage.success(`已接受 ${targets.length} 条修改`)
  } catch {
    // 取消
  }
}
const compareAcceptedCount = computed(() => compareAllIssues.value.filter(i => i._accepted).length)
const comparePendingCount = computed(() => compareAllIssues.value.filter(i => !i._accepted && !i._ignored).length)
function comparePendingCountOf(r: { issues: any[] }): number {
  return r.issues.filter(i => !i._accepted && !i._ignored).length
}

/** 对比视图：接受单条修改（同步应用到全文预览；共识问题在其他模型页同步状态） */
function acceptCompareIssue(issue: any) {
  if (issue.original && issue.suggestion) {
    currentText.value = currentText.value.replace(issue.original, issue.suggestion)
  }
  issue._accepted = true
  syncCompareIssueState(issue)
}

/** 同一原文在多个模型结果里出现时，保持状态一致 */
function syncCompareIssueState(source: any) {
  if (!compareResult.value) return
  const key = (source.original || '').trim()
  for (const r of compareResult.value.results) {
    for (const issue of r.issues) {
      if ((issue.original || '').trim() === key) {
        issue._accepted = source._accepted
        issue._ignored = source._ignored
      }
    }
  }
}

/** 对比视图：撤销单条 */
function undoCompareIssue(issue: any) {
  if (issue._accepted && issue.original && issue.suggestion) {
    currentText.value = currentText.value.replace(issue.suggestion, issue.original)
  }
  issue._accepted = false
  issue._ignored = false
  syncCompareIssueState(issue)
}

/** 对比视图：一键接受全部待处理问题 */
async function handleCompareAcceptAll() {
  const pending = compareAllIssues.value.filter(i => !i._accepted && !i._ignored && i.original && i.suggestion)
  if (pending.length === 0) return
  try {
    await ElMessageBox.confirm(
      `确认接受全部 ${pending.length} 条修改建议？`,
      '一键修改',
      { confirmButtonText: '确认', cancelButtonText: '取消', type: 'warning' }
    )
    for (const issue of pending) {
      currentText.value = currentText.value.replace(issue.original, issue.suggestion)
      issue._accepted = true
      syncCompareIssueState(issue)
    }
    ElMessage.success('已接受所有修改')
  } catch {
    // 取消
  }
}

onMounted(async () => {
  try {
    const res = await getAvailableModelsApi()
    modelOptions.value = res.models
    const active = res.models.find(m => m.is_active)
    selectedModelId.value = active ? active.id : (res.models[0]?.id ?? null)
  } catch { /* 模型列表加载失败时用默认活跃模型 */ }
})

// 领域标签
const domainLabel = computed(() => {
  const map: Record<string, string> = {
    general: '通用', official: '公文', legal: '法律',
    power: '电力', new_energy: '新能源', meter: '电能表',
  }
  return map[domain.value] || '通用'
})

// 筛选后的问题列表
const filteredIssues = computed(() => {
  if (!filterType.value) return issues.value
  return issues.value.filter(i => i.type === filterType.value)
})

// 转义 HTML，避免原文中含有 < > 等字符破坏 DOM
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

// 获取问题在全局列表中的索引（用于联动高亮）
function getGlobalIndex(issue: IssueWithStatus): number {
  return issues.value.indexOf(issue)
}

// 高亮原文（支持鼠标悬停联动）
const highlightedText = computed(() => {
  let text = escapeHtml(currentText.value)
  // 按原文片段进行高亮标记
  const activeIssues = issues.value.filter(i => !i._accepted && !i._ignored)
  for (const issue of activeIssues) {
    if (!issue.original) continue
    const globalIdx = issues.value.indexOf(issue)
    const escaped = escapeHtml(issue.original)
    if (!text.includes(escaped)) continue
    const isHover = activeIssueIndex.value === globalIdx
    const color = isHover ? '#fef3c7' : severityHighlight(issue.severity)
    const border = isHover ? 'box-shadow:0 0 0 2px #f59e0b;font-weight:600;' : ''
    const mark = `<mark data-issue-idx="${globalIdx}" style="background:${color};padding:2px 3px;border-radius:3px;cursor:pointer;transition:all .2s;${border}" title="[${typeLabel(issue.type)}] ${escapeHtml(issue.suggestion)}">${escaped}</mark>`
    text = text.replace(escaped, mark)
  }
  return text.replace(/\n/g, '<br/>')
})

function severityHighlight(severity: string): string {
  switch (severity) {
    case 'error': return '#fee2e2'  // 柔和红
    case 'warning': return '#fef3c7' // 柔和琥珀
    default: return '#dbeafe'        // 柔和蓝
  }
}

function severityColor(severity: string): 'danger' | 'warning' | 'info' | 'success' | 'primary' {
  switch (severity) {
    case 'error': return 'danger'
    case 'warning': return 'warning'
    default: return 'info'
  }
}

function severityTagType(severity: string): 'danger' | 'warning' | 'info' | 'success' | 'primary' {
  return severityColor(severity)
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

// 开始校对
async function handleProofread() {
  if (!inputText.value.trim()) return
  // 多模型对比模式
  if (compareMode.value) {
    if (!canCompare.value) return ElMessage.warning('请至少选择 2 个模型')
    loading.value = true
    compareResult.value = null
    try {
      compareResult.value = await proofreadCompareApi({
        text: inputText.value,
        domain: domain.value,
        config_ids: compareModelIds.value,
      })
      activeCompareTab.value = '__summary__'   // 默认展示综合建议
      currentText.value = inputText.value   // 供接受修改/复制/导出使用
      showResult.value = true
      const okCount = compareResult.value.results.filter(r => r.success).length
      ElMessage.success(`${okCount}/${compareResult.value.results.length} 个模型校对完成`)
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
      text: inputText.value,
      domain: domain.value,
      config_id: selectedModelId.value ?? undefined,
    })
    issues.value = res.issues.map(i => ({ ...i, _accepted: false, _ignored: false }))
    currentText.value = inputText.value
    showResult.value = true
    if (res.total_issues === 0) {
      ElMessage.success('太棒了！文本没有发现任何问题')
    } else {
      ElMessage.info(`共发现 ${res.total_issues} 个问题`)
    }
  } catch (e: any) {
    // 错误已在拦截器中处理
  } finally {
    loading.value = false
  }
}

// 接受单条修改
function acceptIssue(index: number) {
  const issue = filteredIssues.value[index]
  if (issue.original && issue.suggestion) {
    currentText.value = currentText.value.replace(issue.original, issue.suggestion)
  }
  issue._accepted = true
}

// 忽略
function ignoreIssue(index: number) {
  filteredIssues.value[index]._ignored = true
}

// 撤销
function undoIssue(index: number) {
  const issue = filteredIssues.value[index]
  if (issue._accepted && issue.original && issue.suggestion) {
    currentText.value = currentText.value.replace(issue.suggestion, issue.original)
  }
  issue._accepted = false
  issue._ignored = false
}

// 一键修改全部
async function handleAcceptAll() {
  try {
    await ElMessageBox.confirm(
      `确认接受全部 ${issues.value.filter(i => !i._accepted && !i._ignored).length} 条修改建议？`,
      '一键修改',
      { confirmButtonText: '确认', cancelButtonText: '取消', type: 'warning' }
    )
    for (const issue of issues.value) {
      if (!issue._accepted && !issue._ignored && issue.original && issue.suggestion) {
        currentText.value = currentText.value.replace(issue.original, issue.suggestion)
        issue._accepted = true
      }
    }
    ElMessage.success('已接受所有修改')
  } catch {
    // 取消
  }
}

// 复制结果
function handleCopy() {
  navigator.clipboard.writeText(currentText.value)
  ElMessage.success('已复制到剪贴板')
}

// 导出：text=修改后全文；report=问题报告
function handleExport(kind: string) {
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
      `问题总数：${issues.value.length}（已采纳 ${accepted} / 已忽略 ${ignored} / 待处理 ${issues.value.length - accepted - ignored}）`,
      '',
      '='.repeat(50),
      '',
    ]
    issues.value.forEach((issue, idx) => {
      const status = issue._accepted ? '已采纳' : issue._ignored ? '已忽略' : '待处理'
      lines.push(`【${idx + 1}】${typeLabel(issue.type)}｜${severityLabel(issue.severity)}｜${status}`)
      lines.push(`原文：${issue.original}`)
      lines.push(`建议：${issue.suggestion}`)
      if (issue.explanation) lines.push(`说明：${issue.explanation}`)
      lines.push('')
    })
    lines.push('='.repeat(50), '', '【修改后全文】', currentText.value)
    content = lines.join('\n')
    filename = `校对报告_${dateStr}.txt`
  } else {
    content = currentText.value
    filename = `校对结果_${dateStr}.txt`
  }

  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
  ElMessage.success('导出成功')
}

// 返回编辑
function goBack() {
  showResult.value = false
  compareResult.value = null
}
</script>

<style scoped lang="scss">
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

.proofread-settings {
  padding: 16px;
  background: var(--surface-soft);
  border: 1px solid #edf1f6;
  border-radius: 12px;
  margin-bottom: 16px;

  .setting-row {
    display: flex;
    align-items: center;
    margin-bottom: 12px;

    &:last-child {
      margin-bottom: 0;
    }

    .setting-label {
      width: 80px;
      font-weight: 500;
      color: var(--color-text);
      flex-shrink: 0;
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
    padding: 12px;

    .setting-row {
      flex-direction: column;
      align-items: flex-start;
      gap: 6px;

      .setting-label {
        width: auto;
      }
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

/* ===== 多模型对比视图 ===== */
.compare-card {
  margin-top: 14px;
}

.summary-stats {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 14px;

  .stat-item {
    min-width: 96px;
    padding: 10px 14px;
    border-radius: 10px;
    background: var(--surface-soft, #f7f9fc);
    border: 1px solid var(--surface-border, #e5ebf3);
    text-align: center;

    .stat-num {
      font-size: 22px;
      font-weight: 700;
      color: var(--color-text, #374151);

      &.is-consensus { color: #3a8a3a; }
      &.is-unique { color: #c04040; }
      &.is-accepted { color: #286dd7; }
    }

    .stat-label {
      margin-top: 2px;
      font-size: 11px;
      color: #8d99a9;
    }
  }
}

.summary-strategy {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 10px 12px;
  margin-bottom: 12px;
  border: 1px dashed var(--surface-border, #e5ebf3);
  border-radius: 10px;
  background: var(--surface-soft, #f7f9fc);

  .strategy-label {
    font-size: 13px;
    color: var(--color-text-secondary, #738197);
    font-weight: 600;
  }
}

.issue-source {
  font-size: 11px;
  color: #8d99a9;
  margin-left: auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 200px;
}

.compare-meta {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.compare-issues {
  max-height: 520px;
  overflow-y: auto;
}

.compare-issue-item {
  padding: 10px 12px;
  margin-bottom: 8px;
  border: 1px solid var(--surface-border, #e5ebf3);
  border-radius: 10px;
  background: var(--surface, #fff);

  &.is-consensus {
    border-color: rgba(103, 194, 58, .45);
    background: rgba(103, 194, 58, .05);
  }

  &.is-accepted {
    border-color: rgba(103, 194, 58, .45);
    opacity: .75;
  }

  &.is-ignored {
    opacity: .5;
  }

  .issue-head {
    display: flex;
    gap: 8px;
    margin-bottom: 6px;
    align-items: center;
  }

  .issue-actions, .issue-status {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 8px;
  }

  .issue-body {
    font-size: 13px;
    line-height: 1.7;

    .label { color: #8d99a9; }
    .text-del { color: #c04040; text-decoration: line-through; }
    .text-add { color: #3a8a3a; font-weight: 600; }
    .text-muted { color: #8d99a9; }
  }
}

.compare-text-preview {
  max-height: 220px;
  overflow-y: auto;
  font-size: 13px;
  line-height: 1.8;
  color: var(--color-text, #374151);
  white-space: pre-wrap;
  padding: 4px 2px;
}

.compare-error {
  padding: 8px 0;
}
</style>
