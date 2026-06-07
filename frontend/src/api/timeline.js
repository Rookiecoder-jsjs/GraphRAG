import service from './index'

export const timelineApi = {
  // Returns:
  //   documents_by_month: [{ month: "2025-10", count: 3 }, ...]
  //   recent_documents:   [{ id, title, original_filename, created_at }, ...] (last 10)
  //   entity_timeline:    [{ name, type, first_seen, first_seen_doc_id,
  //                          first_seen_doc_title, doc_count, mention_count }, ...]
  get: () => service.get('/timeline')
}
