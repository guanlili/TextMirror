<template>
  <div class="admin-policy">
    <header class="policy-heading">
      <h2>把使用范围与额度分开管理</h2><p>游客按平台策略使用，登录成员的额度与功能权限分别管理。</p>
    </header>
    <div class="policy-links">
      <router-link to="/admin/users">
        <strong>成员每日额度</strong><span>在成员资料中按人设置 →</span>
      </router-link><router-link to="/admin/roles">
        <strong>功能访问权限</strong><span>通过角色分配可用功能 →</span>
      </router-link><router-link to="/admin/usage">
        <strong>实际调用用量</strong><span>查看模型消耗与异常请求 →</span>
      </router-link>
    </div>
    <el-alert
      v-if="loadError"
      title="游客策略加载失败，暂不能保存。"
      type="error"
      :closable="false"
    >
      <el-button
        link
        @click="loadPolicy"
      >
        重新加载
      </el-button>
    </el-alert>
    <el-card>
      <template #header>
        <span style="font-weight: 600;">游客策略</span>
      </template>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom: 16px;"
      >
        此处仅影响未登录游客。保存后对后续请求生效；登录成员的额度请在成员管理中设置。
      </el-alert>
      <el-form
        :disabled="loading || loadError || saving"
        label-width="160px"
        style="max-width: 500px;"
      >
        <el-form-item label="每日校对次数上限">
          <el-input-number
            v-model="guestPolicy.daily_limit"
            :min="0"
            :max="100000"
          />
        </el-form-item>
        <el-form-item label="单次最大字数">
          <el-input-number
            v-model="guestPolicy.max_text_length"
            :min="100"
            :max="500000"
            :step="1000"
          />
        </el-form-item>
        <el-form-item label="允许上传文档">
          <el-switch v-model="guestPolicy.allow_upload" />
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            :loading="saving"
            @click="saveGuestPolicy"
          >
            保存游客策略
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { type GuestPolicyConfig, getGuestPolicyApi, updateGuestPolicyApi } from '@/api/admin'
import { getErrorDetail } from '@/utils/request'

const guestPolicy = reactive<GuestPolicyConfig>({
  daily_limit: 0,
  max_text_length: 100,
  allow_upload: true,
})
const saving = ref(false)

const loading = ref(true)
const loadError = ref(false)
onMounted(loadPolicy)
async function loadPolicy() {
  loading.value = true
  loadError.value = false
  try {
    Object.assign(guestPolicy, await getGuestPolicyApi())
  } catch (e: unknown) {
    loadError.value = true
    ElMessage.error(getErrorDetail(e) || '游客策略加载失败')
  } finally { loading.value = false }
}

async function saveGuestPolicy() {
  if (loading.value || loadError.value || saving.value) return
  saving.value = true
  try {
    Object.assign(guestPolicy, await updateGuestPolicyApi(guestPolicy))
    ElMessage.success('游客策略已保存，即时生效')
  } catch (e: unknown) {
    ElMessage.error(getErrorDetail(e) || '保存失败')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped lang="scss">
code {
  padding: 0 4px;
  border-radius: 3px;
  background: rgba(0, 0, 0, 0.06);
}

.policy-heading { margin-bottom:24px; }.policy-heading h2 { font-size:24px; margin-bottom:12px; }.policy-heading p { font-size:13px; color:var(--color-text-secondary); }.policy-links { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px; margin-bottom:28px; }.policy-links a { background:var(--surface); border:1px solid var(--color-border); border-radius:12px; padding:22px; text-decoration:none; color:var(--color-text); }.policy-links strong { display:block; font-size:14px; }.policy-links span { display:block; color:var(--color-primary); margin-top:12px; font-size:12px; }.admin-policy :deep(.el-card) { box-shadow:none; } @media(max-width:700px) { .policy-links { grid-template-columns:1fr; }.policy-heading h2 { font-size:21px; }.admin-policy :deep(.el-form-item__label) { width:130px !important; } }
</style>
