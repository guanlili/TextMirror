<template>
  <div class="text-proofread-page">
    <CollaborationProgress
      v-if="collaboration || collaborationTaskId"
      :report="collaboration" :task-id="collaborationTaskId" :task-status="collaborationStatus"
      :message="collaborationMessage" :error="collaborationError" :monitoring="collaborationMonitoring"
      :cancelling="collaborationCancelling" :cancel-requested="collaborationCancelRequested" :view-only="!showResult"
      @reconnect="reconnectCollaboration" @cancel="cancelCollaboration" @rerun="rerunCollaboration"
    />
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
            :disabled="controlsLocked"
            type="textarea"
            :rows="10"
            placeholder="请在此粘贴或输入需要校对的文本内容..."
            resize="vertical"
            :maxlength="collaborationMode ? undefined : 100000"
            :show-word-limit="!collaborationMode"
          />
        </div>

        <!-- 校对设置 -->
        <div class="proofread-settings">
          <div class="setting-row">
            <span class="setting-label">审校方式：</span>
            <el-radio-group v-model="proofreadMode" :disabled="controlsLocked" aria-label="审校方式" aria-describedby="proofread-mode-help">
              <el-radio-button value="single">单模型审校</el-radio-button>
              <el-radio-button value="compare" :disabled="modelOptions.length < 2">多模型对比</el-radio-button>
              <el-radio-button value="collaboration">协作审校</el-radio-button>
            </el-radio-group>
            <p id="proofread-mode-help" class="setting-help" aria-live="polite">{{ proofreadModeHints[proofreadMode] }}</p>
          </div>
          <p v-if="collaborationMode" class="collaboration-note">最多 8000 字，一轮最多复核 20 条；每任务一次应用额度，模型用量另计；不联网、不自动采纳。</p>
          <p v-if="collaborationMode && collaborationBlocked" class="collaboration-warning" role="alert">{{ collaborationBlocked }}</p>
          <p v-if="collaborationPending" class="collaboration-warning" role="alert">提交结果尚未确认。请保持此页，使用原参数重试同一请求，避免重复任务；取得任务编号后可通过地址刷新恢复。</p>
          <p v-if="collaborationError && !collaborationTaskId" class="collaboration-warning" role="alert">{{ collaborationError }}</p>
          <div class="setting-row">
            <span class="setting-label">领域选择：</span>
            <el-radio-group v-model="domain" :disabled="controlsLocked" class="setting-value" aria-describedby="proofread-domain-help">
              <el-radio value="auto">自动</el-radio>
              <el-radio value="general">通用</el-radio>
              <el-radio value="official">公文</el-radio>
              <el-radio value="legal">法律</el-radio>
            </el-radio-group>
            <p id="proofread-domain-help" class="setting-help" aria-live="polite">{{ proofreadDomainHints[domain] }}</p>
          </div>
          <div v-if="proofreadMode === 'single'" class="setting-row">
            <span class="setting-label">审校深度：</span>
            <el-radio-group v-model="depth" :disabled="controlsLocked" size="small" aria-describedby="proofread-depth-help">
              <el-radio-button value="quick">快查</el-radio-button>
              <el-radio-button value="standard">标准</el-radio-button>
              <el-radio-button value="deep">深度</el-radio-button>
            </el-radio-group>
            <p id="proofread-depth-help" class="setting-help" aria-live="polite">{{ proofreadDepthHints[depth] }}</p>
          </div>
          <div v-if="modelOptions.length" class="setting-row">
            <span class="setting-label">校对模型：</span>
            <template v-if="compareMode">
              <el-select
                v-model="compareModelIds"
                :disabled="controlsLocked"
                multiple
                collapse-tags
                size="default"
                class="setting-value"
                style="max-width: 420px;"
                placeholder="选择 2-4 个模型并发校对"
                aria-describedby="proofread-model-help"
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
              :disabled="controlsLocked"
              size="default"
              class="setting-value"
              style="max-width: 320px;"
              placeholder="默认当前模型"
              aria-describedby="proofread-model-help"
            >
              <el-option
                v-for="m in modelOptions"
                :key="m.id"
                :label="m.is_active ? `${m.name}（${m.model}）· 当前` : `${m.name}（${m.model}）`"
                :value="m.id"
              />
            </el-select>
            <p id="proofread-model-help" class="setting-help" aria-live="polite">{{ modelHint }}</p>
          </div>
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
            {{ loading ? '校对中...' : collaborationMode ? (collaborationPending ? '重试提交（同一请求）' : '开始协作审校') : (compareMode ? '开始对比校对' : '开始校对') }}
          </el-button>
          <el-button size="large" :disabled="controlsLocked" @click="inputText = ''">清空</el-button>
          <el-button v-if="collaborationTerminal && !showResult" @click="clearCollaborationTask">返回编辑</el-button>
          <span class="text-count">{{ Array.from(inputText).length }}{{ collaborationMode ? ' / 8000' : '' }} 字</span>
        </div>
      </el-card>
    </div>

    <!-- 结果区域 -->
    <div v-else class="result-section">
      <ReviewWorkspace
        :record-id="recordId" :source-text="sourceText" :issues="issues"
        :coverage="coverage" :compare="compareSnapshot" :collaboration="collaboration" :domain="domain" :depth="depth" :config-id="compareResult ? null : selectedModelId"
        :saved-review="savedReview" @saved="markSaved" @restore="restoreVersion"
      />
      <QualityFeedbackDialog ref="qualityFeedback" :record-id="recordId" :source-text="sourceText" />
      <FactCheckPanel :record-id="recordId" :source-text="sourceText" @started="router.replace({ query: { ...route.query, review: String($event) } })" />
      <ProofreadCoveragePanel
        v-if="!compareResult && !collaboration" v-model:coverage="coverage" :source-text="sourceText"
        :domain="domain" :depth="depth" :config-id="selectedModelId" @issues="mergeIssues"
      />
      <!-- ===== 多模型对比视图 ===== -->
      <template v-if="compareResult">
        <div class="result-toolbar">
          <el-button @click="goBack">
            <el-icon><Back /></el-icon>返回编辑
          </el-button>
          <div class="toolbar-info">
            <el-tag type="success">共识问题 {{ summaryStats.consensus }} 处</el-tag>
            <el-tag type="info">领域：{{ domainLabel }}</el-tag>
            <el-tag type="warning">已接受 {{ compareAcceptedCount }} 条</el-tag>
          </div>
          <div class="toolbar-actions">
            <el-button type="warning" @click="handleAcceptAll" :disabled="comparePendingCount === 0">
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

              <el-alert v-if="compareIncomplete" type="warning" :closable="false" :title="`${compareCoverageLabel(compareResult)}；请查看模型明细，当前共识仅代表已发现的问题。`" />
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
                    <div class="issue-context">{{ issueContext(item.issue) }}</div>
                    <div><span class="label">原文：</span><span class="text-del">{{ item.issue.original }}</span></div>
                    <div><span class="label">建议：</span><span class="text-add">{{ item.issue.suggestion }}</span></div>
                    <div v-if="item.issue.explanation"><span class="label">说明：</span><span class="text-muted">{{ item.issue.explanation }}</span></div>
                  </div>
                  <div class="issue-actions" v-if="!item.issue._accepted && !item.issue._ignored">
                    <el-button v-if="item.issue.suggestion" type="primary" size="small" @click="acceptIssue(item.issue)">
                      <el-icon><Check /></el-icon>仅修改此处
                    </el-button>
                    <el-button v-else-if="item.issue.type === 'sensitive' && item.issue.original" type="warning" size="small" @click="deleteIssue(item.issue)">
                      <el-icon><Delete /></el-icon>删除该词
                    </el-button>
                    <el-button v-if="item.issue.suggestion || item.issue.type === 'sensitive'" size="small" @click="acceptMatching(item.issue)">全文同类</el-button>
                    <el-button size="small" @click="ignoreIssue(item.issue)">
                      <el-icon><Close /></el-icon>忽略
                    </el-button>
                  </div>
                  <div class="issue-status" v-else>
                    <el-tag v-if="item.issue._accepted" type="success" size="small">已接受</el-tag>
                    <el-tag v-if="item.issue._ignored" type="info" size="small">已忽略</el-tag>
                    <el-button v-if="item.issue._ignored" text size="small" :disabled="recordId === null" @click="qualityFeedback?.open(item.issue)">补充原因（可选）</el-button>
                    <el-button text size="small" @click="undoIssue(item.issue)">撤销</el-button>
                  </div>
                </div>
                <el-empty v-if="summaryIssues.length === 0" description="当前结果暂无问题，请同时确认各模型是否完整审校" :image-size="60" />
              </div>
            </el-tab-pane>

            <!-- ===== 各模型明细 ===== -->
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
                >{{ r.success ? comparePendingCountOf(r) : '失败' }}</el-tag>
              </template>

              <div v-if="!r.success" class="compare-error">
                <el-alert type="error" :closable="false" show-icon :title="`校对失败：${r.error || '未知错误'}`" />
              </div>
              <template v-else>
                <ProofreadCoveragePanel :coverage="r.coverage" :source-text="sourceText" :domain="r.domain || domain" :depth="r.depth || 'standard'" :config-id="r.config_id" @update:coverage="updateCompareCoverage(r.config_id, $event)" @issues="mergeCompareRetry(r.config_id, $event)" />
                <div class="compare-meta">
                  <el-tag type="info" effect="plain" size="small">模型：{{ r.model }}</el-tag>
                  <el-tag type="info" effect="plain" size="small">耗时 {{ (r.elapsed_ms / 1000).toFixed(1) }}s</el-tag>
                  <el-tag type="success" effect="plain" size="small">
                    独有 {{ summaryIssues.filter(item => item.modelCount === 1 && item.modelIds.includes(r.config_id)).length }} 个
                  </el-tag>
                </div>
                <div class="compare-issues">
                  <div
                    v-for="(issue, i) in r.issues"
                    :key="i"
                    class="compare-issue-item"
                    :class="{ 'is-consensus': isConsensusIssue(issue), 'is-accepted': issue._accepted, 'is-ignored': issue._ignored }"
                  >
                    <div class="issue-head">
                      <el-tag :type="severityColor(issue.severity)" size="small">{{ typeLabel(issue.type) }}</el-tag>
                      <el-tag
                        :type="isConsensusIssue(issue) ? 'success' : 'warning'"
                        size="small" effect="plain"
                      >
                        {{ isConsensusIssue(issue) ? '共识' : '独有' }}
                      </el-tag>
                    </div>
                    <div class="issue-body">
                      <div class="issue-context">{{ issueContext(issue) }}</div>
                      <div><span class="label">原文：</span><span class="text-del">{{ issue.original }}</span></div>
                      <div><span class="label">建议：</span><span class="text-add">{{ issue.suggestion }}</span></div>
                      <div v-if="issue.explanation"><span class="label">说明：</span><span class="text-muted">{{ issue.explanation }}</span></div>
                    </div>
                    <div class="issue-actions" v-if="!issue._accepted && !issue._ignored">
                      <el-button v-if="issue.suggestion" type="primary" size="small" @click="acceptIssue(issue)">
                        <el-icon><Check /></el-icon>仅修改此处
                      </el-button>
                      <el-button v-else-if="issue.type === 'sensitive' && issue.original" type="warning" size="small" @click="deleteIssue(issue)">
                        <el-icon><Delete /></el-icon>删除该词
                      </el-button>
                      <el-button v-if="issue.suggestion || issue.type === 'sensitive'" size="small" @click="acceptMatching(issue)">全文同类</el-button>
                      <el-button size="small" @click="ignoreIssue(issue)">
                        <el-icon><Close /></el-icon>忽略
                      </el-button>
                    </div>
                    <div class="issue-status" v-else>
                      <el-tag v-if="issue._accepted" type="success" size="small">已接受</el-tag>
                      <el-tag v-if="issue._ignored" type="info" size="small">已忽略</el-tag>
                      <el-button v-if="issue._ignored" text size="small" :disabled="recordId === null" @click="qualityFeedback?.open(issue)">补充原因（可选）</el-button>
                      <el-button text size="small" @click="undoIssue(issue)">撤销</el-button>
                    </div>
                  </div>
                  <el-empty v-if="r.issues.length === 0" :description="r.coverage?.status === 'partial' ? '已完成范围暂无问题，仍有未审段落' : '该模型未发现问题'" :image-size="60" />
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
          <ReviewPreview :source-text="sourceText" :current-text="currentText" :issues="issues" :patches="patches" :active-index="activeIssueIndex" />
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
                <div class="issue-context">{{ issueContext(issue) }}</div>
                <div class="issue-diff">
                  <span class="text text-del" :title="issue.original">{{ issue.original }}</span>
                  <el-icon class="arrow-icon"><Right /></el-icon>
                  <span class="text text-add" :title="issue.suggestion">{{ issue.suggestion }}</span>
                </div>
                <div v-if="issue.explanation" class="issue-explanation">
                  <el-icon><InfoFilled /></el-icon>
                  <span>{{ issue.explanation }}</span>
                </div>
                <p v-if="collaboration" class="collaboration-note" data-testid="collaboration-provenance">{{ issueProvenance(issue) }}</p>
              </div>
              <div class="issue-actions" v-if="!issue._accepted && !issue._ignored">
                <el-button v-if="issue.suggestion" type="primary" size="small" @click="acceptIssue(issue)">
                  <el-icon><Check /></el-icon>仅修改此处
                </el-button>
                <el-button v-else-if="issue.type === 'sensitive' && issue.original" type="warning" size="small" @click="deleteIssue(issue)">
                  <el-icon><Delete /></el-icon>删除该词
                </el-button>
                <el-button v-if="issue.suggestion || issue.type === 'sensitive'" size="small" @click="acceptMatching(issue)">全文同类</el-button>
                <el-button size="small" @click="ignoreIssue(issue)">
                  <el-icon><Close /></el-icon>忽略
                </el-button>
              </div>
              <div class="issue-status" v-else>
                <el-tag v-if="issue._accepted" type="success" size="small">已接受</el-tag>
                <el-tag v-if="issue._ignored" type="info" size="small">已忽略</el-tag>
                <el-button v-if="issue._ignored" text size="small" :disabled="recordId === null" @click="qualityFeedback?.open(issue)">补充原因（可选）</el-button>
                <el-button text size="small" @click="undoIssue(issue)">撤销</el-button>
              </div>
            </div>
            <el-empty v-if="filteredIssues.length === 0" :description="collaboration?.status === 'partial' ? '已发现范围暂无问题，协作流程尚未完整完成' : coverage?.status === 'partial' ? '已完成范围暂无问题，仍有未审段落' : (filterType ? '此类型暂无问题' : '没有发现问题，仍需人工复核')" />
          </div>
        </el-card>
      </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { textProofreadApi, proofreadCompareApi, type ProofreadCompareResponse, type ProofreadCoverage, type ProofreadIssue } from '@/api/proofread'
