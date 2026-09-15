<template>
  <div class="admin-dashboard">
    <div class="stats-row">
      <el-card class="stat-card" shadow="hover">
        <div class="stat-content">
          <div class="stat-icon" style="background: #ecf5ff;"><el-icon :size="28" color="#409eff"><Edit /></el-icon></div>
          <div class="stat-info">
            <div class="stat-value">{{ stats.today_proofread_count }}</div>
            <div class="stat-label">今日校对次数</div>
          </div>
        </div>
      </el-card>
      <el-card class="stat-card" shadow="hover">
        <div class="stat-content">
          <div class="stat-icon" style="background: #f0f9eb;"><el-icon :size="28" color="#67c23a"><Document /></el-icon></div>
          <div class="stat-info">
            <div class="stat-value">{{ stats.total_proofread_count }}</div>
            <div class="stat-label">累计校对次数</div>
          </div>
        </div>
      </el-card>
      <el-card class="stat-card" shadow="hover">
        <div class="stat-content">
          <div class="stat-icon" style="background: #fdf6ec;"><el-icon :size="28" color="#e6a23c"><User /></el-icon></div>
          <div class="stat-info">
            <div class="stat-value">{{ stats.total_users }}</div>
            <div class="stat-label">总用户数</div>
          </div>
        </div>
      </el-card>
      <el-card class="stat-card" shadow="hover">
        <div class="stat-content">
          <div class="stat-icon" style="background: #fef0f0;"><el-icon :size="28" color="#f56c6c"><UserFilled /></el-icon></div>
          <div class="stat-info">
            <div class="stat-value">{{ stats.active_users_today }}</div>
            <div class="stat-label">今日活跃用户</div>
          </div>
        </div>
      </el-card>
      <el-card class="stat-card" shadow="hover">
        <div class="stat-content">
          <div class="stat-icon" style="background: #f3e8ff;"><el-icon :size="28" color="#7c3aed"><Folder /></el-icon></div>
          <div class="stat-info">
            <div class="stat-value">{{ stats.today_document_count }} / {{ stats.total_document_count }}</div>
            <div class="stat-label">今日/累计上传文档</div>
          </div>
        </div>
      </el-card>
      <el-card class="stat-card" shadow="hover">
        <div class="stat-content">
          <div class="stat-icon" style="background: #e0f2fe;"><el-icon :size="28" color="#0284c7"><Coin /></el-icon></div>
          <div class="stat-info">
            <div class="stat-value">{{ formatTokens(stats.total_token_usage) }}</div>
            <div class="stat-label">调用账本已知 Token</div>
          </div>
        </div>
      </el-card>
    </div>

    <div class="report-row">
      <el-card class="trend-card">
        <template #header>
          <div class="card-header-flex">
            <span style="font-weight: 600;">校对趋势（近 {{ trendDays }} 日）</span>
            <el-radio-group v-model="trendDays" size="small" @change="loadTrend">
              <el-radio-button :value="7">7日</el-radio-button>
              <el-radio-button :value="30">30日</el-radio-button>
              <el-radio-button :value="90">90日</el-radio-button>
            </el-radio-group>
          </div>
        </template>
        <div ref="trendChartRef" class="trend-chart" v-loading="trendLoading"></div>
      </el-card>

      <el-card class="top-card">
        <template #header><span style="font-weight: 600;">校对量 Top 用户（近 {{ trendDays }} 日）</span></template>
        <el-table :data="topUsers" v-loading="topLoading" size="small" stripe>
          <el-table-column type="index" label="#" width="44" align="center" />
          <el-table-column prop="username" label="用户" min-width="100" show-overflow-tooltip />
          <el-table-column prop="employee_id" label="工号" min-width="90" show-overflow-tooltip />
          <el-table-column prop="count" label="校对次数" width="90" align="center">
            <template #default="{ row }">
              <el-tag v-if="row.count > 0" size="small" type="primary">{{ row.count }}</el-tag>
              <span v-else>0</span>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </div>

    <el-card class="usage-card">
      <template #header>
        <div class="card-header-flex usage-header">
          <span style="font-weight: 600;">模型调用账本</span>
          <el-radio-group v-model="usageDays" size="small" @change="loadModelUsage">
            <el-radio-button :value="7">7日</el-radio-button>
            <el-radio-button :value="30">30日</el-radio-button>
            <el-radio-button :value="90">90日</el-radio-button>
          </el-radio-group>
        </div>
      </template>
      <div v-loading="usageLoading">
        <el-alert v-if="usageError" title="调用账本加载失败，请重试" type="error" :closable="false">
          <el-button link type="primary" @click="loadModelUsage">重新加载</el-button>
        </el-alert>
        <template v-else-if="modelUsage">
          <div class="usage-totals">
            <span><strong>{{ modelUsage.calls }}</strong> 次实际请求</span>
            <span><strong>{{ formatTokens(modelUsage.total_tokens) }}</strong> 已知 Token</span>
            <el-tag :type="modelUsage.unknown_usage_calls ? 'warning' : 'info'" size="small">
              {{ modelUsage.unknown_usage_calls }} 次未返回用量
            </el-tag>
          </div>
          <p class="usage-note">含自检、润色、评测、协作及事实核查；重试按实际请求计数。未返回用量不等于零消耗，Token 不等于费用账单，搜索工具费用请以供应商为准。</p>
          <p class="usage-note">{{ modelUsage.tracked_since ? `首条记录：${formatTime(modelUsage.tracked_since)}` : '暂无调用记录' }}。仅记录启用账本后的调用，不回填或叠加旧审校记录中的估算用量。</p>
          <el-table :data="modelUsage.items" size="small" stripe empty-text="所选时间范围内暂无调用">
            <el-table-column label="业务 / 阶段" min-width="145">
              <template #default="{ row }">{{ businessLabel(row.business) }} / {{ operationLabel(row.operation) }}</template>
            </el-table-column>
            <el-table-column label="模型" min-width="190" show-overflow-tooltip>
              <template #default="{ row }">{{ row.config_name || '未保存配置' }} · {{ row.model }}</template>
            </el-table-column>
            <el-table-column prop="calls" label="请求数" width="75" />
            <el-table-column label="已知 Token" width="110">
              <template #default="{ row }">{{ row.unknown_usage_calls === row.calls ? '未知' : formatTokens(row.total_tokens) }}</template>
            </el-table-column>
            <el-table-column prop="unknown_usage_calls" label="用量未知" width="90" />
            <el-table-column label="失败 / 未完整 / 取消" min-width="160">
              <template #default="{ row }">{{ row.errors }} / {{ row.incomplete }} / {{ row.cancelled }}</template>
            </el-table-column>
            <el-table-column label="平均耗时" width="100">
              <template #default="{ row }">{{ (row.average_ms / 1000).toFixed(2) }}s</template>
            </el-table-column>
            <el-table-column prop="search_queries" label="联网检索" width="90" />
          </el-table>
        </template>
      </div>
    </el-card>

    <el-card style="margin-top: 16px;">
      <template #header><span style="font-weight: 600;">系统信息</span></template>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="平台名称">{{ siteStore.platformName }} {{ siteStore.platformSubtitle }}</el-descriptions-item>
        <el-descriptions-item label="后端框架">FastAPI + SQLAlchemy</el-descriptions-item>
        <el-descriptions-item label="前端框架">Vue 3 + Element Plus</el-descriptions-item>
        <el-descriptions-item label="AI 模型">DeepSeek</el-descriptions-item>
        <el-descriptions-item label="数据库">PostgreSQL</el-descriptions-item>
        <el-descriptions-item label="缓存">Redis</el-descriptions-item>
      </el-descriptions>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import {
  getDashboardStatsApi, getUsageTrendApi, getTopUsersApi, getModelUsageApi,
  type DashboardStats, type TrendPoint, type TopUserItem, type ModelUsageSummary,
} from '@/api/admin'
import { useSiteStore } from '@/stores/site'
import { formatTime } from '@/utils/format'

