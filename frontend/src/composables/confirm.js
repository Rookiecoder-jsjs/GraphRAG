import { reactive } from 'vue'

// 全局确认对话框单例 — useConfirm() 返回异步 confirm()，替换原生 window.confirm。
// 由 App.vue 挂载 <ConfirmDialog/> 消费本状态。
const state = reactive({
  open: false,
  title: '',
  message: '',
  confirmLabel: 'Confirm',
  cancelLabel: 'Cancel',
  danger: false,
  busy: false,
  _resolve: null,
  _promise: null
})

function ask(opts) {
  // Re-entrant guard: a second ask() while the dialog is open (e.g. a
  // double-click racing the overlay render) must not clobber the pending
  // _resolve, or the first caller's promise never settles. Reuse it instead
  // — both callers await the same dialog outcome.
  if (state.open && state._resolve) {
    return state._promise
  }
  state.title = opts.title ?? 'Confirm'
  state.message = opts.message ?? ''
  state.confirmLabel = opts.confirmLabel ?? 'Confirm'
  state.cancelLabel = opts.cancelLabel ?? 'Cancel'
  state.danger = opts.danger ?? false
  state.busy = false
  state.open = true
  state._promise = new Promise(resolve => {
    state._resolve = resolve
  })
  return state._promise
}

function settle(value) {
  state.open = false
  state._resolve?.(value)
  state._resolve = null
  state._promise = null
}

function setBusy(v) {
  state.busy = v
}

export function useConfirm() {
  return { confirmState: state, confirm: ask, settle, setBusy }
}
