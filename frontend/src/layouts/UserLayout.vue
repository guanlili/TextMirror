<template>
  <el-container class="user-layout">
    <aside class="workspace-sidebar desktop-only">
      <div
        class="brand"
        @click="router.push('/workbench')"
      >
        <div class="brand-mark">
          <img
            :src="siteStore.faviconUrl"
            alt=""
          >
        </div>
        <div class="brand-copy">
          <strong>{{ siteStore.platformName }}</strong>
          <span>{{ siteStore.platformSubtitle }}</span>
        </div>
      </div>

      <div class="sidebar-label">
        创作空间
      </div>
      <el-menu
        :default-active="activeMenu"
        router
        class="workspace-menu"
      >
        <el-menu-item index="/workbench">
          <el-icon><House /></el-icon><span>工作台</span>
        </el-menu-item>
        <el-menu-item index="/proofread/text">
          <el-icon><Edit /></el-icon><span>新建审校</span>
        </el-menu-item>
        <el-menu-item
          v-if="userStore.isLoggedIn && userStore.hasPermission('fact-check:run')"
          index="/fact-check"
        >
          <el-icon><Search /></el-icon><span>事实核查</span>
        </el-menu-item>
      </el-menu>

      <template v-if="userStore.isLoggedIn">
        <div class="sidebar-label secondary-label">
          我的内容
        </div>
        <el-menu
          :default-active="activeMenu"
          router
          class="workspace-menu"
        >
          <el-menu-item index="/dictionary">
            <el-icon><Collection /></el-icon><span>个性化词库</span>
          </el-menu-item>
          <el-menu-item index="/whitelist">
            <el-icon><CircleCheck /></el-icon><span>放行词管理</span>
          </el-menu-item>
          <el-menu-item index="/history">
            <el-icon><Clock /></el-icon><span>校对历史</span>
          </el-menu-item>
          <el-menu-item index="/apikeys">
            <el-icon><Key /></el-icon><span>API 密钥</span>
          </el-menu-item>
        </el-menu>
      </template>

      <div class="sidebar-label secondary-label">
        更多工具
      </div>
      <el-menu
        :default-active="activeMenu"
        router
        class="workspace-menu"
      >
        <el-menu-item index="/polish">
          <el-icon><MagicStick /></el-icon><span>AI 智能润色</span>
        </el-menu-item>
      </el-menu>

      <div class="sidebar-spacer" />
      <button
        class="theme-toggle"
        :title="isDark ? '切换到亮色模式' : '切换到暗色模式'"
        @click="toggle"
      >
        <el-icon><component :is="isDark ? Sunny : Moon" /></el-icon>
        <span>{{ isDark ? '亮色模式' : '暗色模式' }}</span>
      </button>
      <div class="security-note">
        <div class="security-icon">
          <el-icon><Lock /></el-icon>
        </div>
        <div><strong>内容安全保护</strong><span>传输加密 · 权限隔离</span></div>
      </div>
    </aside>

    <el-container class="workspace-shell">
      <el-header class="workspace-header">
        <div class="header-left">
          <el-button
            class="mobile-menu-btn"
            text
            circle
            aria-label="打开导航菜单"
            @click="mobileMenuVisible = true"
          >
            <el-icon><Operation /></el-icon>
          </el-button>
          <div
            class="mobile-brand"
            @click="router.push('/workbench')"
          >
            <img
              :src="siteStore.faviconUrl"
              alt=""
            ><strong>{{ siteStore.platformName }}</strong>
          </div>
          <span class="mobile-page-title">{{ currentPage.title }}</span>
          <div class="page-context desktop-only">
            <h1>{{ currentPage.title }}</h1>
            <span>{{ currentPage.subtitle }}</span>
          </div>
        </div>

        <div class="header-right">
          <el-tooltip
            v-if="userStore.isLoggedIn && quotaText"
            :content="quotaTooltip"
            placement="bottom"
          >
            <div class="status-pill desktop-only">
              <span
                class="status-dot"
                :class="{ 'is-warning': quotaWarning }"
              />
              {{ quotaText }}
            </div>
          </el-tooltip>
          <template v-if="userStore.isLoggedIn">
            <el-dropdown trigger="click">
              <div class="user-info">
                <el-avatar
                  :size="34"
                  :src="userStore.userInfo?.avatar"
                >
                  {{ userStore.userInfo?.username?.charAt(0) }}
                </el-avatar>
                <div class="user-copy desktop-only">
                  <strong>{{ userStore.userInfo?.username }}</strong><span>已登录</span>
                </div>
                <el-icon class="chevron desktop-only">
                  <ArrowDown />
                </el-icon>
              </div>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item @click="router.push('/profile')">
                    <el-icon><User /></el-icon>个人中心
                  </el-dropdown-item>
                  <el-dropdown-item
                    v-if="userStore.isSuperAdmin || userStore.hasPermission('admin:access')"
                    @click="router.push('/admin')"
                  >
                    <el-icon><Setting /></el-icon>管理后台
                  </el-dropdown-item>
                  <el-dropdown-item
                    divided
                    @click="handleLogout"
                  >
                    <el-icon><SwitchButton /></el-icon>退出登录
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
          <el-button
            v-else
            type="primary"
            @click="router.push('/login')"
          >
            登录
          </el-button>
        </div>
      </el-header>

      <el-main class="user-main">
        <router-view />
        <footer
          v-if="siteStore.footerText"
          class="app-footer"
        >
          {{ siteStore.footerText }}
        </footer>
      </el-main>
    </el-container>

    <el-drawer
      v-model="mobileMenuVisible"
      direction="ltr"
      size="286px"
      :show-close="false"
      class="mobile-drawer"
    >
      <template #header>
        <div class="drawer-brand">
          <img
            :src="siteStore.faviconUrl"
            alt=""
          ><div><strong>{{ siteStore.platformName }}</strong><span>{{ siteStore.platformSubtitle }}</span></div>
        </div>
      </template>
      <el-menu
        :default-active="activeMenu"
        router
        class="mobile-nav-menu"
        @select="mobileMenuVisible = false"
      >
        <div class="drawer-label">
          创作空间
        </div>
        <el-menu-item index="/workbench">
          <el-icon><House /></el-icon><span>工作台</span>
        </el-menu-item>
        <el-menu-item index="/proofread/text">
          <el-icon><Edit /></el-icon><span>新建审校</span>
        </el-menu-item>
        <el-menu-item
          v-if="userStore.isLoggedIn && userStore.hasPermission('fact-check:run')"
          index="/fact-check"
        >
          <el-icon><Search /></el-icon><span>事实核查</span>
        </el-menu-item>
        <template v-if="userStore.isLoggedIn">
          <div class="drawer-label">
            我的内容
          </div>
          <el-menu-item index="/dictionary">
            <el-icon><Collection /></el-icon><span>个性化词库</span>
          </el-menu-item>
          <el-menu-item index="/whitelist">
            <el-icon><CircleCheck /></el-icon><span>放行词管理</span>
          </el-menu-item>
          <el-menu-item index="/history">
            <el-icon><Clock /></el-icon><span>校对历史</span>
          </el-menu-item>
          <el-menu-item index="/apikeys">
            <el-icon><Key /></el-icon><span>API 密钥</span>
          </el-menu-item>
        </template>
        <div class="drawer-label">
          更多工具
        </div>
        <el-menu-item index="/polish">
          <el-icon><MagicStick /></el-icon><span>AI 智能润色</span>
        </el-menu-item>
      </el-menu>
    </el-drawer>

    <nav
      class="mobile-tabbar"
      aria-label="主导航"
    >
      <router-link
        to="/workbench"
        :class="{ 'is-active': route.path === '/workbench' }"
      >
        <el-icon><House /></el-icon><span>工作台</span>
      </router-link>
      <router-link
        to="/proofread/text"
        :class="{ 'is-active': route.path.startsWith('/proofread') }"
      >
        <el-icon><EditPen /></el-icon><span>审校</span>
      </router-link>
      <router-link
        to="/polish"
        :class="{ 'is-active': route.path === '/polish' }"
      >
        <el-icon><MagicStick /></el-icon><span>润色</span>
      </router-link>
      <router-link
        :to="userStore.isLoggedIn ? '/profile' : '/login'"
        :class="{ 'is-active': route.path === '/profile' }"
      >
        <el-icon><User /></el-icon><span>{{ userStore.isLoggedIn ? '我的' : '登录' }}</span>
      </router-link>
    </nav>
  </el-container>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
