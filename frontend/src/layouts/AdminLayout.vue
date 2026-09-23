<template>
  <el-container class="admin-layout">
    <button
      v-if="mobileOpen"
      class="nav-scrim"
      aria-label="关闭管理导航"
      @click="mobileOpen = false"
    />
    <aside
      class="admin-sidebar"
      :inert="isMobile && !mobileOpen"
      :class="{ collapsed: isCollapse, 'mobile-open': mobileOpen }"
    >
      <router-link
        to="/admin/dashboard"
        class="admin-brand"
      >
        <img
          :src="siteStore.faviconUrl"
          alt=""
        ><span v-if="!isCollapse"><strong>{{ siteStore.platformName }}</strong><small>管理工作台</small></span>
      </router-link>
      <nav aria-label="管理导航">
        <section
          v-for="group in visibleGroups"
          :key="group.title"
          class="nav-group"
        >
          <div
            v-if="!isCollapse"
            class="group-label"
          >
            {{ group.title }}
          </div>
          <router-link
            v-for="item in group.items"
            :key="item.path"
            :to="item.path"
            :class="{ active: route.path === item.path }"
            :aria-current="route.path === item.path ? 'page' : undefined"
            :title="item.title"
            @click="mobileOpen = false"
          >
            <el-icon><component :is="item.icon" /></el-icon><span v-if="!isCollapse">{{ item.title }}</span>
          </router-link>
        </section>
      </nav>
      <router-link
        class="return-link"
        to="/workbench"
      >
        <el-icon><Back /></el-icon><span v-if="!isCollapse">返回用户工作台</span>
      </router-link>
    </aside>
    <el-container class="admin-shell">
      <el-header class="admin-header">
        <div class="header-context">
          <el-button
            text
            circle
            aria-label="切换管理导航"
            @click="toggleNavigation"
          >
            <el-icon><Operation /></el-icon>
          </el-button><div><h1>{{ route.meta.title }}</h1><p>{{ pageDescription }}</p></div>
        </div>
        <el-dropdown trigger="click">
          <button class="account-button">
            <el-avatar
              :size="32"
              :src="userStore.userInfo?.avatar"
            >
              {{ userStore.userInfo?.username?.charAt(0) }}
            </el-avatar><span>{{ userStore.userInfo?.username }}</span><el-icon><ArrowDown /></el-icon>
          </button><template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item @click="router.push('/workbench')">
                用户工作台
              </el-dropdown-item><el-dropdown-item
                divided
                @click="handleLogout"
              >
                退出登录
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </el-header>
      <el-main class="admin-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>
