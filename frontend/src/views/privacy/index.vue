<template>
  <div class="privacy-page">
    <div class="privacy-shell">
      <div class="privacy-head">
        <button
          class="back-link"
          type="button"
          @click="goBack"
        >
          ← 返回
        </button>
        <h1>数据与隐私</h1>
        <p>了解 TextMirror 保存哪些数据、如何使用，以及你如何导出或删除游客数据。</p>
      </div>

      <el-card
        class="privacy-card"
        shadow="never"
      >
        <h2>我们保存什么</h2>
        <ul>
          <li>你提交的校对文本、审校问题与建议、润色结果，以及接受/忽略反馈。</li>
          <li>上传文档的原始文件、提取文本和校对结果；游客文件默认 7 天后回收。</li>
          <li>访问日志：IP、浏览器 UA、操作类型、耗时和 Token 用量，用于安全审计与服务改进。</li>
        </ul>

        <h2>用于什么</h2>
        <ul>
          <li>提供校对、润色和文档审校服务，并保存你的历史记录。</li>
          <li>统计高频误报、优化词库与审校规则；游客数据按匿名访客 ID 归属，不要求登录。</li>
          <li>不用于广告投放，不向无关第三方出售数据。</li>
        </ul>

        <h2>保留多久</h2>
        <ul>
          <li>游客校对记录、反馈长期保留，直到你主动删除；游客上传文件默认 7 天回收。</li>
          <li>操作审计日志默认保留 90 天；登录用户上传文件不自动过期。</li>
          <li>登录账号的历史记录可在「校对历史」中逐条删除。</li>
        </ul>

        <h2>你的数据</h2>
        <p class="muted">
          以下操作只针对当前浏览器关联的匿名游客数据；登录后可先合并到账号，再在账号内管理。
        </p>

        <el-alert
          v-if="loadError"
          title="游客数据读取失败，请稍后重试。"
          type="error"
          :closable="false"
          show-icon
        />

        <div
          v-else
          v-loading="loading"
          class="guest-data-box"
        >
          <template v-if="summary && summary.total > 0">
            <div class="guest-counts">
              <span><strong>{{ summary.records }}</strong> 条校对记录</span>
              <span><strong>{{ summary.feedbacks }}</strong> 条反馈</span>
              <span><strong>{{ summary.documents }}</strong> 个上传文档</span>
            </div>
            <div class="guest-actions">
              <el-button
                :loading="exporting"
                @click="handleExport"
              >
                导出游客数据
              </el-button>
              <el-button
                v-if="userStore.isLoggedIn"
                type="primary"
                :loading="claiming"
                @click="handleClaim"
              >
                合并到当前账号
              </el-button>
              <el-button
                v-else
                type="primary"
                @click="goLogin"
              >
                登录后合并到账号
              </el-button>
              <el-button
                type="danger"
                plain
                :loading="deleting"
                @click="handleDelete"
              >
                删除游客数据
              </el-button>
            </div>
          </template>
          <el-empty
            v-else
            description="当前浏览器没有可管理的游客数据"
          />
        </div>

        <h2>联系与申诉</h2>
        <p class="muted">
          如需删除账号级数据、导出机构数据或咨询数据处理方式，请联系平台管理员。
          本页面是数据说明，不构成法律意见；正式对外发布前请由运营方补齐隐私政策主体与联系方式。
        </p>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  claimGuestDataApi, deleteGuestDataApi, exportGuestDataApi, getGuestDataSummaryApi,
  type GuestDataSummary,
} from '@/api/guestData'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const userStore = useUserStore()
const summary = ref<GuestDataSummary | null>(null)
const loading = ref(false)
const loadError = ref(false)
const exporting = ref(false)
const claiming = ref(false)
const deleting = ref(false)

async function loadSummary() {
  loading.value = true
  loadError.value = false
  try {
    summary.value = await getGuestDataSummaryApi()
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

async function handleExport() {
  exporting.value = true
  try {
    const blob = await exportGuestDataApi()
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `textmirror-guest-data-${new Date().toISOString().slice(0, 10)}.json`
    a.click()
    window.URL.revokeObjectURL(url)
  } catch {
    ElMessage.error('导出失败，请稍后重试')
  } finally {
    exporting.value = false
  }
}

async function handleClaim() {
  claiming.value = true
  try {
    const counts = await claimGuestDataApi()
    ElMessage.success(
      `已合并 ${counts.records} 条记录、${counts.feedbacks} 条反馈、${counts.documents} 个文档`,
    )
    await loadSummary()
  } catch {
    ElMessage.error('合并失败，请稍后重试')
  } finally {
    claiming.value = false
  }
}

async function handleDelete() {
  try {
    await ElMessageBox.prompt(
      '此操作会删除当前浏览器关联的全部游客记录、反馈和上传文件，且不可恢复。请输入 DELETE 确认。',
      '删除游客数据',
      {
        confirmButtonText: '确认删除',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger',
        inputPattern: /^DELETE$/,
        inputErrorMessage: '请输入 DELETE',
      },
    )
  } catch {
    return
  }
  deleting.value = true
  try {
    const counts = await deleteGuestDataApi()
    ElMessage.success(`已删除 ${counts.records} 条记录、${counts.feedbacks} 条反馈、${counts.documents} 个文档`)
    await loadSummary()
  } catch {
    ElMessage.error('删除失败，请稍后重试')
  } finally {
    deleting.value = false
  }
}

function goLogin() {
  router.push({ path: '/login', query: { redirect: '/privacy' } })
}

function goBack() {
  if (window.history.length > 1) router.back()
  else router.push('/')
}

onMounted(loadSummary)
</script>

<style scoped lang="scss">
.privacy-page { min-height: 100vh; background: var(--color-bg); padding: 40px 20px; }
.privacy-shell { max-width: 820px; margin: 0 auto; }
.privacy-head {
  margin-bottom: 20px;
  h1 { font-size: 30px; font-weight: 700; margin: 12px 0 10px; }
  p { color: var(--color-text-secondary); font-size: 14px; line-height: 1.8; }
}
.back-link { border: 0; background: none; padding: 0; color: var(--color-primary); cursor: pointer; font-size: 13px; }
.privacy-card {
  box-shadow: none;
  :deep(.el-card__body) { padding: 26px 28px 30px; }
  h2 { font-size: 16px; margin: 22px 0 8px; &:first-child { margin-top: 0; } }
  ul { margin: 0; padding-left: 20px; color: var(--color-text-secondary); font-size: 13px; line-height: 1.9; }
  p { color: var(--color-text-secondary); font-size: 13px; line-height: 1.9; }
}
.muted { color: var(--color-text-tertiary) !important; }
.guest-data-box { margin: 12px 0 8px; min-height: 90px; }
.guest-counts { display: flex; flex-wrap: wrap; gap: 12px 24px; font-size: 13px; color: var(--color-text-secondary); strong { color: var(--color-text); font-size: 20px; margin-right: 4px; } }
.guest-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 16px; }
</style>
