<template>
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="state.open" class="confirm-overlay" @click.self="cancel">
        <div
          class="confirm-dialog"
          role="alertdialog"
          aria-modal="true"
          :aria-label="state.title"
        >
          <div :class="['confirm-icon', { danger: state.danger }]" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
              <line x1="12" y1="9" x2="12" y2="13" />
              <line x1="12" y1="17" x2="12.01" y2="17" />
            </svg>
          </div>
          <h3 class="confirm-title">{{ state.title }}</h3>
          <p v-if="state.message" class="confirm-message">{{ state.message }}</p>
          <div class="confirm-actions">
            <Button variant="secondary" @click="cancel">{{ state.cancelLabel }}</Button>
            <Button
              :variant="state.danger ? 'danger' : 'primary'"
              :loading="state.busy"
              @click="confirm"
            >
              {{ state.confirmLabel }}
            </Button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import Button from './Button.vue'
import { useConfirm } from '../../composables/confirm'

const { confirmState: state, settle, setBusy } = useConfirm()

function cancel() {
  settle(false)
}

function confirm() {
  settle(true)
}

// 外部可用 setBusy(true) 让确认按钮进入加载态（异步删除等），
// 之后手动调用 settle(value) 关闭。
</script>

<style scoped>
.confirm-overlay {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal);
  background: var(--scrim);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-4);
}

.confirm-dialog {
  width: min(420px, 100%);
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  padding: var(--space-6);
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: var(--space-2);
}

.confirm-icon {
  width: 48px;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius);
  background: var(--primary-light);
  color: var(--primary);
  margin-bottom: var(--space-2);
}
.confirm-icon.danger {
  background: var(--error-light);
  color: var(--error);
}
.confirm-icon svg { width: 24px; height: 24px; }

.confirm-title {
  font-size: 1.125rem;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.confirm-message {
  font-size: 0.875rem;
  color: var(--text-secondary);
  line-height: 1.5;
  margin: 0;
}

.confirm-actions {
  display: flex;
  gap: var(--space-2);
  margin-top: var(--space-4);
  width: 100%;
  justify-content: center;
}
.confirm-actions .btn { min-width: 96px; }

/* 进出场 */
.modal-enter-active,
.modal-leave-active { transition: opacity 200ms ease; }
.modal-enter-active .confirm-dialog,
.modal-leave-active .confirm-dialog { transition: transform 200ms ease; }
.modal-enter-from,
.modal-leave-to { opacity: 0; }
.modal-enter-from .confirm-dialog { transform: scale(0.96); }
.modal-leave-to .confirm-dialog { transform: scale(0.98); }

@media (prefers-reduced-motion: reduce) {
  .modal-enter-active,
  .modal-leave-active,
  .modal-enter-active .confirm-dialog,
  .modal-leave-active .confirm-dialog { transition: none; }
}
</style>
