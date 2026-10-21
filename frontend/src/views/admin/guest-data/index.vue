<template>
  <div class="guest-data">
    <div class="page-head">
      <div>
        <h2>游客数据</h2>
        <p>匿名游客的校对语料与问题分布，仅用于质量分析与审校优化。</p>
      </div>
      <el-button
        :loading="statsLoading || listLoading"
        @click="refreshAll"
      >
        刷新数据
      </el-button>
    </div>

    <div
      v-loading="statsLoading"
      class="stats-row"
    >
      <el-card
        v-for="card in statCards"
        :key="card.label"
        class="stat-card"
        shadow="never"
      >
        <div class="stat-value">
          {{ card.value }}
        </div>
        <div class="stat-label">
          {{ card.label }}
        </div>
      </el-card>
    </div>

    <el-card
      class="list-card"
      shadow="never"
    >
      <template #header>
        <div class="toolbar">
          <el-select
            v-model="days"
            style="width: 120px;"
            @change="handleSearch"
          >
            <el-option
              label="近 7 天"
              :value="7"
            />
            <el-option
              label="近 30 天"
              :value="30"
            />
            <el-option
              label="近 90 天"
              :value="90"
            />
            <el-option
              label="近 365 天"
              :value="365"
            />
          </el-select>
          <el-select
            v-model="recordType"
            placeholder="全部类型"
            clearable
            style="width: 120px;"
            @change="handleSearch"
          >
            <el-option
              label="文本"
              value="text"
            />
            <el-option
              label="文档"
              value="document"
            />
            <el-option
              label="润色"
              value="polish"
            />
          </el-select>
          <el-select
            v-model="domain"
            placeholder="全部领域"
            clearable
            style="width: 130px;"
            @change="handleSearch"
          >
            <el-option
              v-for="item in stats.domains"
              :key="item.domain"
              :label="domainLabel(item.domain)"
              :value="item.domain"
            />
          </el-select>
          <el-input
            v-model="keyword"
            placeholder="搜索原文"
            clearable
            style="width: 220px;"
            @keyup.enter="handleSearch"
            @clear="handleSearch"
          >
            <template #prefix>
              <el-icon><Search /></el-icon>
            </template>
          </el-input>
          <el-button
            type="primary"
            @click="handleSearch"
          >
            查询
          </el-button>
          <el-button @click="handleReset">
            重置
          </el-button>
        </div>
      </template>

      <el-table
        v-loading="listLoading"
        :data="items"
        stripe
        style="width: 100%;"
      >
        <el-table-column
          prop="created_at"
          label="时间"
          width="170"
        />
        <el-table-column
          label="类型"
          width="86"
          align="center"
        >
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="typeTag(row.type)"
            >
              {{ typeLabel(row.type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          label="领域"
          width="100"
        >
          <template #default="{ row }">
            {{ domainLabel(row.domain) }}
          </template>
        </el-table-column>
        <el-table-column
          label="游客"
          width="110"
        >
          <template #default="{ row }">
            <span class="guest-label">{{ row.guest_label }}…</span>
          </template>
        </el-table-column>
        <el-table-column
          prop="preview"
          label="原文预览"
          min-width="280"
          show-overflow-tooltip
        />
        <el-table-column
          prop="chars"
          label="字数"
          width="90"
          align="center"
        />
        <el-table-column
          prop="total_issues"
          label="问题数"
          width="90"
          align="center"
        />
        <el-table-column
          label="操作"
          width="72"
          align="center"
        >
          <template #default="{ row }">
            <el-button
              type="primary"
              link
              size="small"
              @click.stop="openDetail(row)"
            >
              查看
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty
        v-if="!listLoading && items.length === 0"
        description="当前范围内暂无游客语料"
      />

      <div
        v-if="total > 0"
        class="pager"
      >
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[20, 50, 100]"
          layout="total, sizes, prev, pager, next"
          @current-change="loadList"
          @size-change="loadList"
        />
      </div>
    </el-card>

    <el-drawer
      v-model="detailVisible"
      title="游客语料详情"
      size="62%"
    >
      <div
        v-loading="detailLoading"
        class="detail"
      >
        <template v-if="detail">
          <div class="detail-meta">
            <el-tag
              size="small"
              :type="typeTag(detail.type)"
            >
              {{ typeLabel(detail.type) }}
            </el-tag>
            <span>{{ domainLabel(detail.domain) }}</span>
            <span>{{ detail.created_at }}</span>
            <span>游客 {{ detail.guest_label }}…</span>
            <span>问题 {{ detail.total_issues }}</span>
          </div>
          <h4>原文</h4>
          <pre class="text-block">{{ detail.original_text }}</pre>
          <template v-if="detail.type === 'polish'">
            <h4>润色结果</h4>
            <pre class="text-block">{{ detail.modified_text || '无' }}</pre>
          </template>
          <template v-else>
            <h4>审校问题（{{ detail.issues.length }}）</h4>
            <el-table
              :data="detail.issues"
              size="small"
              stripe
            >
              <el-table-column
                prop="type"
                label="类型"
                width="90"
              />
              <el-table-column
                prop="original"
                label="原文"
                min-width="140"
                show-overflow-tooltip
              />
              <el-table-column
                prop="suggestion"
                label="建议"
                min-width="140"
                show-overflow-tooltip
              />
              <el-table-column
                prop="explanation"
                label="说明"
                min-width="180"
                show-overflow-tooltip
              />
            </el-table>
          </template>
        </template>
        <el-empty
          v-else-if="!detailLoading"
          description="记录不存在或已被删除"
        />
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import {
  getGuestDataDetailApi, getGuestDataStatsApi, listGuestDataApi,
  type GuestDataDetail, type GuestDataItem, type GuestDataStats,
} from '@/api/admin'
import { getErrorDetail } from '@/utils/request'

const DOMAIN_LABELS: Record<string, string> = {
  general: '通用', official: '公文', legal: '法律', auto: '自动',
}

const days = ref(30)
const recordType = ref('')
const domain = ref('')
const keyword = ref('')
const page = ref(1)
const pageSize = ref(20)
const statsLoading = ref(false)
const listLoading = ref(false)
const items = ref<GuestDataItem[]>([])
const total = ref(0)
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<GuestDataDetail | null>(null)

const stats = reactive<GuestDataStats>({
  days: 30, records: 0, proofreads: 0, guests: 0, chars: 0, issues: 0,
  avg_issues: 0, texts: 0, documents: 0, polishes: 0, domains: [],
})

const statCards = computed(() => [
  { label: `游客校对次数（近 ${days.value} 天）`, value: stats.proofreads },
  { label: '独立游客', value: stats.guests },
  { label: '累计原文', value: formatChars(stats.chars) },
  { label: '平均问题数', value: stats.avg_issues },
])

function formatChars(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)} 万字`
  return `${n.toLocaleString()} 字`
}

function domainLabel(value: string): string {
  return DOMAIN_LABELS[value] || value || '-'
}

function typeLabel(value: string): string {
  return ({ text: '文本', document: '文档', polish: '润色' } as Record<string, string>)[value] || value
}

function typeTag(value: string): 'primary' | 'success' | 'warning' | 'info' {
  return ({ text: 'primary', document: 'success', polish: 'warning' } as Record<string, 'primary' | 'success' | 'warning' | 'info'>)[value] || 'info'
}

async function loadStats() {
  statsLoading.value = true
  try {
    Object.assign(stats, await getGuestDataStatsApi(days.value))
  } catch (e: unknown) {
    ElMessage.error(getErrorDetail(e) || '游客统计加载失败')
  } finally {
    statsLoading.value = false
  }
}

async function loadList() {
  listLoading.value = true
  try {
    const res = await listGuestDataApi({
      page: page.value,
      page_size: pageSize.value,
      days: days.value,
      type: recordType.value || undefined,
      domain: domain.value || undefined,
      keyword: keyword.value.trim() || undefined,
    })
    items.value = res.items
    total.value = res.total
  } catch (e: unknown) {
    ElMessage.error(getErrorDetail(e) || '游客语料加载失败')
  } finally {
    listLoading.value = false
  }
}

function handleSearch() {
  page.value = 1
  loadList()
  loadStats()
}

function handleReset() {
  days.value = 30
  recordType.value = ''
  domain.value = ''
  keyword.value = ''
  page.value = 1
  loadList()
  loadStats()
}

async function openDetail(row: Record<string, unknown>) {
  const id = Number(row.id)
  if (!Number.isFinite(id)) return
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await getGuestDataDetailApi(id)
  } catch (e: unknown) {
    ElMessage.error(getErrorDetail(e) || '记录详情加载失败')
    detailVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

function refreshAll() {
  loadStats()
  loadList()
}

onMounted(refreshAll)
</script>

<style scoped lang="scss">
.guest-data { padding: 0; }
.page-head {
  display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 20px;
  h2 { font-size: 24px; font-weight: 600; }
  p { margin-top: 8px; color: var(--color-text-secondary); font-size: 13px; }
}
.stats-row {
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin-bottom: 16px;
  @media (max-width: 900px) { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
.stat-card {
  box-shadow: none;
  .stat-value { font-size: 26px; font-weight: 700; font-variant-numeric: tabular-nums; }
  .stat-label { margin-top: 6px; color: var(--color-text-secondary); font-size: 12px; }
}
.list-card { box-shadow: none; }
.toolbar { display: flex; flex-wrap: wrap; gap: 10px; }
.guest-label { color: var(--color-text-secondary); font-family: var(--font-mono, monospace); font-size: 12px; }
.pager { display: flex; justify-content: flex-end; margin-top: 16px; }
.detail { min-height: 160px; }
.detail-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; color: var(--color-text-secondary); font-size: 12px; margin-bottom: 16px; }
.text-block {
  margin: 8px 0 18px; padding: 14px; max-height: 320px; overflow: auto; white-space: pre-wrap; word-break: break-word;
  background: var(--color-bg); border: 1px solid var(--color-border); border-radius: 8px;
  font-family: inherit; font-size: 13px; line-height: 1.8;
}
h4 { margin: 16px 0 6px; font-size: 14px; }
</style>
