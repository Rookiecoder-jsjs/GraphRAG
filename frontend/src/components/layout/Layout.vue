<template>
  <div class="layout">
    <div class="bg-stage" aria-hidden="true">
      <div class="blob blob-1"></div>
      <div class="blob blob-2"></div>
      <div class="blob blob-3"></div>
    </div>
    <Sidebar />
    <main class="main-content">
      <router-view v-slot="{ Component }">
        <keep-alive>
          <component :is="Component" />
        </keep-alive>
      </router-view>
    </main>
  </div>
</template>

<script setup>
import Sidebar from './Sidebar.vue'
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

.bg-stage {
  position: absolute;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
  z-index: 0;
}

.blob {
  position: absolute;
  border-radius: 50%;
  filter: blur(120px);
  opacity: 0.65;
  will-change: transform;
}

.blob-1 {
  width: 60vw;
  height: 60vw;
  background: var(--blob-1);
  top: -15%;
  left: -10%;
  animation: drift-1 30s ease-in-out infinite;
}

.blob-2 {
  width: 55vw;
  height: 55vw;
  background: var(--blob-2);
  top: 25%;
  right: -15%;
  animation: drift-2 35s ease-in-out infinite;
}

.blob-3 {
  width: 65vw;
  height: 65vw;
  background: var(--blob-3);
  bottom: -20%;
  left: 15%;
  animation: drift-3 40s ease-in-out infinite;
}

[data-theme='dark'] .blob {
  opacity: 0.32;
}

@media (prefers-reduced-motion: reduce) {
  .blob { animation: none !important; }
}

.main-content {
  flex: 1;
  overflow: hidden;
  position: relative;
  z-index: 1;
}
</style>