// :is 绑定的是组件对象，按需解析器只处理模板标签，故显式引入
import { Sunny, Moon } from '@element-plus/icons-vue'
import { useUserStore } from '@/stores/user'
import { useSiteStore } from '@/stores/site'
import { getTodayUsageApi } from '@/api/history'
import { useDarkMode } from '@/composables/theme'

const { isDark, toggle } = useDarkMode()

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const siteStore = useSiteStore()
const activeMenu = computed(() => route.path.startsWith('/fact-check') ? '/fact-check' : route.path.startsWith('/proofread/') ? '/proofread/text' : route.path)
const mobileMenuVisible = ref(false)

const pageMap: Record<string, { title: string; subtitle: string }> = {
  '/workbench': { title: '工作台', subtitle: '从这里开始你的内容审校' },
  '/polish': { title: 'AI 智能润色', subtitle: '让表达更准确、更自然、更有说服力' },
  '/proofread/text': { title: '内容审校', subtitle: '快速识别文字、语法与表达问题' },
  '/proofread/document': { title: '内容审校', subtitle: '上传完整文档，获得逐条审校建议' },
  '/dictionary': { title: '个性化词库', subtitle: '沉淀团队专有表达与规范术语' },
  '/whitelist': { title: '放行词管理', subtitle: '管理无需提示的特殊词语' },
  '/history': { title: '校对历史', subtitle: '回顾并继续之前的审校任务' },
  '/apikeys': { title: 'API 密钥', subtitle: '将审校能力集成到你的工作流' },
  '/profile': { title: '个人中心', subtitle: '管理账号与个人偏好' },
}
const currentPage = computed(() => route.path.startsWith('/fact-check') ? { title: '事实核查', subtitle: '独立核查 · 证据追溯 · 人工复核' } : pageMap[route.path] || { title: 'TextMirror', subtitle: siteStore.platformSubtitle })

