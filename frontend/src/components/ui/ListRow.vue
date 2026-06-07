<template>
  <component
    :is="tag"
    :class="['list-row', { clickable, interactive, 'has-meta': $slots.meta }]"
    :to="to"
  >
    <span v-if="rank != null" class="list-row-rank">{{ formattedRank }}</span>
    <div v-if="$slots.icon || icon" class="list-row-icon">
      <slot name="icon">
        <component :is="icon" class="list-row-icon-svg" />
      </slot>
    </div>
    <div class="list-row-body">
      <div v-if="title || $slots.title" class="list-row-title">
        <slot name="title">{{ title }}</slot>
      </div>
      <div v-if="subtitle || $slots.subtitle" class="list-row-subtitle">
        <slot name="subtitle">{{ subtitle }}</slot>
      </div>
    </div>
    <div v-if="$slots.meta" class="list-row-meta">
      <slot name="meta" />
    </div>
    <div v-if="$slots.actions" class="list-row-actions">
      <slot name="actions" />
    </div>
  </component>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  to: { type: [String, Object], default: '' },
  title: { type: String, default: '' },
  subtitle: { type: String, default: '' },
  rank: { type: [Number, String], default: null },
  icon: { type: [Object, Function], default: null },
  clickable: Boolean,
  interactive: Boolean
})

const tag = computed(() => (props.to ? 'router-link' : 'div'))
const formattedRank = computed(() => {
  if (props.rank == null) return ''
  return typeof props.rank === 'number' ? `#${props.rank}` : String(props.rank)
})
</script>

<style scoped>
.list-row {
  display: flex;
  align-items: center;
  gap: 0.875rem;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid var(--border-light);
  color: var(--text-primary);
  text-decoration: none;
  transition: background-color var(--transition-fast);
}
.list-row:last-child { border-bottom: none; }
.list-row.clickable,
.list-row.interactive { cursor: pointer; }
.list-row.clickable:hover,
.list-row.interactive:hover { background: var(--bg-tertiary); }
.list-row.clickable:focus-visible {
  outline: 2px solid var(--primary);
  outline-offset: -2px;
}

.list-row-rank {
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
  min-width: 2rem;
  flex-shrink: 0;
}
.list-row-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  color: var(--text-secondary);
  flex-shrink: 0;
}
.list-row-icon-svg { width: 16px; height: 16px; stroke: currentColor; fill: none; stroke-width: 1.75; stroke-linecap: round; stroke-linejoin: round; }

.list-row-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
}
.list-row-title {
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.list-row-subtitle {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.list-row-meta {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  flex-shrink: 0;
}
.list-row-actions {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  flex-shrink: 0;
  opacity: 0.6;
  transition: opacity var(--transition-fast);
}
.list-row:hover .list-row-actions { opacity: 1; }
</style>
