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

  // FEAT-017: re-run the ingestion pipeline for a FAILED document. The
  // backend returns 202 with the refreshed row (status back to 'pending').
  reprocess: (id) => service.post(`/documents/${id}/reprocess`),

  // FEAT-019: fetch a public URL and ingest it through the upload pipeline.
  // Returns 201 with the new document row (status 'pending'); 400 for a
  // blocked (SSRF) URL, 502 for fetch failures, 415 for unsupported types.
  ingestUrl: (url) => service.post('/documents/ingest-url', { url }),

  // FEAT-022: paste markdown/text directly; the backend stores it as a .md
  // file so FEAT-017 reprocess works. 201 + the new pending document row;
  // 422 for blank/oversized content.
  ingestText: (title, content) =>
    service.post('/documents/ingest-text', { title: title || undefined, content }),

  // Aggregated "knowledge unit" view: metadata + tags + chunk count +
  // sample chunks + key entities + related documents.
  getDetail: (id) => service.get(`/documents/${id}/detail`),

  // 2D PCA projection of all the user's documents (semantic "map").
  // Returns { points: [{doc_id, title, file_type, x, y}, ...] }.
  // Empty when the user has < 2 docs with chunks.
  getClusterMap: () => service.get('/documents/cluster-map'),

  // Get progress history. This is a normal axios call, so it already sends
  // the Authorization header via the request interceptor — the token must
  // NOT also be appended to the query string, where it would leak into
  // server/proxy access logs and the browser's Referer. (The SSE progress
  // stream keeps ?token= only because native EventSource cannot set headers.)
  getProgressHistory: (docId) => service.get(`/progress/${docId}/history`)
}
