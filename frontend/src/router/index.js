import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../store/auth'

// Lazy-load views
const Home = () => import('../views/Home.vue')
const DocumentsPage = () => import('../views/DocumentsPage.vue')
const GraphPage = () => import('../views/GraphPage.vue')
const ChatPage = () => import('../views/ChatPage.vue')
const SearchPage = () => import('../views/SearchPage.vue')
const TimelinePage = () => import('../views/TimelinePage.vue')
const DashboardPage = () => import('../views/DashboardPage.vue')
const EntityDetailPage = () => import('../views/EntityDetailPage.vue')
const DocumentDetailPage = () => import('../views/DocumentDetailPage.vue')
const ClusterMapPage = () => import('../views/ClusterMapPage.vue')
const EntityTimelineAnimationPage = () => import('../views/EntityTimelineAnimationPage.vue')
const Layout = () => import('../components/layout/Layout.vue')

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: Home,
    meta: { requiresAuth: false }
  },
  {
    path: '/',
    component: Layout,
    meta: { requiresAuth: true },
    children: [
      {
        path: '',
        redirect: '/documents'
      },
      {
        path: 'documents',
        name: 'Documents',
        component: DocumentsPage
      },
      {
        // Document detail page. Mounted under the Layout shell so the
        // navigation header is shared with the list page. The `:id` is
        // a UUID — letters, digits, hyphens — no regex tightening needed.
        path: 'documents/:id',
        name: 'DocumentDetail',
        component: DocumentDetailPage
      },
      {
        // 2D PCA cluster map. Mounted under the same layout shell;
        // literal path so it never collides with the :id route above.
        path: 'documents/map',
        name: 'DocumentClusterMap',
        component: ClusterMapPage
      },
      {
        // Entity timeline animation: scrub / autoplay through history
        // of when each entity first appeared in the user's docs.
        path: 'graph/timeline-animation',
        name: 'EntityTimelineAnimation',
        component: EntityTimelineAnimationPage
      },
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: DashboardPage
      },
      {
        path: 'graph',
        name: 'Graph',
        component: GraphPage
      },
      {
        path: 'chat',
        name: 'Chat',
        component: ChatPage
      },
      {
        path: 'search',
        name: 'Search',
        component: SearchPage
      },
      {
        path: 'timeline',
        name: 'Timeline',
        component: TimelinePage
      },
      {
        // Entity detail page. The `:name` may contain almost any
        // character (LLM extraction is unconstrained), so we use
        // `path: '*'` to swallow the rest of the URL after `/entities/`
        // — but we keep `:name` for the route name binding. Frontend
        // `encodeURIComponent`s the name on navigation.
        path: 'entities/:name(.*)*',
        name: 'EntityDetail',
        component: EntityDetailPage
      }
    ]
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

// Navigation guard
router.beforeEach(async (to, from, next) => {
  const authStore = useAuthStore()

  // Check if auth is initialized
  if (!authStore.initialized) {
    await authStore.initialize()
  }

  const requiresAuth = to.matched.some(record => record.meta.requiresAuth !== false)

  if (requiresAuth && !authStore.isAuthenticated) {
    next({ name: 'Login', query: { redirect: to.fullPath } })
  } else if (to.name === 'Login' && authStore.isAuthenticated) {
    next({ name: 'Documents' })
  } else {
    next()
  }
})

export default router
