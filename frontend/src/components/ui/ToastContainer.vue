<template>
  <Teleport to="body">
    <div class="toast-container" aria-live="polite" role="status">
      <TransitionGroup name="toast">
        <div
          v-for="t in toasts"
          :key="t.id"
          :class="['toast', `toast-${t.type}`]"
        >
          <svg
            v-if="t.type === 'success'" class="toast-icon" viewBox="0 0 24 24"
            fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
          >
            <polyline points="20 6 9 17 4 12" />
          </svg>
          <svg
            v-else-if="t.type === 'error'" class="toast-icon" viewBox="0 0 24 24"
            fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
          >
            <circle cx="12" cy="12" r="10" />
            <line x1="15" y1="9" x2="9" y2="15" />
            <line x1="9" y1="9" x2="15" y2="15" />
          </svg>
          <svg
            v-else class="toast-icon" viewBox="0 0 24 24"
            fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
          >
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="12" />
            <line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
          <span class="toast-message">{{ t.message }}</span>
          <button class="toast-close" @click="dismiss(t.id)" aria-label="Dismiss">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      </TransitionGroup>
    </div>
  </Teleport>
</template>

<script setup>
import { useToast } from '../../composables/toast'

const { toasts, dismiss } = useToast()
</script>

<style scoped>
.toast-container {
  position: fixed;
  top: var(--space-4);
  right: var(--space-4);
  z-index: var(--z-toast);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  max-width: min(380px, calc(100vw - 2rem));
  pointer-events: none;
}

.toast {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow-md);
  pointer-events: auto;
}

.toast-success { border-left: 3px solid var(--success); }
.toast-error   { border-left: 3px solid var(--error); }
.toast-warning { border-left: 3px solid var(--warning); }
.toast-info    { border-left: 3px solid var(--primary); }

.toast-icon {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
  margin-top: 1px;
}
.toast-success .toast-icon { color: var(--success); }
.toast-error .toast-icon   { color: var(--error); }
.toast-warning .toast-icon { color: var(--warning); }
.toast-info .toast-icon    { color: var(--primary); }

.toast-message {
  flex: 1;
  font-size: 0.875rem;
  color: var(--text-primary);
  line-height: 1.45;
  word-break: break-word;
}

.toast-close {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  color: var(--text-tertiary);
  border-radius: var(--radius-sm);
  transition: color var(--transition-fast), background-color var(--transition-fast);
}
.toast-close:hover { color: var(--text-primary); background: var(--bg-tertiary); }
.toast-close svg { width: 14px; height: 14px; }

/* 进出场：150-300ms */
.toast-enter-active,
.toast-leave-active { transition: opacity 220ms ease, transform 220ms ease; }
.toast-enter-from,
.toast-leave-to { opacity: 0; transform: translateX(12px); }
.toast-move { transition: transform 220ms ease; }

@media (prefers-reduced-motion: reduce) {
  .toast-enter-active,
  .toast-leave-active,
  .toast-move { transition: none; }
}
</style>
