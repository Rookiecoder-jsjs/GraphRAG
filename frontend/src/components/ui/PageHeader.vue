<template>
  <header :class="['page-header', `tone-${tone}`]" :style="cssVars">
    <div class="header-content">
      <div class="header-title">
        <div v-if="$slots.icon || icon" class="title-icon" :class="`icon-${tone}`">
          <slot name="icon">
            <component :is="icon" class="title-icon-svg" />
          </slot>
        </div>
        <div class="title-text">
          <div v-if="kicker || $slots.kicker" class="kicker tone-primary">
            <slot name="kicker">{{ kicker }}</slot>
          </div>
          <h1 :class="['title', { 'title-italic': italicTitle }]">{{ title }}</h1>
          <p v-if="subtitle || $slots.subtitle" class="subtitle">
            <slot name="subtitle">{{ subtitle }}</slot>
          </p>
        </div>
      </div>
      <div v-if="$slots.actions || $slots.default" class="header-actions">
        <slot name="actions" />
        <slot />
      </div>
    </div>
    <div v-if="$slots.meta || meta" class="header-meta">
      <slot name="meta">{{ meta }}</slot>
    </div>
  </header>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  icon: { type: [Object, Function], default: null },
  title: { type: String, required: true },
  subtitle: { type: String, default: '' },
  meta: { type: String, default: '' },
  tone: { type: String, default: 'primary' },
  kicker: { type: String, default: '' },
  italicTitle: { type: Boolean, default: false }
})

const cssVars = computed(() => ({}))
</script>

<style scoped>
.page-header {
  background: transparent;
  padding: 2rem 2rem 1.25rem;
}
.header-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1.5rem;
  max-width: 1200px;
  margin: 0 auto;
}
.header-title {
  display: flex;
  align-items: center;
  gap: 1rem;
  min-width: 0;
}
.title-icon {
  width: 44px;
  height: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius);
  flex-shrink: 0;
  background: var(--glass-bg);
  -webkit-backdrop-filter: blur(12px);
          backdrop-filter: blur(12px);
  border: 1px solid var(--glass-border);
  color: var(--primary);
  box-shadow: var(--glass-highlight);
  transition:
    background-color var(--transition),
    color var(--transition);
}
.title-icon.icon-primary { background: var(--glass-bg); color: var(--primary); }
.title-icon.icon-accent  { background: var(--glass-bg); color: var(--accent-dark); }
.title-icon.icon-warm    { background: var(--glass-bg); color: var(--accent-dark); }
.title-icon.icon-cool    { background: var(--glass-bg); color: var(--primary); }
.title-icon.icon-success { background: var(--glass-bg); color: var(--success); }
.title-icon.icon-warning { background: var(--glass-bg); color: var(--warning); }
.title-icon.icon-error   { background: var(--glass-bg); color: var(--error); }
.title-icon.icon-muted   { background: var(--glass-bg); color: var(--text-secondary); }
.title-icon-svg { width: 22px; height: 22px; stroke: currentColor; fill: none; stroke-width: 1.75; stroke-linecap: round; stroke-linejoin: round; }

.title-text { min-width: 0; }
.title {
  font-family: var(--font-display);
  font-size: 1.875rem;
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: -0.02em;
  line-height: 1.15;
  margin: 0;
}
.title-italic { font-style: italic; }
.subtitle {
  font-family: var(--font-sans);
  font-size: 0.9375rem;
  color: var(--text-secondary);
  margin: 0.25rem 0 0;
  font-weight: 400;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-shrink: 0;
}

.header-meta {
  max-width: 1200px;
  margin: 0.75rem auto 0;
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
</style>
