import service from './index'

// FEAT-018: 评测用例管理 —— 把 👎 反馈沉淀为 RAG 回归门禁的 gold 用例。
export const evalApi = {
  // 列出当前用户的评测用例；`enabled`(可选 true/false) 过滤。
  // 每项: { id, query, expected_chunk_ids[], expected_keywords[],
  //         expected_answer, difficulty, tags[], enabled, created_at }
  listCases: (enabled) =>
    service.get('/eval/cases', {
      params: enabled === undefined || enabled === null ? {} : { enabled }
    }),

  // 手动新建用例。query 必填（纯空白会被 422 拒绝）。
  createCase: (payload) => service.post('/eval/cases', payload),

  // 把一条（通常点了 👎 的）assistant 消息转为评测用例。服务端幂等：
  // 重复转换返回 created:false + 既有用例；404=消息不存在/非本人，
  // 409=消息前面没有 user 提问。
  createFromMessage: (messageId) =>
    service.post('/eval/cases/from-message', { message_id: messageId }),

  // 部分更新（只传要改的字段）。
  updateCase: (id, patch) => service.patch(`/eval/cases/${id}`, patch),

  deleteCase: (id) => service.delete(`/eval/cases/${id}`)
}
