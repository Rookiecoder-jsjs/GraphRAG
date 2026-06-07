<template>
  <div class="loading-state">
    <Spinner :size="size" />
    <p v-if="message" class="loading-message">{{ message }}</p>
  </div>
</template>

<script setup>
import Spinner from './Spinner.vue'

defineProps({
  size: { type: String, default: 'lg' },
  message: { type: String, default: '' }
})
</script>

<style scoped>
.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 3rem 1rem;
  gap: 0.875rem;
}
.loading-message {
  color: var(--text-tertiary);
  font-size: 0.875rem;
  font-family: var(--font-sans);
}
</style>
