<template>
  <div class="login-page">
    <div class="login-container">
      <div class="login-card">
        <div class="login-header">
          <div class="logo-wrapper">
            <LogoIcon class="logo-icon" />
          </div>
          <h1 class="login-title">智能知识库</h1>
          <p class="login-subtitle">Intelligent Knowledge Base</p>
        </div>

        <form @submit.prevent="handleSubmit" class="login-form">
          <div class="form-group">
            <label for="username" class="form-label">Username</label>
            <input
              id="username"
              v-model="username"
              type="text"
              class="form-input"
              placeholder="Enter username"
              required
              autocomplete="username"
            />
          </div>

          <div class="form-group">
            <label for="password" class="form-label">Password</label>
            <input
              id="password"
              v-model="password"
              type="password"
              class="form-input"
              placeholder="Enter password"
              required
              autocomplete="current-password"
            />
          </div>

          <div v-if="error" class="error-message">{{ error }}</div>

          <Button
            type="submit"
            variant="primary"
            :loading="loading"
            block
            class="submit-btn"
          >
            {{ isRegister ? 'Create Account' : 'Sign In' }}
          </Button>

          <Button
            type="button"
            variant="ghost"
            block
            class="toggle-btn"
            @click="isRegister = !isRegister"
          >
            {{ isRegister ? 'Already have an account? Sign In' : "Don't have an account? Register" }}
          </Button>
        </form>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '../store/auth'
import { Button } from '../components/ui'
import { LogoIcon } from '../components/ui/icons'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)
const isRegister = ref(false)

const handleSubmit = async () => {
  error.value = ''
  loading.value = true

  try {
    let result
    if (isRegister.value) {
      result = await authStore.register(username.value, password.value)
    } else {
      result = await authStore.login(username.value, password.value)
    }

    if (result.success) {
      const redirect = route.query.redirect || '/documents'
      router.push(redirect)
    } else {
      error.value = result.error
    }
  } catch (err) {
    error.value = 'An unexpected error occurred'
    console.error('Auth error:', err)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  overflow: hidden;
  background: var(--bg-base);
}

.login-container {
  position: relative;
  z-index: 1;
  width: 100%;
  max-width: 420px;
  padding: 1rem;
}

.login-card {
  padding: 3rem 2.5rem;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
}

.login-header { text-align: center; margin-bottom: 2rem; }

.logo-wrapper {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 60px;
  height: 60px;
  background: var(--primary-light);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-lg);
  color: var(--primary);
  margin-bottom: 1.25rem;
}

.logo-icon { width: 32px; height: 32px; }

.login-title {
  font-family: var(--font-display);
  font-size: 2.25rem;
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: -0.025em;
  margin-bottom: 0.25rem;
  line-height: 1.1;
}

.login-subtitle {
  font-family: var(--font-sans);
  color: var(--text-secondary);
  font-size: 0.9375rem;
  font-weight: 400;
}

.login-form { display: flex; flex-direction: column; gap: 1rem; }
.form-group { display: flex; flex-direction: column; gap: 0.375rem; }

.form-label {
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.form-input {
  padding: 0.875rem 1rem;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: 0.9375rem;
  color: var(--text-primary);
  background: var(--bg-primary);
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
}

.form-input:focus {
  outline: none;
  border-color: var(--primary);
  box-shadow: 0 0 0 3px var(--primary-light);
}
.form-input::placeholder { color: var(--text-tertiary); }

.error-message {
  padding: 0.625rem 0.75rem;
  background: var(--error-light);
  border: 1px solid var(--error);
  border-radius: var(--radius-sm);
  color: var(--error);
  font-size: 0.8125rem;
  text-align: left;
}

.submit-btn { margin-top: 0.5rem; }
.toggle-btn {
  margin-top: 0.25rem;
  font-size: 0.8125rem;
  color: var(--text-tertiary);
}
.toggle-btn:hover { color: var(--primary); }
</style>
