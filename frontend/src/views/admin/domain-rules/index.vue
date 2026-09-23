<template>
  <div class="domain-rules-page">
    <header class="rules-heading">
      <div><span class="eyebrow">审校标准</span><h2>让每一条建议，有章可循。</h2><p>通用检查默认使用；公文与法律文书规范由用户按需选择。</p></div><el-button
        type="primary"
        :loading="saving"
        :disabled="loading || loadError || !dirty"
        @click="saveRules"
      >
        保存并生效
      </el-button>
    </header>
    <el-alert
      v-if="loadError"
      title="规则加载失败，暂不能编辑或保存，避免覆盖已有配置。"
      type="error"
      :closable="false"
    >
      <el-button
        link
        @click="loadRules"
      >
        重新加载
      </el-button>
    </el-alert>
    <div
      v-loading="loading"
      class="rules-layout"
    >
      <aside class="rules-nav">
        <button
          v-for="d in domains"
          :key="d.code"
          :class="{ selected: activeTab === d.code }"
          :aria-pressed="activeTab === d.code"
          @click="activeTab = d.code"
        >
          <strong>{{ d.label }}</strong><span>{{ d.description }}</span><small>{{ saved[d.code] ? '已启用自定义规则' : '使用内置规则' }}{{ prompts[d.code] !== saved[d.code] ? ' · 待保存' : '' }}</small>
        </button><p>保存后，对后续审校生效。已有结果不会重新生成。</p>
      </aside>
      <el-card
        shadow="never"
        class="rules-editor"
      >
        <template #header>
          <div class="editor-heading">
            <strong>{{ activeDomain.label }}</strong><el-tag
              :type="dirty ? 'warning' : 'info'"
              size="small"
            >
              {{ loading ? '正在加载' : loadError ? '未能读取' : dirty ? '有未保存修改' : '已同步服务端' }}
            </el-tag>
          </div>
        </template>
        <div class="editor-toolbar">
          <span>自定义检查要求</span><div>
            <el-button
              text
              :disabled="loading || loadError || saving"
              @click="fillDefault"
            >
              以内置规则为起点
            </el-button><el-button
              text
              :disabled="loading || loadError || saving || !prompts[activeTab]"
              @click="prompts[activeTab] = ''"
            >
              使用内置规则
            </el-button>
          </div>
        </div>
        <el-input
          v-model="prompts[activeTab]"
          type="textarea"
          :rows="13"
          :disabled="loading || loadError || saving"
          maxlength="3000"
          show-word-limit
          aria-label="自定义审校规则"
          placeholder="留空时使用内置规则。建议分条说明具体检查要求、适用场景与例外情况。"
        />
        <p class="editor-note">
          自定义内容会替换该领域的默认规则。请确认需要保留的检查要求也包含在内。
        </p>
        <details
          class="rule-preview"
          open
        >
          <summary>保存后将使用的规则 · {{ prompts[activeTab].trim() ? '自定义' : '内置默认' }}</summary><pre>{{ prompts[activeTab].trim() || defaults[activeTab] || (loading ? '正在读取…' : '暂无可展示的规则') }}</pre>
        </details>
        <div class="save-footer">
          <span>{{ loading ? '正在读取规则' : loadError ? '请先重新加载规则' : dirty ? '当前修改尚未生效' : '规则与服务端一致' }}</span><el-button
            :disabled="!dirty || saving || loading || loadError"
            @click="discardChanges"
          >
            撤回本页修改
          </el-button>
        </div>
      </el-card>
    </div>
  </div>
