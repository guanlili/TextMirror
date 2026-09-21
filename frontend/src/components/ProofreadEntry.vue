<template>
  <nav
    class="entry-nav"
    aria-label="内容来源"
  >
    <div class="entry-tabs">
      <router-link
        to="/proofread/text"
        :aria-current="route.path === '/proofread/text' ? 'page' : undefined"
      >
        <el-icon><EditPen /></el-icon>输入文本
      </router-link>
      <router-link
        to="/proofread/document"
        :aria-current="route.path === '/proofread/document' ? 'page' : undefined"
      >
        <el-icon><Upload /></el-icon>上传文档
      </router-link>
    </div>
    <span>输入内容 · 检查问题 · 确认修改</span>
  </nav>
</template>
<script setup lang="ts">
import { useRoute } from 'vue-router'
const route = useRoute()
</script>
<style scoped>
.entry-nav { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 24px; }
.entry-tabs { display: flex; padding: 4px; border: 1px solid var(--color-border); border-radius: 10px; background: var(--surface); }
a { display: flex; gap: 8px; align-items: center; padding: 10px 18px; text-decoration: none; color: var(--color-text-secondary); border-radius: 7px; font-weight: 500; }
a[aria-current] { background: var(--el-color-primary-light-9); color: var(--color-primary); }
.entry-nav > span { font-size: 12px; color: var(--color-text-secondary); }
@media(max-width: 650px) { .entry-nav > span { display: none; } .entry-tabs { width: 100%; } a { flex: 1; justify-content: center; } }
</style>
