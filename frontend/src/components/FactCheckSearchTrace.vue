<template>
  <section
    class="search-trace"
    aria-label="检索资料与佐证"
    data-testid="fact-check-search-trace"
  >
    <header class="trace-heading">
      <h4>检索资料与佐证</h4><span>SEARCH → EVIDENCE</span>
    </header>
    <div class="trace-counts">
      <span>候选链接 <b>{{ traced ? sourceCount : '未记录' }}</b></span><span>已引用材料 <b>{{ claim.evidence.length }}</b></span>
    </div>
    <p class="trace-note">
      搜到不等于证实，标题仅为资料线索。请结合逐字正文引文与口径检查判断支持、反驳或仅作背景；重复材料不计作独立佐证。
    </p>
    <p class="trace-note">
      这里只展示检索接口返回的候选资料，不代表模型内部访问的全部网页。
    </p>
    <p
      v-if="!rounds.length"
      class="trace-notice"
    >
      此报告未记录检索过程，不能还原当时搜索过哪些资料。
    </p>
    <article
      v-for="(round, roundIndex) in rounds"
      :key="round.kind"
      class="search-round"
      :data-status="round.status"
    >
      <header class="round-heading">
        <strong><span class="round-number">{{ String(roundIndex + 1).padStart(2, '0') }}</span>{{ roundLabels[round.kind] }}</strong><span>{{ roundStatusLabels[round.status] }}</span>
      </header>
      <p class="search-query">
        <span>搜索词</span>{{ round.query }}
      </p>
      <p class="trace-note">
        本轮取得正文 {{ round.pages_fetched }} 页<template v-if="round.error_codes.length">
          · {{ round.error_codes.join('、') }}
        </template>
      </p>
      <p
        v-if="round.sources == null"
        class="trace-notice"
      >
        历史记录未保存候选资料列表，无法补还原当时搜索的链接；不表示没有搜索。
      </p>
      <p
        v-else-if="!round.sources.length"
        class="trace-notice"
      >
        {{ emptyRoundLabel(round.status) }}
      </p>
      <ol
        v-else
        class="source-list"
        :aria-label="`${roundLabels[round.kind]}的资料`"
      >
        <li
          v-for="(source, sourceIndex) in round.sources"
          :key="`${sourceIndex}-${source.url}`"
          class="source-card"
          :data-status="source.status"
          data-testid="fact-check-search-source"
        >
          <div class="source-heading">
            <span class="source-number">{{ roundIndex + 1 }}.{{ sourceIndex + 1 }}</span><span class="source-status">{{ sourceStatusLabels[source.status] }}</span><span
              v-if="source.origin === 'supplemental'"
              class="source-origin"
            >用户补充</span>
          </div>
          <h5>
            <a
              v-if="sourceLink(source)"
              :href="sourceLink(source)"
              target="_blank"
              rel="noopener noreferrer"
            >{{ source.title || '未提供标题' }}</a><span v-else>{{ source.title || '未提供标题' }}</span>
          </h5>
          <p class="source-url">
            {{ source.url }}
          </p>
          <p class="source-reason">
            {{ source.reason || defaultReason(source) }}<code v-if="source.error_code"> · {{ source.error_code }}</code>
          </p>
          <template
            v-for="evidence in sourceEvidence(source)"
            :key="evidence.id"
          >
            <div
              class="source-relation"
              :data-stance="evidence.stance"
            >
              <strong>{{ stanceLabels[evidence.stance] }}</strong><span>引用 {{ evidence.id }}</span>
            </div>
            <blockquote>{{ evidence.quote }}</blockquote>
            <details
              v-if="evidence.checks"
              class="source-checks"
            >
              <summary>如何与这条事实对应？</summary><dl>
                <template
                  v-for="(label, key) in checkLabels"
                  :key="key"
                >
                  <dt>{{ label }} · {{ consistencyLabels[evidence.checks[key].status] }}</dt><dd>{{ evidence.checks[key].reason }}</dd>
                </template>
              </dl><p class="trace-note">
                以上是模型依据正文的语义评估，不是程序独立验证。
              </p>
            </details>
            <p
              v-else
              class="trace-note"
            >
              此历史证据未记录逐项检查，不能补造佐证理由。
            </p>
            <button
              type="button"
              class="evidence-jump"
              @click="emit('show-evidence', evidence.id)"
            >
              查看证据详情与原文快照 →
            </button>
          </template>
          <p
            v-if="!sourceEvidence(source).length"
            class="not-evidence"
          >
            {{ source.status === 'failed' ? '未读到正文，无法判断支持或反驳。' : source.status === 'pending' ? '尚未形成正文证据。' : '未作为本次结论的引用依据；不等于该资料无关或陈述为假。' }}
          </p>
        </li>
      </ol>
    </article>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { FactCheckClaim, FactSearchRound, FactSearchSource } from '@/api/factCheck'

const props = defineProps<{ claim: FactCheckClaim }>()
const emit = defineEmits<{ 'show-evidence': [id: string] }>()
const rounds = computed(() => props.claim.search_rounds || [])
const traced = computed(() => rounds.value.some(round => round.sources != null))
const sourceCount = computed(() => new Set(rounds.value.flatMap(round => round.sources?.map(source => source.url) || [])).size)
const roundLabels = { initial: '初始检索', counter: '反证与更正', followup: '原始来源与口径补充' }
const roundStatusLabels = { pending: '尚未检索', searching: '正在搜索', fetching: '正在读取资料', complete: '本轮执行完成', partial: '部分资料抓取受阻', failed: '检索失败' }
const sourceStatusLabels = { pending: '等待读取', fetched: '正文已读取', failed: '抓取受阻', duplicate: '重复资料', skipped: '未读取' }
const stanceLabels = { supports: '支持这条事实', refutes: '反驳这条事实', context: '仅作背景，不能单独支持或反驳' }
const checkLabels = { subject: '主体 / 事件', event_time: '事件时间', scope_unit: '统计范围 / 单位' }
const consistencyLabels = { match: '一致', mismatch: '不一致', unknown: '无法确定', not_applicable: '不适用' }

