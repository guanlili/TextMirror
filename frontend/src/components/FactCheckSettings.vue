<template>
  <el-card class="fact-check-settings" data-testid="fact-check-settings">
    <template #header><strong>事实核查设置</strong></template>
    <p class="muted">优先使用模型原生联网，也可手动选择 Tavily。事实核查默认关闭；启用后仍需用户确认并手动启动，不会自动发送审阅内容。</p>
    <p v-if="loading" role="status">正在读取事实核查设置…</p>
    <div v-if="error" class="error-box" role="alert">
      <p>{{ error }}</p>
      <el-button v-if="!loaded" :disabled="loading" @click="load">重试加载事实核查设置</el-button>
    </div>
    <el-form label-position="top" :disabled="loading || saving || !loaded" @submit.prevent="save">
      <div class="settings-grid">
        <el-form-item label="启用事实核查"><el-switch v-model="draft.enabled" aria-label="启用事实核查" /></el-form-item>
        <el-form-item label="每次最多核查事实数"><el-input-number v-model="draft.max_claims" :min="1" :max="10" :precision="0" :step="1" aria-label="每次最多核查事实数" /></el-form-item>
      </div>
      <el-form-item label="事实核查模型">
        <el-select v-model="draft.model_config_id" clearable placeholder="跟随当前活跃模型" aria-label="事实核查模型">
          <el-option v-for="model in models" :key="model.id" :value="model.id" :label="`${model.name} · ${model.model}`" />
        </el-select>
        <p class="muted">留空跟随活跃模型；单独选择不会改变文字审校模型。保存时校验所选配置。</p>
      </el-form-item>
      <el-form-item label="搜索服务">
        <el-select v-model="draft.provider" aria-label="事实核查搜索服务">
          <el-option label="模型原生联网（默认）" value="model" />
          <el-option label="Tavily（手动配置）" value="tavily" />
        </el-select>
      </el-form-item>
      <template v-if="draft.provider === 'model'">
        <p class="muted">复用当前启用的模型配置：{{ modelName || '未配置模型' }}。使用其已保存密钥，无需另填搜索密钥。</p>
        <p class="muted">模型版本及账号需支持原生联网，可能产生搜索工具及 Token 费用。适配器端点支持不代表实际模型或账号可用，运行时仍可能拒绝；不会自动切换到 Tavily。供应商搜索次数限制为尽力执行，并非费用硬上限。</p>
        <p v-if="loaded" :class="modelSearchSupported ? 'muted' : 'warning-text'" role="status">{{ modelSearchSupported ? '当前配置的适配器端点支持原生联网。' : '当前配置的适配器端点不支持原生联网。' }}{{ modelSearchReason }}</p>
      </template>
      <el-form-item v-else label="Tavily API Key">
        <el-input v-model="apiKey" type="password" autocomplete="new-password" aria-label="Tavily API Key" placeholder="留空保持现有密钥，不会回显已保存密钥" />
        <p class="muted" role="status">{{ keyConfigured ? '已配置 Tavily 密钥' : '尚未配置 Tavily 密钥' }} · 留空保留已保存密钥，仅在 Tavily 模式保存时发送新密钥。</p>
      </el-form-item>
      <div class="sources-heading"><strong>可信信源</strong><el-button :disabled="loading || saving || !loaded" data-testid="fact-check-add-source" @click="addSource">添加信源</el-button></div>
      <p class="muted">名称必填，域名只填纯域名（不是 URL），路径前缀默认为 /。不预置站点；已保存信源可停用，未保存项可移除。</p>
      <p v-if="!draft.sources.length" class="muted">暂无可信信源。启用检索服务后可使用联网搜索，可信信源模式需至少一个已启用信源。</p>
      <div v-for="(source, index) in draft.sources" :key="source.id" class="source-row" data-testid="fact-check-source-row">
        <div class="source-fields">
          <el-form-item label="名称" required><el-input v-model="source.name" :aria-label="`信源 ${index + 1} 名称`" placeholder="请输入信源名称" /></el-form-item>
          <el-form-item label="域名" required><el-input v-model="source.domain" :aria-label="`信源 ${index + 1} 域名`" placeholder="纯域名，不含协议、端口或路径" /></el-form-item>
          <el-form-item label="路径前缀"><el-input v-model="source.path_prefix" :aria-label="`信源 ${index + 1} 路径前缀`" placeholder="/" /></el-form-item>
        </div>
        <div class="source-actions">
          <el-switch v-model="source.is_enabled" :aria-label="`启用信源 ${index + 1}`" active-text="启用" inactive-text="停用" />
          <el-button v-if="!savedIds.has(source.id)" text type="danger" :disabled="saving" :aria-label="`移除未保存信源 ${index + 1}`" @click="removeSource(source.id)">移除未保存项</el-button>
          <span v-else class="muted">已保存 · 不删除历史引用</span>
        </div>
      </div>
      <p v-if="loaded && validationError" class="warning-text" role="status">{{ validationError }}</p>
      <p v-if="notice" class="success-text" role="status">{{ notice }}</p>
      <el-button type="primary" :loading="saving" :disabled="!loaded || loading || saving || !!validationError" data-testid="fact-check-save-settings" @click="save">保存事实核查设置</el-button>
    </el-form>
  </el-card>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElButton, ElCard, ElForm, ElFormItem, ElInput, ElInputNumber, ElOption, ElSelect, ElSwitch } from 'element-plus'