</template>
<script setup lang="ts">
import { reactive, ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { type DomainPromptsConfig, getDomainPromptsApi, updateDomainPromptsApi, getDomainPromptsDefaultsApi } from '@/api/admin'
type Domain = keyof DomainPromptsConfig
const domains = [
 { code: 'general' as Domain, label: '通用检查', description: '文字、语法与日常表达' },
 { code: 'official' as Domain, label: '公文规范', description: '正式措辞与行文规范' },
 { code: 'legal' as Domain, label: '法律文书规范', description: '术语、主体与条款表达' },
]
const activeTab = ref<Domain>('general')
const activeDomain = computed(() => domains.find(d => d.code === activeTab.value)!)
const prompts = reactive<DomainPromptsConfig>({ general: '', official: '', legal: '' })
const defaults = reactive<DomainPromptsConfig>({ general: '', official: '', legal: '' })
const saved = reactive<DomainPromptsConfig>({ general: '', official: '', legal: '' })
const loading = ref(true)
const loadError = ref(false)
const saving = ref(false)
const dirty = computed(() => domains.some(d => prompts[d.code] !== saved[d.code]))
let alive = true
async function loadRules() {
 loading.value = true; loadError.value = false
 try { const [current, base] = await Promise.all([getDomainPromptsApi(), getDomainPromptsDefaultsApi()]); if (!alive) return; Object.assign(prompts, current); Object.assign(saved, current); Object.assign(defaults, base) }
 catch { if (alive) loadError.value = true }
 finally { if (alive) loading.value = false }
}
function fillDefault() { prompts[activeTab.value] = defaults[activeTab.value] }
async function discardChanges() { try { await ElMessageBox.confirm('撤回本页尚未保存的规则修改？', '撤回修改', { confirmButtonText: '撤回', cancelButtonText: '继续编辑' }); Object.assign(prompts, saved) } catch { /* 保留修改 */ } }
async function saveRules() {
 if (loading.value || loadError.value || saving.value || !dirty.value) return
 const payload = { ...prompts }
 saving.value = true
 try { const result = await updateDomainPromptsApi(payload); if (!alive) return; Object.assign(saved, result); Object.assign(prompts, result); ElMessage.success('规则已保存，后续审校使用新规则') }
 catch { /* 请求层显示错误，保留编辑内容。 */ }
 finally { if (alive) saving.value = false }
}
onBeforeRouteLeave(async () => { if (!dirty.value && !saving.value) return true; try { await ElMessageBox.confirm(saving.value ? '规则正在保存，离开后请重新查看保存结果。' : '尚有未保存的规则，离开后将丢失修改。', '离开规则编辑', { confirmButtonText: '离开', cancelButtonText: '继续编辑' }); return true } catch { return false } })
function beforeUnload(event: { preventDefault(): void; returnValue: string }) { if (dirty.value || saving.value) { event.preventDefault(); event.returnValue = '' } }
onMounted(() => { void loadRules(); window.addEventListener('beforeunload', beforeUnload) })
onBeforeUnmount(() => { alive = false; window.removeEventListener('beforeunload', beforeUnload) })
</script>
<style scoped>
.rules-heading { display:flex; align-items:center; justify-content:space-between; gap:20px; margin-bottom:28px; }.eyebrow { color:var(--color-primary); font-size:12px; letter-spacing:2px; }.rules-heading h2 { font-size:26px; margin:12px 0; font-weight:600; }.rules-heading p { color:var(--color-text-secondary); font-size:13px; line-height:1.8; }
.rules-layout { display:grid; grid-template-columns:240px minmax(0,1fr); gap:24px; }.rules-nav { display:flex; flex-direction:column; gap:10px; }.rules-nav button { text-align:left; padding:20px; border:1px solid var(--color-border); border-radius:10px; background:var(--surface); color:var(--color-text); cursor:pointer; }.rules-nav button.selected { border-color:var(--color-primary); background:var(--el-color-primary-light-9); }.rules-nav strong { font-size:14px; }.rules-nav span,.rules-nav small { display:block; margin-top:10px; color:var(--color-text-secondary); font-size:12px; }.rules-nav small { color:var(--color-primary); }.rules-nav p,.editor-note { font-size:12px; color:var(--color-text-secondary); line-height:1.8; margin-top:12px; }.editor-heading,.editor-toolbar,.save-footer { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; }.editor-toolbar { margin-bottom:12px; font-size:13px; }.rules-editor :deep(textarea) { font-size:14px; line-height:1.9; padding:16px; }.rule-preview { border-top:1px solid var(--color-border); margin-top:24px; padding-top:18px; }.rule-preview summary { font-size:13px; cursor:pointer; }.rule-preview pre { white-space:pre-wrap; overflow-wrap:anywhere; line-height:1.9; padding:18px; background:var(--surface-soft); border-radius:8px; margin-top:14px; font-family:inherit; font-size:13px; }.save-footer { border-top:1px solid var(--color-border); margin-top:20px; padding-top:18px; }.save-footer span { font-size:12px; color:var(--color-text-secondary); }
@media(max-width:1000px) { .rules-layout { grid-template-columns:1fr; }.rules-nav { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); }.rules-nav p { grid-column:1/-1; }.rules-nav button { padding:14px; }.rules-nav span { display:none; } } @media(max-width:600px) { .rules-heading h2 { font-size:21px; }.rules-heading { align-items:flex-start; }.rules-heading .el-button { flex-shrink:0; }.rules-nav small { font-size:10px; } }
</style>
