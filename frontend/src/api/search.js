import service from './index'

export const searchApi = {
  search: (query, topK = 5, includeContext = true) => {
    return service.post('/search', {
      query,
      top_k: topK,
      include_context: includeContext
    })
  }
}
