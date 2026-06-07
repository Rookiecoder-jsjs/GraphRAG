import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi } from '../api/auth'

export const useAuthStore = defineStore('auth', () => {
  // State
  const token = ref(localStorage.getItem('token') || '')
  const user = ref(null)
  const loading = ref(false)
  const initialized = ref(false)

  // Computed
  const isAuthenticated = computed(() => !!token.value)

  // Actions
  async function initialize() {
    if (initialized.value) return

    const storedToken = localStorage.getItem('token')
    if (storedToken) {
      token.value = storedToken
      try {
        const { data } = await authApi.me()
        user.value = data
      } catch (error) {
        console.error('Failed to fetch user:', error)
        logout()
      }
    }
    initialized.value = true
  }

  async function login(username, password) {
    loading.value = true
    try {
      const { data } = await authApi.login(username, password)
      token.value = data.access_token
      localStorage.setItem('token', data.access_token)

      // Fetch user info
      const { data: userData } = await authApi.me()
      user.value = userData

      return { success: true }
    } catch (error) {
      console.error('Login error:', error)
      const message = error.response?.data?.detail || 'Login failed'
      return { success: false, error: message }
    } finally {
      loading.value = false
    }
  }

  async function register(username, password) {
    loading.value = true
    try {
      await authApi.register(username, password)
      // Auto-login after registration
      return login(username, password)
    } catch (error) {
      console.error('Register error:', error)
      const message = error.response?.data?.detail || 'Registration failed'
      return { success: false, error: message }
    } finally {
      loading.value = false
    }
  }

  function logout() {
    token.value = ''
    user.value = null
    localStorage.removeItem('token')
  }

  return {
    token,
    user,
    loading,
    initialized,
    isAuthenticated,
    initialize,
    login,
    register,
    logout
  }
})
