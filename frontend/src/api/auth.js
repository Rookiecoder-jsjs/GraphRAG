import service from './index'

export const authApi = {
  login: (username, password) => {
    const params = new URLSearchParams()
    params.append('username', username)
    params.append('password', password)
    return service.post('/auth/login', params, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
    })
  },

  register: (username, password) => {
    return service.post('/auth/register', { username, password })
  },

  me: () => service.get('/auth/me')
}
