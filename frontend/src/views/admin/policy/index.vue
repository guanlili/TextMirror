<template>
  <div class="admin-policy">
    <el-card>
      <template #header><span style="font-weight: 600;">游客策略</span></template>
      <el-alert type="info" :closable="false" show-icon style="margin-bottom: 16px;">
        保存后即时生效，无需重启；未保存过时以 <code>.env</code> 的
        <code>GUEST_DAILY_LIMIT</code> / <code>GUEST_TEXT_MAX_LENGTH</code> 为准。
        登录用户的每日额度在「用户管理」逐人设置，功能开关在「角色权限」配置。
      </el-alert>
      <el-form label-width="160px" style="max-width: 500px;">
        <el-form-item label="每日校对次数上限">
          <el-input-number v-model="guestPolicy.daily_limit" :min="0" :max="100000" />
        </el-form-item>
        <el-form-item label="单次最大字数">
          <el-input-number v-model="guestPolicy.max_text_length" :min="100" :max="500000" :step="1000" />
        </el-form-item>
        <el-form-item label="允许上传文档">
          <el-switch v-model="guestPolicy.allow_upload" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="saving" @click="saveGuestPolicy">保存游客策略</el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { type GuestPolicyConfig, getGuestPolicyApi, updateGuestPolicyApi } from '@/api/admin'

const guestPolicy = reactive<GuestPolicyConfig>({
  daily_limit: 0,
  max_text_length: 100,
  allow_upload: true,
})
const saving = ref(false)

onMounted(async () => {
  try {
    Object.assign(guestPolicy, await getGuestPolicyApi())
  } catch (e: unknown) {
    ElMessage.error((e as any)?.response?.data?.detail || '游客策略加载失败')
  }
})

async function saveGuestPolicy() {
  saving.value = true
  try {
    Object.assign(guestPolicy, await updateGuestPolicyApi(guestPolicy))
    ElMessage.success('游客策略已保存，即时生效')
  } catch (e: unknown) {
    ElMessage.error((e as any)?.response?.data?.detail || '保存失败')
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
</style>
