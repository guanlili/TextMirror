<template>
  <div
    class="fact-workbench"
    data-testid="fact-workbench"
  >
    <header class="workbench-heading">
      <div><span class="eyebrow">EVIDENCE · CONTEXT · REVIEW</span><h2>事实核查工作台</h2><p>从一段陈述，到可追溯的证据。独立核查，也可衔接文字审校。</p></div>
      <el-button
        v-if="run"
        @click="router.push('/fact-check')"
      >
        新建核查
      </el-button>
    </header>
    <div
      v-if="error"
      class="notice error"
      role="alert"
    >
      {{ error }} <el-button
        text
        @click="load"
      >
        重新读取
      </el-button>
    </div>
    <p
      v-if="loading"
      role="status"
      class="muted"
    >
      正在读取核查工作台…
    </p>
    <template v-if="!route.params.id">
      <section class="paper intake">
        <div class="section-heading">
          <h3><span class="step">01</span> 提交待核查材料</h3><span class="muted">不需要先做文字审校</span>
        </div>
        <div
          v-if="recordId"
          class="notice"
        >
          从审校记录 #{{ recordId }} 导入原始版本，核查不会修改审校内容。<el-button
            text
            @click="router.replace('/fact-check')"
          >
            改用独立输入
          </el-button>
        </div>
        <template v-else>
          <div class="input-toolbar">
            <el-radio-group
              v-model="inputKind"
              :disabled="!!busy || !!pendingCreate"
            >
              <el-radio-button value="text">
                粘贴文本
              </el-radio-button><el-radio-button value="document">
                上传文档
              </el-radio-button>
            </el-radio-group><span class="muted">{{ textLength }} / {{ options?.max_text_chars || 20000 }} 字符</span>
          </div>
          <el-input
            v-if="inputKind === 'text'"
            v-model="text"
            type="textarea"
            :rows="9"
            :disabled="!!busy || !!pendingCreate"
            aria-label="待核查文本"
            placeholder="粘贴新闻稿、报告或需要核实的陈述。系统提取客观事实，不判断观点与主观评价。"
          />
          <div
            v-else
            class="upload-area"
          >
            <label for="fact-file">选择文档，仅提取文本，不执行审校</label><input
              id="fact-file"
              type="file"
              accept=".doc,.docx,.pdf,.txt"
              :disabled="!!busy || !!pendingCreate"
              @change="upload"
            ><p v-if="fileId">
              {{ filename }} · 已提取 {{ textLength }} 字符
            </p><p class="muted">
              支持现有文档格式；无法提取文字或超长材料会明确提示，不自动截断。
            </p>
          </div>
        </template>
        <div class="configuration">
          <el-select
            v-model="mode"
            aria-label="检索范围"
            :disabled="!!busy || !!pendingCreate"
          >
            <el-option
              value="web"
              label="开放网络检索"
            /><el-option
              value="trusted"
              label="指定可信信源"
            />
          </el-select><el-select
            v-if="mode === 'trusted'"
            v-model="sourceIds"
            multiple
            aria-label="选择可信信源"
            :disabled="!!busy || !!pendingCreate"
          >
            <el-option
              v-for="source in options?.sources || []"
              :key="source.id"
              :value="source.id"
              :label="source.name"
            />
          </el-select><span class="muted">{{ options?.model_name || '读取模型配置中' }}</span>
        </div>
        <p
          v-if="options && !options.available"
          class="notice warning"
        >
          {{ options.unavailable_reason }}
        </p>
        <p
          v-if="tooLong"
          class="notice warning"
        >
          材料超过长度限制，请缩短材料后提交；系统不会截断原文。
        </p>
        <el-checkbox
          v-model="consented"
          :disabled="!!busy || !!pendingCreate"
          aria-label="同意材料外发"
        >
          确认材料可发送至外部模型与检索服务，不含涉密内容
        </el-checkbox>
        <p class="muted">
          两种模式均会联网。可信信源约束可采纳证据，不等于离线检索，也不保证来源内容正确。
        </p>
        <p class="muted">
          原文、证据与复核从任务创建起保留 {{ options?.retention_days || 90 }} 天；到期清理，也可在任务结束后主动清理。上传文件沿用文档管理的独立保留规则。
        </p>
        <div class="actions">
          <el-button
            type="primary"
            :disabled="!canCreate"
            :loading="busy === 'create'"
            @click="create(false)"
          >
            {{ pendingCreate ? '重试提交（同一请求）' : '开始事实核查' }}
          </el-button><el-button
            :disabled="!canCreate || !!pendingCreate"
            @click="create(true)"
          >
            先确认事实项
          </el-button><span class="muted">最多核查 {{ options?.max_claims || 10 }} 条 · 每日最多 {{ options?.daily_limit || 20 }} 次</span>
        </div>
        <p
          v-if="pendingCreate"
          class="notice warning"
        >
          提交结果尚未确认；重试使用同一请求编号，不会重复创建。可先查看下方历史。
        </p>
      </section>
      <section class="paper history">
        <div class="section-heading">
          <h3><span class="step">02</span> 核查记录</h3><el-button
            text
            @click="loadHistory"
          >
            刷新
          </el-button>
        </div>
        <div class="history-tools">
          <el-input
            v-model="query"
            clearable
            placeholder="搜索材料标题"
            aria-label="搜索核查记录"
            @keyup.enter="searchHistory"
          /><el-select
            v-model="statusFilter"
            clearable
            placeholder="全部状态"
            aria-label="筛选任务状态"
            @change="searchHistory"
          >
            <el-option
              v-for="(label, value) in statusLabels"
              :key="value"
              :value="value"
              :label="label"
            />
          </el-select><el-button @click="searchHistory">
            查询
          </el-button>
        </div>
        <p
          v-if="!history.length"
          class="empty"
        >
          还没有核查记录。提交一段材料，开始建立证据链。
        </p>
        <button
          v-for="item in history"
          :key="item.id"
          class="history-item"
          @click="router.push(`/fact-check/${item.id}`)"
        >
          <span class="history-number">#{{ item.id }}</span><span class="history-title"><strong>{{ item.title || '未命名材料' }}</strong><small>{{ formatDate(item.created_at) }} · {{ item.source_kind === 'record' ? '审校导入' : item.source_kind === 'document' ? '文档导入' : '独立文本' }} · {{ item.depth === 'deep' ? '单条深查' : '标准核查' }}</small></span><el-tag :type="item.status === 'FAILURE' ? 'danger' : 'info'">
            {{ runStatusLabel(item, true) }}
          </el-tag><span aria-hidden="true">→</span>
        </button>
        <el-pagination
          v-if="total > 20"
          v-model:current-page="page"
          :total="total"
          :page-size="20"
          layout="prev, pager, next"
          @current-change="loadHistory"
        />
      </section>
    </template>
    <template v-else-if="run">
      <section class="paper run-overview">
        <div class="section-heading">
          <div><span class="eyebrow">核查记录 #{{ run.id }} · {{ run.depth === 'deep' ? '单条深入核查' : '标准核查' }}</span><h3>{{ run.title }}</h3></div><el-tag :type="run.status === 'FAILURE' ? 'danger' : run.result?.coverage.status === 'partial' ? 'warning' : 'info'">
            {{ runLabel }}
          </el-tag>
        </div>
        <p
          v-if="run.parent_run_id"
          class="notice"
        >
          仅重新核查选中的一条事实，其他历史结论未刷新。<router-link :to="`/fact-check/${run.parent_run_id}`">
            查看来源报告 #{{ run.parent_run_id }}
          </router-link>
        </p>
        <p :class="{ 'notice warning': run.status === 'SUCCESS' && run.result?.coverage.status === 'partial' }">
          {{ runMessage }} <span
            v-if="run.error_code"
            class="muted"
          >{{ run.error_code }}</span>
        </p>
        <el-progress
          v-if="running"
          :percentage="run.progress"
          :stroke-width="5"
        />
        <div
          v-if="run.result"
          class="metrics"
        >
          <div><strong>{{ run.result.coverage.extracted }}</strong><span>识别事实</span></div><div><strong>{{ selectedCount }}</strong><span>本次选择</span></div><div><strong>{{ run.result.coverage.checked }}</strong><span>已尝试核查</span></div><div><strong>{{ citedClaims }}</strong><span>有正文引用</span></div><div><strong>{{ insufficientClaims }}</strong><span>仍证据不足</span></div><div><strong>{{ run.result.coverage.unverified }}</strong><span>尚未尝试</span></div>
        </div>
        <p
          v-if="run.result?.claims.length"
          class="muted"
        >
          尝试完成不等于已证实。有正文引用也可能只是背景或反驳材料，请逐条查看“检索资料与佐证”。
        </p>
        <p
          v-if="run.result?.coverage.reason"
          class="muted"
        >
          {{ run.result.coverage.reason }}
        </p>
        <div class="actions">
          <el-button
            v-if="running || waiting"
            :disabled="!!busy"
            @click="cancel"
          >
            取消核查
          </el-button><el-button
            :disabled="!!busy"
            @click="load"
          >
            刷新状态
          </el-button><template v-if="terminal && user.hasPermission('fact-check:export')">
            <el-button
              :disabled="!!busy"
              @click="download('html')"
            >
              下载打印版
            </el-button><el-button
              :disabled="!!busy"
              @click="download('json')"
            >
              导出 JSON
            </el-button>
          </template><el-button
            v-if="!running"
            text
            type="danger"
            :disabled="!!busy"
            @click="clearRun"
          >
            清理材料与证据
          </el-button>
        </div>
      </section>
      <section
        v-if="waiting"
        class="paper confirmation"
      >
        <h3>确认本次核查范围</h3><p class="muted">
          保留原文不变，可调整待核查陈述；每条应是独立事实。最多选择 {{ run.max_claims }} 条。
        </p>
        <div
          v-for="claim in run.result?.claims || []"
          :key="claim.id"
          class="selection-row"
        >
          <el-checkbox
            v-model="selection[claim.id]"
            :disabled="!!busy"
            :aria-label="`核查 ${claim.id}`"
          >
            {{ claim.id }}
          </el-checkbox><el-input
            v-model="statements[claim.id]"
            :disabled="!!busy || !selection[claim.id]"
            :maxlength="2000"
            :aria-label="`待核查陈述 ${claim.id}`"
          /><span class="muted">原文：{{ claim.original }}</span>
        </div>
        <el-button
          type="primary"
          :disabled="!!busy || !selectionValid"
          :loading="busy === 'execute'"
          @click="execute"
        >
          确认并核查 {{ chosenIds.length }} 条事实
        </el-button>
      </section>
      <div class="mobile-tabs">
        <button
          v-for="(label, key) in { source: '原文', claims: '事实项', evidence: '证据与复核' }"
          :key="key"
          :class="{ active: mobileTab === key }"
          @click="mobileTab = key"
        >
          {{ label }}
        </button>
      </div>
      <div class="review-grid">
        <section
          class="paper source-pane"
          :class="{ 'mobile-active': mobileTab === 'source' }"
        >
          <div class="section-heading">
            <h3>原文快照</h3><span class="eyebrow">SOURCE</span>
          </div><div
            v-if="highlight"
            class="source-body"
          >
            <span>{{ highlight.before }}</span><mark>{{ highlight.target }}</mark><span>{{ highlight.after }}</span>
          </div><div
            v-else
            class="source-body"
          >
            {{ sourceText || '原文已清理或暂不可用' }}
          </div><details class="muted">
            <summary>原文指纹</summary><p class="hash">
              {{ run.source_hash }}
            </p>
          </details>
        </section>
        <section
          class="paper claims-pane"
          :class="{ 'mobile-active': mobileTab === 'claims' }"
        >
          <div class="section-heading">
            <h3>事实清单</h3><span class="eyebrow">CLAIMS</span>
          </div><el-select
            v-model="verdictFilter"
            clearable
            placeholder="全部事实"
            aria-label="筛选事实结论"
          >
            <el-option
              v-for="(label, key) in verdictLabels"
              :key="key"
              :value="key"
              :label="label"
            />
          </el-select><p
            v-if="!visibleClaims.length"
            class="empty"
          >
            {{ run.result?.claims.length ? '没有符合筛选的事实项' : emptyMessage }}
          </p><button
            v-for="claim in visibleClaims"
            :key="claim.id"
            class="claim-item"
            :class="{ active: activeClaim?.id === claim.id }"
            @click="selectClaim(claim.id)"
          >
            <span class="claim-label">{{ claim.id }} <el-tag
              size="small"
              :type="claim.verdict === 'refuted' ? 'danger' : claim.verdict === 'conflicting' ? 'warning' : 'info'"
            >{{ claim.checked ? verdictLabels[claim.verdict] : '未检查' }}</el-tag></span><strong>{{ claim.statement }}</strong><small>{{ claim.reason }}</small>
          </button>
        </section>
        <section
          class="paper evidence-pane"
          :class="{ 'mobile-active': mobileTab === 'evidence' }"
        >
          <div class="section-heading">
            <h3>证据与复核</h3><span class="eyebrow">EVIDENCE</span>
          </div><template v-if="activeClaim">
            <h4>{{ activeClaim.statement }}</h4><p>{{ activeClaim.reason }}</p><p
              v-if="activeClaim.original_statement && activeClaim.original_statement !== activeClaim.statement"
              class="notice"
            >
              提取原始陈述：{{ activeClaim.original_statement }}
            </p><p
              v-if="activeClaim.suggestion"
              class="notice"
            >
              人工参考建议：{{ activeClaim.suggestion }}
            </p>
            <FactCheckSearchTrace
              :claim="activeClaim"
              @show-evidence="showEvidence"
            />
            <h4 v-if="activeClaim.evidence.length">
              已引用证据 · 正文快照
            </h4>
            <p
              v-if="!activeClaim.evidence.length"
              class="empty"
            >
              没有可引用的正文证据；搜索摘要和模型知识不作为事实保证。
            </p>
            <article
              v-for="evidence in activeClaim.evidence"
              :id="evidenceAnchor(evidence.id)"
              :key="evidence.id"
              tabindex="-1"
              class="evidence-card"
            >
              <span class="eyebrow">{{ stanceLabels[evidence.stance] }}</span><h4>
                <a
                  v-if="safeUrl(evidence.url)"
                  :href="safeUrl(evidence.url)"
                  target="_blank"
                  rel="noopener noreferrer"
                >{{ evidence.title }}</a><span v-else>{{ evidence.title }}</span>
              </h4><blockquote>{{ evidence.quote }}</blockquote><p class="muted">
                {{ evidence.publisher }} · 发布：{{ formatDate(evidence.published_at) }}<br>抓取：{{ formatDate(evidence.retrieved_at) }}
              </p><ul
                v-if="evidence.checks"
                class="check-list"
              >
                <li
                  v-for="(label, key) in checkLabels"
                  :key="key"
                >
                  <strong>{{ label }} · {{ consistencyLabels[evidence.checks[key].status] }}</strong><span>{{ evidence.checks[key].reason }}</span>
                </li>
              </ul><p
                v-else
                class="muted"
              >
                历史报告未记录结构化口径检查。
              </p><details v-if="evidence.body_text">
                <summary>查看当次模型可见正文</summary><pre>{{ evidence.body_text }}</pre><p class="hash muted">
                  SHA-256：{{ evidence.body_sha256 }}
                </p>
              </details><p
                v-else
                class="notice warning"
              >
                历史报告未保存正文快照，不能复现当时全文依据。
              </p>
            </article>
            <p class="muted">
              口径检查是模型的语义评估；程序验证逐字引文与结构约束，不独立保证语义正确。
            </p>
            <div
              v-if="terminal && sourceText"
              class="deep-section"
            >
              <h4>针对这条事实深入核查</h4><el-input
                v-model="supplemental"
                type="textarea"
                :rows="2"
                placeholder="可选：补充原始证据链接，每行一个，最多3个"
                aria-label="补充证据链接"
                :disabled="!!busy"
              /><el-checkbox
                v-model="deepConsent"
                :disabled="!!busy"
              >
                确认可再次对外检索
              </el-checkbox><el-button
                :disabled="!!busy || !deepConsent"
                :loading="busy === 'deepen'"
                @click="deepen"
              >
                发起单条深查
              </el-button><p class="muted">
                创建新报告，最多三轮检索。保留本次结论，不自动修改原文。
              </p>
            </div>
            <div class="human-review">
              <h4>人工复核记录</h4><p
                v-if="!claimReviews.length"
                class="muted"
              >
                尚未复核
              </p><article
                v-for="review in claimReviews"
                :key="review.id"
                class="review-entry"
              >
                <strong>{{ reviewLabels[review.decision] }}</strong> · #{{ review.user_id }} · {{ formatDate(review.created_at) }}<p>{{ review.note }}</p>
              </article><template v-if="terminal && activeClaim.checked && user.hasPermission('fact-check:review')">
                <el-select
                  v-model="decision"
                  aria-label="人工复核意见"
                  :disabled="!!busy"
                >
                  <el-option
                    v-for="(label, key) in reviewLabels"
                    :key="key"
                    :label="label"
                    :value="key"
                  />
                </el-select><el-input
                  v-model="note"
                  type="textarea"
                  :rows="3"
                  :maxlength="2000"
                  :disabled="!!busy"
                  aria-label="复核说明"
                  placeholder="填写复核依据；提出异议时必填。不会覆盖机器结论。"
                /><el-button
                  :disabled="!!busy || (decision === 'disagree' && !note.trim())"
                  :loading="busy === 'review'"
                  @click="saveReview"
                >
                  保存复核意见
                </el-button>
              </template>
            </div>
          </template><p
            v-else
            class="empty"
          >
            {{ run.result?.claims.length ? '选择一个事实项，查看其证据、检索过程及复核记录。' : emptyMessage }}
          </p>
        </section>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { useUserStore } from '@/stores/user'
