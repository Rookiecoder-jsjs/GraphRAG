<template>
  <aside class="sidebar">
    <!-- Logo Section -->
    <div class="logo-section">
      <div class="logo-container">
        <svg class="logo-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <polygon points="12 2 22 8.5 22 15.5 12 22 2 15.5 2 8.5 12 2" />
          <line x1="12" y1="22" x2="12" y2="15.5" />
          <polyline points="22 8.5 12 15.5 2 8.5" />
        </svg>
      </div>
      <div class="logo-text">
        <h1 class="logo-title">智能知识库</h1>
        <p class="logo-subtitle">Intelligent Knowledge</p>
      </div>
    </div>

    <!-- Navigation -->
    <nav class="nav">
      <ul class="nav-list">
        <li v-for="item in navItems" :key="item.path">
          <router-link
            :to="item.path"
            class="nav-item"
            :class="{ active: isActive(item.path) }"
          >
            <component :is="item.icon" class="nav-icon" />
            <span class="nav-label">{{ item.label }}</span>
          </router-link>
        </li>
      </ul>
    </nav>

    <!-- Theme Toggle -->
    <div class="theme-toggle-container">
      <button class="theme-toggle" @click="toggleTheme" :title="theme === 'dark' ? 'Switch to light' : 'Switch to dark'">
        <svg v-if="theme === 'dark'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
        </svg>
        <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
        </svg>
        <span class="theme-label">{{ theme === 'dark' ? 'Light' : 'Dark' }}</span>
      </button>
    </div>

    <!-- Status -->
    <div class="status-container">
      <div class="status-card">
        <span class="status-dot"></span>
        <span class="status-text">SYSTEM ONLINE</span>
      </div>
    </div>

    <!-- User Section -->
    <div class="user-section">
      <div class="user-card">
        <div class="user-info">
          <p class="user-name">{{ user?.username || 'User' }}</p>
          <p class="user-email">{{ user?.username || 'user' }}@zhishiku</p>
        </div>
        <button class="logout-btn" @click="handleLogout" title="Logout">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
            <polyline points="16 17 21 12 16 7" />
            <line x1="21" y1="12" x2="9" y2="12" />
          </svg>
        </button>
      </div>
    </div>
  </aside>
</template>

<script setup>
import { computed, h } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../../store/auth'
import { useTheme } from '../../composables/useTheme'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const { theme, toggleTheme } = useTheme()

const user = computed(() => authStore.user)

