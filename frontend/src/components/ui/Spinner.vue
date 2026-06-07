<template>
  <span class="spinner" :class="`spinner-${size}`" :style="cssVars" role="status" aria-label="Loading" />
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  size: { type: String, default: 'md' }, // sm | md | lg
  color: { type: String, default: '' }
})

const cssVars = computed(() => (props.color ? { '--spinner-color': props.color } : {}))
</script>

<style scoped>
.spinner {
  display: inline-block;
  border: 2px solid currentColor;
  border-top-color: var(--spinner-color, var(--primary));
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  flex-shrink: 0;
  color: var(--bg-tertiary);
}
.spinner-sm { width: 14px; height: 14px; border-width: 2px; }
.spinner-md { width: 24px; height: 24px; }
.spinner-lg { width: 40px; height: 40px; border-width: 3px; }
</style>
