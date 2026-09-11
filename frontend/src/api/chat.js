import service from './index'

export const chatApi = {
  // FEAT-026: `tag` / `documentIds` 是加性可选参数（检索范围限定）——不传时
  // 请求体不含该字段，后端按"全库"处理。放在既有位置参数之后，老调用方
  // （按位传参）不受影响。
  send: (message, conversationId, includeContext = true, useGraphRag = false, compareMode = false, tag = null, documentIds = null) => {
    const body = {
      message,
      conversation_id: conversationId,
      include_context: includeContext,
      use_graph_rag: useGraphRag,
      compare_mode: compareMode
    }
    if (tag) body.tag = tag
    if (documentIds) body.document_ids = documentIds
    return service.post('/chat', body)
  },

  // `signal` lets the caller abort an in-flight stream (e.g. when the page
  // is deactivated) - without it a navigated-away chat keeps pulling tokens
  // in the background until the provider finishes.
  stream: (message, conversationId, useGraphRag = false, compareMode = false, enableThinking = false, signal = undefined, tag = null, documentIds = null) => {
    const token = localStorage.getItem('token')
    const body = {
      message,
      conversation_id: conversationId,
      include_context: true,
      use_graph_rag: useGraphRag,
      compare_mode: compareMode,
      enable_thinking: enableThinking
    }
    if (tag) body.tag = tag
    if (documentIds) body.document_ids = documentIds
    return fetch('/api/chat/stream', {
      method: 'POST',
      signal,
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      },
      body: JSON.stringify(body)
    })
  },

  getConversations: () => service.get('/chat/conversations'),

  getMessages: (conversationId) =>
    service.get(`/chat/conversations/${conversationId}/messages`),

  deleteConversation: (conversationId) =>
    service.delete(`/chat/conversations/${conversationId}`),

  submitFeedback: (messageId, rating, note = null) =>
    service.post(`/chat/messages/${messageId}/feedback`, { rating, note }),

  getFeedback: (messageId) =>
    service.get(`/chat/messages/${messageId}/feedback`),

  deleteFeedback: (messageId) =>
    service.delete(`/chat/messages/${messageId}/feedback`)
}