// Icon components
const FileTextIcon = {
  render() {
    return h('svg', {
      viewBox: '0 0 24 24',
      fill: 'none',
      stroke: 'currentColor',
      'stroke-width': '2',
      'stroke-linecap': 'round',
      'stroke-linejoin': 'round'
    }, [
      h('path', { d: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z' }),
      h('polyline', { points: '14 2 14 8 20 8' }),
      h('line', { x1: '16', y1: '13', x2: '8', y2: '13' }),
      h('line', { x1: '16', y1: '17', x2: '8', y2: '17' }),
      h('polyline', { points: '10 9 9 9 8 9' })
    ])
  }
}

const SearchIcon = {
  render() {
    return h('svg', {
      viewBox: '0 0 24 24',
      fill: 'none',
      stroke: 'currentColor',
      'stroke-width': '2',
      'stroke-linecap': 'round',
      'stroke-linejoin': 'round'
    }, [
      h('circle', { cx: '11', cy: '11', r: '8' }),
      h('line', { x1: '21', y1: '21', x2: '16.65', y2: '16.65' })
    ])
  }
}

const Share2Icon = {
  render() {
    return h('svg', {
      viewBox: '0 0 24 24',
      fill: 'none',
      stroke: 'currentColor',
      'stroke-width': '2',
      'stroke-linecap': 'round',
      'stroke-linejoin': 'round'
    }, [
      h('circle', { cx: '18', cy: '5', r: '3' }),
      h('circle', { cx: '6', cy: '12', r: '3' }),
      h('circle', { cx: '18', cy: '19', r: '3' }),
      h('line', { x1: '8.59', y1: '13.51', x2: '15.42', y2: '17.49' }),
      h('line', { x1: '15.41', y1: '6.51', x2: '8.59', y2: '10.49' })
    ])
  }
}

const MessageSquareIcon = {
  render() {
    return h('svg', {
      viewBox: '0 0 24 24',
      fill: 'none',
      stroke: 'currentColor',
      'stroke-width': '2',
      'stroke-linecap': 'round',
      'stroke-linejoin': 'round'
    }, [
      h('path', { d: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z' })
    ])
  }
}

const BarChartIcon = {
  render() {
    return h('svg', {
      viewBox: '0 0 24 24',
      fill: 'none',
      stroke: 'currentColor',
      'stroke-width': '2',
      'stroke-linecap': 'round',
      'stroke-linejoin': 'round'
    }, [
      h('line', { x1: '12', y1: '20', x2: '12', y2: '10' }),
      h('line', { x1: '18', y1: '20', x2: '18', y2: '4' }),
      h('line', { x1: '6', y1: '20', x2: '6', y2: '16' })
    ])
  }
}

const LayoutGridIcon = {
  render() {
    return h('svg', {
      viewBox: '0 0 24 24',
      fill: 'none',
      stroke: 'currentColor',
      'stroke-width': '2',
      'stroke-linecap': 'round',
      'stroke-linejoin': 'round'
    }, [
      h('rect', { x: '3', y: '3', width: '7', height: '7' }),
      h('rect', { x: '14', y: '3', width: '7', height: '7' }),
      h('rect', { x: '14', y: '14', width: '7', height: '7' }),
      h('rect', { x: '3', y: '14', width: '7', height: '7' })
    ])
  }
}

const navItems = [
  { path: '/dashboard', label: 'Dashboard', icon: LayoutGridIcon },
  { path: '/documents', label: 'Documents', icon: FileTextIcon },
  { path: '/search', label: 'Search', icon: SearchIcon },
  { path: '/graph', label: 'Knowledge Graph', icon: Share2Icon },
  { path: '/chat', label: 'Chat', icon: MessageSquareIcon },
  { path: '/timeline', label: 'Timeline', icon: BarChartIcon }
]

const isActive = (path) => route.path === path

const handleLogout = () => {
  authStore.logout()
  router.push('/login')
}
</script>

<style scoped>
.sidebar {
  width: var(--sidebar-width);
  height: 100vh;
  background: var(--glass-bg-strong);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
          backdrop-filter: blur(24px) saturate(180%);
  border-right: 1px solid var(--glass-border);
  display: flex;
  flex-direction: column;
  position: relative;
  z-index: 2;
  overflow: hidden;
}

/* ---- Logo ---- */
.logo-section {
  padding: 1.75rem 1.5rem 1.5rem;
}

.logo-container {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  background: var(--glass-bg);
  -webkit-backdrop-filter: blur(12px);
          backdrop-filter: blur(12px);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius);
  color: var(--primary);
}

.logo-icon {
  width: 26px;
  height: 26px;
}

.logo-text {
  margin-top: 0.875rem;
}

.logo-title {
  font-family: var(--font-display);
  font-size: 1.5rem;
  font-weight: 500;
  color: var(--text-primary);
  letter-spacing: -0.02em;
  line-height: 1.1;
}

.logo-subtitle {
  font-family: var(--font-display);
  font-style: italic;
  font-size: 0.8125rem;
  color: var(--text-tertiary);
  margin-top: 0.125rem;
}

/* ---- Navigation ---- */
.nav {
  flex: 1;
  padding: 1rem 0.75rem;
}

.nav-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.nav-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.625rem 0.875rem;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  transition: background-color var(--transition-fast), color var(--transition-fast);
  text-decoration: none;
}

.nav-item::before {
  content: '';
  position: absolute;
  left: 0;
  top: 6px;
  bottom: 6px;
  width: 2px;
  background: transparent;
  border-radius: 1px;
  transition: background-color var(--transition);
}

.nav-item:hover {
  background: var(--glass-bg);
  -webkit-backdrop-filter: blur(8px);
          backdrop-filter: blur(8px);
  color: var(--text-primary);
}

.nav-item.active {
  background: var(--glass-bg-strong);
  -webkit-backdrop-filter: blur(12px) saturate(180%);
          backdrop-filter: blur(12px) saturate(180%);
  border: 1px solid var(--glass-border);
  color: var(--primary);
  box-shadow: var(--glass-shadow), var(--glass-highlight);
  padding: calc(0.625rem - 1px) calc(0.875rem - 1px);
}

.nav-item.active::before {
  background-color: var(--primary);
}

.nav-icon {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
}

.nav-label {
  font-size: 0.875rem;
  font-weight: 500;
  letter-spacing: -0.005em;
}

/* ---- Theme Toggle ---- */
.theme-toggle-container {
  padding: 0.5rem 1rem;
}

.theme-toggle {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  width: 100%;
  padding: 0.5rem 0.75rem;
  background: var(--glass-bg);
  -webkit-backdrop-filter: blur(12px);
          backdrop-filter: blur(12px);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius);
  color: var(--text-secondary);
  font-size: 0.8125rem;
  font-family: var(--font-sans);
  transition: border-color var(--transition-fast), color var(--transition-fast);
}

.theme-toggle:hover {
  border-color: var(--primary);
  color: var(--text-primary);
}

.theme-toggle svg {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}

.theme-label {
  font-weight: 500;
}

/* ---- Status ---- */
.status-container {
  padding: 0 1rem 0.5rem;
}

.status-card {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--text-tertiary);
  letter-spacing: 0.05em;
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background-color: var(--success);
}

/* ---- User ---- */
.user-section {
  padding: 1rem;
}

.user-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.625rem 0.75rem;
  background: var(--glass-bg);
  -webkit-backdrop-filter: blur(12px);
          backdrop-filter: blur(12px);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius);
  box-shadow: var(--glass-shadow), var(--glass-highlight);
}

.user-info {
  flex: 1;
  min-width: 0;
}

.user-name {
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-email {
  font-size: 0.6875rem;
  color: var(--text-tertiary);
  font-family: var(--font-mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-top: 1px;
}

.logout-btn {
  padding: 0.375rem;
  color: var(--text-tertiary);
  border-radius: var(--radius-sm);
  transition: color var(--transition-fast), background-color var(--transition-fast);
}

.logout-btn:hover {
  color: var(--error);
  background: var(--error-light);
}

.logout-btn svg {
  width: 16px;
  height: 16px;
  display: block;
}
</style>
