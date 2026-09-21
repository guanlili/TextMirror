<template>
  <section
    class="fact-check-panel"
    data-testid="fact-check-panel"
    aria-label="事实核查"
  >
    <header class="panel-heading">
      <div><strong>事实核查</strong><span class="muted heading-note">独立证据报告 · 按需联网</span></div>
      <router-link
        v-if="user.isLoggedIn && user.hasPermission('fact-check:run')"
        :to="active ? `/fact-check/${active.id}` : { path: '/fact-check', query: recordId ? { record_id: recordId } : {} }"
      >
        进入事实核查工作台
      </router-link>
      <el-button
        text
        :aria-expanded="expanded"
        aria-controls="fact-check-content"
        data-testid="fact-check-toggle"
        @click="expanded = !expanded"
      >
        {{ expanded ? '收起事实核查' : '展开事实核查' }}
      </el-button>
    </header>
    <div
      v-if="expanded"
      id="fact-check-content"
      class="panel-content"
    >
      <p class="scope-note">
        核查对象为这条记录的原始版本，不是采纳修改后的文本。结论与建议仅供人工参考，不参与一键采纳。
      </p>
      <p class="muted">
        联网搜索、可信信源两种模式都会联网，向外部服务发送待核查内容；可信信源严格限定可采纳证据的域名与路径，但模型搜索可能访问其他站点，并非离线或封闭检索。
      </p>
      <p
        v-if="busy === 'load'"
        role="status"
      >
        正在读取核查配置与历史…
      </p>
      <p
        v-if="blockedReason"
        class="warning-text"
        role="status"
        data-testid="fact-check-blocked"
      >
        {{ blockedReason }}
      </p>
      <p
        v-if="options"
        class="muted"
        data-testid="fact-check-current-provider"
      >
        当前搜索服务：{{ providerLabels[options.provider] || '未记录' }} · 当前模型配置：{{ options.model_name || '未配置模型' }}
      </p>
      <p
        v-if="options"
        class="muted"
      >
        每次最多核查 {{ options.max_claims }} 条事实 · 每日最多 {{ options.daily_limit }} 次 · 原文 {{ textLength }} / {{ textLimit }} 字符
      </p>
      <div class="setup-grid">
        <el-form-item label="核查模式">
          <el-select
            v-model="mode"
            aria-label="事实核查模式"
            :disabled="controlsLocked"
          >
            <el-option
              label="联网搜索"
              value="web"
            /><el-option
              label="可信信源"
              value="trusted"
            />
          </el-select>
        </el-form-item>
        <el-form-item
          v-if="mode === 'trusted'"
          label="可信信源"
        >
          <el-select
            v-model="sourceIds"
            multiple
            aria-label="选择可信信源"
            placeholder="请选择已启用的信源"
            :disabled="controlsLocked"
          >
            <el-option
              v-for="source in enabledSources"
              :key="source.id"
              :value="source.id"
              :label="`${source.name} · ${source.domain}${source.path_prefix}`"
            />
          </el-select>
        </el-form-item>
      </div>
      <el-checkbox
        v-model="consented"
        :disabled="busy === 'start'"
        data-testid="fact-check-consent"
        aria-label="内容可对外检索；涉密材料请勿使用"
      >
        内容可对外检索；涉密材料请勿使用
      </el-checkbox>
      <div class="actions">
        <el-button
          type="primary"
          :disabled="!canStart"
          :loading="busy === 'start'"
          data-testid="fact-check-start"
          @click="startRun"
        >
          {{ pendingCreate ? '重试启动（同一请求）' : '开始事实核查' }}
        </el-button>
        <el-button
          :disabled="!user.isLoggedIn || recordId === null || !!busy"
          @click="loadPanel"
        >
          刷新配置与历史
        </el-button>
        <span
          v-if="!consented"
          class="muted"
        >请先确认内容可以对外检索，再点击启动。</span>
      </div>
      <p
        v-if="pendingCreate && busy !== 'start'"
        class="warning-text"
      >
        启动结果尚未确认；重试将使用同一请求编号，不会主动创建重复任务。也可刷新历史确认状态。
      </p>
      <div
        v-if="error"
        class="error-box"
        role="alert"
        data-testid="fact-check-error"
      >
        <p>{{ error }}</p>
        <el-button
          v-if="errorKind === 'load'"
          :disabled="!!busy"
          @click="loadPanel"
        >
          重试加载
        </el-button>
        <el-button
          v-if="errorKind === 'poll' || errorKind === 'cancel'"
          :disabled="!!busy || authExpired"
          @click="refreshRun"
        >
          重试读取状态
        </el-button>
      </div>
      <div class="history-row">
        <span class="muted">最近 20 次</span>
        <el-select
          :model-value="active?.id"
          aria-label="事实核查历史"
          placeholder="暂无核查历史"
          :disabled="busy === 'load' || busy === 'start' || busy === 'cancel'"
          @update:model-value="selectRun"
        >
          <el-option
            v-for="run in history"
            :key="run.id"
            :value="run.id"
            :label="`#${run.id} · ${formatDate(run.created_at)} · ${modeLabels[run.mode]} · ${providerLabels[run.provider] || '未记录'} · ${runStatusLabel(run, true)}`"
          />
        </el-select>
        <el-button
          v-if="otherRunning"
          @click="selectRun(otherRunning.id)"
        >
          查看运行中任务
        </el-button>
      </div>
      <p class="muted">
        打开面板只读取配置与历史，不会启动核查。收起或离开不取消服务端任务；重新打开原记录并展开面板可恢复状态。
      </p>
      <section
        v-if="active"
        class="run-report"
        aria-label="事实核查报告"
        data-testid="fact-check-report"
      >
        <div class="run-heading">
          <strong>#{{ active.id }} · {{ modeLabels[active.mode] }}</strong>
          <el-tag :type="active.status === 'FAILURE' ? 'danger' : active.status === 'SUCCESS' ? (active.result?.coverage.status === 'partial' ? 'warning' : 'success') : 'info'">
            {{ runStatusLabel(active) }}
          </el-tag>
          <el-button
            v-if="isRunning(active)"
            :disabled="busy === 'cancel' || busy === 'start' || authExpired"
            :loading="busy === 'cancel'"
            data-testid="fact-check-cancel"
            @click="cancelRun"
          >
            取消核查
          </el-button>
        </div>
        <p
          class="muted"
          data-testid="fact-check-run-provider"
        >
          本次搜索服务：{{ providerLabels[active.provider] || '未记录' }}
        </p>
        <p
          v-if="runMessage"
          :class="active.status === 'FAILURE' || (active.status === 'SUCCESS' && active.result?.coverage.status === 'partial') ? 'warning-text' : 'muted'"
        >
          {{ runMessage }}
        </p>
        <p
          v-if="active.error_code"
          class="muted"
        >
          错误代码：{{ active.error_code }}
        </p>
        <el-progress
          v-if="isRunning(active)"
          :percentage="Math.min(100, Math.max(0, active.progress || 0))"
          :stroke-width="6"
        />
        <p class="muted">
          创建：{{ formatDate(active.created_at) }}<template v-if="active.finished_at">
            · 结束：{{ formatDate(active.finished_at) }}
          </template>
        </p>
        <template v-if="active.result">
          <div
            class="coverage"
            data-testid="fact-check-coverage"
          >
            <strong>{{ coverageLabel }}</strong>
            <span>识别 {{ active.result.coverage.extracted }} · 已检查 {{ active.result.coverage.checked }} · 未检查 {{ active.result.coverage.unverified }}</span>
          </div>
          <p class="muted">
            仅覆盖本次预算范围，不保证识别或核查全文所有事实。“证据不足”不是错误，也不等于原文正确。
          </p>
          <p
            v-if="active.result.claims.length"
            class="muted"
          >
            模型的搜索回答与搜索元数据本身不是证据，请核对引用原文。
          </p>
          <p class="muted">
            口径检查是模型依据所提供正文的评估；程序校验结构、逐字引文及保守约束，并未独立验证语义。反证轮完成不代表找到反证，未找到反证也不等于证实原文。
          </p>
          <p
            v-if="active.result.coverage.reason"
            class="muted"
          >
            {{ active.result.coverage.reason }}
          </p>
          <p
            v-if="!active.result.claims.length"
            class="muted"
          >
            {{ emptyMessage }}
          </p>
          <ol
            class="claims"
            aria-label="事实条目"
          >
            <li
              v-for="(claim, index) in active.result.claims"
              :key="claim.id"
              class="claim"
            >
              <div class="claim-heading">
                <el-tag
                  size="small"
                  :type="verdictTypes[claim.verdict]"
                >
                  {{ verdictLabels[claim.verdict] }}
                </el-tag>
                <span
                  v-if="!claim.checked"
                  class="muted"
                >未检查</span>
                <button
                  type="button"
                  class="claim-title"
                  :aria-expanded="selectedClaim === index"
                  data-testid="fact-check-claim"
                  @click="selectedClaim = selectedClaim === index ? null : index"
                >
                  {{ claim.statement || claim.original }}
                </button>
              </div>
              <p>{{ claim.reason }}</p>
              <div
                v-if="claim.search_rounds?.length"
                class="muted"
                data-testid="fact-check-search-rounds"
                aria-live="polite"
              >
                <p
                  v-for="round in claim.search_rounds"
                  :key="round.kind"
                  :class="round.status === 'failed' || round.status === 'partial' ? 'warning-text' : 'muted'"
                >
                  {{ round.kind === 'followup' ? '补充核查轮' : round.kind === 'counter' ? '反证/更正轮' : '初始检索轮' }}：{{ roundStatusLabels[round.status] }} · 抓取 {{ round.pages_fetched }} 页
                  <span v-if="round.error_codes.length"> · {{ round.error_codes.join('、') }}</span>
                  <span v-if="selectedClaim === index"> · 查询：{{ round.query }}</span>
                </p>
              </div>
              <p
                v-else
                class="muted"
              >
                此报告未记录反证轮状态，不能视为已完成反证检索。
              </p>
              <template v-if="selectedClaim === index">
                <p class="muted">
                  原始版本上下文 · Unicode 位置 [{{ claim.start }}, {{ claim.end }})
                </p>
                <p
                  v-if="claimContext(claim)"
                  class="source-context"
                  data-testid="fact-check-context"
                >
                  {{ claimContext(claim)!.before }}<mark>{{ claimContext(claim)!.target }}</mark>{{ claimContext(claim)!.after }}
                </p>
                <p
                  v-else
                  class="warning-text"
                >
                  位置与原文不匹配，无法安全高亮，请人工核对：{{ claim.original }}
                </p>
                <p
                  v-if="claim.suggestion"
                  class="suggestion"
                >
                  人工参考建议：{{ claim.suggestion }}
                </p>
                <FactCheckSearchTrace
                  :claim="claim"
                  @show-evidence="id => showEvidence(claim.id, id)"
                />
                <ul
                  class="evidence-list"
                  aria-label="核查证据"
                >
                  <li
                    v-for="evidence in claim.evidence"
                    :id="evidenceAnchor(claim.id, evidence.id)"
                    :key="evidence.id"
                    tabindex="-1"
                  >
                    <div class="evidence-heading">
                      <span class="muted">{{ stanceLabels[evidence.stance] }}</span>
                      <a
                        v-if="safeEvidenceUrl(evidence.url)"
                        :href="safeEvidenceUrl(evidence.url)"
                        target="_blank"
                        rel="noopener noreferrer"
                        data-testid="fact-check-evidence-link"
                      >{{ evidence.title || '查看证据来源' }}</a>
                      <span v-else>{{ evidence.title || '证据来源' }}（链接不可用）</span>
                    </div>
                    <blockquote>{{ evidence.quote }}</blockquote>
                    <div
                      v-if="evidence.checks"
                      class="muted"
                      data-testid="fact-check-evidence-checks"
                    >
                      <p
                        v-for="(label, key) in checkLabels"
                        :key="key"
                      >
                        {{ label }}：{{ consistencyLabels[evidence.checks[key].status] }} · {{ evidence.checks[key].reason }}
                      </p>
                    </div>
                    <p
                      v-else
                      class="muted"
                    >
                      此报告未记录结构化口径检查，不能视为检查通过。
                    </p>
                    <template v-if="evidence.body_hash_scope === 'normalized_model_visible_text_utf8' && evidence.body_sha256 && evidence.quote_start != null && evidence.quote_end != null && evidence.context_before != null && evidence.context_after != null">
                      <p class="muted">
                        引文在模型可见正文中的首次匹配 · Unicode 位置 [{{ evidence.quote_start }}, {{ evidence.quote_end }}) · 前后各最多 120 字符
                      </p>
                      <p
                        class="source-context"
                        data-testid="fact-check-quote-context"
                      >
                        {{ evidence.context_before }}<mark>{{ evidence.quote }}</mark>{{ evidence.context_after }}
                      </p>
                      <details
                        class="usage"
                        data-testid="fact-check-body-hash"
                      >
                        <summary>正文 SHA-256 与范围</summary>
                        <p>哈希范围：送给模型的归一化正文前缀（{{ evidence.body_text_length }} 个 Unicode 码点，UTF-8 编码），不是原始 HTML 或完整网页。</p>
                        <p>{{ evidence.body_sha256 }}</p>
                      </details>
                    </template>
                    <p
                      v-else
                      class="muted"
                    >
                      此报告未记录正文指纹或引文上下文。
                    </p>
                    <details
                      v-if="evidence.body_text"
                      class="usage"
                    >
                      <summary>查看当次模型可见正文</summary><pre class="body-snapshot">{{ evidence.body_text }}</pre>
                    </details>
                    <p
                      v-else
                      class="muted"
                    >
                      历史报告未保存正文快照，不能复现当时全文依据。
                    </p>
                    <p class="muted">
                      {{ evidence.publisher || '发布方未提供' }} · 发布：{{ evidence.published_at ? formatDate(evidence.published_at) : '未提供' }} · 检索：{{ formatDate(evidence.retrieved_at) }}
                    </p>
                  </li>
                </ul>
                <p
                  v-if="!claim.evidence.length"
                  class="muted"
                >
                  没有可展示的证据，请勿将此视为事实保证。
                </p>
              </template>
            </li>
          </ol>
          <details class="usage">
            <summary>核查时间与用量</summary>
            <p>核查：{{ formatDate(active.result.checked_at) }} · 查询 {{ active.result.usage.search_queries }} 次 · 抓取 {{ active.result.usage.pages_fetched }} 页</p>
            <p>Token：输入 {{ active.result.usage.prompt_tokens }} / 输出 {{ active.result.usage.completion_tokens }} / 合计 {{ active.result.usage.total_tokens }}</p>
            <p>原始版本指纹：{{ active.source_hash }}</p>
          </details>
        </template>
      </section>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElButton, ElCheckbox, ElFormItem, ElOption, ElProgress, ElSelect, ElTag } from 'element-plus'
