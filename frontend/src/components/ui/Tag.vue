<template>
  <component
    :is="clickable ? 'button' : 'span'"
    :class="['tag', `shape-${shape}`, `tone-${tone}`, { active, removable, 'has-score': score != null }]"
    :type="clickable ? 'button' : undefined"
    :style="cssVars"
    @click="clickable && $emit('click', $event)"
  >
    <span v-if="$slots.dot" class="tag-dot"><slot name="dot" /></span>
    <span class="tag-content"><slot /></span>
    <span v-if="score != null" class="tag-score">{{ formattedScore }}</span>
    <button
      v-if="removable"
      type="button"
      class="tag-remove"
      aria-label="Remove"
      @click.stop="$emit('remove', $event)"
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round">
        <line x1="18" y1="6" x2="6" y2="18" />
        <line x1="6" y1="6" x2="18" y2="18" />
      </svg>
    </button>
  </component>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  shape: { type: String, default: 'pill' },
  tone:  { type: String, default: 'muted' },
  active: Boolean,
  removable: Boolean,
  clickable: Boolean,
  score: { type: [Number, String], default: null },
  color: { type: String, default: '' }
})

defineEmits(['click', 'remove'])

const formattedScore = computed(() => {
  if (props.score == null) return ''
  return typeof props.score === 'number' ? props.score.toFixed(2) : String(props.score)
})

const cssVars = computed(() => {
  const v = {}
  if (props.color) v['--tag-color'] = props.color
  return v
})
</script>

<style scoped>
.tag {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  font-family: var(--font-sans);
  font-size: 0.8125rem;
  line-height: 1;
  border: 1px solid transparent;
  white-space: nowrap;
  transition:
    background-color var(--transition-fast),
    border-color var(--transition-fast),
    color var(--transition-fast);
}
button.tag { cursor: pointer; background: transparent; }
span.tag  { cursor: default; }

.shape-pill {
  padding: 0.375rem 0.75rem;
  border-radius: 999px;
  border-color: var(--border);
  background: var(--bg-primary);
  color: var(--text-secondary);
  font-size: 0.8125rem;
}
.shape-pill.clickable:hover { border-color: var(--border-strong); color: var(--text-primary); }
.shape-pill.active { background: var(--primary-light); border-color: transparent; color: var(--primary); }
.shape-pill.tone-accent.active  { background: var(--accent-light);  color: var(--accent-dark); }
.shape-pill.tone-success.active { background: var(--success-light); color: var(--success); }
.shape-pill.tone-error.active   { background: var(--error-light);   color: var(--error); }

.shape-badge {
  padding: 0.25rem 0.5rem;
  border-radius: var(--radius-sm);
  background: var(--bg-tertiary);
  color: var(--text-tertiary);
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.shape-badge.tone-primary { background: var(--primary-light); color: var(--primary); }
.shape-badge.tone-accent  { background: var(--accent-light);  color: var(--accent-dark); }
.shape-badge.tone-success { background: var(--success-light); color: var(--success); }
.shape-badge.tone-warning { background: var(--accent-light);  color: var(--accent-dark); }
.shape-badge.tone-error   { background: var(--error-light);   color: var(--error); }
.shape-badge.tone-muted   { background: var(--bg-tertiary);   color: var(--text-tertiary); }

.shape-score {
  padding: 0.25rem 0.5rem 0.25rem 0.625rem;
  border-radius: var(--radius-sm);
  background: var(--primary-light);
  color: var(--primary);
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  font-weight: 600;
  gap: 0.5rem;
}
.shape-score.tone-success { background: var(--success-light); color: var(--success); }
.shape-score.tone-warning { background: var(--accent-light);  color: var(--accent-dark); }
.shape-score.tone-error   { background: var(--error-light);   color: var(--error); }
.shape-score .tag-content  { font-weight: 500; }
.tag-score { font-variant-numeric: tabular-nums; opacity: 0.85; }

.tag-dot { display: inline-flex; }
.tag-remove {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  padding: 0;
  margin-left: 0.125rem;
  margin-right: -0.25rem;
  background: transparent;
  border: none;
  color: var(--text-tertiary);
  border-radius: 50%;
  cursor: pointer;
  transition: color var(--transition-fast), background-color var(--transition-fast);
}
.tag-remove:hover { color: var(--text-primary); background: var(--bg-tertiary); }
.tag-remove svg { width: 10px; height: 10px; }
</style>