function sourceEvidence(source: FactSearchSource) {
  return props.claim.evidence.filter(evidence => evidence.id === source.evidence_id)
}
function sourceLink(source: FactSearchSource) {
  if (source.status !== 'fetched' && !(source.status === 'duplicate' && sourceEvidence(source).length)) return ''
  if (!/^https?:\/\//i.test(source.url) || /\s|\\/.test(source.url)) return ''
  try {
    const url = new URL(source.url)
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : ''
  } catch { return '' }
}
function emptyRoundLabel(status: FactSearchRound['status']) {
  if (status === 'pending' || status === 'searching') return '尚未取得搜索结果。'
  if (status === 'failed') return '本轮搜索失败，未取得候选资料。'
  return '本轮未返回候选资料；检索不到不等于事实为假。'
}
function defaultReason(source: FactSearchSource) {
  if (source.status === 'pending') return '等待正文抓取；未读取或未完成判定。'
  if (source.status === 'fetched') return props.claim.checked ? '正文已读取，未记录额外处理说明。' : '正文已读取，等待合并判定。'
  if (source.status === 'duplicate') return '与已处理资料重复，不作为独立交叉佐证。'
  if (source.status === 'skipped') return '未纳入本轮正文抓取。'
  return '正文抓取失败，搜索标题与摘要不能代替证据。'
}
</script>

<style scoped>
.search-trace{--trace-accent:var(--accent,#247f77);margin:24px 0;color:var(--el-text-color-primary);font-size:13px;line-height:1.7;min-width:0}.trace-heading,.round-heading,.source-heading,.source-relation{display:flex;align-items:baseline;justify-content:space-between;gap:10px;flex-wrap:wrap}.trace-heading h4{font-size:15px;margin:0}.trace-heading>span{font:9px/1.6 monospace;letter-spacing:1px;color:var(--trace-accent)}.trace-counts{display:flex;gap:22px;margin-top:14px;font-size:12px;color:var(--el-text-color-secondary)}.trace-counts b{font:22px/1.3 Georgia,serif;margin-left:7px;color:var(--el-text-color-primary)}.trace-note,.trace-notice,.not-evidence{font-size:12px;color:var(--el-text-color-secondary);overflow-wrap:anywhere}.trace-notice{padding:10px 12px;background:var(--el-fill-color-light);border-left:2px solid var(--el-border-color)}.search-round{margin:20px 0 0;border-top:1px solid var(--el-border-color);padding-top:16px}.round-heading>span{font-size:11px;color:var(--el-text-color-secondary)}.search-round[data-status="partial"] .round-heading>span,.search-round[data-status="failed"] .round-heading>span{color:var(--el-color-warning-dark-2)}.round-number{font:12px monospace;color:var(--trace-accent);margin-right:10px}.search-query{font-size:12px;overflow-wrap:anywhere;margin:12px 0}.search-query>span{display:block;font-size:10px;letter-spacing:1px;color:var(--el-text-color-secondary)}.source-list{list-style:none;margin:0;padding:0}.source-card{background:var(--el-fill-color-lighter);border:1px solid var(--el-border-color-lighter);border-left:2px solid var(--trace-accent);padding:14px;margin:12px 0;overflow-wrap:anywhere}.source-card[data-status="failed"]{border-left-color:var(--el-color-warning)}.source-card[data-status="skipped"],.source-card[data-status="duplicate"]{border-left-color:var(--el-border-color)}.source-heading{justify-content:flex-start;font-size:10px;gap:8px;color:var(--el-text-color-secondary)}.source-number{font-family:monospace;color:var(--trace-accent)}.source-status{margin-left:auto}.source-card h5{font-size:13px;font-weight:600;line-height:1.7;margin:8px 0 4px}.source-card a{color:var(--trace-accent);text-underline-offset:3px}.source-url{font:10px/1.7 monospace;color:var(--el-text-color-secondary);margin:4px 0}.source-reason{font-size:12px;margin:10px 0}.source-reason code{font-size:10px}.source-relation{border-top:1px solid var(--el-border-color);padding-top:12px;font-size:12px;color:var(--trace-accent)}.source-relation[data-stance="refutes"]{color:var(--el-color-danger)}.source-relation[data-stance="context"]{color:var(--el-text-color-secondary)}.source-relation>span{font:10px monospace}blockquote{font:14px/1.9 'Songti SC','STSong',serif;margin:10px 0;padding-left:12px;border-left:2px solid var(--el-border-color);white-space:pre-wrap}.source-checks{font-size:12px}.source-checks summary{cursor:pointer;color:var(--trace-accent)}dt{font-weight:600}dd{margin:3px 0 10px;color:var(--el-text-color-secondary)}.evidence-jump{border:0;padding:0;margin-top:10px;font:inherit;font-size:12px;background:transparent;color:var(--trace-accent);text-decoration:underline;text-underline-offset:3px;cursor:pointer}.evidence-jump:focus-visible,a:focus-visible,summary:focus-visible{outline:2px solid var(--trace-accent);outline-offset:3px}.not-evidence{margin-bottom:0}
</style>
