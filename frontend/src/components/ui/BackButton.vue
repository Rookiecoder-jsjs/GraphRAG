<template>
  <component
    :is="to ? 'router-link' : 'button'"
    :class="['back-btn', `size-${size}`]"
    :to="to || undefined"
    :type="to ? undefined : 'button'"
    :title="title"
  >
    <svg class="back-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <line x1="19" y1="12" x2="5" y2="12" />
      <polyline points="12 19 5 12 12 5" />
    </svg>
    <span v-if="label" class="back-label">{{ label }}</span>
  </component>
</template>

<script setup>
defineProps({
  to: { type: [String, Object], default: '' },
  label: { type: String, default: 'Back' },
  title: { type: String, default: 'Back' },
  size: { type: String, default: 'md' }
})
</script>

<style scoped>
.back-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  font-family: var(--font-sans);
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--text-secondary);
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 0.5rem 0.75rem;
  cursor: pointer;
  text-decoration: none;
  transition:
    background-color var(--transition-fast),
    border-color var(--transition-fast),
    color var(--transition-fast);
}
.size-sm { padding: 0.375rem 0.625rem; font-size: 0.75rem; }
.size-md { padding: 0.5rem 0.875rem; font-size: 0.8125rem; }

.back-btn:hover {
  color: var(--text-primary);
  background: var(--bg-tertiary);
  border-color: var(--border-strong);
}
.back-icon { width: 14px; height: 14px; flex-shrink: 0; }
</style>