<script setup lang="ts">
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { DataAnalysis, TrendCharts, Reading, Notebook, ChatDotRound, Cpu, User, Lock, Setting, Key, Folder, List, Tools } from '@element-plus/icons-vue'
import { useUserStore } from '@/stores/user'
import { useSiteStore } from '@/stores/site'
const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const siteStore = useSiteStore()
const isCollapse = ref(false)
const isMobile = ref(false)
const mobileOpen = ref(false)
const groups = [
 { title: '运营与质量', items: [
  { path: '/admin/dashboard', title: '运营概览', icon: DataAnalysis, permission: 'admin:access' },
  { path: '/admin/usage', title: '调用与用量', icon: TrendCharts, permission: 'admin:access' },
  { path: '/admin/quality', title: '质量反馈', icon: ChatDotRound, permission: 'admin:global_dict:edit' },
 ] },
 { title: '审校能力', items: [
  { path: '/admin/domain-rules', title: '审校规则', icon: Reading, permission: 'admin:settings:edit' },
  { path: '/admin/global-dict', title: '组织词库', icon: Notebook, permission: 'admin:global_dict:edit' },
  { path: '/admin/llm', title: '模型服务', icon: Cpu, permission: 'admin:llm:edit' },
 ] },
 { title: '成员与资源', items: [
  { path: '/admin/users', title: '成员管理', icon: User, permission: 'admin:users:view' },
  { path: '/admin/roles', title: '角色权限', icon: Lock, permission: 'admin:roles:view' },
  { path: '/admin/policy', title: '访问与额度', icon: Setting, permission: 'admin:policy:edit' },
  { path: '/admin/documents', title: '文档管理', icon: Folder, permission: 'admin:documents:view' },
 ] },
 { title: '系统管理', items: [
  { path: '/admin/apikeys', title: '接口密钥', icon: Key, permission: 'admin:access' },
  { path: '/admin/audit', title: '操作审计', icon: List, permission: 'admin:access' },
  { path: '/admin/settings', title: '系统设置', icon: Tools, permission: 'admin:settings:edit' },
 ] },
]
const visibleGroups = computed(() => groups.map(group => ({ ...group, items: group.items.filter(item => userStore.hasPermission(item.permission)) })).filter(group => group.items.length))
const descriptions: Record<string, string> = {
 '/admin/dashboard': '平台使用情况与常用管理入口', '/admin/usage': '真实调用记录、模型消耗与异常请求', '/admin/quality': '人工审核反馈，验证审校质量', '/admin/domain-rules': '维护通用检查与可选专业规范', '/admin/global-dict': '统一术语、纠错与放行规则', '/admin/llm': '管理模型连接与默认服务', '/admin/users': '管理成员、角色和个人额度', '/admin/roles': '按职责分配管理与使用权限', '/admin/policy': '配置游客访问范围与每日额度', '/admin/documents': '查看平台上传的文档记录', '/admin/apikeys': '管理接口访问密钥', '/admin/audit': '追溯操作记录与使用行为', '/admin/settings': '平台配置与系统维护',
}
const pageDescription = computed(() => descriptions[route.path] || '管理工作台')
function checkMobile() { isMobile.value = window.innerWidth <= 768; if (isMobile.value) isCollapse.value = false; else mobileOpen.value = false }
function toggleNavigation() { if (isMobile.value) mobileOpen.value = !mobileOpen.value; else isCollapse.value = !isCollapse.value }
function handleEscape(event: { key: string }) { if (event.key === 'Escape') mobileOpen.value = false }
watch(() => route.path, () => { mobileOpen.value = false })
onMounted(() => { checkMobile(); window.addEventListener('resize', checkMobile); window.addEventListener('keydown', handleEscape) })
onBeforeUnmount(() => { window.removeEventListener('resize', checkMobile); window.removeEventListener('keydown', handleEscape) })
async function handleLogout() {
  const failure = await router.push({ name: 'Login', query: { logout: '1' } })
  if (failure) return
  userStore.logout()
  await router.replace({ name: 'Login' })
}
</script>
<style scoped>
.admin-layout { height:100dvh; background:var(--color-bg); }.admin-sidebar { width:224px; flex:0 0 224px; display:flex; flex-direction:column; background:var(--surface); border-right:1px solid var(--color-border); overflow-y:auto; padding:24px 12px 16px; transition:width .2s, flex-basis .2s; }.admin-sidebar.collapsed { width:72px; flex-basis:72px; }
.admin-brand { display:flex; gap:12px; align-items:center; padding:0 10px 24px; text-decoration:none; color:var(--color-text); }.admin-brand img { width:34px; height:34px; }.admin-brand strong { font-size:16px; }.admin-brand small { display:block; color:var(--color-text-secondary); font-size:11px; margin-top:4px; }
.nav-group { margin-bottom:20px; }.group-label { font-size:11px; color:var(--color-text-secondary); padding:0 14px 7px; letter-spacing:1px; }.nav-group a,.return-link { display:flex; align-items:center; gap:12px; padding:12px 14px; border-radius:8px; text-decoration:none; color:var(--color-text-secondary); font-size:13px; margin:2px 0; white-space:nowrap; }.nav-group .el-icon { font-size:18px; flex-shrink:0; }.nav-group a:hover { background:var(--surface-soft); color:var(--color-text); }.nav-group a.active { background:var(--el-color-primary-light-9); color:var(--color-primary); font-weight:600; }.return-link { margin-top:auto; border:1px solid var(--color-border); font-size:12px; }
.admin-shell { flex-direction:column; min-width:0; }.admin-header { height:76px; background:var(--surface); border-bottom:1px solid var(--color-border); display:flex; justify-content:space-between; align-items:center; padding:0 28px; flex-shrink:0; }.header-context { display:flex; align-items:center; gap:14px; }.header-context h1 { font-size:17px; font-weight:600; }.header-context p { margin-top:5px; color:var(--color-text-secondary); font-size:12px; }.account-button { display:flex; align-items:center; gap:10px; border:0; background:none; color:var(--color-text); cursor:pointer; }.account-button > span { font-size:13px; }.admin-main { padding:28px; overflow:auto; }.admin-main :deep(.el-card) { border-color:var(--color-border); border-radius:12px; }.nav-scrim { display:none; }
@media(max-width:768px) { .admin-sidebar { position:fixed; inset:0 auto 0 0; z-index:100; transform:translateX(-100%); transition:transform .2s; width:240px; }.admin-sidebar.mobile-open { transform:translateX(0); }.nav-scrim { display:block; position:fixed; inset:0; z-index:99; border:0; background:rgba(15,23,42,.4); }.admin-header { height:66px; padding:0 12px; }.header-context { gap:6px; }.header-context p { display:none; }.account-button > span:not(.el-avatar) { display:none; }.admin-main { padding:18px 14px; } }
</style>
