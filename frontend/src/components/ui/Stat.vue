<template>
  <div :class="['stat', `variant-${variant}`, `tone-${tone}`]" :style="cssVars">
    <div v-if="$slots.icon || icon" class="stat-icon" :class="`icon-${tone}`">
      <slot name="icon">
        <component :is="icon" class="stat-icon-svg" />
      </slot>
    </div>
    <div class="stat-body">
      <div class="stat-value">{{ formattedValue }}</div>
      <div v-if="label" class="stat-label">{{ label }}</div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  variant: { type: String, default: 'icon' },
  tone:    { type: String, default: 'primary' },
  icon: { type: [Object, Function], default: null },
  value: { type: [Number, String], default: '' },
  label: { type: String, default: '' }
})

const formattedValue = computed(() => {
  if (props.value == null) return ''
  if (typeof props.value === 'number') return props.value.toLocaleString()
  return String(props.value)
})

const cssVars = computed(() => ({}))
</script>

<style scoped>
.stat {
  display: flex;
  align-items: center;
  gap: 0.875rem;
  padding: 1rem 1.125rem;
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
.stat:hover {
  transform: translateY(-2px);
  box-shadow:
    0 12px 40px -8px rgba(28, 25, 23, 0.16),
    var(--glass-highlight);
}
[data-theme='dark'] .stat:hover {
  box-shadow:
    0 12px 40px -8px rgba(0, 0, 0, 0.60),
    var(--glass-highlight);
}
.stat.variant-inline {
  gap: 0.375rem;
  align-items: baseline;
  background: transparent;
  border: none;
  box-shadow: none;
  padding: 0;
  border-radius: 0;
}
.stat.variant-inline:hover { transform: none; box-shadow: none; }
.stat.variant-tile {
  flex-direction: column;
  text-align: center;
  gap: 0.25rem;
  padding: 0.5rem 0;
  background: transparent;
  border: none;
  box-shadow: none;
  border-radius: 0;
}
.stat.variant-tile:hover { transform: none; box-shadow: none; }

.stat-icon {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  flex-shrink: 0;
  background: var(--glass-bg);
  -webkit-backdrop-filter: blur(8px);
          backdrop-filter: blur(8px);
  border: 1px solid var(--glass-border);
  box-shadow: var(--glass-highlight);
}
.stat-icon.icon-primary { color: var(--primary); }
.stat-icon.icon-accent  { color: var(--accent-dark); }
.stat-icon.icon-warm    { color: var(--accent-dark); }
.stat-icon.icon-cool    { color: var(--primary); }
.stat-icon.icon-success { color: var(--success); }
.stat-icon.icon-warning { color: var(--warning); }
.stat-icon.icon-error   { color: var(--error); }
.stat-icon-svg { width: 18px; height: 18px; stroke: currentColor; fill: none; stroke-width: 1.75; stroke-linecap: round; stroke-linejoin: round; }

.stat-body { display: flex; flex-direction: column; min-width: 0; }
.stat.variant-inline .stat-body { flex-direction: row; align-items: baseline; gap: 0.375rem; }

.stat-value {
  font-family: var(--font-display);
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.02em;
}
.stat.variant-inline .stat-value {
  font-size: 1.625rem;
  color: var(--primary);
}
.stat.variant-tile .stat-value {
  font-size: 1.625rem;
}

.stat-label {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-tertiary);
  margin-top: 0.125rem;
}
.stat.variant-inline .stat-label { margin-top: 0; }
</style>