import { getAvailableModelsCached, type AvailableModel } from '@/api/polish'
import { getReviewApi, type ReviewResponse, type ReviewRestorePayload } from '@/api/review'
import { severityColor, severityLabel, typeLabel, downloadTextFile, proofreadModeHints, proofreadDomainHints, proofreadDepthHints } from '@/utils/proofread'
import { useProofreadReview, type ReviewIssue } from '@/composables/useProofreadReview'
import { expandReviewIssues, reviewIssueKey } from '@/utils/review'
import { serializeReviewDraft } from '@/utils/reviewVersions'
import { compareCoverageLabel, modelReviewIssues, restoreCompareReview, serializeCompareReview } from '@/utils/compareReview'
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
  getGlobalIndex,
  sourceText,
  patches,
  initialize,
  restore,
  mergeIssues,
  applyIssues,
  acceptMatching,
  acceptIssue,
  ignoreIssue,
  deleteIssue,
  undoIssue,
  handleAcceptAll,
} = useProofreadReview()

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
      await ElMessageBox.confirm('协作提交结果尚未确认，离开将丢失本次请求编号，重新提交可能重复计费。建议留在此页重试同一请求。', '提交尚未确认', { type: 'warning', confirmButtonText: '仍然离开', cancelButtonText: '留在此页' })
    } catch { return false }
  }
  if (!hasUnsavedChanges.value) return true
  try {
    await ElMessageBox.confirm('尚有未保存的审阅修改，离开后将丢失。请先保存草稿，或确认离开。', '未保存的审阅', { confirmButtonText: '离开', cancelButtonText: '继续审阅', type: 'warning' })
    return true
  } catch { return false }
}
onBeforeRouteLeave(confirmLeave)
function handleBeforeUnload(event: { preventDefault(): void; returnValue: string }) {
  if (!hasUnsavedChanges.value && !collaborationPending.value) return
  event.preventDefault()
  event.returnValue = ''
}
onMounted(() => window.addEventListener('beforeunload', handleBeforeUnload))
onBeforeUnmount(() => window.removeEventListener('beforeunload', handleBeforeUnload))

