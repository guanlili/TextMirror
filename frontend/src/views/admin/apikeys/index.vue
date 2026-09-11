<template>
  <div class="admin-apikeys-page">
    <el-card>
      <template #header>
        <div class="card-header">
          <span class="card-title">API 密钥管理</span>
          <div class="filter-bar">
            <el-select v-model="statusFilter" placeholder="全部状态" clearable style="width: 120px;" @change="handleFilterChange">
              <el-option label="正常" value="active" />
              <el-option label="已吊销" value="revoked" />
              <el-option label="已过期" value="expired" />
            </el-select>
            <el-input
              v-model="keyword"
              placeholder="搜索名称/用户名/工号"
              clearable
              style="width: 220px;"
              @keyup.enter="handleFilterChange"
              @clear="handleFilterChange"
            />
            <el-button @click="handleFilterChange">
              <el-icon><Search /></el-icon>查询
            </el-button>
          </div>
        </div>
      </template>

      <el-table :data="list" v-loading="loading" stripe>
        <el-table-column label="归属用户" min-width="140">
          <template #default="{ row }">
            <div class="user-cell">
              <span>{{ row.username }}</span>
              <span class="employee-id">{{ row.employee_id }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="名称" min-width="110" show-overflow-tooltip />
        <el-table-column label="密钥" min-width="170">
          <template #default="{ row }">
            <code class="key-display">{{ row.key_display }}</code>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="86" align="center">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="今日调用" width="90" align="center">
          <template #default="{ row }">
            <span>{{ row.used_today ?? '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="近7日" width="80" align="center">
          <template #default="{ row }">
            <span>{{ row.used_7d ?? 0 }}</span>
          </template>
        </el-table-column>
        <el-table-column label="回调" width="90" align="center">
          <template #default="{ row }">
            <el-tooltip v-if="row.webhook_last" :hide-after="0" placement="top">
              <template #content>
                最近投递：{{ row.webhook_last.event }}
                （{{ row.webhook_last.status === 'delivered' ? '送达' : '失败' }}
                <template v-if="row.webhook_last.status_code">HTTP {{ row.webhook_last.status_code }}</template>）
              </template>
              <el-tag :type="row.webhook_last.status === 'delivered' ? 'success' : 'danger'" size="small" style="cursor: default;">
                {{ row.webhook_last.status === 'delivered' ? '已送达' : '投递失败' }}
              </el-tag>
            </el-tooltip>
            <el-tag v-else-if="row.webhook_url" type="info" size="small" style="cursor: default;">未投递</el-tag>
            <span v-else style="color: #ccc;">-</span>
          </template>
        </el-table-column>
        <el-table-column label="每日上限" width="100" align="center">
          <template #default="{ row }">
            <span v-if="row.daily_quota">{{ row.daily_quota }}</span>
            <span v-else style="color: #999;">跟随用户</span>
          </template>
        </el-table-column>
        <el-table-column label="过期时间" width="160">
          <template #default="{ row }">
            <span v-if="row.expires_at" style="font-size: 12px; color: #999;">{{ formatTime(row.expires_at) }}</span>
            <span v-else style="color: #999;">永不</span>
          </template>
        </el-table-column>
        <el-table-column label="最近使用" width="160">
          <template #default="{ row }">
            <span v-if="row.last_used_at" style="font-size: 12px; color: #999;">{{ formatTime(row.last_used_at) }}</span>
            <span v-else style="color: #999;">未使用</span>
          </template>
        </el-table-column>
        <el-table-column prop="remark" label="备注" min-width="110" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.remark" style="color: #999;">{{ row.remark }}</span>
            <span v-else style="color: #ccc;">-</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" align="center" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEditDialog(row)">编辑</el-button>
            <el-popconfirm
              v-if="row.is_active"
              :title="`吊销「${row.name}」？立即失效，可恢复。`"
              confirm-button-text="吊销"
              confirm-button-type="danger"
              @confirm="handleToggleActive(row, false)"
            >
              <template #reference>
                <el-button type="danger" link size="small">吊销</el-button>
              </template>
            </el-popconfirm>
            <el-popconfirm
              v-else
              title="恢复该密钥？恢复后立即可用。"
              confirm-button-text="恢复"
              @confirm="handleToggleActive(row, true)"
            >
              <template #reference>
                <el-button type="success" link size="small">恢复</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && list.length === 0" description="暂无密钥" />

      <div class="pagination-wrapper">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          :page-sizes="[20, 50, 100]"
          layout="total, sizes, prev, pager, next"
          @change="fetchList"
        />
      </div>
    </el-card>

    <!-- 编辑弹窗：每日上限 / 备注 -->
    <el-dialog v-model="showEditDialog" title="编辑密钥" width="440px">
      <el-form :model="editForm" label-width="90px">
        <el-form-item label="归属用户">
          <span>{{ editingKey?.username }}（{{ editingKey?.employee_id }}）</span>
        </el-form-item>
        <el-form-item label="每日上限">
          <el-input-number
            v-model="editForm.daily_quota"
            :min="1"
            :max="100000"
            placeholder="留空则跟随用户配额"
            style="width: 100%;"
          />
          <div class="form-tip">清空表示跟随该用户的每日配额</div>
        </el-form-item>
        <el-form-item label="备注">
          <el-input
            v-model="editForm.remark"
            type="textarea"
            :rows="2"
            placeholder="管理备注（可选）"
            maxlength="500"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showEditDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { formatTime } from '@/utils/format'
import {
  listAdminApiKeysApi, updateAdminApiKeyApi,
  type AdminApiKeyItem,
} from '@/api/adminApiKeys'

const loading = ref(false)
const saving = ref(false)
const list = ref<AdminApiKeyItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

const keyword = ref('')
const statusFilter = ref<'active' | 'revoked' | 'expired' | ''>('')

const showEditDialog = ref(false)
const editingKey = ref<AdminApiKeyItem | null>(null)
const editForm = ref<{ daily_quota: number | null; remark: string }>({ daily_quota: null, remark: '' })

onMounted(() => fetchList())

async function fetchList() {
  loading.value = true
  try {
    const res = await listAdminApiKeysApi({
      page: page.value,
      page_size: pageSize.value,
      keyword: keyword.value.trim() || undefined,
      key_status: statusFilter.value || undefined,
    })
    list.value = res.items
    total.value = res.total
  } catch {
    // 拦截器已处理
  }
  loading.value = false
}

function handleFilterChange() {
  page.value = 1
  fetchList()
}

function openEditDialog(row: AdminApiKeyItem) {
  editingKey.value = row
  editForm.value = {
    daily_quota: row.daily_quota,
    remark: row.remark ?? '',
  }
  showEditDialog.value = true
}

async function handleSave() {
  if (!editingKey.value) return
  saving.value = true
  try {
    await updateAdminApiKeyApi(editingKey.value.id, {
      daily_quota: editForm.value.daily_quota ?? null,
      remark: editForm.value.remark.trim() || null,
    })
    ElMessage.success('已保存')
    showEditDialog.value = false
    await fetchList()
  } catch {
    // 拦截器已处理
  }
  saving.value = false
}

async function handleToggleActive(row: AdminApiKeyItem, isActive: boolean) {
  try {
    await updateAdminApiKeyApi(row.id, { is_active: isActive })
    ElMessage.success(isActive ? '密钥已恢复' : '密钥已吊销')
    await fetchList()
  } catch {
    // 拦截器已处理
  }
}

function statusText(status: string) {
  return { active: '正常', revoked: '已吊销', expired: '已过期' }[status] || status
}

function statusTagType(status: string): 'success' | 'info' | 'warning' {
  return ({ active: 'success', revoked: 'info', expired: 'warning' } as const)[status] || 'info'
}
</script>

<style scoped lang="scss">
.admin-apikeys-page {
  .card-header {
    display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px;
    .card-title { font-size: 18px; font-weight: 600; }
    .filter-bar { display: flex; gap: 10px; align-items: center; }
  }
  .user-cell {
    display: flex; flex-direction: column; line-height: 1.4;
    .employee-id { font-size: 12px; color: #999; }
  }
  .key-display {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 12px; background: rgba(0, 0, 0, .04); padding: 2px 6px; border-radius: 4px;
  }
  .form-tip { font-size: 12px; color: #999; line-height: 1.4; margin-top: 4px; }
  .pagination-wrapper {
    display: flex; justify-content: flex-end; margin-top: 14px;
  }
}
</style>
