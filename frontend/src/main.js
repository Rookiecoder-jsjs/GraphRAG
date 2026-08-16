import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { useAuthStore } from './store/auth'
import './styles/variables.css'

const app = createApp(App)

const pinia = createPinia()
app.use(pinia)
app.use(router)

// The axios 401 interceptor (api/index.js) dispatches `auth:logout` instead
// of hard-reloading, so any background 401 (tag add, dashboard refresh)
// resets the store and soft-redirects without tearing down in-flight state
// like an active chat stream. Router guard also handles direct 401s on nav.
window.addEventListener('auth:logout', () => {
  const authStore = useAuthStore(pinia)
  authStore.logout()
  if (router.currentRoute.value.path !== '/login') {
    router.push('/login')
  }
})

app.mount('#app')