function issueContext(issue: ReviewIssue) {
  if (issue.start == null || issue.start < 0) return '无法精确定位，请人工核对'
  const chars = Array.from(sourceText.value)
  return `第 ${issue.start + 1} 字：${chars.slice(Math.max(0, issue.start - 12), Math.min(chars.length, (issue.end ?? issue.start) + 12)).join('')}`
}

const issueKey = reviewIssueKey

const severityTagType = severityColor

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
const domain = ref('auto')
const depth = ref('standard')

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
const modelHint = computed(() => {
  if (compareMode.value) return '选择 2–4 个不同模型；各自独立审校，不会互相复核。'
  if (collaborationMode.value) return '各角色共用所选模型，按分工分别调用；不是多个不同模型投票。'
  if (depth.value === 'quick') return '快查不调用 AI，所选模型不会参与本次检查。'
  return '默认使用标记“当前”的模型；不同模型的速度、效果和用量不同。'
})
const compareModelIds = ref<number[]>([])
const compareResult = ref<ProofreadCompareResponse | null>(null)
const activeCompareTab = ref('')
const canCompare = computed(() => compareModelIds.value.length >= 2)
const compareSnapshot = computed(() => serializeCompareReview(compareResult.value))
const compareModels = computed(() => compareResult.value?.results.map(result => ({
  ...result, issues: modelReviewIssues(result.issues, issues.value),
})) || [])
const compareIncomplete = computed(() => compareResult.value
  ? compareResult.value.results.some(result => !result.success || result.coverage?.status !== 'complete')
  : false)