import { uploadDocumentApi, fetchExtractedTextApi } from '@/api/document'
import { getReviewErrorDetail } from '@/api/review'
import FactCheckSearchTrace from '@/components/FactCheckSearchTrace.vue'
import {
  createFactCheckId, createFactCheckRunApi, getFactCheckOptionsApi, getFactCheckRunApi, cancelFactCheckRunApi,
  factCheckHistoryApi, factCheckSourceApi, executeFactCheckApi, deepenFactCheckApi, factCheckReviewsApi,
  addFactCheckReviewApi, exportFactCheckApi, deleteFactCheckApi,
  type CreateFactCheckPayload, type FactCheckRun, type FactCheckOptions, type FactCheckMode, type FactCheckReview,
} from '@/api/factCheck'

const route = useRoute(), router = useRouter(), user = useUserStore()
const options = ref<FactCheckOptions | null>(null), run = ref<FactCheckRun | null>(null)
const sourceText = ref(''), text = ref(''), inputKind = ref('text'), fileId = ref(''), filename = ref('')
const mode = ref<FactCheckMode>('web'), sourceIds = ref<string[]>([]), consented = ref(false)
const history = ref<FactCheckRun[]>([]), total = ref(0), page = ref(1), query = ref(''), statusFilter = ref('')
const loading = ref(false), busy = ref(''), error = ref(''), selectedId = ref(''), verdictFilter = ref('')
const selection = ref<Record<string, boolean>>({}), statements = ref<Record<string, string>>({})
const reviews = ref<FactCheckReview[]>([]), decision = ref<FactCheckReview['decision']>('agree'), note = ref('')
const supplemental = ref(''), deepConsent = ref(false), mobileTab = ref('claims')
const pendingCreate = ref<CreateFactCheckPayload | null>(null)
let executeId = '', executeHash = '', reviewId = '', reviewHash = '', deepId = '', deepHash = ''
let epoch = 0, alive = true, timer: ReturnType<typeof setTimeout> | undefined, controller: AbortController | undefined
const statusLabels = { PENDING: '等待执行', RUNNING: '核查中', WAITING_CONFIRMATION: '待确认事实', SUCCESS: '执行完成', FAILURE: '执行失败', CANCELLED: '已取消' }
const verdictLabels = { supported: '证据支持', refuted: '证据反驳', insufficient: '证据不足', conflicting: '证据冲突' }
const stanceLabels = { supports: '支持证据', refutes: '反驳证据', context: '背景材料' }
const checkLabels = { subject: '主体 / 事件', event_time: '事件时间', scope_unit: '统计范围 / 单位' }
const consistencyLabels = { match: '一致', mismatch: '不一致', unknown: '无法确定', not_applicable: '不适用' }
const reviewLabels = { agree: '认可结论', disagree: '提出异议', unresolved: '仍待核实' }
const recordId = computed(() => /^\d+$/.test(String(route.query.record_id || '')) ? Number(route.query.record_id) : undefined)
const textLength = computed(() => Array.from(text.value).length)
const tooLong = computed(() => textLength.value > (options.value?.max_text_chars || 20000))
const canCreate = computed(() => !busy.value && user.hasPermission('fact-check:run') && options.value?.available && consented.value && !tooLong.value
  && (recordId.value || (inputKind.value === 'document' ? fileId.value : text.value.trim()))
  && (mode.value === 'web' || sourceIds.value.length > 0))
