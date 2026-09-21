<template>
  <div class="admin-dashboard">
    <div class="overview-heading">
      <div><span class="overview-eyebrow">运营概览</span><h2>让内容质量，持续可控。</h2><p>了解平台使用情况，管理审校标准与模型服务。</p></div><el-button
        :loading="statsLoading || trendLoading"
        @click="loadStats(); loadTrend()"
      >
        刷新数据
      </el-button>
    </div>
    <div class="admin-shortcuts">
      <router-link
        v-if="user.hasPermission('admin:settings:edit')"
        to="/admin/domain-rules"
      >
        <el-icon><Reading /></el-icon><strong>维护审校标准</strong><span>查看与编辑专业规范 →</span>
      </router-link>
      <router-link
        v-if="user.hasPermission('admin:global_dict:edit')"
        to="/admin/quality"
      >
        <el-icon><ChatDotRound /></el-icon><strong>处理质量反馈</strong><span>审核样例与评测结果 →</span>
      </router-link>
      <router-link
        v-if="user.hasPermission('admin:llm:edit')"
        to="/admin/llm"
      >
        <el-icon><Cpu /></el-icon><strong>管理模型服务</strong><span>连接测试与默认模型 →</span>
      </router-link>
      <router-link to="/admin/usage">
        <el-icon><TrendCharts /></el-icon><strong>查看调用用量</strong><span>模型消耗与异常请求 →</span>
      </router-link>
    </div>
    <div class="section-label">
      <h3>平台使用情况</h3><span v-if="refreshedAt && !statsError">更新于 {{ refreshedAt }}</span>
    </div>
    <el-alert
      v-if="statsError"
      title="概览数据暂不可用，请刷新重试。"
      type="error"
      :closable="false"
    />
    <div
      v-if="!statsError"
      v-loading="statsLoading"
      class="stats-row"
    >
      <el-card
        class="stat-card"
        shadow="hover"
      >
        <div class="stat-content">
          <div
            class="stat-icon"
            style="background: #ecf5ff;"
          >
            <el-icon
              :size="28"
              color="#409eff"
            >
              <Edit />
            </el-icon>
          </div>
          <div class="stat-info">
            <div class="stat-value">
              {{ stats.today_proofread_count }}
            </div>
            <div class="stat-label">
              今日校对次数
            </div>
          </div>
        </div>
      </el-card>
      <el-card
        class="stat-card"
        shadow="hover"
      >
        <div class="stat-content">
          <div
            class="stat-icon"
            style="background: #f0f9eb;"
          >
            <el-icon
              :size="28"
              color="#67c23a"
            >
              <Document />
            </el-icon>
          </div>
          <div class="stat-info">
            <div class="stat-value">
              {{ stats.total_proofread_count }}
            </div>
            <div class="stat-label">
              累计校对次数
            </div>
          </div>
        </div>
      </el-card>
      <el-card
        class="stat-card"
        shadow="hover"
      >
        <div class="stat-content">
          <div
            class="stat-icon"
            style="background: #fdf6ec;"
          >
            <el-icon
              :size="28"
              color="#e6a23c"
            >
              <User />
            </el-icon>
          </div>
          <div class="stat-info">
            <div class="stat-value">
              {{ stats.total_users }}
            </div>
            <div class="stat-label">
              总用户数
            </div>
          </div>
        </div>
      </el-card>
      <el-card
        class="stat-card"
        shadow="hover"
      >
        <div class="stat-content">
          <div
            class="stat-icon"
            style="background: #fef0f0;"
          >
            <el-icon
              :size="28"
              color="#f56c6c"
            >
              <UserFilled />
            </el-icon>
          </div>
          <div class="stat-info">
            <div class="stat-value">
              {{ stats.active_users_today }}
            </div>
            <div class="stat-label">
              今日活跃用户
            </div>
          </div>
        </div>
      </el-card>
      <el-card
        class="stat-card"
        shadow="hover"
      >
        <div class="stat-content">
          <div
            class="stat-icon"
            style="background: #f3e8ff;"
          >
            <el-icon
              :size="28"
              color="#7c3aed"
            >
              <Folder />
            </el-icon>
          </div>
          <div class="stat-info">
            <div class="stat-value">
              {{ stats.today_document_count }} / {{ stats.total_document_count }}
            </div>
            <div class="stat-label">
              今日/累计上传文档
            </div>
          </div>
        </div>
      </el-card>
      <el-card
        class="stat-card"
        shadow="hover"
      >
        <div class="stat-content">
          <div
            class="stat-icon"
            style="background: #e0f2fe;"
          >
            <el-icon
              :size="28"
              color="#0284c7"
            >
              <Coin />
            </el-icon>
          </div>
          <div class="stat-info">
            <div class="stat-value">
              {{ formatTokens(stats.total_token_usage) }}
            </div>
            <div class="stat-label">
              调用账本已知 Token
            </div>
          </div>
        </div>
      </el-card>
    </div>

    <div class="report-row">
      <el-card class="trend-card">
        <template #header>
          <div class="card-header-flex">
            <span style="font-weight: 600;">校对趋势（近 {{ trendDays }} 日）</span>
            <el-radio-group
              v-model="trendDays"
              size="small"
              @change="loadTrend"
            >
              <el-radio-button :value="7">
                7日
              </el-radio-button>
              <el-radio-button :value="30">
                30日
              </el-radio-button>
              <el-radio-button :value="90">
                90日
              </el-radio-button>
            </el-radio-group>
          </div>
        </template>
        <div
          ref="trendChartRef"
          v-loading="trendLoading"
          class="trend-chart"
        >
          <el-empty
            v-if="trendError && !trendLoading"
            description="趋势数据加载失败"
          >
            <el-button
              size="small"
              @click="loadTrend"
            >
              重试
            </el-button>
          </el-empty>
        </div>
      </el-card>

      <el-card class="top-card">
        <template #header>
          <span style="font-weight: 600;">校对量 Top 用户（近 {{ trendDays }} 日）</span>
        </template>
        <el-alert
          v-if="trendError"
          title="排行暂不可用，请刷新重试"
          type="error"
          :closable="false"
        />
        <el-table
          v-else
          v-loading="topLoading"
          :data="topUsers"
          size="small"
          stripe
        >
          <el-table-column
            type="index"
            label="#"
            width="44"
            align="center"
          />
          <el-table-column
            prop="username"
            label="用户"
            min-width="100"
            show-overflow-tooltip
          />
          <el-table-column
            prop="employee_id"
            label="工号"
            min-width="90"
            show-overflow-tooltip
          />
          <el-table-column
            prop="count"
            label="校对次数"
            width="90"
            align="center"
          >
            <template #default="{ row }">
              <el-tag
                v-if="row.count > 0"
                size="small"
                type="primary"
              >
                {{ row.count }}
              </el-tag>
              <span v-else>0</span>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import {
  getDashboardStatsApi, getUsageTrendApi, getTopUsersApi,
  type DashboardStats, type TrendPoint, type TopUserItem,
} from '@/api/admin'
import { useUserStore } from '@/stores/user'


