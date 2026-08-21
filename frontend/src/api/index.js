import axios from 'axios'

// Create axios instance
const service = axios.create({
  baseURL: '/api',
  timeout: 300000, // 5 minutes timeout
  headers: {
    'Content-Type': 'application/json'
  }
})

// Request interceptor
service.interceptors.request.use(
  config => {
    const token = localStorage.getItem('token')
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  error => {
    console.error('Request error:', error)
    return Promise.reject(error)
  }
)

// 401 handling: de-duplicated + dispatched as an event so the auth store can
// reset state in a single place (see main.js listener → soft router redirect).
// No hard `window.location.href` reload here: a full reload would tear down
// any in-flight state (e.g. an active chat SSE stream) on a background 401.
// Avoids redirect-loop if /login itself 401s.
let isRedirecting = false
service.interceptors.response.use(
  response => response,
  error => {
    if (error.response?.status === 401 && !isRedirecting) {
      const onLoginPage = window.location.pathname === '/login'
      if (!onLoginPage) {
        isRedirecting = true
        window.dispatchEvent(new CustomEvent('auth:logout'))
        setTimeout(() => { isRedirecting = false }, 2000)
      }
    }
    return Promise.reject(error)
  }
)

export default service