const running = computed(() => run.value?.status === 'PENDING' || run.value?.status === 'RUNNING')
const waiting = computed(() => run.value?.status === 'WAITING_CONFIRMATION')
const terminal = computed(() => !!run.value && !running.value && !waiting.value)
const activeClaim = computed(() => run.value?.result?.claims.find(item => item.id === selectedId.value))
const citedClaims = computed(() => run.value?.result?.claims.filter(claim => claim.evidence.length > 0).length || 0)
const insufficientClaims = computed(() => run.value?.result?.claims.filter(claim => claim.checked && claim.verdict === 'insufficient').length || 0)
// 兼容旧版 SUCCESS + partial 空报告，仅调整展示，不改写保存的状态。
function extractionFailed(value: FactCheckRun) {
  return (value.status === 'FAILURE' && value.error_code === 'EXTRACTION_LOCATION_FAILED')
    || (value.status === 'SUCCESS' && value.result?.coverage.status === 'partial' && value.result.claims.length === 0)
}
function noFacts(value: FactCheckRun) {
  return value.status === 'SUCCESS' && value.result?.coverage.status === 'complete' && value.result.claims.length === 0
}
function fetchBlocked(value: FactCheckRun) {
  return value.status === 'SUCCESS' && value.result?.usage.pages_fetched === 0
    && value.result.claims.some(claim => claim.search_rounds?.some(round => round.error_codes.length))
}
function runStatusLabel(value: FactCheckRun, summary = false) {
  if (summary && value.status === 'SUCCESS' && !value.result) return '任务已结束，查看报告'
  if (extractionFailed(value)) return '事实提取失败'
  if (noFacts(value)) return '未识别到可核查事实'
  if (fetchBlocked(value)) return '核查受阻'
  return value.status === 'SUCCESS' && value.result?.coverage.status === 'partial' ? '核查不完整' : statusLabels[value.status]
}
const runLabel = computed(() => run.value ? runStatusLabel(run.value) : '')
const runMessage = computed(() => {
  const value = run.value
  if (!value) return ''
  if (extractionFailed(value)) return '事实提取失败：未能获得可准确定位到原文的事实项；本次未完成事实核查，不代表全文没有事实或事实正确。'
  if (noFacts(value)) return '未识别到可核查事实；本次未进行搜索或证据核查，不代表全文事实正确。'
  if (fetchBlocked(value)) return '搜索或正文读取受阻，未取得可核对的正文；任务已结束，但不能形成可靠结论。'
  return value.status === 'SUCCESS' && value.result?.coverage.status === 'partial'
    ? '任务已结束，但部分核查未完整完成；请查看各条事实的资料处理情况。' : value.message
})
const emptyMessage = computed(() => {
  const value = run.value
  if (value && (extractionFailed(value) || noFacts(value))) return runMessage.value
  if (running.value) return '任务尚未完成，暂无法展示事实项；不代表全文没有可核查事实。'
  if (waiting.value) return '任务待确认，当前没有可展示的事实项；尚未完成核查。'
  if (value?.status === 'CANCELLED') return '核查已取消，没有可展示的事实项；不代表全文事实正确。'
  if (value?.status === 'FAILURE') return '核查失败，没有可展示的事实项；不能据此判断原文是否包含可核查事实。'
  return '尚无可展示的事实项；不代表全文正确。'
})
const visibleClaims = computed(() => (run.value?.result?.claims || []).filter(item => !verdictFilter.value || item.verdict === verdictFilter.value))
const selectedCount = computed(() => waiting.value ? chosenIds.value.length : run.value?.result?.claims.filter(item => item.selected !== false).length || 0)
const chosenIds = computed(() => Object.keys(selection.value).filter(id => selection.value[id]))
const selectionValid = computed(() => chosenIds.value.length > 0 && chosenIds.value.length <= (run.value?.max_claims || 10) && chosenIds.value.every(id => statements.value[id]?.trim()))
const claimReviews = computed(() => reviews.value.filter(item => item.claim_id === activeClaim.value?.id))
const highlight = computed(() => {
  const claim = activeClaim.value, chars = Array.from(sourceText.value)
  if (!claim || chars.slice(claim.start, claim.end).join('') !== claim.original) return null
  return { before: chars.slice(0, claim.start).join(''), target: claim.original, after: chars.slice(claim.end).join('') }
})
function formatDate(value?: string | null) { return value ? new Date(value).toLocaleString('zh-CN') : '未提供' }
function safeUrl(value: string) {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : '' } catch { return '' }
}
function showError(cause: unknown) { error.value = getReviewErrorDetail(cause) }
function selectClaim(id: string) { selectedId.value = id; mobileTab.value = 'evidence'; note.value = ''; supplemental.value = ''; deepConsent.value = false; decision.value = 'agree' }
function evidenceAnchor(id: string) { return `workbench-evidence-${run.value?.id}-${activeClaim.value?.id}-${id}` }
function showEvidence(id: string) {
  const target = document.getElementById(evidenceAnchor(id))
  target?.scrollIntoView({ block: 'start' }); target?.focus({ preventScroll: true })
}
function acceptRun(value: FactCheckRun) {
  const initialize = run.value?.id !== value.id || (value.status === 'WAITING_CONFIRMATION' && run.value?.status !== 'WAITING_CONFIRMATION')
  run.value = value
  if (!value.result?.claims.some(item => item.id === selectedId.value)) selectedId.value = value.result?.claims[0]?.id || ''
  if (initialize && value.status === 'WAITING_CONFIRMATION') {
    selection.value = Object.fromEntries((value.result?.claims || []).map((claim, index) => [claim.id, index < value.max_claims]))
    statements.value = Object.fromEntries((value.result?.claims || []).map(claim => [claim.id, claim.statement]))
  }
}
function schedulePoll() {
  clearTimeout(timer)
  if (!alive || !running.value || !run.value || error.value) return
  timer = setTimeout(() => { void refresh() }, 2000)
}
async function refresh() {
  if (!run.value || busy.value) { schedulePoll(); return }
  const id = run.value.id, token = epoch
  try { const result = await getFactCheckRunApi(id, { signal: controller?.signal }); if (alive && token === epoch) acceptRun(result) }
  catch (cause) { if (alive && token === epoch) showError(cause) }
  finally { if (alive && token === epoch) schedulePoll() }
}
async function loadHistory() {
  const token = epoch
  try { const result = await factCheckHistoryApi({ page: page.value, page_size: 20, status: statusFilter.value || undefined, q: query.value || undefined }, { signal: controller?.signal }); if (alive && token === epoch) { history.value = result.items; total.value = result.total } }
  catch (cause) { if (alive && token === epoch) showError(cause) }
}
function searchHistory() { page.value = 1; void loadHistory() }
async function load() {
  const token = ++epoch
  controller?.abort(); controller = new AbortController(); clearTimeout(timer)
  loading.value = true; error.value = ''
  try {
    if (!user.userInfo) await user.fetchUserInfo()
    if (!user.hasPermission('fact-check:run')) { await router.replace('/403'); return }
    if (route.params.id) {
      const id = Number(route.params.id)
      const [result, source, entries] = await Promise.all([getFactCheckRunApi(id, { signal: controller.signal }), factCheckSourceApi(id, { signal: controller.signal }), factCheckReviewsApi(id, { signal: controller.signal })])
      if (!alive || token !== epoch) return
      acceptRun(result); sourceText.value = source.text; reviews.value = entries
    } else {
      run.value = null; sourceText.value = ''; reviews.value = []
      const result = await getFactCheckOptionsApi({ signal: controller.signal })
      if (!alive || token !== epoch) return
      options.value = result; sourceIds.value = result.sources.filter(item => item.is_enabled).map(item => item.id)
      await loadHistory()
    }
  } catch (cause) { if (alive && token === epoch) showError(cause) }
  finally { if (alive && token === epoch) { loading.value = false; schedulePoll() } }
}
async function upload(event: globalThis.Event) {
  const file = (event.target as globalThis.HTMLInputElement).files?.[0]
  if (!file) return
  const token = epoch; busy.value = 'upload'; error.value = ''; fileId.value = ''; text.value = ''
  try { const result = await uploadDocumentApi(file, controller?.signal); if (alive && token === epoch) { fileId.value = result.file_id; filename.value = result.filename; const textRes = await fetchExtractedTextApi(result.file_id); if (alive && token === epoch) { text.value = textRes.extracted_text } } }
  catch (cause) { if (alive && token === epoch) showError(cause) }
  finally { if (alive && token === epoch) busy.value = '' }
}
async function create(confirm: boolean) {
  if (!canCreate.value) return
  pendingCreate.value ??= { ...(recordId.value ? { record_id: recordId.value } : inputKind.value === 'document' ? { file_id: fileId.value } : { text: text.value }), mode: mode.value, source_ids: mode.value === 'trusted' ? [...sourceIds.value] : [], confirm_claims: confirm, request_id: createFactCheckId(), allow_external_search: true }
  busy.value = 'create'; error.value = ''; const token = epoch
  try { const result = await createFactCheckRunApi(pendingCreate.value); if (alive && token === epoch) { pendingCreate.value = null; await router.push(`/fact-check/${result.id}`) } }
  catch (cause) { if (alive && token === epoch) { showError(cause); const status = (cause as { response?: { status: number } }).response?.status; if (status && status >= 400 && status < 500 && status !== 408) pendingCreate.value = null } }
  finally { busy.value = '' }
}
async function action(kind: string, work: (id: number) => Promise<void>) {
  if (!run.value || busy.value) return
  const id = run.value.id, token = epoch; busy.value = kind; error.value = ''; clearTimeout(timer)
  try { await work(id) } catch (cause) { if (alive && token === epoch) showError(cause) }
  finally { if (alive) { busy.value = ''; if (token === epoch) schedulePoll() } }
}
async function execute() {
  if (!selectionValid.value) return
  const claims = chosenIds.value.map(id => ({ id, statement: statements.value[id].trim() })), hash = JSON.stringify(claims)
  if (hash !== executeHash) { executeHash = hash; executeId = createFactCheckId() }
  await action('execute', async id => { const result = await executeFactCheckApi(id, { claims, request_id: executeId }); if (Number(route.params.id) === id) acceptRun(result) })
}
async function cancel() { await action('cancel', async id => { const result = await cancelFactCheckRunApi(id); if (Number(route.params.id) === id) acceptRun(result) }) }
async function deepen() {
  if (!activeClaim.value || !deepConsent.value) return
  const urls = supplemental.value.split('\n').map(value => value.trim()).filter(Boolean)
  if (urls.length > 3 || urls.some(value => !safeUrl(value))) { error.value = '最多填写3个有效 HTTP(S) 证据链接'; return }
  const claimId = activeClaim.value.id, hash = JSON.stringify([run.value?.id, claimId, urls])
  if (hash !== deepHash) { deepHash = hash; deepId = createFactCheckId() }
  await action('deepen', async id => { const result = await deepenFactCheckApi(id, { claim_id: claimId, supplemental_urls: urls, request_id: deepId, allow_external_search: true }); if (Number(route.params.id) === id) await router.push(`/fact-check/${result.id}`) })
}
async function saveReview() {
  if (!activeClaim.value) return
  const data = { claim_id: activeClaim.value.id, decision: decision.value, note: note.value.trim() }, hash = JSON.stringify([run.value?.id, data])
  if (hash !== reviewHash) { reviewHash = hash; reviewId = createFactCheckId() }
  await action('review', async id => { const result = await addFactCheckReviewApi(id, { ...data, request_id: reviewId }); if (Number(route.params.id) === id) { reviews.value = [...reviews.value.filter(item => item.id !== result.id), result]; note.value = '' } })
}
async function download(format: 'html' | 'json') {
  await action('export', async id => { const blob = await exportFactCheckApi(id, format); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = `fact-check-${id}.${format}`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000) })
}
async function clearRun() {
  try { await ElMessageBox.confirm('清理本次原文、证据快照和复核意见？保留请求计数，已生成的其他版本不受影响。此操作无法撤销。', '清理核查材料', { type: 'warning', confirmButtonText: '确认清理', cancelButtonText: '保留' }) } catch { return }
  await action('delete', async id => { await deleteFactCheckApi(id); if (Number(route.params.id) === id) await load() })
}
watch(() => route.fullPath, () => { run.value = null; selectedId.value = ''; verdictFilter.value = ''; consented.value = false; deepConsent.value = false; note.value = ''; supplemental.value = ''; executeHash = ''; reviewHash = ''; deepHash = ''; pendingCreate.value = null; void load() }, { immediate: true })
onBeforeUnmount(() => { alive = false; epoch++; controller?.abort(); clearTimeout(timer) })
</script>

