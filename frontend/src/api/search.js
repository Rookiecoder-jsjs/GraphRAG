import service from './index'

export const searchApi = {
  search: (query, topK = 5, includeContext = true, useGraphRag = false) => {
    return service.post('/search', {
      query,
      top_k: topK,
      include_context: includeContext,
      use_graph_rag: useGraphRag
    })
  },

  // FEAT-024: 全价检索管线调试。绕过缓存（后端 debug=True），
  // 返回 {chunks, entities, relations, debug, titles}。
  searchDebug: (query, topK = 5, useGraphRag = false) => {
    return service.post('/search/debug', {
      query,
      top_k: topK,
      use_graph_rag: useGraphRag
    })
  }
}
