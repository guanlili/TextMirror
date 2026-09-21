<template>
  <div class="admin-usage">
    <div class="usage-intro">
      <div><h2>看清每一次调用</h2><p>按时间、业务和模型追踪真实用量，定位失败与未完整请求。</p></div>
    </div>    <el-card class="usage-card">
      <template #header>
        <div class="card-header-flex usage-header">
          <span style="font-weight: 600;">模型调用账本</span>
          <el-radio-group
            v-model="usageDays"
            size="small"
            @change="loadModelUsage"
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
      <div v-loading="usageLoading">
        <el-alert
          v-if="usageError"
          title="调用账本加载失败，请重试"
          type="error"
          :closable="false"
        >
          <el-button
            link
            type="primary"
            @click="loadModelUsage"
          >
            重新加载
          </el-button>
        </el-alert>
        <template v-else-if="modelUsage">
          <div class="usage-totals">
            <span><strong>{{ modelUsage.calls }}</strong> 次实际请求</span>
            <span><strong>{{ formatTokens(modelUsage.total_tokens) }}</strong> 已知 Token</span>
            <el-tag
              :type="modelUsage.unknown_usage_calls ? 'warning' : 'info'"
              size="small"
            >
              {{ modelUsage.unknown_usage_calls }} 次未返回用量
            </el-tag>
          </div>
          <p class="usage-note">
            上方合计为当前时间范围的全部调用，筛选仅影响下方分组列表。含自检、润色、评测、协作及事实核查；重试按实际请求计数。未返回用量不等于零消耗，Token 不等于费用账单，搜索工具费用请以供应商为准。
          </p>
          <p class="usage-note">
            {{ modelUsage.tracked_since ? `首条记录：${formatTime(modelUsage.tracked_since)}` : '暂无调用记录' }}。仅记录启用账本后的调用，不回填或叠加旧审校记录中的估算用量。
          </p>
          <div class="usage-filters">
            <el-input
              v-model="keyword"
              clearable
              placeholder="搜索模型或业务"
              aria-label="搜索模型或业务"
            /><el-checkbox v-model="onlyProblems">
              只看含失败、未完整或取消的分组
            </el-checkbox><el-button
              :loading="usageLoading"
              @click="loadModelUsage"
            >
              刷新
            </el-button>
          </div>
          <el-table
            :data="filteredItems"
            size="small"
            stripe
            empty-text="所选时间范围内暂无调用"
          >
            <el-table-column
              label="业务 / 阶段"
              min-width="145"
            >
              <template #default="{ row }">
                {{ businessLabel(row.business) }} / {{ operationLabel(row.operation) }}
              </template>
            </el-table-column>
            <el-table-column
              label="模型"
              min-width="190"
              show-overflow-tooltip
            >
              <template #default="{ row }">
                {{ row.config_name || '未保存配置' }} · {{ row.model }}
              </template>
            </el-table-column>
            <el-table-column
              prop="calls"
              label="请求数"
              width="75"
            />
            <el-table-column
              label="已知 Token"
              width="110"
            >
              <template #default="{ row }">
                {{ row.unknown_usage_calls === row.calls ? '未知' : formatTokens(row.total_tokens) }}
              </template>
            </el-table-column>
            <el-table-column
              prop="unknown_usage_calls"
              label="用量未知"
              width="90"
            />
            <el-table-column
              label="失败 / 未完整 / 取消"
              min-width="160"
            >
              <template #default="{ row }">
                {{ row.errors }} / {{ row.incomplete }} / {{ row.cancelled }}
              </template>
            </el-table-column>
            <el-table-column
              label="平均耗时"
              width="100"
            >
              <template #default="{ row }">
                {{ (row.average_ms / 1000).toFixed(2) }}s
              </template>
            </el-table-column>
            <el-table-column
              prop="search_queries"
              label="联网检索"
              width="90"
            />
          </el-table>
        </template>
      </div>
    </el-card>
  </div>
</template>
<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { getModelUsageApi, type ModelUsageSummary } from '@/api/admin'
import { formatTime } from '@/utils/format'
function formatTokens(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K'
  return String(n)
}

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
  const labels: Record<string, string> = { chat: '生成', stream: '流式生成', self_check: '二次自检', native_search: '联网搜索', connection_test: '连接测试', format_validation: '格式校验', segment_validation: '分段校验' }
  return labels[value] || value
}


const keyword = ref('')
const onlyProblems = ref(false)
const filteredItems = computed(() => (modelUsage.value?.items || []).filter(item => {
 const query = keyword.value.trim().toLowerCase()
 return (!query || `${item.config_name} ${item.model} ${businessLabel(item.business)} ${operationLabel(item.operation)}`.toLowerCase().includes(query)) && (!onlyProblems.value || item.errors > 0 || item.incomplete > 0 || item.cancelled > 0)
}))
onMounted(loadModelUsage)
onBeforeUnmount(() => { usageRequest++ })
</script>
<style scoped>
.usage-intro { margin-bottom: 24px; }.usage-intro h2 { font-size: 24px; margin-bottom: 10px; }.usage-intro p,.usage-note { color: var(--color-text-secondary); font-size: 13px; line-height: 1.8; }.usage-header { display:flex; justify-content:space-between; gap:16px; flex-wrap:wrap; }.usage-totals { display:flex; align-items:center; gap:24px; flex-wrap:wrap; padding:12px 0 20px; }.usage-totals strong { font-size:28px; color:var(--color-text); }.usage-note { margin-bottom:12px; }.usage-filters { display:flex; gap:20px; align-items:center; flex-wrap:wrap; margin:24px 0 16px; }.usage-filters .el-input { max-width:340px; }.usage-card { box-shadow:none; }
</style>