import { createFactCheckId, getFactCheckSettingsApi, saveFactCheckSettingsApi, type FactCheckSettings, type SaveFactCheckSettingsPayload } from '@/api/factCheck'
import { getReviewErrorDetail } from '@/api/review'
import { listLLMConfigsApi, type LLMConfigItem } from '@/api/admin'
import { useUserStore } from '@/stores/user'
const models = ref<LLMConfigItem[]>([])
const savedModelId = ref<number | null>(null)

const draft = ref<SaveFactCheckSettingsPayload>({ enabled: false, provider: 'model', model_config_id: null, max_claims: 10, sources: [] })
const apiKey = ref('')
const keyConfigured = ref(false)
const modelName = ref('')
const modelSearchSupported = ref(false)
const modelSearchReason = ref('')
const savedIds = ref(new Set<string>())
const loaded = ref(false)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')
let alive = true
let sequence = 0
let controller: AbortController | null = null

function validDomain(value: string) {
  if (!value || /[\s/:?#@\\%]/.test(value) || value.startsWith('.') || value.endsWith('.')) return false
  try {
    const host = new URL(`https://${value}`).hostname
    return host.includes('.') && host.length <= 253 && host.split('.').every(label => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(label))
  } catch { return false }
}
const validationError = computed(() => {
  if (!Number.isInteger(draft.value.max_claims) || draft.value.max_claims < 1 || draft.value.max_claims > 10) return '每次核查事实数必须为 1–10 之间的整数。'
  if (draft.value.enabled && draft.value.provider === 'model' && (draft.value.model_config_id ?? null) === savedModelId.value && !modelSearchSupported.value) return '当前模型配置不支持原生联网，请更新模型配置或手动选择 Tavily。'
  if (draft.value.enabled && draft.value.provider === 'tavily' && !keyConfigured.value && !apiKey.value.trim()) return '启用前请填写 Tavily API Key。'
  for (const [index, source] of draft.value.sources.entries()) {
    if (!source.name.trim()) return `信源 ${index + 1}：名称必填。`
    if (!validDomain(source.domain.trim())) return `信源 ${index + 1}：请填写纯域名，不含协议、端口、路径或查询参数。`
    const path = source.path_prefix.trim() || '/'
    if (!path.startsWith('/') || path.startsWith('//') || /[\s?#%\\]/.test(path) || path.split('/').some(part => part === '..' || part === '.')) return `信源 ${index + 1}：路径前缀须以 / 开头，不能包含查询参数或相对路径。`
  }
  return ''
})
function applySettings(response: FactCheckSettings) {
  // 只接收契约字段，绝不把响应中的任意字段回填到密钥输入框。
  draft.value = { enabled: response.enabled, provider: response.provider ?? 'model', max_claims: response.max_claims, sources: response.sources.map(source => ({ ...source })) }
  draft.value.model_config_id = response.model_config_id ?? null
  savedModelId.value = response.model_config_id ?? null
  keyConfigured.value = response.api_key_configured
  modelName.value = response.model_name
  modelSearchSupported.value = response.model_search_supported
  modelSearchReason.value = response.model_search_reason
  savedIds.value = new Set(response.sources.map(source => source.id))
  apiKey.value = ''
  loaded.value = true
}
async function load() {
  if (!alive || loading.value || saving.value) return
  const token = ++sequence
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  error.value = ''
  try {
    const response = await getFactCheckSettingsApi({ signal: controller.signal })
    if (alive && token === sequence) applySettings(response)
    if (useUserStore().hasPermission('admin:llm:view')) {
      const available = await listLLMConfigsApi()
      if (alive && token === sequence) models.value = available.filter(item => item.is_enabled)
    }
  } catch (cause) {
    if (alive && token === sequence) error.value = getReviewErrorDetail(cause)
  } finally {
    if (alive && token === sequence) loading.value = false
  }
}
function addSource() {
  if (!loaded.value || loading.value || saving.value) return
  if (draft.value.sources.length >= 20) {
    error.value = '最多配置 20 个可信信源，可编辑现有信源。'
    return
  }
  if (typeof globalThis.crypto?.getRandomValues !== 'function') {
    error.value = '浏览器不支持安全随机数，请更换浏览器后重试。'
    return
  }
  draft.value.sources.push({ id: createFactCheckId(), name: '', domain: '', path_prefix: '/', is_enabled: true })
  notice.value = ''
}
function removeSource(id: string) {
  if (saving.value || savedIds.value.has(id)) return
  draft.value.sources = draft.value.sources.filter(source => source.id !== id)
  notice.value = ''
}
async function save() {
  if (!alive || !loaded.value || loading.value || saving.value || validationError.value) return
  const token = ++sequence
  controller?.abort()
  controller = new AbortController()
  saving.value = true
  error.value = ''
  notice.value = ''
  const payload = { ...draft.value, sources: draft.value.sources.map(source => ({ ...source })), ...(draft.value.provider === 'tavily' && apiKey.value.trim() ? { api_key: apiKey.value.trim() } : {}) }
  try {
    const response = await saveFactCheckSettingsApi(payload, { signal: controller.signal })
    if (!alive || token !== sequence) return
    applySettings(response)
    notice.value = '事实核查设置已保存；用户仍需明确确认后启动。'
  } catch (cause) {
    if (alive && token === sequence) error.value = getReviewErrorDetail(cause)
  } finally {
    if (alive && token === sequence) saving.value = false
  }
}
watch(() => draft.value.provider, () => { apiKey.value = ''; notice.value = '' }, { flush: 'sync' })
onMounted(() => { void load() })
onBeforeUnmount(() => { alive = false; sequence++; controller?.abort(); apiKey.value = '' })
</script>

<style scoped>
.fact-check-settings { margin-top: 16px; }
.fact-check-settings p { margin: 8px 0 12px; line-height: 1.65; overflow-wrap: anywhere; }
.fact-check-settings .el-form { max-width: 960px; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
.settings-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; max-width: 560px; }
.sources-heading, .source-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; }
.sources-heading { justify-content: space-between; margin-top: 20px; }
.source-row { border: 1px solid var(--el-border-color-lighter); padding: 14px; border-radius: 6px; margin: 12px 0; }
.source-fields { display: grid; grid-template-columns: 1fr 1.4fr 1fr; gap: 12px; }
.source-fields > * { min-width: 0; }
.error-box { color: var(--el-color-danger); padding: 10px 12px; background: var(--el-color-danger-light-9); margin-bottom: 12px; }
.warning-text { color: var(--el-color-warning-dark-2); font-size: 13px; }
.success-text { color: var(--el-color-success-dark-2); font-size: 13px; }
@media (max-width: 640px) { .settings-grid, .source-fields { grid-template-columns: minmax(0, 1fr); gap: 0; } }
</style>