<style scoped>
.fact-workbench{--ink:var(--el-text-color-primary);--accent:#247f77;max-width:1600px;margin:0 auto;color:var(--ink)}
.workbench-heading{display:flex;justify-content:space-between;align-items:center;gap:20px;margin:0 0 28px}.eyebrow{font-size:10px;letter-spacing:2px;color:var(--accent);font-weight:700}.workbench-heading h2{font-size:30px;font-weight:600;letter-spacing:1px;margin:8px 0;font-family:'Songti SC','STSong',serif}.workbench-heading p{font-size:13px;color:var(--el-text-color-secondary);margin:0}.paper{background:var(--el-bg-color);border:1px solid var(--el-border-color-lighter);border-radius:8px;padding:24px;min-width:0}.intake{border-top:3px solid var(--accent)}.section-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:18px}.section-heading h3{font-size:17px;margin:0}.step{font-family:monospace;color:var(--accent);font-size:13px;margin-right:12px}.muted{font-size:12px;color:var(--el-text-color-secondary);line-height:1.7}.input-toolbar,.configuration,.actions,.history-tools{display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin:16px 0}.input-toolbar{justify-content:space-between}.configuration>.el-select{width:230px}.configuration>.el-select:nth-child(2){width:320px}.actions{margin-bottom:0}.notice{font-size:13px;line-height:1.7;padding:12px 16px;background:var(--el-fill-color-light);border-left:3px solid var(--accent);margin:12px 0;overflow-wrap:anywhere}.error{border-color:var(--el-color-danger);color:var(--el-color-danger)}.warning{border-color:var(--el-color-warning)}.upload-area{border:1px dashed var(--el-border-color);background:var(--el-fill-color-lighter);padding:30px;display:flex;flex-direction:column;gap:12px}.history{margin-top:24px}.history-tools>.el-input{max-width:400px}.history-tools>.el-select{width:180px}.history-item{width:100%;display:flex;align-items:center;gap:20px;border:0;border-top:1px solid var(--el-border-color-lighter);padding:20px 4px;background:transparent;text-align:left;color:inherit;cursor:pointer}.history-item:hover{background:var(--el-fill-color-light)}.history-number{font:12px monospace;color:var(--el-text-color-secondary)}.history-title{flex:1;min-width:0}.history-title strong{font-weight:500;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.history-title small{display:block;color:var(--el-text-color-secondary);font-size:11px;margin-top:7px}.empty{padding:28px 12px;text-align:center;color:var(--el-text-color-secondary);font-size:13px;line-height:1.8}.run-overview h3{margin:10px 0;overflow-wrap:anywhere}.metrics{display:flex;gap:40px;margin:24px 0}.metrics>div{display:flex;flex-direction:column;gap:7px}.metrics strong{font:30px Georgia,serif;color:var(--accent)}.metrics span{font-size:11px;color:var(--el-text-color-secondary)}.confirmation{margin-top:20px;border-left:3px solid var(--accent)}.selection-row{display:grid;grid-template-columns:70px 1fr;gap:10px;margin:16px 0}.selection-row>span{grid-column:2}.review-grid{display:grid;grid-template-columns:minmax(210px,.9fr) minmax(230px,.9fr) minmax(320px,1.35fr);gap:16px;align-items:start;margin-top:20px}.review-grid>.paper{padding:20px}.source-body{white-space:pre-wrap;overflow-wrap:anywhere;font:16px/2 'Songti SC','STSong',serif;max-height:760px;overflow:auto;margin-bottom:20px}.source-body mark{background:var(--el-color-warning-light-7);color:inherit;border-bottom:2px solid var(--el-color-warning)}.hash{overflow-wrap:anywhere}.claim-item{width:100%;display:flex;flex-direction:column;gap:10px;text-align:left;padding:18px 12px;background:transparent;color:inherit;border:0;border-bottom:1px solid var(--el-border-color-lighter);cursor:pointer}.claim-item.active{background:var(--el-fill-color-light);box-shadow:inset 3px 0 var(--accent)}.claim-item strong{font-size:14px;line-height:1.8;font-weight:500}.claim-item small{color:var(--el-text-color-secondary);line-height:1.6}.claim-label{display:flex;justify-content:space-between;font:11px monospace}.evidence-pane h4{font-size:15px;line-height:1.8;margin:12px 0;overflow-wrap:anywhere}.evidence-pane p{font-size:13px;line-height:1.8;overflow-wrap:anywhere}.evidence-card{border-top:1px solid var(--el-border-color-lighter);margin-top:22px;padding-top:18px}.evidence-card a{color:var(--accent);text-decoration:underline;text-underline-offset:3px}.evidence-card blockquote{font:15px/1.9 'Songti SC','STSong',serif;margin:16px 0;padding:12px 16px;border-left:2px solid var(--accent);background:var(--el-fill-color-lighter);white-space:pre-wrap;overflow-wrap:anywhere}.check-list{padding:0;list-style:none;font-size:12px}.check-list li{margin:10px 0}.check-list strong,.check-list span{display:block;line-height:1.7}.check-list span{color:var(--el-text-color-secondary)}details{font-size:12px;line-height:1.8}summary{cursor:pointer;color:var(--accent)}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.8 monospace;max-height:350px;overflow:auto}.human-review,.deep-section{border-top:1px solid var(--el-border-color);margin-top:28px;padding-top:12px}.human-review>.el-select{width:100%;margin-bottom:12px}.human-review>.el-button{margin-top:12px}.deep-section>.el-checkbox{display:flex;margin:12px 0}.review-entry{font-size:12px;border-left:2px solid var(--el-border-color);padding-left:12px;margin:14px 0}.mobile-tabs{display:none}:deep(.el-checkbox){white-space:normal;height:auto}:deep(.el-checkbox__label){white-space:normal;line-height:1.7}.history-item:focus-visible,.claim-item:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.metrics{flex-wrap:wrap}.evidence-card{scroll-margin-top:24px}.evidence-card:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
@media(max-width:1100px){.review-grid{grid-template-columns:1fr 1.3fr}.source-pane{grid-column:1/-1}.source-body{max-height:240px}}@media(max-width:700px){.workbench-heading{align-items:flex-start}.workbench-heading h2{font-size:25px}.workbench-heading p{max-width:270px;line-height:1.8}.paper{padding:18px}.review-grid{display:block}.review-grid>.paper{display:none}.review-grid>.mobile-active{display:block}.mobile-tabs{display:flex;margin:20px 0 0;border-bottom:1px solid var(--el-border-color)}.mobile-tabs button{flex:1;border:0;background:transparent;color:var(--el-text-color-secondary);padding:14px 4px}.mobile-tabs button.active{color:var(--accent);border-bottom:2px solid var(--accent)}.metrics{justify-content:space-between;gap:10px}.history-item{gap:10px}.history-number{display:none}.configuration>.el-select,.configuration>.el-select:nth-child(2){width:100%}.section-heading{align-items:flex-start}.section-heading>.muted{display:none}.source-body{max-height:65vh}.selection-row{grid-template-columns:60px 1fr}.input-toolbar{gap:16px}}
@media(prefers-reduced-motion:no-preference){.paper{animation:appear .22s ease-out}@keyframes appear{from{opacity:.5;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}}
</style>
