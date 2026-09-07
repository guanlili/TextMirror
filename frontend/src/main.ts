import { createApp } from 'vue'
import { createPinia } from 'pinia'
// 组件与图标按需引入（见 vite.config.ts 的 resolvers）；语言包由 App.vue 的
// el-config-provider 提供。ElMessage/ElMessageBox 是编程式调用、各处显式 import，
// 解析器不接管其样式，故在此显式引入
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'

import App from './App.vue'
import router from './router'
import permissionDirective from './directives/permission'
import './styles/global.scss'

const app = createApp(App)

// 注册自定义指令
app.directive('permission', permissionDirective)

const pinia = createPinia()
app.use(pinia)
app.use(router)

app.mount('#app')

// 启动时加载站点配置（平台名称、图标等）
import { useSiteStore } from './stores/site'
const siteStore = useSiteStore()
siteStore.ensureLoaded()

// 刷新页面后恢复登录用户信息与权限（否则管理后台入口/权限指令失效）
import { useUserStore } from './stores/user'
const userStore = useUserStore()
if (userStore.isLoggedIn && !userStore.userInfo) {
  userStore.fetchUserInfo().catch(() => {
    // token 失效时由 request.ts 统一处理登出
  })
}
