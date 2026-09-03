<template>
  <div class="admin-llm">
    <!-- 顶部操作栏 -->
    <div class="top-bar">
      <div>
        <span class="page-title">大模型配置管理</span>
        <el-tag v-if="activeConfig" type="success" size="small" style="margin-left: 12px;">
          当前使用：{{ activeConfig.name }} ({{ activeConfig.model }})
        </el-tag>
      </div>
      <div style="display: flex; gap: 8px;">
        <el-dropdown @command="(cmd: string | number | object) => handleExport(cmd === 'with-keys')">
          <el-button>
            <el-icon><Download /></el-icon>导出配置<el-icon class="el-icon--right"><ArrowDown /></el-icon>
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="masked">脱敏导出（分享用，密钥打码）</el-dropdown-item>
              <el-dropdown-item command="with-keys">完整导出（含密钥，迁移用）</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button @click="showImportDialog = true"><el-icon><Upload /></el-icon>导入配置</el-button>
        <el-button type="primary" @click="openAddDialog"><el-icon><Plus /></el-icon>添加模型</el-button>
      </div>
    </div>

    <!-- 模型卡片列表 -->
    <div class="config-grid" v-loading="loading">
      <el-card
        v-for="item in configList" :key="item.id"
        :class="['config-card', { 'active-card': item.is_active, 'disabled-card': !item.is_enabled }]"
        shadow="hover"
      >
        <div class="card-top">
          <div class="card-name">
            <span class="name-text">{{ item.name }}</span>
            <el-tag v-if="item.is_active" type="success" size="small" effect="dark">使用中</el-tag>
            <el-tag v-if="!item.is_enabled" type="info" size="small">已停用</el-tag>
          </div>
          <el-dropdown trigger="click" @command="(cmd: string) => handleCommand(cmd, item)">
            <el-button :icon="MoreFilled" link />
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="edit">编辑</el-dropdown-item>
                <el-dropdown-item command="test">测试连接</el-dropdown-item>
                <el-dropdown-item v-if="!item.is_active && item.is_enabled" command="activate">设为当前使用</el-dropdown-item>
                <el-dropdown-item v-if="item.is_enabled" command="disable">停用</el-dropdown-item>
                <el-dropdown-item v-if="!item.is_enabled" command="enable">启用</el-dropdown-item>
                <el-dropdown-item v-if="!item.is_active" command="delete" divided style="color: #f56c6c;">删除</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>

        <div class="card-info">
          <div class="info-row">
            <span class="info-label">供应商</span>
            <el-tag size="small" type="info">{{ providerNameMap[item.provider] || item.provider }}</el-tag>
          </div>
          <div class="info-row">
            <span class="info-label">模型</span>
            <span class="info-value model-name">{{ item.model }}</span>
          </div>
          <div class="info-row">
            <span class="info-label">密钥</span>
            <span class="info-value" style="font-family: monospace; color: #999;">{{ item.api_key_masked }}</span>
          </div>
          <div v-if="testResults[item.id]" class="info-row">
            <span class="info-label">测试</span>
            <span class="info-value" :style="{ fontSize: '12px', color: testResults[item.id].success ? '#67C23A' : '#F56C6C' }">
              {{ testResults[item.id].success ? `通过 · ${testResults[item.id].latency}` : testResults[item.id].message }}
            </span>
          </div>
        </div>

        <div class="card-footer">
          <el-button
            v-if="!item.is_active && item.is_enabled"
            type="primary" size="small" plain
            @click="handleActivate(item.id)"
          >设为当前使用</el-button>
          <el-button size="small" plain :loading="testingId === item.id" @click="handleTest(item.id)">
            {{ testingId === item.id ? '测试中...' : '测试连接' }}
          </el-button>
        </div>
      </el-card>

      <div v-if="!loading && configList.length === 0" class="empty-guide">
        <el-empty description="还没有模型配置，两步即可完成接入">
          <div class="quick-start">
            <p style="color: #999; font-size: 13px; margin-bottom: 12px;">选择常用供应商快速开始：</p>
            <el-button v-for="p in quickStartProviders" :key="p.code" size="small" plain @click="quickAdd(p.code)">
              {{ p.name }}
            </el-button>
          </div>
        </el-empty>
      </div>
    </div>

    <!-- 添加/编辑弹窗 -->
    <el-dialog v-model="showFormDialog" :title="editingId ? '编辑模型配置' : '添加模型配置'" width="600px" destroy-on-close>
      <el-form :model="formData" label-width="100px">
        <el-form-item label="供应商" required>
          <el-select v-model="formData.provider" placeholder="选择供应商" style="width: 100%;" @change="onProviderChange">
            <el-option
              v-for="p in providerOptions" :key="p.code"
              :label="p.name" :value="p.code"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="API Key" required>
          <el-input v-model="formData.api_key" type="password" show-password placeholder="sk-..." />
          <div v-if="editingId" style="font-size: 12px; color: #999; margin-top: 4px;">留空则保持原密钥不变</div>
        </el-form-item>
        <el-form-item label="模型名称">
          <el-select
            v-model="formData.model"
            filterable
            allow-create
            default-first-option
            :placeholder="currentProviderModels.length ? '选择或输入模型名' : '输入模型名'"
            style="width: 100%;"
          >
            <el-option v-for="m in currentProviderModels" :key="m" :label="m" :value="m" />
          </el-select>
          <div style="font-size: 12px; color: #999; margin-top: 4px;">
            <template v-if="currentProviderModels.length">已预置主流模型，其他型号直接输入</template>
            <template v-else>请输入模型名称</template>
            <a
              v-if="currentProviderDocs"
              :href="currentProviderDocs"
              target="_blank"
              rel="noopener noreferrer"
              style="color: var(--el-color-primary); margin-left: 6px;"
            >如何获取模型名称？<el-icon style="vertical-align: -2px;"><Link /></el-icon></a>
          </div>
        </el-form-item>
        <el-form-item v-if="formData.provider === 'custom'" label="API 地址" required>
          <el-input v-model="formData.api_base" placeholder="https://your-endpoint.com/v1" />
        </el-form-item>
        <el-form-item v-else-if="currentProviderBase" label="API 地址">
          <el-input :model-value="currentProviderBase" readonly>
            <template #suffix><el-icon style="color: #67C23A;"><CircleCheckFilled /></el-icon></template>
          </el-input>
          <div style="font-size: 12px; color: #999; margin-top: 4px;">已使用官方地址，无需填写</div>
        </el-form-item>
        <el-form-item label="配置名称">
          <el-input v-model="formData.name" placeholder="留空自动使用供应商名" />
        </el-form-item>

        <!-- 高级参数：默认值即可用，折叠收纳 -->
        <el-collapse style="margin: 4px 0 12px;">
          <el-collapse-item title="高级设置（温度 / Token 上限 / 超时 / 重试）" name="advanced">
            <el-form-item label="温度">
              <el-slider v-model="formData.temperature" :min="0" :max="2" :step="0.1" show-input style="width: 100%;" />
            </el-form-item>
            <el-form-item label="最大 Token">
              <el-input-number v-model="formData.max_tokens" :min="0" :max="128000" placeholder="0 表示不限" />
              <span style="margin-left: 8px; font-size: 12px; color: #999;">0 或空表示不限制</span>
            </el-form-item>
            <el-form-item label="超时(秒)">
              <el-input-number v-model="formData.timeout" :min="10" :max="600" />
            </el-form-item>
            <el-form-item label="重试次数">
              <el-input-number v-model="formData.max_retries" :min="0" :max="10" />
            </el-form-item>
            <el-form-item label="备注">
              <el-input v-model="formData.remark" type="textarea" :rows="2" placeholder="备注说明" />
            </el-form-item>
          </el-collapse-item>
        </el-collapse>
      </el-form>
      <!-- 弹窗内测试结果（内联展示） -->
      <el-alert
        v-if="draftTestResult"
        :type="draftTestResult.success ? 'success' : 'error'"
        :closable="false"
        show-icon
        style="margin-top: 4px;"
      >
        <template #title>
          {{ draftTestResult.success
            ? `连接成功 · ${draftTestResult.model} · 回复: ${draftTestResult.message}`
            : `连接失败: ${draftTestResult.message}` }}
        </template>
      </el-alert>

      <template #footer>
        <div style="display: flex; justify-content: space-between; width: 100%;">
          <el-button :loading="draftTesting" @click="handleDraftTest">
            <el-icon v-if="!draftTesting"><Connection /></el-icon>
            {{ draftTesting ? '测试中...' : '测试连接' }}
          </el-button>
          <div>
            <el-button @click="showFormDialog = false">取消</el-button>
            <el-button type="primary" :loading="submitting" @click="handleSubmit">{{ editingId ? '保存' : '添加' }}</el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <!-- 导入配置弹窗 -->
    <el-dialog v-model="showImportDialog" title="导入模型配置" width="560px" destroy-on-close>
      <el-upload
        drag
        :auto-upload="false"
        :limit="1"
        :on-change="handleImportFileChange"
        accept=".json"
      >
        <el-icon style="font-size: 36px; color: #909399;"><UploadFilled /></el-icon>
        <div class="el-upload__text">将导出的 JSON 文件拖到此处，或 <em>点击选择</em></div>
      </el-upload>

      <div v-if="importPreview" class="import-preview">
        <el-alert type="info" :closable="false" show-icon :title="`文件包含 ${importPreview.count} 条配置`" />
        <div class="import-list">
          <div v-for="c in importPreview.configs" :key="c.name" class="import-item">
            <span>{{ c.name }}</span>
            <span class="import-model">{{ c.model }}</span>
            <el-tag v-if="c.api_key && c.api_key.includes('****')" size="small" type="warning" effect="plain">密钥脱敏</el-tag>
          </div>
        </div>
        <el-form-item label="同名冲突" label-width="80px" style="margin-top: 12px;">
          <el-radio-group v-model="importConflict">
            <el-radio value="skip">跳过（保留现有配置）</el-radio>
            <el-radio value="overwrite">覆盖（密钥为脱敏时保留已存密钥）</el-radio>
          </el-radio-group>
        </el-form-item>
      </div>

      <template #footer>
        <el-button @click="showImportDialog = false">取消</el-button>
        <el-button
          type="primary"
          :disabled="!importPreview"
          :loading="importing"
          @click="handleImportSubmit"
        >导入</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { MoreFilled } from '@element-plus/icons-vue'
