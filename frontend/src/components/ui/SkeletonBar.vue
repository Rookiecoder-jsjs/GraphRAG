<template>
  <div
    class="skeleton-bar"
    :class="{ rounded }"
    :style="{ width, height }"
    aria-hidden="true"
  />
</template>

<script setup>
defineProps({
  width: { type: String, default: '100%' },
  height: { type: String, default: '0.75rem' },
  rounded: { type: Boolean, default: false }
})
</script>

<style scoped>
.skeleton-bar {
  background: linear-gradient(
    90deg,
    var(--bg-tertiary) 0%,
    var(--border-light) 50%,
    var(--bg-tertiary) 100%
  );
  background-size: 200% 100%;
  border-radius: var(--radius-sm);
  animation: shimmer 1.4s ease-in-out infinite;
}
.skeleton-bar.rounded { border-radius: 999px; }

@keyframes shimmer {
  0%   { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

@media (prefers-reduced-motion: reduce) {
  .skeleton-bar { animation: none; background: var(--bg-tertiary); }
}
</style>
