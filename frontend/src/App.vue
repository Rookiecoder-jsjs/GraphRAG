<template>
  <router-view v-slot="{ Component }">
    <Transition name="page" mode="out-in">
      <component :is="Component" />
    </Transition>
  </router-view>
</template>

<script setup>
// 主题在 useTheme 模块加载时即应用到 <html data-theme>，此处仅需 import 触发副作用
import { useTheme } from './composables/useTheme'
useTheme()
</script>

<style>
/* Global styles are in variables.css */
</style>
