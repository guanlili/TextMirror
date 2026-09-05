<template>
  <div class="domain-rules-page">
    <el-card>
      <template #header>
        <div class="card-header">
          <div>
            <span class="card-title">审校领域规则</span>
            <div class="card-subtitle">
              用户选择领域后，对应规则注入 AI 审校提示词。留空使用内置默认，保存后下一次审校即时生效（无需重启）。
            </div>
          </div>
          <el-button type="primary" :loading="saving" @click="saveRules">保存规则</el-button>
        </div>
      </template>

      <el-alert type="info" :closable="false" class="intro-alert">
        <template #title>
          规则即行业审校标准（如电力：变电站非变电所、110kV 的 k 小写；公文：成文日期用汉字、发文字号用〔〕）。
          建议参考内置默认的写法：分条列出、每条一个具体规则，避免笼统描述。
        </template>
      </el-alert>

      <el-tabs v-model="activeTab">
        <el-tab-pane v-for="d in DOMAIN_TABS" :key="d.code" :name="d.code">
          <template #label>
            <span>{{ d.label }}</span>
            <el-tag v-if="prompts[d.code]" type="warning" size="small" class="tab-badge">自定义</el-tag>
          </template>

          <div class="rule-editor">
            <div class="editor-toolbar">
              <el-tag v-if="prompts[d.code]" type="warning" size="small">当前使用自定义规则</el-tag>
              <el-tag v-else type="success" size="small">当前使用内置默认规则</el-tag>
              <div class="toolbar-actions">
                <el-button size="small" @click="fillDefault(d.code)">参照内置默认</el-button>
                <el-button size="small" @click="clearRule(d.code)">恢复内置默认</el-button>
              </div>
            </div>
            <el-input
              v-model="prompts[d.code]"
              type="textarea"
              :rows="14"
              :placeholder="`留空使用内置默认规则：\n${defaults[d.code] || ''}`"
              maxlength="3000"
              show-word-limit
              class="rule-textarea"
            />
            <el-collapse v-if="defaults[d.code]" class="default-ref">
              <el-collapse-item title="查看内置默认规则（当前未自定义时生效的内容）">
                <pre class="default-content">{{ defaults[d.code] }}</pre>
              </el-collapse-item>
            </el-collapse>
          </div>
        </el-tab-pane>
      </el-tabs>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  type DomainPromptsConfig,
  getDomainPromptsApi, updateDomainPromptsApi, getDomainPromptsDefaultsApi,
} from '@/api/admin'

const DOMAIN_TABS = [
  { code: 'general', label: '通用' },
  { code: 'official', label: '公文' },
  { code: 'legal', label: '法律' },
  { code: 'power', label: '电力' },
  { code: 'new_energy', label: '新能源' },
  { code: 'meter', label: '电能表' },
] as const

const activeTab = ref('general')
const saving = ref(false)
const prompts = reactive<DomainPromptsConfig>({
  general: '', official: '', legal: '', power: '', new_energy: '', meter: '',
})
const defaults = reactive<DomainPromptsConfig>({
  general: '', official: '', legal: '', power: '', new_energy: '', meter: '',
})

onMounted(async () => {
  try {
    const [current, def] = await Promise.all([getDomainPromptsApi(), getDomainPromptsDefaultsApi()])
    Object.assign(prompts, current)
    Object.assign(defaults, def)
  } catch {}
})

/** 把内置默认填进编辑框作为起点（可再改） */
function fillDefault(code: string) {
  const key = code as keyof DomainPromptsConfig
  prompts[key] = defaults[key]
}

/** 清空 = 回退内置默认 */
async function clearRule(code: string) {
  if (!prompts[code as keyof DomainPromptsConfig]) return
  try {
    await ElMessageBox.confirm('清空后该领域将恢复使用内置默认规则，确定？', '恢复默认', {
      confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning',
    })
  } catch { return }
  prompts[code as keyof DomainPromptsConfig] = ''
}

async function saveRules() {
  saving.value = true
  try {
    await updateDomainPromptsApi({ ...prompts })
    ElMessage.success('审校规则已保存，下一次审校即时生效')
  } catch {}
  saving.value = false
}
</script>

<style scoped lang="scss">
.domain-rules-page { max-width: 1000px; }
.card-header {
  display: flex; align-items: flex-start; justify-content: space-between; gap: 12px;
  .card-title { font-size: 18px; font-weight: 600; }
  .card-subtitle { font-size: 12px; color: #999; margin-top: 4px; max-width: 620px; }
}
.intro-alert { margin-bottom: 14px; }
.tab-badge { margin-left: 4px; }
.rule-editor {
  .editor-toolbar {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 8px;
    .toolbar-actions { display: flex; gap: 8px; }
  }
  .rule-textarea :deep(textarea) { font-size: 13px; line-height: 1.8; }
  .default-ref { margin-top: 10px;
    .default-content {
      margin: 0; padding: 10px; background: rgba(0,0,0,.03); border-radius: 6px;
      font-size: 12px; line-height: 1.8; white-space: pre-wrap; word-break: break-all;
      color: #666;
    }
  }
}

@media (max-width: 768px) {
  .card-header { flex-direction: column; align-items: stretch; }
}
</style>
