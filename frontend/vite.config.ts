/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import * as ElementPlusIcons from '@element-plus/icons-vue'
import { resolve } from 'path'

// 模板里直接写 <Edit /> <MagicStick /> 等图标标签，按需解析为具名导入
const iconNames = new Set(Object.keys(ElementPlusIcons))

export default defineConfig({
  plugins: [
    vue(),
    AutoImport({
      resolvers: [ElementPlusResolver()],
      imports: ['vue', 'vue-router', 'pinia'],
      dts: 'src/auto-imports.d.ts',
    }),
    Components({
      resolvers: [
        ElementPlusResolver(),
        (name) => (iconNames.has(name) ? { name, from: '@element-plus/icons-vue' } : undefined),
      ],
      dts: 'src/components.d.ts',
    }),
  ],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  build: {
    rollupOptions: {
      output: {
        // echarts（仅 admin 仪表盘）/ marked（仅润色页）按需进路由 chunk 之外再分家；
        // element-plus 变动少独立成 chunk——发版只重下业务 chunk，缓存命中率高
        manualChunks(id: string) {
          if (!id.includes('/node_modules/')) return undefined
          // 按包路径前缀精确匹配，避免裸子串误吞（如 @vueuse 落进 vue-vendor、路径含 marked 的无关包）
          const pkg = id.split('/node_modules/').pop() || ''
          if (pkg.startsWith('echarts/') || pkg.startsWith('zrender/')) return 'echarts'
          if (pkg.startsWith('marked/') || pkg.startsWith('dompurify/')) return 'markdown'
          if (pkg.startsWith('element-plus/') || pkg.startsWith('@element-plus/')) return 'element-plus'
          if (pkg.startsWith('vue/') || pkg.startsWith('@vue/') || pkg.startsWith('vue-router/')
            || pkg.startsWith('vue-demi/') || pkg.startsWith('pinia/')) return 'vue-vendor'
          return undefined
        },
      },
    },
  },
  server: {
    port: 3022,
    proxy: {
      // 带斜杠前缀，避免劫持 /apikeys 这类 SPA 路由（与 nginx 的 location /api/ 行为一致）
      '/api/': {
        target: 'http://127.0.0.1:3020',
        changeOrigin: true,
      },
    },
  },
  test: {
    include: ['src/**/*.test.ts'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html', 'lcov'],
      include: ['src/**/*.{ts,vue}'],
      exclude: ['src/**/*.test.ts', 'src/**/*.d.ts', 'src/env.d.ts'],
    },
  },
})