const user = useUserStore()
const collaborationBlocked = computed(() => {
  if (!user.isLoggedIn) return '请先登录后使用协作审校。'
  if (!user.hasPermission('proofread:text')) return '当前账号没有文本审校权限（proofread:text）。'
  if (Array.from(inputText.value).length > MAX_COLLABORATION_CHARS) return '协作审校最多支持 8000 个 Unicode 字符，请自行缩短文本；不会截断提交。'
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
    await ElMessageBox.confirm('将以原始文本重新运行完整协作，另扣一次应用额度并按实际 Token 产生供应商费用；不会自动采纳任何建议。', '重新运行协作（另计费）', { type: 'warning', confirmButtonText: '重新运行', cancelButtonText: '保留当前结果' })
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

/** 对比视图：所有模型问题的扁平列表（用于统计与批量操作；共识问题只计一次） */
const compareAllIssues = computed(() => compareResult.value ? issues.value : [])

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

/** 汇总建议：每个问题聚合各模型意见（发现该问题的模型名列表 + 排序） */
const summaryIssues = computed(() => {
  if (!compareResult.value) return []
  const okModels = compareModels.value.filter(r => r.success)
  const map = new Map<string, { issue: ReviewIssue; models: Map<number, string> }>()
  for (const r of okModels) {
    for (const issue of r.issues) {
      const key = issueKey(issue)
      if (!map.has(key)) map.set(key, { issue, models: new Map() })
      map.get(key)!.models.set(r.config_id, r.config_name)
    }
  }
  const total = okModels.length
  const severityOrder: Record<string, number> = { error: 3, warning: 2, info: 1 }
  const items = [...map.values()].map(e => ({
    issue: e.issue,
    modelCount: e.models.size,
    modelIds: [...e.models.keys()],
    isConsensus: total >= 2 && e.models.size >= 2,
    sources: [...e.models.values()].join(' / '),
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
  let targets = summaryIssues.value.filter(i =>
    !i.issue._accepted && !i.issue._ignored && i.issue.original
    && (i.issue.suggestion || i.issue.type === 'sensitive')
  )
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
    applyIssues(targets.map(t => t.issue))
  } catch {
    // 取消
  }
}
const compareAcceptedCount = computed(() => compareAllIssues.value.filter(i => i._accepted).length)
const comparePendingCount = computed(() => compareAllIssues.value.filter(i => !i._accepted && !i._ignored).length)
function comparePendingCountOf(r: { issues: ReviewIssue[] }): number {
  return r.issues.filter(i => !i._accepted && !i._ignored).length
}

function isConsensusIssue(issue: ReviewIssue) {
  return summaryIssues.value.some(item => issueKey(item.issue) === issueKey(issue) && item.isConsensus)
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
  if (compareMode.value && !canCompare.value) return ElMessage.warning('请至少选择 2 个模型')
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
.collaboration-note { font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.7; overflow-wrap: anywhere; }
.collaboration-warning { font-size: 13px; color: var(--el-color-warning-dark-2); line-height: 1.7; }
.issue-context { margin-bottom: 8px; font-size: 12px; color: var(--color-text-secondary); overflow-wrap: anywhere; }
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
    flex-wrap: wrap;
    row-gap: 6px;
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
</style>
