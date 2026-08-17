<template>
  <div :class="['layout', { 'sidebar-collapsed': sidebar.state.collapsed }]">
    <Sidebar />
    <main class="main-content">
      <router-view v-slot="{ Component }">
        <!-- keep-alive caches every visited page; :max bounds it so long
             sessions don't accumulate large page trees (graph nodes, chat
             transcripts, doc lists) in memory forever. -->
        <keep-alive :max="6">
          <component :is="Component" />
        </keep-alive>
      </router-view>
    </main>
  </div>
</template>

<script setup>
import Sidebar from './Sidebar.vue'
import { useSidebar } from '../../composables/sidebar'

const sidebar = useSidebar()
</script>

<style scoped>
.layout {
  display: flex;
  height: 100vh;
  width: 100vw;
  position: relative;
  overflow: hidden;
  background: var(--bg-base);
}

.main-content {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  position: relative;
  z-index: 1;
}
</style>
