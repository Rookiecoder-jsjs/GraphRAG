import service from './index'

export const documentApi = {
  // `tag` (optional) — when set, the backend filters by this tag (case- and
  // '#'-insensitive, normalised server-side).
  list: (tag) => service.get('/documents', { params: tag ? { tag } : {} }),

  upload: (file) => {
    const formData = new FormData()
    formData.append('file', file)
    return service.post('/documents/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })
  },

  delete: (id) => service.delete(`/documents/${id}`),

  getChunks: (id) => service.get(`/documents/${id}/chunks`),

  // Aggregated "knowledge unit" view: metadata + tags + chunk count +
  // sample chunks + key entities + related documents.
  getDetail: (id) => service.get(`/documents/${id}/detail`),

  // 2D PCA projection of all the user's documents (semantic "map").
  // Returns { points: [{doc_id, title, file_type, x, y}, ...] }.
  // Empty when the user has < 2 docs with chunks.
  getClusterMap: () => service.get('/documents/cluster-map'),

  // Connect to SSE progress stream
  getProgressStream: (docId) => {
    const token = localStorage.getItem('token')
    const eventSource = new EventSource(`/api/progress/${docId}?token=${token}`)
    return eventSource
  },

  // Get progress history. This is a normal axios call, so it already sends
  // the Authorization header via the request interceptor — the token must
  // NOT also be appended to the query string, where it would leak into
  // server/proxy access logs and the browser's Referer. (getProgressStream
  // keeps ?token= only because native EventSource cannot set headers.)
  getProgressHistory: (docId) => service.get(`/progress/${docId}/history`)
}