echarts.use([LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])

const siteStore = useSiteStore()

const stats = reactive<DashboardStats>({
  today_proofread_count: 0,
  total_proofread_count: 0,
  total_users: 0,
  active_users_today: 0,
  total_token_usage: 0,
  today_document_count: 0,
  total_document_count: 0,
})

function formatTokens(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K'
  return String(n)
}

onMounted(async () => {
  try {
    const data = await getDashboardStatsApi()
    Object.assign(stats, data)
  } catch {
    // 拦截器已处理
  }
  loadTrend()
  loadModelUsage()
  window.addEventListener('resize', handleResize)
})

const usageDays = ref(30)
const modelUsage = ref<ModelUsageSummary | null>(null)
const usageLoading = ref(false)
const usageError = ref(false)
let usageRequest = 0

async function loadModelUsage() {
  const requestId = ++usageRequest
  usageLoading.value = true
  usageError.value = false
  try {
    const result = await getModelUsageApi(usageDays.value)
    if (requestId === usageRequest) modelUsage.value = result
  } catch {
    if (requestId === usageRequest) usageError.value = true
  } finally {
    if (requestId === usageRequest) usageLoading.value = false
  }
}

function businessLabel(value: string): string {
  const labels: Record<string, string> = { proofread: '审校', polish: '润色', evaluation: '质量评测', fact_check: '事实核查', collaboration: '协作审校', other: '其他' }
  return labels[value] || value
}

