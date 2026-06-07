import service from './index'

export const dashboardApi = {
  // Returns:
  //   stats:            { documents, chunks, entities, relations,
  //                       conversations, messages, tags }
  //   recent_activity:  [{ kind, id, title, created_at, conversation_id?,
  //                        conversation_title?, role? }, ...] (max 10, mixed)
  //   top_entities:     [{ name, type, mention_count, doc_count }, ...] (max 10)
  //   top_tags:         [{ tag, count }, ...] (max 10)
  //   growth:           [{ month, count }, ...] (always 6 buckets,
  //                       contiguous, oldest first; missing months = 0)
  getSummary: () => service.get('/dashboard/summary')
}
