<template>
  <div class="apikeys-page">
    <el-card>
      <template #header>
        <div class="card-header">
          <span class="card-title">API 密钥</span>
          <el-button type="primary" size="small" @click="openCreateDialog">
            <el-icon><Plus /></el-icon>创建密钥
          </el-button>
        </div>
      </template>

      <el-alert type="info" :closable="false" style="margin-bottom: 14px;">
        <template #title>
          用于将文本审校能力集成到你的工作流中：请求头携带
          <code>Authorization: Bearer tm_...</code>
          调用 <code>POST /api/v1/open/proofread</code>。
          <el-link type="primary" style="vertical-align: baseline;" @click="openDocs">查看接口文档</el-link>
        </template>
      </el-alert>

      <el-table :data="list" v-loading="loading" stripe>
        <el-table-column prop="name" label="名称" min-width="120" show-overflow-tooltip />
        <el-table-column prop="remark" label="备注" min-width="120" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.remark" style="color: #999;">{{ row.remark }}</span>
            <span v-else style="color: #ccc;">-</span>
          </template>
        </el-table-column>
        <el-table-column label="密钥" min-width="190">
          <template #default="{ row }">
            <code class="key-display">{{ row.key_display }}</code>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="今日调用" width="90" align="center">
          <template #default="{ row }">
            <span>{{ row.used_today ?? '-' }}<template v-if="row.daily_quota"> / {{ row.daily_quota }}</template></span>
          </template>
        </el-table-column>
        <el-table-column label="过期时间" width="170">
          <template #default="{ row }">
            <span v-if="row.expires_at" style="font-size: 12px; color: #999;">{{ formatTime(row.expires_at) }}</span>
            <span v-else>永不</span>
          </template>
        </el-table-column>
        <el-table-column label="最近使用" width="170">
          <template #default="{ row }">
            <span v-if="row.last_used_at" style="font-size: 12px; color: #999;">{{ formatTime(row.last_used_at) }}</span>
            <span v-else style="color: #999;">未使用</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" align="center">
          <template #default="{ row }">
            <el-popconfirm
              v-if="row.status === 'active'"
              title="吊销后立即失效且不可恢复，确定？"
              confirm-button-text="吊销"
              confirm-button-type="danger"
              @confirm="handleRevoke(row.id)"
            >
              <template #reference>
                <el-button type="danger" link size="small">吊销</el-button>
              </template>
            </el-popconfirm>
            <span v-else style="color: #999;">-</span>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && list.length === 0" description="暂无密钥，点击右上角创建" />
    </el-card>

    <!-- 创建弹窗：表单态 ⇄ 密钥展示态（单弹窗，避免双弹窗动画叠加导致遮罩残留） -->
    <el-dialog
      v-model="showDialog"
      :title="createdKey ? '密钥创建成功' : '创建 API 密钥'"
      :width="createdKey ? '520px' : '440px'"
      :close-on-click-modal="!createdKey"
      @closed="onDialogClosed"
    >
      <template v-if="!createdKey">
        <el-form :model="form" label-width="90px">
          <el-form-item label="名称">
            <el-input v-model="form.name" placeholder="如：CI 自动审校（可留空自动命名）" maxlength="100" />
          </el-form-item>
          <el-form-item label="每日上限">
            <el-input-number
              v-model="form.daily_quota"
              :min="1"
              :max="100000"
              placeholder="不填则跟随账号配额"
              style="width: 100%;"
            />
            <div class="form-tip">留空表示跟随账号的每日配额</div>
          </el-form-item>
          <el-form-item label="过期时间">
            <el-date-picker
              v-model="form.expires_at"
              type="datetime"
              placeholder="不填则永不过期"
              style="width: 100%;"
            />
          </el-form-item>
          <el-form-item label="备注">
            <el-input v-model="form.remark" placeholder="如：供 Jenkins 流水线使用（可选）" maxlength="500" />
          </el-form-item>
        </el-form>
      </template>
      <template v-else>
        <el-alert type="warning" :closable="false" style="margin-bottom: 14px;"
          title="完整密钥仅展示这一次，关闭后无法找回，请立即复制保存" />
        <div class="key-box">
          <code>{{ createdKey.key }}</code>
          <el-button type="primary" size="small" @click="copyKey">
            {{ copied ? '已复制' : '复制' }}
          </el-button>
        </div>
        <p class="usage-hint">
          调用示例：<code>curl -X POST /api/v1/open/proofread -H "Authorization: Bearer {{ createdKey.key.slice(0, 10) }}..." -d '{"text": "..."}'</code>
        </p>
      </template>
      <template #footer>
        <template v-if="!createdKey">
          <el-button @click="showDialog = false">取消</el-button>
          <el-button type="primary" :loading="saving" @click="handleCreate">创建</el-button>
        </template>
        <el-button v-else type="primary" @click="showDialog = false">我已保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  listApiKeysApi, createApiKeyApi, revokeApiKeyApi,
  type ApiKeyItem,
} from '@/api/apiKeys'

