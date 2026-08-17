<template>
  <aside :class="['sidebar', { collapsed: state.collapsed }]">
    <!-- Logo Section -->
    <div class="logo-section">
      <router-link to="/dashboard" class="logo-link" aria-label="Nexus dashboard">
        <div class="logo-container">
          <LogoIcon class="logo-icon" />
        </div>
        <div class="logo-text" v-show="!state.collapsed">
          <h1 class="logo-title">NEXUS</h1>
          <p class="logo-subtitle">Knowledge Graph</p>
        </div>
      </router-link>
      <button
        class="collapse-btn"
        @click="toggle"
        :title="state.collapsed ? 'Expand sidebar' : 'Collapse sidebar'"
        :aria-label="state.collapsed ? 'Expand sidebar' : 'Collapse sidebar'"
      >
        <ChevronLeftIcon v-if="!state.collapsed" />
        <ChevronRightIcon v-else />
      </button>
    </div>

    <!-- Navigation -->
    <nav class="nav" aria-label="Main navigation">
      <template v-for="group in navGroups" :key="group.label">
        <div v-if="!state.collapsed" class="nav-group-label">{{ group.label }}</div>
        <ul class="nav-list">
          <li v-for="item in group.items" :key="item.path">
            <router-link
              :to="item.path"
              class="nav-item"
              :class="{ active: isActive(item.path) }"
              :title="state.collapsed ? item.label : undefined"
            >
              <component :is="item.icon" class="nav-icon" />
              <span v-show="!state.collapsed" class="nav-label">{{ item.label }}</span>
            </router-link>
          </li>
        </ul>
      </template>
    </nav>

    <!-- Theme Toggle -->
    <div class="footer-section">
      <button
        class="footer-btn"
        @click="toggleTheme"
        :title="theme === 'dark' ? 'Switch to light' : 'Switch to dark'"
      >
        <SunIcon v-if="theme === 'dark'" class="footer-icon" />
        <MoonIcon v-else class="footer-icon" />
        <span v-show="!state.collapsed" class="footer-label">
          {{ theme === 'dark' ? 'Light mode' : 'Dark mode' }}
        </span>
      </button>

      <!-- User -->
      <div class="user-card">
        <div class="user-avatar" aria-hidden="true">{{ initials }}</div>
        <div v-show="!state.collapsed" class="user-info">
          <p class="user-name">{{ user?.username || 'User' }}</p>
          <p class="user-role">{{ roleLabel }}</p>
        </div>
        <button
          v-show="!state.collapsed"
          class="logout-btn"
          @click="handleLogout"
          title="Logout"
          aria-label="Logout"
        >
          <LogOutIcon />
        </button>
      </div>
    </div>
  </aside>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../../store/auth'
import { useTheme } from '../../composables/useTheme'
import { useSidebar } from '../../composables/sidebar'
import {
  LayoutGridIcon, FileTextIcon, SearchIcon, Share2Icon,
  MessageSquareIcon, BarChartIcon, MapIcon, PlayIcon,
  SunIcon, MoonIcon, LogOutIcon, ChevronLeftIcon, ChevronRightIcon,
  LogoIcon
} from '../ui/icons'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const { theme, toggleTheme } = useTheme()
const { state, toggle } = useSidebar()

const user = computed(() => authStore.user)

const initials = computed(() => {
  const name = user.value?.username || 'U'
  return name.slice(0, 2).toUpperCase()
})

const roleLabel = computed(() =>
  user.value?.role ? String(user.value.role) : 'member'
)

// 分组导航：工作台 / 分析 / 协作
const navGroups = [
  {
    label: 'Workspace',
    items: [
      { path: '/dashboard', label: 'Dashboard', icon: LayoutGridIcon },
      { path: '/documents', label: 'Documents', icon: FileTextIcon },
      { path: '/search', label: 'Search', icon: SearchIcon }
    ]
  },
  {
    label: 'Analytics',
    items: [
      { path: '/graph', label: 'Graph', icon: Share2Icon },
      { path: '/timeline', label: 'Timeline', icon: BarChartIcon },
      { path: '/documents/map', label: 'Cluster Map', icon: MapIcon },
      { path: '/graph/timeline-animation', label: 'Entity Timeline', icon: PlayIcon }
    ]
  },
  {
    label: 'Collaborate',
    items: [
      { path: '/chat', label: 'Chat', icon: MessageSquareIcon }
    ]
  }
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
  background: var(--bg-primary);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  position: relative;
  z-index: 2;
  overflow: hidden;
  transition: width var(--transition-slow);
}
.sidebar.collapsed {
  width: var(--sidebar-width-collapsed);
}