// ---- 今日用量与配额展示 ----
const usage = ref<{ used_today: number; daily_quota: number | null } | null>(null)

const quotaText = computed(() => {
  if (!usage.value) return ''
  const { used_today, daily_quota } = usage.value
  return daily_quota == null ? `今日已用 ${used_today} 次` : `今日 ${used_today}/${daily_quota} 次`
})
const quotaWarning = computed(() => {
  const u = usage.value
  return !!u && u.daily_quota != null && u.daily_quota > 0 && u.used_today >= u.daily_quota * 0.8
})
const quotaTooltip = computed(() => {
  const u = usage.value
  if (!u) return ''
  return u.daily_quota == null
    ? `今日已使用 ${u.used_today} 次（未设配额限制）`
    : `今日已用 ${u.used_today} / 配额 ${u.daily_quota} 次，北京时间零点重置`
})

// 登录态变化与路由切换（新记录产生）时刷新（路由切换节流 60s，避免频繁请求）
let lastUsageFetch = 0
async function loadUsage() {
  if (!userStore.isLoggedIn) { usage.value = null; return }
  try {
    lastUsageFetch = Date.now()
    usage.value = await getTodayUsageApi()
  } catch { usage.value = null }
}

watch(() => userStore.isLoggedIn, loadUsage, { immediate: true })
watch(() => route.path, () => {
  if (!userStore.isLoggedIn) return
  const now = Date.now()
  if (now - lastUsageFetch < 60_000) return
  lastUsageFetch = now
  loadUsage()
})

