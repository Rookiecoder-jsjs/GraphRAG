<template>
  <span
    class="dot"
    :class="[`tone-${tone}`, { square }]"
    :style="cssVars"
  />
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  tone: { type: String, default: 'muted' }, // primary|accent|success|warning|error|muted
  square: { type: Boolean, default: false },
  color: { type: String, default: '' }
})

const cssVars = computed(() => (props.color ? { '--dot-color': props.color } : {}))
</script>

<style scoped>
.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--dot-color, var(--text-tertiary));
  flex-shrink: 0;
}
.dot.square { border-radius: 1px; }
.tone-primary { --dot-color: var(--primary); }
.tone-accent  { --dot-color: var(--accent); }
.tone-success { --dot-color: var(--success); }
.tone-warning { --dot-color: var(--warning); }
.tone-error   { --dot-color: var(--error); }
.tone-muted   { --dot-color: var(--text-tertiary); }
</style>
