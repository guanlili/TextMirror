/**
 * 暗色模式管理
 * localStorage 持久化，index.html 内联脚本提前应用防闪烁
 */
import { ref } from 'vue'

const STORAGE_KEY = 'tm_theme'

const isDark = ref(document.documentElement.classList.contains('dark'))

function applyTheme(dark: boolean) {
  document.documentElement.classList.toggle('dark', dark)
  localStorage.setItem(STORAGE_KEY, dark ? 'dark' : 'light')
  isDark.value = dark
}

export function useDarkMode() {
  function toggle() {
    applyTheme(!isDark.value)
  }
  return { isDark, toggle }
}