async function handleLogout() {
  const failure = await router.push({ name: 'Login', query: { logout: '1' } })
  if (failure) return
  userStore.logout()
  await router.replace({ name: 'Login' })
}
</script>

<style scoped lang="scss">
.user-layout { min-height: 100vh; background: var(--color-bg); }
.workspace-sidebar {
  width: 220px; flex: 0 0 220px; min-height: 100vh; padding: 24px 12px 16px;
  background: var(--surface); border-right: 1px solid var(--color-border);
  display: flex; flex-direction: column; position: relative; overflow: hidden;
}
.brand {
  display: flex; align-items: center; gap: 12px; padding: 0 8px 28px; cursor: pointer; position: relative; z-index: 1;
  .brand-mark { width: 40px; height: 40px; display: grid; place-items: center; border-radius: 10px; background: linear-gradient(145deg,#3986f6,#1c64d6); }
  .brand-mark img { width: 24px; height: 24px; object-fit: contain; filter: brightness(0) invert(1); }
  .brand-copy { min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  .brand-copy strong { color: var(--color-text); font-size: 17px; letter-spacing: .2px; }
  .brand-copy span { color: var(--color-text-secondary); font-size: 11px; }
}
.sidebar-label { padding: 0 12px 8px; color: var(--color-text-secondary); font-size: 11px; font-weight: 600; letter-spacing: .6px; }
.secondary-label { margin-top: 24px; }
.workspace-menu {
  border: 0; background: transparent;
  :deep(.el-menu-item) { height: 44px; margin: 3px 0; border-radius: 8px; color: var(--color-text-secondary); font-weight: 500; gap: 4px; }
  :deep(.el-menu-item .el-icon) { font-size: 18px; }
  :deep(.el-menu-item:hover) { color: var(--color-text); background: var(--surface-soft); }
  :deep(.el-menu-item.is-active) { color: var(--color-primary); background: var(--el-color-primary-light-9); }
}
.sidebar-spacer { flex: 1; }
.theme-toggle {
  display: flex; align-items: center; gap: 9px; width: 100%; padding: 10px 13px; margin-bottom: 10px;
  border-radius: 11px; cursor: pointer; font-size: 12px; font-weight: 600;
  color: var(--color-text-secondary); background: var(--surface-soft); border: 1px solid var(--color-border);
  transition: background .2s, color .2s;
  &:hover { background: var(--surface-soft); color: var(--color-primary); }
  .el-icon { font-size: 14px; color: #78aaff; }
}
.security-note {
  display: flex; align-items: center; gap: 10px; padding: 13px; border-radius: 12px;
  background: var(--surface-soft); border: 1px solid var(--color-border);
  .security-icon { width: 30px; height: 30px; display: grid; place-items: center; border-radius: 8px; color: #78aaff; background: rgba(78,145,255,.13); }
  div:last-child { display: flex; flex-direction: column; gap: 2px; }
  strong { color: var(--color-text); font-size: 12px; font-weight: 600; }
  span { color: var(--color-text-secondary); font-size: 10px; }
}
.workspace-shell { min-width: 0; min-height: 100vh; flex-direction: column; }
.workspace-header {
  height: 68px; padding: 0 28px; display: flex; align-items: center; justify-content: space-between;
  background: rgba(255,255,255,.92); border-bottom: 1px solid #e8edf4; backdrop-filter: blur(12px); position: relative; z-index: 20;
  .header-left, .header-right, .user-info { display: flex; align-items: center; }
  .header-right { gap: 18px; }
}
html.dark .workspace-header {
  background: rgba(16, 22, 33, .92); border-bottom-color: #202b3d;
}
.page-context {
  flex-direction: column; align-items: flex-start; gap: 3px;
  h1 { margin: 0; color: #172033; font-size: 18px; line-height: 1.2; font-weight: 650; }
  span { color: #8a97a8; font-size: 12px; }
}
html.dark .page-context h1 { color: #dbe4f0; }
html.dark .page-context span { color: #8a99b0; }
.status-pill {
  align-items: center; gap: 7px; padding: 7px 10px; color: #4c6179; background: #f5f8fc; border: 1px solid #e6edf6; border-radius: 999px; font-size: 12px;
  .status-dot { width: 7px; height: 7px; border-radius: 50%; background: #23b77e; box-shadow: 0 0 0 3px rgba(35,183,126,.12); }
  .status-dot.is-warning { background: #e6a23c; box-shadow: 0 0 0 3px rgba(230,162,60,.15); }
}
html.dark .status-pill { color: #b6c2d4; background: #202b3d; border-color: #2c3950; }
.user-info {
  gap: 9px; cursor: pointer; padding: 4px 5px 4px 4px; border-radius: 10px; transition: background .2s;
  &:hover { background: #f5f7fa; }
  :deep(.el-avatar) { color: #fff; background: linear-gradient(145deg,#607895,#3e536c); }
  .user-copy { flex-direction: column; align-items: flex-start; gap: 1px; min-width: 68px; }
  .user-copy strong { color: #28364a; font-size: 13px; font-weight: 600; }
  .user-copy span { color: #98a3b2; font-size: 10px; }
  .chevron { color: #9aa5b3; font-size: 13px; }
}
html.dark .user-info:hover { background: #202b3d; }
html.dark .user-info .user-copy strong { color: #dbe4f0; }
.user-main {
  flex: 1; min-height: 0; padding: 24px 28px 28px; overflow: auto;
  background: var(--color-bg);
}
.app-footer {
  margin-top: 24px; padding: 16px 0 4px; text-align: center;
  font-size: 12px; color: #9aa5b3; border-top: 1px solid rgba(0,0,0,.06);
}
html.dark .app-footer { color: #7a8797; border-top-color: #202b3d; }
.mobile-menu-btn, .mobile-brand { display: none; }
.mobile-page-title, .mobile-tabbar { display: none; }
.mobile-nav-menu { border-right: 0; }
.drawer-brand { display: flex; align-items: center; gap: 10px; }
.drawer-brand img { width: 34px; height: 34px; }
.drawer-brand div { display: flex; flex-direction: column; }
.drawer-brand strong { color: #172033; font-size: 17px; }
.drawer-brand span { color: #8b98a9; font-size: 11px; }
html.dark .drawer-brand strong { color: var(--color-text); }
.drawer-label { padding: 16px 12px 6px; color: var(--color-text-secondary); font-size: 11px; font-weight: 600; letter-spacing: .6px; }
.drawer-label:first-child { padding-top: 4px; }
.desktop-only { display: flex; }
@media (max-width: 900px) {
  .desktop-only { display: none !important; }
  .workspace-header { height: 60px; padding: 0 14px; }
  .mobile-menu-btn { display: inline-flex; margin-right: 4px; }
  .mobile-brand { display: flex; align-items: center; gap: 8px; cursor: pointer; }
  .mobile-brand img { width: 26px; height: 26px; }
  .mobile-brand strong { color: #18365e; font-size: 17px; }
  html.dark .mobile-brand strong { color: var(--color-text); }
  .mobile-page-title {
    display: block; flex: 1; min-width: 0; margin-left: 10px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    color: var(--color-text); font-size: 15px; font-weight: 600;
  }
  .user-main { padding: 14px 14px calc(var(--mobile-tabbar-h) + env(safe-area-inset-bottom) + 14px); }
  .mobile-tabbar {
    position: fixed; left: 0; right: 0; bottom: 0; z-index: 30;
    display: flex; height: calc(var(--mobile-tabbar-h) + env(safe-area-inset-bottom));
    padding-bottom: env(safe-area-inset-bottom);
    background: var(--surface); border-top: 1px solid var(--color-border);
    a {
      flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
      gap: 3px; color: var(--color-text-secondary); font-size: 10px; text-decoration: none;
      .el-icon { font-size: 20px; }
      &.is-active { color: var(--color-primary); }
    }
  }
}
</style>