import { useUserStore } from '@/stores/user'
import { getReviewErrorDetail } from '@/api/review'
import FactCheckSearchTrace from '@/components/FactCheckSearchTrace.vue'
import {
  cancelFactCheckRunApi, createFactCheckId, createFactCheckRunApi, getFactCheckOptionsApi, getFactCheckRunApi, listFactCheckRunsApi,
  type CreateFactCheckPayload, type FactCheckClaim, type FactCheckMode, type FactCheckOptions, type FactCheckRun,
} from '@/api/factCheck'

const props = defineProps<{ recordId: number | null; sourceText: string }>()
const emit = defineEmits<{ started: [recordId: number] }>()
const user = useUserStore()
const expanded = ref(false)
const options = ref<FactCheckOptions | null>(null)
const history = ref<FactCheckRun[]>([])
const active = ref<FactCheckRun | null>(null)
const selectedClaim = ref<number | null>(null)
const mode = ref<FactCheckMode>('web')
const sourceIds = ref<string[]>([])
const consented = ref(false)
const busy = ref<'' | 'load' | 'start' | 'poll' | 'cancel'>('')
const error = ref('')
const errorKind = ref('')
const authExpired = ref(false)
const pendingCreate = ref<CreateFactCheckPayload | null>(null)
let sequence = 0
let controller: AbortController | null = null
let timer: ReturnType<typeof setTimeout> | null = null
let alive = true
const statusLabels = { PENDING: '等待核查', RUNNING: '正在核查', WAITING_CONFIRMATION: '待确认事实，请进入工作台', SUCCESS: '核查完成', FAILURE: '核查失败', CANCELLED: '已取消' }
const modeLabels = { web: '联网搜索', trusted: '可信信源' }
const providerLabels = { model: '模型原生联网', tavily: 'Tavily' }
const verdictLabels = { supported: '证据支持', refuted: '证据反驳', insufficient: '证据不足', conflicting: '证据冲突' }
const verdictTypes = { supported: 'success', refuted: 'danger', insufficient: 'info', conflicting: 'warning' } as const
const stanceLabels = { supports: '支持', refutes: '反驳', context: '背景' }
const roundStatusLabels = { pending: '未执行', searching: '检索中', fetching: '抓取中', complete: '检索与抓取完成', partial: '抓取不完整', failed: '检索失败' }
const checkLabels = { subject: '主体/事件', event_time: '事件时间', scope_unit: '统计范围/单位' } as const
const consistencyLabels = { match: '一致', mismatch: '不一致', unknown: '无法确定', not_applicable: '不适用' }
const textLength = computed(() => Array.from(props.sourceText).length)
const textLimit = computed(() => Math.min(20000, options.value?.max_text_chars ?? 20000))
const enabledSources = computed(() => options.value?.sources.filter(source => source.is_enabled) ?? [])
const isRunning = (run: FactCheckRun | null) => run?.status === 'PENDING' || run?.status === 'RUNNING'
const otherRunning = computed(() => history.value.find(run => run.id !== active.value?.id && isRunning(run)))
const controlsLocked = computed(() => !!busy.value || !!pendingCreate.value)
const blockedReason = computed(() => {
  if (!user.isLoggedIn || authExpired.value) return '请先登录或重新登录后使用事实核查。'
  if (props.recordId === null) return '当前结果没有可核查的记录，请登录后重新校对并打开原记录。'
  if (textLength.value > textLimit.value) return `原文超过 ${textLimit.value} 字符，暂不支持事实核查；不会截断后提交。`
  if (!props.sourceText.trim()) return '当前记录原文为空，无法核查。'
  if (!options.value) return busy.value === 'load' ? '' : '尚未取得核查配置，请加载或重试。'
  if (!options.value.available) return `事实核查不可用：${options.value.unavailable_reason || '管理员尚未启用或配置搜索服务。'}`
  if (mode.value === 'trusted' && !enabledSources.value.length) return '没有已启用的可信信源，请联系管理员配置，或选择联网搜索。'
  if (mode.value === 'trusted' && (!sourceIds.value.length || sourceIds.value.some(id => !enabledSources.value.some(source => source.id === id)))) return '请至少选择一个已启用的可信信源。'
  if (typeof globalThis.crypto?.getRandomValues !== 'function') return '浏览器不支持安全随机数，请更换浏览器后重试。'
  return ''
})
const canStart = computed(() => expanded.value && !busy.value && !blockedReason.value && !!options.value?.available && consented.value
  && (!!pendingCreate.value || !history.value.some(isRunning)))