import {
  type LLMConfigItem, type LLMProviderOption, type LLMTestResult,
  listLLMConfigsApi, listLLMProvidersApi,
  createLLMConfigApi, updateLLMConfigApi,
  deleteLLMConfigApi, activateLLMConfigApi, testLLMConfigApi,
  testLLMDraftApi, importLLMConfigsApi,
} from '@/api/admin'

const loading = ref(false)
const submitting = ref(false)
const testingId = ref<number | null>(null)
const configList = ref<LLMConfigItem[]>([])
const providerOptions = ref<LLMProviderOption[]>([])
// 测试连接结果（展示在卡片上，刷新列表不清除）
const testResults = ref<Record<number, { success: boolean; latency: string; message: string }>>({})

const showFormDialog = ref(false)
const editingId = ref<number | null>(null)
// 弹窗内测试
const draftTesting = ref(false)
const draftTestResult = ref<LLMTestResult | null>(null)

// 导入/导出
const showImportDialog = ref(false)
const importing = ref(false)
const importPreview = ref<{ count: number; configs: Record<string, any>[] } | null>(null)
const importConflict = ref<'skip' | 'overwrite'>('skip')

/** 导出配置：withKeys=false 脱敏（分享用）/ true 含明文密钥（迁移用，需确认） */
async function handleExport(withKeys: boolean) {
  if (withKeys) {
    try {
      await ElMessageBox.confirm(
        '导出文件将包含明文 API 密钥，请妥善保管，不要分享给他人。确认导出？',
        '安全提示',
        { confirmButtonText: '导出含密钥文件', cancelButtonText: '取消', type: 'warning' }
      )
    } catch { return }
  }
  const token = localStorage.getItem('access_token') || ''
  // 触发浏览器下载（带鉴权头的请求不能直接用 <a href>）
  const resp = await fetch(`/api/v1/admin/llm-config/export?include_keys=${withKeys}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!resp.ok) { ElMessage.error('导出失败'); return }
  const blob = await resp.blob()
  const dispo = resp.headers.get('Content-Disposition') || ''
  const match = dispo.match(/filename="?([^";]+)"?/)
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = match?.[1] || 'textmirror-llm-configs.json'
  a.click()
  URL.revokeObjectURL(url)
  ElMessage.success(withKeys ? '已导出（含密钥，注意保管）' : '已导出（密钥已脱敏）')
}

/** 选择导入文件后解析预览 */
function handleImportFileChange(file: any) {
  const reader = new FileReader()
  reader.onload = () => {
    try {
      const data = JSON.parse(String(reader.result))
      const configs = Array.isArray(data?.configs) ? data.configs : null
      if (!configs || configs.length === 0) {
        ElMessage.warning('文件中没有配置数据（需要 TextMirror 导出的 JSON 格式）')
        importPreview.value = null
        return
      }
      importPreview.value = { count: configs.length, configs }
    } catch {
      ElMessage.error('文件解析失败，请确认是有效的 JSON 文件')
      importPreview.value = null
    }
  }
  reader.readAsText(file.raw)
}

/** 执行导入 */
async function handleImportSubmit() {
  if (!importPreview.value) return
  importing.value = true
  try {
    const res = await importLLMConfigsApi({
      configs: importPreview.value.configs,
      conflict: importConflict.value,
    })
    const parts: string[] = []
    if (res.created) parts.push(`新建 ${res.created}`)
    if (res.updated) parts.push(`覆盖 ${res.updated}`)
    if (res.kept_key) parts.push(`保留密钥 ${res.kept_key}`)
    if (res.skipped) parts.push(`跳过 ${res.skipped}`)
    ElMessage.success(`导入完成：${parts.join('，')}`)
    showImportDialog.value = false
    importPreview.value = null
    fetchList()
    fetchProviders()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '导入失败')
  } finally {
    importing.value = false
  }
}
const formData = reactive({
  name: '', provider: 'deepseek', api_base: '', api_key: '', model: '',
  temperature: 0.3, max_tokens: 0, timeout: 60, max_retries: 3, remark: '',
})

const activeConfig = computed(() => configList.value.find(c => c.is_active))

const providerNameMap = computed(() => {
  const map: Record<string, string> = {}
  providerOptions.value.forEach(p => { map[p.code] = p.name })
  return map
})

/** 当前供应商的推荐模型列表 */
const currentProviderModels = computed(() => {
  const p = providerOptions.value.find(o => o.code === formData.provider)
  return p?.models || []
})

/** 当前供应商的官方 API 地址（只读展示，用户不填） */
const currentProviderBase = computed(() => {
  const p = providerOptions.value.find(o => o.code === formData.provider)
  return p?.default_base || ''
})

/** 当前供应商的模型列表文档地址（帮助管理员找到模型名） */
const currentProviderDocs = computed(() => {
  const p = providerOptions.value.find(o => o.code === formData.provider)
  return p?.model_docs || ''
})

/** 空状态快捷入口：国内外最常用的 6 家 */
const quickStartProviders = computed(() => {
  const preferred = ['deepseek', 'volcengine', 'qwen', 'hunyuan', 'openai', 'siliconflow']
  return preferred
    .map(code => providerOptions.value.find(p => p.code === code))
    .filter((p): p is LLMProviderOption => !!p)
})

/** 从空状态快速添加：预选供应商直接开弹窗 */
function quickAdd(code: string) {
  openAddDialog()
  formData.provider = code
  onProviderChange(code)
}

onMounted(() => {
  fetchProviders()
  fetchList()
})

async function fetchProviders() {
  try {
    providerOptions.value = await listLLMProvidersApi()
  } catch (e) { /* 静默 */ }
}

async function fetchList() {
  loading.value = true
  try {
    configList.value = await listLLMConfigsApi()
  } catch (e) {
    ElMessage.error('加载模型配置失败')
  } finally {
    loading.value = false
  }
}

function onProviderChange(code: string) {
  const p = providerOptions.value.find(o => o.code === code)
  if (!p) return
  // 切换供应商时强制刷新默认值；名称若仍是某家默认名（用户未自定义）则跟随切换
  formData.api_base = p.default_base
  formData.model = p.default_model
  const isDefaultName = providerOptions.value.some(o => o.name === formData.name)
  if (!formData.name || isDefaultName) formData.name = p.name
}

/** 弹窗内测试当前表单配置（无需先保存） */
async function handleDraftTest() {
  const p = providerOptions.value.find(o => o.code === formData.provider)
  const apiBase = formData.api_base?.trim() || p?.default_base || ''
  const model = formData.model?.trim() || p?.default_model || ''
  if (!formData.api_key.trim() && !editingId.value) return ElMessage.warning('请先填写 API Key')
  if (!apiBase) return ElMessage.warning('缺少 API 地址')
  if (!model) return ElMessage.warning('请选择或输入模型名称')

  draftTesting.value = true
  draftTestResult.value = null
  const t0 = performance.now()
  try {
    const result = await testLLMDraftApi({
      provider: formData.provider,
      api_base: apiBase,
      api_key: formData.api_key.trim() || undefined,
      model,
      config_id: editingId.value || undefined,
    })
    draftTestResult.value = result
    if (result.success) {
      ElMessage.success(`连接成功（${Math.round(performance.now() - t0)}ms）`)
    } else {
      ElMessage.error('连接失败，详见弹窗内提示')
    }
  } catch (e: any) {
    draftTestResult.value = {
      success: false, model,
      message: e?.response?.data?.detail || '测试请求失败',
      usage: {},
    }
  } finally {
    draftTesting.value = false
  }
}

function openAddDialog() {
  editingId.value = null
  Object.assign(formData, {
    name: '', provider: 'deepseek', api_base: '', api_key: '', model: '',
    temperature: 0.3, max_tokens: 0, timeout: 60, max_retries: 3, remark: '',
  })
  // 预填默认供应商的地址/模型/名称，用户打开即可只填 Key
  onProviderChange(formData.provider)
  draftTestResult.value = null
  showFormDialog.value = true
}

function openEditDialog(item: LLMConfigItem) {
  editingId.value = item.id
  Object.assign(formData, {
    name: item.name, provider: item.provider, api_base: item.api_base,
    api_key: '', model: item.model, temperature: item.temperature,
    max_tokens: item.max_tokens || 0, timeout: item.timeout,
    max_retries: item.max_retries, remark: item.remark || '',
  })
  draftTestResult.value = null
  showFormDialog.value = true
}

async function handleSubmit() {
  // 必填仅 Key 一项；名称/地址/模型留空时按供应商默认值兜底
  if (!editingId.value && !formData.api_key.trim()) return ElMessage.warning('请输入 API Key')
  const p = providerOptions.value.find(o => o.code === formData.provider)
  if (!formData.api_base.trim() && !p?.default_base) return ElMessage.warning('请输入 API 地址')
  if (!formData.model.trim() && !p?.default_model) return ElMessage.warning('请选择或输入模型名称')

  submitting.value = true
  try {
    const payload: any = { ...formData }
    if (!payload.name?.trim()) payload.name = p?.name || formData.provider
    if (!payload.api_base?.trim()) payload.api_base = p?.default_base
    if (!payload.model?.trim()) payload.model = p?.default_model
    if (payload.max_tokens === 0) payload.max_tokens = null
    // 编辑时如果密钥留空则不提交
    if (editingId.value && !payload.api_key) delete payload.api_key

    if (editingId.value) {
      await updateLLMConfigApi(editingId.value, payload)
      ElMessage.success('配置已更新')
    } else {
      await createLLMConfigApi(payload)
      ElMessage.success('配置已添加')
    }
    showFormDialog.value = false
    fetchList()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '操作失败')
  } finally {
    submitting.value = false
  }
}

async function handleActivate(id: number) {
  try {
    await activateLLMConfigApi(id)
    ElMessage.success('已切换当前使用的模型')
    fetchList()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '切换失败')
  }
}

async function handleTest(id: number) {
  testingId.value = id
  const t0 = performance.now()
  try {
    const result = await testLLMConfigApi(id)
    const latency = `${Math.round(performance.now() - t0)}ms`
    testResults.value[id] = {
      success: result.success,
      latency,
      message: result.success ? '通过' : (result.message || '失败').slice(0, 60),
    }
    if (result.success) {
      ElMessage.success(`连接成功！模型: ${result.model}，回复: ${result.message}`)
    } else {
      ElMessage.error(`连接失败: ${result.message}`)
    }
  } catch (e: any) {
    testResults.value[id] = { success: false, latency: '', message: '请求异常' }
    ElMessage.error('测试请求失败')
  } finally {
    testingId.value = null
  }
}

async function handleDelete(id: number) {
  try {
    await ElMessageBox.confirm('确定删除该模型配置？', '删除确认', { type: 'warning' })
    await deleteLLMConfigApi(id)
    ElMessage.success('已删除')
    fetchList()
  } catch (e: any) {
    if (e !== 'cancel') ElMessage.error(e?.response?.data?.detail || '删除失败')
  }
}

async function handleToggleEnabled(item: LLMConfigItem, enabled: boolean) {
  try {
    await updateLLMConfigApi(item.id, { is_enabled: enabled })
    ElMessage.success(enabled ? '已启用' : '已停用')
    fetchList()
  } catch (e) {
    ElMessage.error('操作失败')
  }
}

function handleCommand(cmd: string, item: LLMConfigItem) {
  switch (cmd) {
    case 'edit': openEditDialog(item); break
    case 'test': handleTest(item.id); break
    case 'activate': handleActivate(item.id); break
    case 'disable': handleToggleEnabled(item, false); break
    case 'enable': handleToggleEnabled(item, true); break
    case 'delete': handleDelete(item.id); break
  }
}
</script>

<style scoped lang="scss">
.admin-llm {
  .top-bar {
    display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px;
    .page-title { font-size: 18px; font-weight: 600; color: #333; }
  }

  .config-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
    gap: 16px;
  }

  .config-card {
    border-radius: 8px;
    transition: all 0.2s;
    &.active-card { border: 2px solid #0056b3; }
    &.disabled-card { opacity: 0.6; }

    .card-top {
      display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px;
      .card-name {
        display: flex; align-items: center; gap: 8px;
        .name-text { font-size: 16px; font-weight: 600; color: #333; }
      }
    }

    .card-info {
      .info-row {
        display: flex; align-items: center; padding: 4px 0; gap: 8px;
        .info-label { font-size: 13px; color: #999; min-width: 50px; flex-shrink: 0; }
        .info-value { font-size: 13px; color: #333; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .model-name { font-weight: 600; color: #0056b3; }
      }
    }

    .card-footer {
      display: flex; gap: 8px; margin-top: 16px; padding-top: 12px; border-top: 1px solid #f0f0f0;
    }
  }
}

.empty-guide {
  grid-column: 1 / -1;

  .quick-start {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;

    .el-button { margin: 2px; }
  }
}

/* 导入预览 */
.import-preview {
  margin-top: 14px;

  .import-list {
    margin-top: 10px;
    max-height: 180px;
    overflow-y: auto;
    border: 1px solid var(--surface-border, #e5ebf3);
    border-radius: 8px;
    padding: 4px 10px;
  }

  .import-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 2px;
    font-size: 13px;
    border-bottom: 1px dashed #f0f2f5;

    &:last-child { border-bottom: none; }

    .import-model {
      color: #8d99a9;
      font-size: 12px;
      font-family: monospace;
      margin-left: auto;
      margin-right: 6px;
    }
  }
}
</style>
