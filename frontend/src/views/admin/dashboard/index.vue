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
            <div class="stat-label">累计 Token 消耗</div>
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
  getDashboardStatsApi, getUsageTrendApi, getTopUsersApi,
  type DashboardStats, type TrendPoint, type TopUserItem,
} from '@/api/admin'
import { useSiteStore } from '@/stores/site'

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
  window.addEventListener('resize', handleResize)
})

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