function invalidate() {
  sequence++
  controller?.abort()
  controller = null
  if (timer !== null) clearTimeout(timer)
  timer = null
  busy.value = ''
}
function begin(kind: typeof busy.value) {
  invalidate()
  controller = new AbortController()
  busy.value = kind
  error.value = ''
  errorKind.value = ''
  return { token: sequence, signal: controller.signal }
}
function current(token: number) { return alive && expanded.value && token === sequence }
function showError(cause: unknown, kind: string) {
  authExpired.value = (cause as { response?: { status?: number } })?.response?.status === 401
  errorKind.value = kind
  error.value = authExpired.value ? '登录已过期，请重新登录后读取核查状态。' : `${kind === 'poll' ? '状态读取失败，已暂停自动刷新：' : ''}${getReviewErrorDetail(cause)}`
}
function acceptRun(run: FactCheckRun, expectedId?: number) {
  if (run.record_id !== props.recordId || (expectedId !== undefined && run.id !== expectedId)) throw new Error('返回的核查任务与当前记录不匹配，请重新读取。')
  active.value = run
  history.value = [run, ...history.value.filter(item => item.id !== run.id)].sort((a, b) => b.id - a.id).slice(0, 20)
}
function schedulePoll() {
  if (!alive || !expanded.value || !isRunning(active.value) || error.value || authExpired.value) return
  timer = setTimeout(() => { timer = null; void refreshRun() }, 1800)
}
async function loadPanel() {
  if (!alive || !expanded.value || !user.isLoggedIn || props.recordId === null) return
  const recordId = props.recordId
  const { token, signal } = begin('load')
  options.value = null
  active.value = null
  history.value = []
  selectedClaim.value = null
  try {
    const [configuration, runs] = await Promise.all([getFactCheckOptionsApi({ signal }), listFactCheckRunsApi(recordId, { signal })])
    if (!current(token)) return
    if (runs.some(run => run.record_id !== recordId)) throw new Error('核查历史与当前记录不匹配。')
    options.value = configuration
    sourceIds.value = configuration.sources.filter(source => source.is_enabled).map(source => source.id)
    history.value = [...runs].sort((a, b) => b.id - a.id).slice(0, 20)
    active.value = history.value.find(isRunning) ?? history.value[0] ?? null
    authExpired.value = false
  } catch (cause) {
    if (current(token)) showError(cause, 'load')
  } finally {
    if (current(token)) { busy.value = ''; schedulePoll() }
  }
}
async function refreshRun() {
  if (!active.value || !expanded.value || !user.isLoggedIn || authExpired.value) return
  const id = active.value.id
  const { token, signal } = begin('poll')
  try {
    const run = await getFactCheckRunApi(id, { signal })
    if (current(token)) acceptRun(run, id)
  } catch (cause) {
    if (current(token)) showError(cause, 'poll')
  } finally {
    if (current(token)) { busy.value = ''; schedulePoll() }
  }
}
function selectRun(id: number) {
  if (busy.value === 'start' || busy.value === 'cancel' || busy.value === 'load') return
  const run = history.value.find(item => item.id === id)
  if (!run) return
  invalidate()
  active.value = run
  selectedClaim.value = null
  void refreshRun()
}
async function startRun() {
  if (!canStart.value || props.recordId === null) return
  pendingCreate.value ??= {
    record_id: props.recordId, mode: mode.value, source_ids: mode.value === 'trusted' ? [...sourceIds.value] : [],
    allow_external_search: true, request_id: createFactCheckId(),
  }
  const payload = pendingCreate.value
  const { token, signal } = begin('start')
  try {
    const run = await createFactCheckRunApi(payload, { signal })
    if (!current(token)) return
    acceptRun(run)
    pendingCreate.value = null
    selectedClaim.value = null
    if (run.record_id !== null) emit('started', run.record_id)
  } catch (cause) {
    if (!current(token)) return
    const status = (cause as { response?: { status?: number } })?.response?.status
    // 确定被拒绝的请求可重新填写；断网/超时/服务端错误保留原编号，供显式幂等重试。
    if (status && status >= 400 && status < 500 && status !== 408) pendingCreate.value = null
    showError(cause, 'start')
  } finally {
    if (current(token)) { busy.value = ''; schedulePoll() }
  }
}
async function cancelRun() {
  if (!active.value || !isRunning(active.value) || busy.value === 'start' || busy.value === 'cancel' || authExpired.value) return
  const id = active.value.id
  const { token, signal } = begin('cancel')
  try {
    const run = await cancelFactCheckRunApi(id, { signal })
    if (current(token)) acceptRun(run, id)
  } catch (cause) {
    if (current(token)) showError(cause, 'cancel')
  } finally {
    if (current(token)) { busy.value = ''; schedulePoll() }
  }
}
function claimContext(claim: FactCheckClaim) {
  const chars = Array.from(props.sourceText)
  const { start, end, original } = claim
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start || end > chars.length || !original || chars.slice(start, end).join('') !== original) return null
  return {
    before: `${start > 36 ? '…' : ''}${chars.slice(Math.max(0, start - 36), start).join('')}`,
    target: original,
    after: `${chars.slice(end, end + 36).join('')}${end + 36 < chars.length ? '…' : ''}`,
  }
}
function safeEvidenceUrl(raw: string): string {
  if (typeof raw !== 'string' || !/^https?:\/\//i.test(raw.trim()) || Array.from(raw.trim()).some(char => char.charCodeAt(0) <= 32 || char.charCodeAt(0) === 127)) return ''
  try {
    const url = new URL(raw.trim())
    return (url.protocol === 'http:' || url.protocol === 'https:') && !url.username && !url.password ? url.href : ''
  } catch { return '' }
}
function evidenceAnchor(claimId: string, id: string) { return `panel-evidence-${active.value?.id}-${claimId}-${id}` }
function showEvidence(claimId: string, id: string) {
  const target = document.getElementById(evidenceAnchor(claimId, id))
  target?.scrollIntoView({ block: 'start' }); target?.focus({ preventScroll: true })
}
// 兼容旧版 SUCCESS + partial 空报告，仅调整展示，不改写保存的状态。
function extractionFailed(run: FactCheckRun) {
  return (run.status === 'FAILURE' && run.error_code === 'EXTRACTION_LOCATION_FAILED')
    || (run.status === 'SUCCESS' && run.result?.coverage.status === 'partial' && run.result.claims.length === 0)
}
function noFacts(run: FactCheckRun) {
  return run.status === 'SUCCESS' && run.result?.coverage.status === 'complete' && run.result.claims.length === 0
}
function fetchBlocked(run: FactCheckRun) {
  return run.status === 'SUCCESS' && run.result?.usage.pages_fetched === 0
    && run.result.claims.some(claim => claim.search_rounds?.some(round => round.error_codes.length))
}
function runStatusLabel(run: FactCheckRun, summary = false) {
  if (summary && run.status === 'SUCCESS' && !run.result) return '任务已结束，查看报告'
  if (extractionFailed(run)) return '事实提取失败'
  if (noFacts(run)) return '未识别到可核查事实'
  if (fetchBlocked(run)) return '核查受阻'
  return run.status === 'SUCCESS' && run.result?.coverage.status === 'partial' ? '核查部分完成' : statusLabels[run.status]
}
const runMessage = computed(() => {
  const run = active.value
  if (!run) return ''
  if (extractionFailed(run)) return '事实提取失败：未能获得可准确定位到原文的事实项；本次未完成事实核查，不代表全文没有事实或事实正确。'
  if (noFacts(run)) return '未识别到可核查事实；本次未进行搜索或证据核查，不代表全文事实正确。'
  if (fetchBlocked(run)) return '搜索或正文读取受阻，未取得可核对的正文；任务已结束，但不能形成可靠结论。'
  return run.status === 'SUCCESS' && run.result?.coverage.status === 'partial'
    ? '任务已结束，但部分核查未完整完成；请查看各条事实的资料处理情况。' : run.message
})
const coverageLabel = computed(() => {
  const run = active.value
  if (!run) return ''
  if (run.status !== 'SUCCESS' || extractionFailed(run) || noFacts(run) || fetchBlocked(run)) return runStatusLabel(run)
  return run.result?.coverage.status === 'partial' ? '部分完成' : '预算范围内完成'
})
const emptyMessage = computed(() => {
  const run = active.value
  if (run && (extractionFailed(run) || noFacts(run))) return runMessage.value
  if (isRunning(run)) return '任务尚未完成，暂无法展示事实项；不代表全文没有可核查事实。'
  if (run?.status === 'WAITING_CONFIRMATION') return '任务待确认，当前没有可展示的事实项；尚未完成核查。'
  if (run?.status === 'CANCELLED') return '核查已取消，没有可展示的事实项；不代表全文事实正确。'
  if (run?.status === 'FAILURE') return '核查失败，没有可展示的事实项；不能据此判断原文是否包含可核查事实。'
  return '本次没有可展示的事实条目，不代表全文事实正确。'
})
function formatDate(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}
watch(expanded, value => { if (value) void loadPanel(); else invalidate() }, { flush: 'sync' })
watch(() => [props.recordId, props.sourceText, user.token], () => {
  invalidate()
  options.value = null
  history.value = []
  active.value = null
  selectedClaim.value = null
  pendingCreate.value = null
  consented.value = false
  error.value = ''
  errorKind.value = ''
  authExpired.value = false
  if (expanded.value) void loadPanel()
}, { flush: 'sync' })
onBeforeUnmount(() => { alive = false; invalidate() })
</script>

<style scoped>
.fact-check-panel { margin: 16px 0; border: 1px solid var(--el-border-color); border-radius: 6px; background: var(--el-bg-color); color: var(--el-text-color-primary); font-size: 14px; line-height: 1.65; }
.panel-heading { padding: 10px 16px; justify-content: space-between; }
.panel-heading, .actions, .history-row, .run-heading, .claim-heading, .evidence-heading, .coverage { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
.heading-note { margin-left: 12px; }
.panel-content { border-top: 1px solid var(--el-border-color-lighter); padding: 0 16px 16px; }
p { margin: 10px 0; overflow-wrap: anywhere; }
.muted, .usage { color: var(--el-text-color-secondary); font-size: 12px; }
.scope-note { padding: 10px 12px; background: var(--el-fill-color-light); border-left: 2px solid var(--el-border-color); }
.setup-grid { display: grid; grid-template-columns: minmax(180px, 240px) minmax(240px, 1fr); gap: 0 20px; margin-top: 16px; }
.setup-grid .el-form-item { display: block; }
.setup-grid :deep(.el-form-item__label) { display: block; text-align: left; }
.actions { margin: 12px 0 16px; }
.actions :deep(.el-button + .el-button) { margin-left: 0; }
.history-row { padding-top: 16px; border-top: 1px solid var(--el-border-color-lighter); }
.history-row .el-select { width: min(100%, 480px); }
.run-report { margin-top: 20px; }
.coverage { padding: 12px 0; border-bottom: 1px solid var(--el-border-color-lighter); }
.claims, .evidence-list { list-style: none; margin: 0; padding: 0; }
.claim { padding: 16px 0; border-bottom: 1px solid var(--el-border-color-lighter); }
.claim-title { flex: 1; min-width: 160px; padding: 0; border: 0; background: transparent; color: var(--el-text-color-primary); font: inherit; font-weight: 600; cursor: pointer; text-align: left; overflow-wrap: anywhere; }
.claim-title:hover { color: var(--el-color-primary); }
.claim-title:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 4px; }
.source-context { white-space: pre-wrap; padding: 12px 14px; background: var(--el-fill-color-light); line-height: 1.9; }
mark { background: var(--el-color-warning-light-7); color: inherit; border-bottom: 2px solid var(--el-color-warning); }
.suggestion { font-size: 13px; }
.evidence-list li { padding: 12px 0 4px 14px; border-left: 2px solid var(--el-border-color-lighter); margin-top: 12px; }
.evidence-heading a { color: var(--el-color-primary); text-decoration: underline; text-underline-offset: 3px; overflow-wrap: anywhere; }
blockquote { white-space: pre-wrap; overflow-wrap: anywhere; margin: 8px 0; }
.usage { margin-top: 16px; overflow-wrap: anywhere; }
.usage summary { cursor: pointer; }
.body-snapshot { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 350px; overflow: auto; font: 12px/1.8 monospace; }
.evidence-list li { scroll-margin-top: 24px; }
.evidence-list li:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 3px; }
.warning-text { color: var(--el-color-warning-dark-2); font-size: 13px; }
.error-box { padding: 8px 12px; margin: 12px 0; background: var(--el-color-danger-light-9); color: var(--el-color-danger); }
.fact-check-panel :deep(.el-checkbox) { white-space: normal; height: auto; }
.fact-check-panel :deep(.el-checkbox__label) { white-space: normal; line-height: 1.65; }
@media (max-width: 640px) {
  .setup-grid { grid-template-columns: minmax(0, 1fr); }
  .heading-note { display: block; margin-left: 0; }
  .panel-heading, .panel-content { padding-left: 12px; padding-right: 12px; }
}
</style>
