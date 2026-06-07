import service from './index'

export const tagApi = {
  // User-wide tag rollup: distinct tags the user has used, with counts.
  // Sorted by count desc, tag asc server-side.
  listAll: (q) => service.get('/tags', { params: q ? { q } : {} }),

  // Tags attached to one document (alphabetical, server-normalised).
  getDocTags: (docId) => service.get(`/documents/${docId}/tags`),

  // Add a tag. Idempotent — server returns the full updated tag list so the
  // client can replace its local state in one round-trip.
  addDocTag: (docId, tag) => service.post(`/documents/${docId}/tags`, { tag }),

  // Remove a tag. Path-encoding is handled by the service layer.
  // Returns the updated tag list for the document.
  removeDocTag: (docId, tag) => service.delete(`/documents/${docId}/tags/${encodeURIComponent(tag)}`)
}
