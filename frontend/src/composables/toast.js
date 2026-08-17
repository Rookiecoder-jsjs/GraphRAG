import { reactive } from 'vue'

// 全局 Toast 单例 — 任一组件可经 useToast() 弹出通知。
// 由 App.vue 挂载 <ToastContainer/> 消费本列表。
const toasts = reactive([])

let nextId = 1

function push(type, message, opts = {}) {
  const id = nextId++
  toasts.push({ id, type, message, duration: opts.duration ?? 3500 })
  if (opts.duration !== 0) {
    setTimeout(() => dismiss(id), opts.duration)
  }
  return id
}

function dismiss(id) {
  const i = toasts.findIndex(t => t.id === id)
  if (i !== -1) toasts.splice(i, 1)
}

export function useToast() {
  return {
    toasts,
    toast: (message, opts) => push('info', message, opts),
    success: (message, opts) => push('success', message, opts),
    error: (message, opts) => push('error', message, opts),
    warning: (message, opts) => push('warning', message, opts),
    dismiss
  }
}
