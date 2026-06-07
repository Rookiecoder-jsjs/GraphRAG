<template>
  <div :class="['empty-state', `variant-${variant}`, { 'has-decor': !!decor }]">
    <div v-if="decor" class="empty-decor" :class="`tone-${decorTone}`" aria-hidden="true">
      <Decor :kind="decor" :tone="decorTone" />
    </div>
    <div class="empty-icon">
      <component v-if="icon" :is="icon" class="empty-icon-svg" />
      <slot v-else name="icon" />
    </div>
    <h2 v-if="title" class="empty-title">{{ title }}</h2>
    <p v-if="description || $slots.description" class="empty-description">
      <slot name="description">{{ description }}</slot>
    </p>
    <div v-if="actionLabel || $slots.action" class="empty-action">
      <slot name="action">
        <Button
          v-if="actionLabel"
          :variant="actionVariant"
          @click="$emit('action', $event)"
        >
          {{ actionLabel }}
        </Button>
      </slot>
    </div>
  </div>
</template>

<script setup>
import Button from './Button.vue'
import { Decor } from '../decor'

defineProps({
  icon: { type: [Object, Function], default: null },
  title: { type: String, default: '' },
  description: { type: String, default: '' },
  actionLabel: { type: String, default: '' },
  actionVariant: { type: String, default: 'primary' },
  variant: { type: String, default: 'default' },
  decor: { type: String, default: '' },         // 'contour' | 'geometric' | 'paper'
  decorTone: { type: String, default: 'muted' } // 'primary' | 'accent' | 'muted'
})

defineEmits(['action'])
</script>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 3rem 1.5rem;
  gap: 0.875rem;
  position: relative;
}
.empty-state.variant-initial { padding: 4rem 1.5rem; }

/* Decorative hero — sits behind icon / text, radial mask softens edges */
.empty-decor {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 200px;
  height: 200px;
  transform: translate(-50%, calc(-50% - 60px));
  pointer-events: none;
  opacity: 0.55;
  -webkit-mask-image: radial-gradient(ellipse at center, black 30%, transparent 75%);
          mask-image: radial-gradient(ellipse at center, black 30%, transparent 75%);
  z-index: 0;
}
.empty-decor.tone-primary { color: var(--primary); }
.empty-decor.tone-accent  { color: var(--accent); }
.empty-decor.tone-muted   { color: var(--text-tertiary); }
.empty-state.has-decor .empty-icon,
.empty-state.has-decor .empty-title,
.empty-state.has-decor .empty-description,
.empty-state.has-decor .empty-action { position: relative; z-index: 1; }
.empty-state.has-decor .empty-icon { background: var(--bg-secondary); border-color: transparent; }

.empty-icon {
  width: 64px;
  height: 64px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--glass-bg);
  -webkit-backdrop-filter: blur(12px);
          backdrop-filter: blur(12px);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-lg);
  color: var(--text-tertiary);
  margin-bottom: 0.25rem;
  box-shadow: var(--glass-highlight);
}
.empty-state.variant-initial .empty-icon {
  background: var(--primary-light);
  border-color: transparent;
  color: var(--primary);
}
.empty-icon-svg {
  width: 28px;
  height: 28px;
  stroke: currentColor;
  fill: none;
  stroke-width: 1.5;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.empty-title {
  font-family: var(--font-display);
  font-size: 1.375rem;
  font-weight: 500;
  color: var(--text-secondary);
  margin: 0;
  letter-spacing: -0.005em;
}
.empty-description {
  font-size: 0.875rem;
  color: var(--text-tertiary);
  max-width: 36ch;
  line-height: 1.5;
  margin: 0;
}
.empty-action {
  margin-top: 0.5rem;
}
</style>