const loading = ref(false)
const saving = ref(false)
const list = ref<ApiKeyItem[]>([])

const copied = ref(false)
const createdKey = ref<{ key: string; key_display: string } | null>(null)
const form = ref<{ name: string; daily_quota: number | null; expires_at: string | null; remark: string }>({
  name: '', daily_quota: null, expires_at: null, remark: '',
})

const showDialog = ref(false)

onMounted(() => fetchList())

function openCreateDialog() {
  createdKey.value = null
  copied.value = false
  showDialog.value = true
}

async function fetchList() {
  loading.value = true
  try { list.value = (await listApiKeysApi()).items } catch {}
  loading.value = false
}

function resetForm() {
  form.value = { name: '', daily_quota: null, expires_at: null, remark: '' }
}

function onDialogClosed() {
  createdKey.value = null
  copied.value = false
  resetForm()
}

async function handleCreate() {
  saving.value = true
  try {
    const payload: any = { name: form.value.name.trim() || undefined }
    if (form.value.daily_quota != null) payload.daily_quota = form.value.daily_quota
    if (form.value.expires_at) payload.expires_at = form.value.expires_at
    if (form.value.remark.trim()) payload.remark = form.value.remark.trim()
    const res = await createApiKeyApi(payload)
    createdKey.value = res
    copied.value = false
    await fetchList()
  } catch {}
  saving.value = false
}

async function handleRevoke(id: number) {
  try {
    await revokeApiKeyApi(id)
    ElMessage.success('密钥已吊销')
    await fetchList()
  } catch {}
}

async function copyKey() {
  if (!createdKey.value) return
  try {
    await navigator.clipboard.writeText(createdKey.value.key)
    copied.value = true
  } catch {
    // 剪贴板权限被拒时降级选中文本
    const selection = window.getSelection()
    const range = document.createRange()
    const el = document.querySelector('.key-box code')
    if (selection && el) {
      range.selectNodeContents(el)
      selection.removeAllRanges()
      selection.addRange(range)
      ElMessage.info('已选中文本，请手动复制')
    }
  }
}

function openDocs() {
  window.open('/api/v1/open/docs', '_blank')
}

function statusText(status: string) {
  return { active: '正常', revoked: '已吊销', expired: '已过期' }[status] || status
}

function statusTagType(status: string): 'success' | 'info' | 'warning' {
  return ({ active: 'success', revoked: 'info', expired: 'warning' } as const)[status] || 'info'
}

function formatTime(t?: string | null) {
  if (!t) return '-'
  return new Date(t).toLocaleString('zh-CN', { hour12: false })
}
</script>

<style scoped lang="scss">
.apikeys-page { max-width: 1100px; margin: 0 auto; }
.card-header {
  display: flex; align-items: center; justify-content: space-between;
  .card-title { font-size: 18px; font-weight: 600; }
}
.key-display {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px; background: rgba(0, 0, 0, .04); padding: 2px 6px; border-radius: 4px;
}
.form-tip { font-size: 12px; color: #999; line-height: 1.4; margin-top: 4px; }
.key-box {
  display: flex; align-items: center; gap: 10px;
  padding: 12px; border-radius: 8px; background: rgba(0, 0, 0, .04);
  code {
    flex: 1; word-break: break-all;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 13px;
  }
}
.usage-hint {
  margin: 12px 0 0; font-size: 12px; color: #999;
  code {
    display: block; margin-top: 6px; word-break: break-all;
    font-size: 11px; padding: 8px; border-radius: 6px;
    background: rgba(0, 0, 0, .04); color: #666;
  }
}

@media (max-width: 768px) {
  .card-header {
    flex-direction: column; align-items: flex-start; gap: 8px;
  }
}
</style>
