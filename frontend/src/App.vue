<template>
  <el-config-provider :locale="zhCn">
    <router-view />
  </el-config-provider>
</template>

<script setup lang="ts">
// Element Plus 改为按需引入后语言包不再通过 app.use 传入，由此处统一提供
import zhCn from 'element-plus/es/locale/lang/zh-cn'
</script>
