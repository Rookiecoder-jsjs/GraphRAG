<template>
  <div class="login-page">
    <div class="bg-stage" aria-hidden="true">
      <div class="blob blob-1"></div>
      <div class="blob blob-2"></div>
      <div class="blob blob-3"></div>
    </div>

    <div class="login-container">
      <div class="login-card">
        <div class="login-header">
          <div class="logo-wrapper">
            <svg class="logo-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <polygon points="12 2 22 8.5 22 15.5 12 22 2 15.5 2 8.5 12 2" />
              <line x1="12" y1="22" x2="12" y2="15.5" />
              <polyline points="22 8.5 12 15.5 2 8.5" />
            </svg>
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

.bg-stage {
  position: absolute;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
  z-index: 0;
}

.blob {
  position: absolute;
  border-radius: 50%;
  filter: blur(120px);
  opacity: 0.65;
  will-change: transform;
}
.blob-1 { width: 60vw; height: 60vw; background: var(--blob-1); top: -15%; left: -10%; animation: drift-1 30s ease-in-out infinite; }
.blob-2 { width: 55vw; height: 55vw; background: var(--blob-2); top: 25%;  right: -15%; animation: drift-2 35s ease-in-out infinite; }
.blob-3 { width: 65vw; height: 65vw; background: var(--blob-3); bottom: -20%; left: 15%; animation: drift-3 40s ease-in-out infinite; }
[data-theme='dark'] .blob { opacity: 0.32; }
@media (prefers-reduced-motion: reduce) {
  .blob { animation: none !important; }
}

.login-container {
  position: relative;
  z-index: 10;
  width: 100%;
  max-width: 420px;
  padding: 1rem;
}

.login-card {
  padding: 3rem 2.5rem;
  background: var(--glass-bg-strong);
  -webkit-backdrop-filter: blur(var(--glass-blur-lg)) saturate(180%);
          backdrop-filter: blur(var(--glass-blur-lg)) saturate(180%);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-2xl);
  box-shadow: var(--glass-shadow), var(--glass-highlight);
}

.login-header { text-align: center; margin-bottom: 2rem; }

.logo-wrapper {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 60px;
  height: 60px;
  background: var(--glass-bg);
  -webkit-backdrop-filter: blur(12px);
          backdrop-filter: blur(12px);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-lg);
  color: var(--primary);
  margin-bottom: 1.25rem;
  box-shadow: var(--glass-highlight);
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
  border: 1px solid var(--glass-border);
  border-radius: var(--radius);
  font-size: 0.9375rem;
  color: var(--text-primary);
  background: var(--glass-bg);
  -webkit-backdrop-filter: blur(8px);
          backdrop-filter: blur(8px);
  transition: border-color var(--transition-fast), background-color var(--transition-fast);
}

.form-input:focus {
  outline: none;
  border-color: var(--primary);
  background: var(--glass-bg-strong);
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
