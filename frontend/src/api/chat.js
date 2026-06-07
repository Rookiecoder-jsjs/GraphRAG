import service from './index'

export const chatApi = {
  send: (message, conversationId, includeContext = true, useGraphRag = false, compareMode = false, withFollowups = true) => {
    return service.post('/chat', {
      message,
      conversation_id: conversationId,
      include_context: includeContext,
      use_graph_rag: useGraphRag,
      compare_mode: compareMode,
      with_followups: withFollowups
    })
  },

  stream: (message, conversationId, useGraphRag = false, compareMode = false) => {
    const token = localStorage.getItem('token')
    return fetch('/api/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      },
      body: JSON.stringify({
        message,
        conversation_id: conversationId,
        include_context: true,
        use_graph_rag: useGraphRag,
        compare_mode: compareMode
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
