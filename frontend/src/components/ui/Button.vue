<template>
  <component
    :is="tag"
    :class="['btn', `variant-${variant}`, `size-${size}`, { block, mono, loading, 'icon-only': isIconOnly }]"
    :type="tag === 'button' ? type : undefined"
    :disabled="disabled || loading"
    :to="to"
    :style="cssVars"
    @click="$emit('click', $event)"
  >
    <Spinner v-if="loading" size="sm" class="btn-spinner" />
    <component :is="icon" v-else-if="icon && iconPosition !== 'right'" class="btn-icon" />
    <span v-if="!isIconOnly" class="btn-label"><slot /></span>
    <component :is="icon" v-if="icon && iconPosition === 'right' && !loading" class="btn-icon icon-right" />
  </component>
</template>

<script setup>
import { computed } from 'vue'
import Spinner from './Spinner.vue'

const props = defineProps({
  variant: { type: String, default: 'secondary' },
  size: { type: String, default: 'md' },
  type: { type: String, default: 'button' },
  loading: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  block: { type: Boolean, default: false },
  mono: { type: Boolean, default: false },
  icon: { type: [Object, Function], default: null },
  iconPosition: { type: String, default: 'left' },
  to: { type: [String, Object], default: '' }
})

defineEmits(['click'])

const isIconOnly = computed(() => props.iconPosition === 'only')
const tag = computed(() => (props.to ? 'router-link' : 'button'))
const cssVars = computed(() => ({}))
</script>

<style scoped>
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  font-family: var(--font-sans);
  font-weight: 500;
  letter-spacing: -0.005em;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition:
    background-color var(--transition-fast),
    border-color var(--transition-fast),
    color var(--transition-fast),
    opacity var(--transition-fast),
    transform 80ms ease;
  text-decoration: none;
  white-space: nowrap;
  user-select: none;
}
.btn:active:not(:disabled):not(.loading) { transform: scale(0.97); }
.btn:disabled, .btn.loading { cursor: not-allowed; opacity: 0.5; }
.btn.block { display: flex; width: 100%; }
.btn.mono  { font-family: var(--font-mono); }
.btn .btn-icon { width: 1em; height: 1em; flex-shrink: 0; }
.btn .btn-icon.icon-right { margin-left: 0.125rem; }

.size-sm { height: 30px; padding: 0 0.75rem; font-size: 0.8125rem; }
.size-md { height: 36px; padding: 0 0.875rem; font-size: 0.875rem; }
.size-lg { height: 40px; padding: 0 1.25rem; font-size: 0.875rem; }
.icon-only.size-sm { width: 30px; padding: 0; }
.icon-only.size-md { width: 36px; padding: 0; }
.icon-only.size-lg { width: 40px; padding: 0; }
.btn-spinner { color: currentColor; }

.variant-primary {
  background: var(--primary);
  color: var(--primary-fg);
  border-color: var(--primary);
}
.variant-primary:hover:not(:disabled) { background: var(--primary-dark); border-color: var(--primary-dark); }

.variant-secondary {
  background: var(--glass-bg);
  -webkit-backdrop-filter: blur(12px);
          backdrop-filter: blur(12px);
  color: var(--text-primary);
  border-color: var(--glass-border);
  box-shadow: var(--glass-highlight);
}
.variant-secondary:hover:not(:disabled) {
  border-color: var(--primary);
  background: var(--glass-bg-strong);
  color: var(--primary);
}

.variant-tertiary {
  background: var(--primary-light);
  color: var(--primary);
  border-color: transparent;
}
.variant-tertiary:hover:not(:disabled) { background: var(--primary-light); color: var(--primary-dark); }

.variant-ghost {
  background: transparent;
  color: var(--text-tertiary);
  border-color: transparent;
}
.variant-ghost:hover:not(:disabled) { color: var(--text-primary); background: var(--bg-tertiary); }

.variant-danger {
  background: var(--error);
  color: #fff;
  border-color: var(--error);
}
.variant-danger:hover:not(:disabled) { background: #8b2e2e; border-color: #8b2e2e; }

.variant-outline-danger {
  background: transparent;
  color: var(--error);
  border-color: var(--error);
}
.variant-outline-danger:hover:not(:disabled) {
  background: var(--error);
  color: #fff;
}

.variant-outline-dashed {
  background: var(--glass-bg);
  -webkit-backdrop-filter: blur(8px);
          backdrop-filter: blur(8px);
  color: var(--text-secondary);
  border-style: dashed;
  border-color: var(--glass-border);
}
.variant-outline-dashed:hover:not(:disabled) {
  color: var(--primary);
  border-color: var(--primary);
  border-style: solid;
  background: var(--primary-light);
}

.variant-link {
  background: transparent;
  border-color: transparent;
  color: var(--primary);
  padding: 0;
  height: auto;
  text-decoration: none;
}
.variant-link:hover:not(:disabled) { color: var(--primary-dark); text-decoration: underline; }
</style>
