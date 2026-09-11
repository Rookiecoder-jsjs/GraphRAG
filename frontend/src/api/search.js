import service from './index'

export const searchApi = {
  // FEAT-026: `tag` / `documentIds` 是加性可选参数——不传（null）时请求体
  // 不含该字段，后端按"全库"处理，老调用方行为不变。
  search: (query, topK = 5, includeContext = true, useGraphRag = false, tag = null, documentIds = null) => {
    const body = {
      query,
      top_k: topK,
      include_context: includeContext,
      use_graph_rag: useGraphRag
    }
    if (tag) body.tag = tag
    if (documentIds) body.document_ids = documentIds
    return service.post('/search', body)
  },

  // FEAT-024: 全价检索管线调试。绕过缓存（后端 debug=True），
  // 返回 {chunks, entities, relations, debug, titles}。
  searchDebug: (query, topK = 5, useGraphRag = false, tag = null) => {
    const body = {
      query,
      top_k: topK,
      use_graph_rag: useGraphRag
    }
    if (tag) body.tag = tag
    return service.post('/search/debug', body)
  }
}
