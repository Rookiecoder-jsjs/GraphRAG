import service from './index'

export const chatApi = {
  send: (message, conversationId, includeContext = true, useGraphRag = false, compareMode = false) => {
    return service.post('/chat', {
      message,
      conversation_id: conversationId,
      include_context: includeContext,
      use_graph_rag: useGraphRag,
      compare_mode: compareMode
    })
  },

  // `signal` lets the caller abort an in-flight stream (e.g. when the page
  // is deactivated) - without it a navigated-away chat keeps pulling tokens
  // in the background until the provider finishes.
  stream: (message, conversationId, useGraphRag = false, compareMode = false, enableThinking = false, signal = undefined) => {
    const token = localStorage.getItem('token')
    return fetch('/api/chat/stream', {
      method: 'POST',
      signal,
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      },
      body: JSON.stringify({
        message,
        conversation_id: conversationId,
        include_context: true,
        use_graph_rag: useGraphRag,
        compare_mode: compareMode,
        enable_thinking: enableThinking
      })
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
