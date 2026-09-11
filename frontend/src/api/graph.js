import service from './index'

export const graphApi = {
  // POST /api/graph/query — semantic + name fallback. Returns the
  // GraphQueryResponse envelope; we flatten to the visualization shape
  // so callers can use the same `{data.nodes, data.edges}` pattern as
  // getFullGraph() without caring about center_nodes / related_nodes.
  search: async (queryStr, depth = 2) => {
    const r = await service.post('/graph/query', { query: queryStr, depth })
    return { data: r.data?.visualization || { nodes: [], edges: [] } }
  },

  // GET /api/graph/visualization — full graph for the current user.
  getFullGraph: () => service.get('/graph/visualization'),

  // Find a single entity by exact name match. The list endpoint returns
  // an array; we collapse to `{found, entity}` so the merge-pill UI
  // can do `if (data?.found && data.entity)` without further matching.
  lookupEntity: async (name) => {
    const r = await service.get('/graph/entities', { params: { query: name } })
    const list = r.data || []
    const match = list.find((e) => e && e.name === name)
    return { data: match ? { found: true, entity: match } : { found: false } }
  },

  // PATCH an entity's type and/or description. Either or both fields
  // can be sent. Pass an empty string for `description` to clear it.
  updateEntity: (name, { entity_type, description }) => {
    return service.patch(
      `/graph/entities/${encodeURIComponent(name)}`,
      { entity_type, description }
    )
  },

  // Hard-delete an entity and clean up MENTIONS / RELATES_TO references.
  // 204 No Content on success; 404 if the entity doesn't exist.
  deleteEntity: (name) => {
    return service.delete(`/graph/entities/${encodeURIComponent(name)}`)
  },

  // Merge `source` into `target`. Source is deleted; all references
  // are re-pointed to target (with dedup). Accepts either two args
  // (source, target) or a single {source, target} object — GraphPage's
  // merge panel passes the object form, and passing it through the old
  // two-arg signature sent `{"source": {...}}` and got a 422 back.
  mergeEntities: (source, target) =>
    service.post(
      '/graph/entities/merge',
      source && typeof source === 'object' ? source : { source, target }
    ),

  // One-shot detail page payload for an entity. Returns
  //   { entity, stats, documents[], related_entities[], sample_chunks[] }
  // or null when the entity doesn't exist (the API responds 404; the
  // caller turns that into a "not found" page).
  getEntityDetail: (name) =>
    service.get(`/graph/entities/${encodeURIComponent(name)}/detail`),

  // FEAT-025: duplicate-entity suggestions grouped by case/punct variants.
  // Suggestions only — merging stays a manual, confirmed action.
  getDuplicates: () => service.get('/graph/entities/duplicates'),

  // Aliases recorded for one canonical entity (names it absorbed on merge).
  getEntityAliases: (name) =>
    service.get(`/graph/entities/${encodeURIComponent(name)}/aliases`),

  // Unbind one alias. POST body rather than DELETE + query param: `+` in a
  // query string decodes to a space and entity names are unconstrained.
  deleteAlias: (alias) => service.post('/graph/aliases/delete', { alias }),
}
