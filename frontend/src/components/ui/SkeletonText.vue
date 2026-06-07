<template>
  <div class="skeleton-text" aria-hidden="true">
    <SkeletonBar
      v-for="i in lines"
      :key="i"
      :width="widthFor(i)"
      :height="lineHeight"
    />
  </div>
</template>

<script setup>
import SkeletonBar from './SkeletonBar.vue'

const props = defineProps({
  lines: { type: Number, default: 3 },
  lineHeight: { type: String, default: '0.875rem' }
})

function widthFor(i) {
  if (i === 1) return '40%'
  if (i === props.lines) return '70%'
  return '100%'
}
</script>

<style scoped>
.skeleton-text {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
</style>
