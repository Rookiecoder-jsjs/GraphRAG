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
// reset state in a single place. Avoids redirect-loop if /login itself 401s.
let isRedirecting = false
service.interceptors.response.use(
  response => response,
  error => {
    if (error.response?.status === 401 && !isRedirecting) {
      const onLoginPage = window.location.pathname === '/login'
      if (!onLoginPage) {
        isRedirecting = true
        localStorage.removeItem('token')
        localStorage.removeItem('user')
        window.dispatchEvent(new CustomEvent('auth:logout'))
        window.location.href = '/login'
        setTimeout(() => { isRedirecting = false }, 2000)
      }
    }
    return Promise.reject(error)
  }
)

// Request with retry mechanism (exponential backoff, abort on 4xx)
export const requestWithRetry = async (requestFn, maxRetries = 3, delay = 1000) => {
  let lastError
  for (let i = 0; i < maxRetries; i++) {
    try {
      return await requestFn()
    } catch (error) {
      lastError = error
      const status = error?.response?.status
      if (status && status >= 400 && status < 500) throw error
      if (i === maxRetries - 1) throw error
      console.warn(`Request failed, retrying (${i + 1}/${maxRetries})...`)
      await new Promise(resolve => setTimeout(resolve, delay * Math.pow(2, i)))
    }
  }
  throw lastError
}

export default service
