<template>
  <section :class="['card', { flush, hoverable, compact }]">
    <header v-if="title || $slots.header || $slots.actions" class="card-header">
      <div class="card-header-text">
        <h3 v-if="title" class="card-title">{{ title }}</h3>
        <span v-if="meta" class="card-meta">{{ meta }}</span>
      </div>
      <div v-if="$slots.actions" class="card-actions">
        <slot name="actions" />
      </div>
      <slot name="header" />
    </header>
    <div class="card-body" :class="{ 'no-pad': flush || noPad }">
      <slot />
    </div>
    <footer v-if="$slots.footer" class="card-footer">
      <slot name="footer" />
    </footer>
  </section>
</template>

<script setup>
defineProps({
  title: { type: String, default: '' },
  meta:  { type: String, default: '' },
  flush:    { type: Boolean, default: false },
  noPad:    { type: Boolean, default: false },
  hoverable:{ type: Boolean, default: false },
  compact:  { type: Boolean, default: false }
})
</script>

<style scoped>
.card {
  background: var(--glass-bg);
  -webkit-backdrop-filter: blur(var(--glass-blur)) saturate(180%);
          backdrop-filter: blur(var(--glass-blur)) saturate(180%);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--glass-shadow), var(--glass-highlight);
  transition:
    transform var(--transition),
    box-shadow var(--transition);
}
.card.hoverable:hover {
  transform: translateY(-2px);
  box-shadow:
    0 12px 40px -8px rgba(28, 25, 23, 0.16),
    var(--glass-highlight);
}
[data-theme='dark'] .card.hoverable:hover {
  box-shadow:
    0 12px 40px -8px rgba(0, 0, 0, 0.60),
    var(--glass-highlight);
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--border-light);
}
.card-header-text {
  display: flex;
  align-items: baseline;
  gap: 0.75rem;
  min-width: 0;
  flex: 1;
}
.card-title {
  font-family: var(--font-display);
  font-size: 1.0625rem;
  font-weight: 500;
  color: var(--text-primary);
  letter-spacing: -0.005em;
  line-height: 1.3;
  margin: 0;
}
.card-meta {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.card-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-shrink: 0;
}

.card-body { padding: 1.25rem; }
.card.compact .card-body { padding: 0.875rem 1rem; }
.card-body.no-pad { padding: 0; }

.card-footer {
  padding: 0.875rem 1.25rem;
  border-top: 1px solid var(--border-light);
  background: var(--bg-secondary);
  border-bottom-left-radius: var(--radius);
  border-bottom-right-radius: var(--radius);
}
</style>