/* ---- Logo ---- */
.logo-section {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: var(--space-4) var(--space-4);
  border-bottom: 1px solid var(--border-light);
}
.sidebar.collapsed .logo-section {
  justify-content: center;
  padding: var(--space-4) var(--space-2);
}

.logo-link {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  min-width: 0;
  text-decoration: none;
  flex: 1;
}
.sidebar.collapsed .logo-link { flex: 0; }

.logo-container {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  background: var(--primary-light);
  border: 1px solid var(--border-light);
  border-radius: var(--radius);
  color: var(--primary);
  flex-shrink: 0;
}

.logo-icon { width: 24px; height: 24px; }

.logo-text { min-width: 0; }

.logo-title {
  font-family: var(--font-display);
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: 0.02em;
  line-height: 1.1;
  margin: 0;
}

.logo-subtitle {
  font-size: 0.6875rem;
  color: var(--text-tertiary);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  font-family: var(--font-mono);
  margin-top: 0.125rem;
  white-space: nowrap;
}

.collapse-btn {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  color: var(--text-tertiary);
  border-radius: var(--radius-sm);
  transition: color var(--transition-fast), background-color var(--transition-fast);
}
.collapse-btn:hover { color: var(--text-primary); background: var(--bg-tertiary); }
.collapse-btn svg { width: 16px; height: 16px; }

/* ---- Navigation ---- */
.nav {
  flex: 1;
  padding: var(--space-3) var(--space-3);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
.sidebar.collapsed .nav { gap: var(--space-3); }

.nav-group-label {
  font-family: var(--font-mono);
  font-size: 0.625rem;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--text-tertiary);
  padding: 0 var(--space-3) var(--space-2);
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
  padding: 0.5rem 0.75rem;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-size: 0.875rem;
  font-weight: 500;
  transition: background-color var(--transition-fast), color var(--transition-fast);
  text-decoration: none;
}
.sidebar.collapsed .nav-item { justify-content: center; padding: 0.625rem; }

.nav-item:hover {
  background: var(--bg-secondary);
  color: var(--text-primary);
}

.nav-item.active {
  background: var(--primary-light);
  color: var(--primary);
  font-weight: 600;
}

.nav-item.active::before {
  content: '';
  position: absolute;
  left: -3px;
  top: 6px;
  bottom: 6px;
  width: 3px;
  background: var(--primary);
  border-radius: 2px;
}

.nav-icon {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
}

.nav-label {
  white-space: nowrap;
}

/* ---- Footer ---- */
.footer-section {
  padding: var(--space-3);
  border-top: 1px solid var(--border-light);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.footer-btn {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  width: 100%;
  padding: 0.5rem 0.75rem;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-size: 0.8125rem;
  font-weight: 500;
  transition: background-color var(--transition-fast), color var(--transition-fast);
}
.sidebar.collapsed .footer-btn { justify-content: center; padding: 0.625rem; }

.footer-btn:hover {
  background: var(--bg-secondary);
  color: var(--text-primary);
}

.footer-icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}

.footer-label { white-space: nowrap; }

.user-card {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  padding: 0.5rem 0.5rem;
  border-radius: var(--radius);
  border: 1px solid var(--border-light);
  background: var(--bg-secondary);
}
.sidebar.collapsed .user-card {
  justify-content: center;
  padding: 0.375rem;
}

.user-avatar {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  background: var(--primary);
  color: var(--primary-fg);
  font-size: 0.75rem;
  font-weight: 600;
  flex-shrink: 0;
}

.user-info {
  flex: 1;
  min-width: 0;
}

.user-name {
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-role {
  font-size: 0.6875rem;
  color: var(--text-tertiary);
  font-family: var(--font-mono);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.logout-btn {
  padding: 0.375rem;
  color: var(--text-tertiary);
  border-radius: var(--radius-sm);
  transition: color var(--transition-fast), background-color var(--transition-fast);
  flex-shrink: 0;
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
