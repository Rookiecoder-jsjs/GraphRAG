import { reactive } from 'vue'

// 侧栏折叠状态单例 — Sidebar 与 Layout 共享，控制图标态导航宽度
const state = reactive({
  collapsed: false
})

export function useSidebar() {
  return {
    state,
    toggle: () => { state.collapsed = !state.collapsed }
  }
}