echarts.use([LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])

const user = useUserStore()
const statsLoading = ref(true)
const statsError = ref(false)
const refreshedAt = ref('')

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

async function loadStats() {
  statsLoading.value = true
  statsError.value = false
  try { Object.assign(stats, await getDashboardStatsApi()); refreshedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false }) }
  catch { statsError.value = true; ElMessage.error('运营数据加载失败，请重试') }
  finally { statsLoading.value = false }
}
onMounted(() => { void loadStats(); void loadTrend(); window.addEventListener('resize', handleResize) })

const trendDays = ref<number>(30)
const trendLoading = ref(false)
const topLoading = ref(false)
const trendError = ref(false)
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

let trendRequest = 0
async function loadTrend() {
  const requestId = ++trendRequest
  trendLoading.value = true
  topLoading.value = true
  trendError.value = false
  try {
    const [trend, top] = await Promise.all([
      getUsageTrendApi(trendDays.value),
      getTopUsersApi(trendDays.value),
    ])
    if (requestId !== trendRequest) return
    trendDaily.value = trend.daily
    topUsers.value = top.items
    await nextTick()
    renderTrend()
  } catch {
    if (requestId !== trendRequest) return
    trendError.value = true
  }
  if (requestId === trendRequest) { trendLoading.value = false; topLoading.value = false }
}

function handleResize() {
  chart?.resize()
}

onBeforeUnmount(() => {
  trendRequest++
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

.overview-heading { display:flex; justify-content:space-between; align-items:center; gap:16px; margin-bottom:28px; }.overview-eyebrow { font-size:12px; color:var(--color-primary); letter-spacing:2px; }.overview-heading h2 { font-size:28px; font-weight:600; margin:12px 0; }.overview-heading p { color:var(--color-text-secondary); font-size:13px; }
.admin-shortcuts { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin-bottom:32px; }.admin-shortcuts a { display:flex; flex-direction:column; align-items:flex-start; gap:14px; padding:22px; border:1px solid var(--color-border); border-radius:12px; background:var(--surface); color:var(--color-text); text-decoration:none; }.admin-shortcuts a:hover { border-color:var(--color-primary); }.admin-shortcuts .el-icon { font-size:22px; color:var(--color-primary); }.admin-shortcuts strong { font-size:14px; }.admin-shortcuts span { color:var(--color-text-secondary); font-size:12px; }
.section-label { display:flex; align-items:center; justify-content:space-between; margin-bottom:16px; }.section-label h3 { font-size:16px; }.section-label span { color:var(--color-text-secondary); font-size:12px; }.stat-card { box-shadow:none; }.stat-card .stat-info .stat-value { color:var(--color-text); font-size:26px; }.stat-card .stat-info .stat-label { color:var(--color-text-secondary); }.stat-card .stat-icon { width:42px; height:42px; }.report-row .el-card { box-shadow:none; }
@media(max-width:1100px) { .admin-shortcuts { grid-template-columns:repeat(2,minmax(0,1fr)); } } @media(max-width:600px) { .overview-heading h2 { font-size:22px; }.overview-heading { align-items:flex-start; }.admin-shortcuts a { padding:16px; }.stat-card .stat-icon { display:none; }.stat-card .stat-info .stat-value { font-size:22px; }.card-header-flex { flex-wrap:wrap; gap:12px; } }
</style>