function operationLabel(value: string): string {
  const labels: Record<string, string> = { chat: '生成', stream: '流式生成', self_check: '二次自检', native_search: '联网搜索', connection_test: '连接测试' }
  return labels[value] || value
}

const trendDays = ref<number>(30)
const trendLoading = ref(false)
const topLoading = ref(false)
const trendChartRef = ref<HTMLElement>()
const trendDaily = ref<TrendPoint[]>([])
const topUsers = ref<TopUserItem[]>([])
let chart: echarts.ECharts | null = null

function renderTrend() {
  if (!trendChartRef.value) return
  if (!chart) chart = echarts.init(trendChartRef.value)
  chart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['校对次数', '活跃用户'], bottom: 0 },
    grid: { left: 40, right: 16, top: 24, bottom: 40 },
    xAxis: { type: 'category', data: trendDaily.value.map(d => d.date), boundaryGap: false },
    yAxis: [
      { type: 'value', name: '次数', minInterval: 1 },
      { type: 'value', name: '用户', minInterval: 1, splitLine: { show: false } },
    ],
    series: [
      {
        name: '校对次数', type: 'line', smooth: true,
        showSymbol: trendDaily.value.length <= 14,
        data: trendDaily.value.map(d => d.count),
        areaStyle: { opacity: 0.12 },
      },
      {
        name: '活跃用户', type: 'line', smooth: true, yAxisIndex: 1,
        data: trendDaily.value.map(d => d.users),
        lineStyle: { type: 'dashed' },
      },
    ],
  })
}

async function loadTrend() {
  trendLoading.value = true
  topLoading.value = true
  try {
    const [trend, top] = await Promise.all([
      getUsageTrendApi(trendDays.value),
      getTopUsersApi(trendDays.value),
    ])
    trendDaily.value = trend.daily
    topUsers.value = top.items
    await nextTick()
    renderTrend()
  } catch {
    // 拦截器已处理
  }
  trendLoading.value = false
  topLoading.value = false
}

function handleResize() {
  chart?.resize()
}

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  chart?.dispose()
  chart = null
})
</script>

<style scoped lang="scss">
.usage-card { margin-top: 16px; }
.usage-totals { display: flex; flex-wrap: wrap; align-items: center; gap: 12px 24px; font-size: 13px; }
.usage-totals strong { font-size: 22px; font-variant-numeric: tabular-nums; margin-right: 4px; }
.usage-note { color: var(--color-text-secondary); font-size: 12px; line-height: 1.7; }
.usage-header { flex-wrap: wrap; gap: 12px; }
.report-row {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 16px;
  margin-top: 16px;

  @media (max-width: 992px) {
    grid-template-columns: 1fr;
  }
}
.trend-chart {
  height: 320px;
}
.card-header-flex {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.stats-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;

  @media (max-width: 768px) {
    grid-template-columns: repeat(2, 1fr);
    gap: 10px;
  }
}
.stat-card {
  .stat-content { display: flex; align-items: center; gap: 16px; }
  .stat-icon { width: 56px; height: 56px; border-radius: 12px; display: flex; align-items: center; justify-content: center; }
  .stat-info {
    .stat-value { font-size: 28px; font-weight: 700; color: #333; line-height: 1.2; }
    .stat-label { font-size: 13px; color: #999; margin-top: 4px; }
  }
}
</style>
