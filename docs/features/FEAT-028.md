# FEAT-028: 全局问答（社区摘要 + 全局检索通道）

- 状态: 已完成（接口与页面在代码中存在）
- 页面: `/graph/global`（GraphGlobalPage.vue；ChatPage 增 ?q=&graph=1 预填）
- API: `/api/graph` 前缀下 `GET /communities`、`POST /communities/rebuild`
- 数据表: `graph_communities`（第 15 张）；存储层另有 Chroma 第二集合
  `community_summaries`

## 功能说明

对标 GraphRAG 的全局搜索：离线把用户实体图按 RELATES_TO 边做确定性社区
划分（networkx greedy modularity），每个社区一次 LLM 调用生成主题标题+
摘要；查询时把 query 与社区摘要做向量匹配，取 top-2 社区，将其**成员实体
的 chunk**（真实可引用）作为第 5 条检索通道送入 RRF 融合。"这批文档整体
讲了什么"类问题从此有据可答。

## 关联

- 决策记录: ADR-010（为什么是成员 chunk 而非摘要上 prompt；为什么 rebuild
  不占查询闸）
- 依赖功能: FEAT-014/007（图谱）、FEAT-026（社区通道沿用 document 范围
  过滤）、FEAT-003（检索管线）
- 实现要点（改代码前必读）：
  - `graph_community.group_entities` 纯函数、端到端确定性（CNM 无 RNG、
    成员排序、按总提及降序截断）——同一边集永远分出同一批社区，测试可钉。
  - **chroma delete 必须先于 upsert**：社区向量 id 是位置编号
    （community_{user_id}_{n}），缩容后不删会残留可被检索到的过期向量。
    SQLite 行在向量落库**之后**替换——中途失败页面仍与上次构建自洽。
  - LLM 摘要失败兜底为"主题 N"+成员拼接——社区照常入库可用，重建永不
    因 summarizer 打嗝而失败。JSON 解析用模块局部 regex（judge.py 先例，
    llm 的 `_extract_json_object` 是实例方法不可导入）。
  - 通道成本：未建社区时仅一次索引 COUNT（在 use_graph_rag 分支内）；失败
    记 degraded "community_skipped"。`COMMUNITY_TOP_K=2`、成员并集 ≤30。
  - rebuild **不占查询闸**（闸是检索准入队列，长任务占槽会饿死检索），
    用 `communities_limiter` 5/300s + 构建内 Semaphore(8)。
  - stale 判定：`count_user_entities` ≠ 构建时 `entity_count_at_build` →
    页面"图谱有更新，建议重建"。
  - ChatPage `?q=&graph=1` 只预填不自动发送；消费后 replace 摘参数。
  - 测试: `tests/test_graph_community.py`（分组确定性/兜底/删序/范围过滤/
    API/retriever 通道 debug kind）。

## 手工冒烟清单

1. `/graph/global` → 构建社区 → 出主题卡片（标题/摘要/成员 chips）。
2. 点成员 chip → 实体详情；点"就此主题提问" → ChatPage 预填 + 图谱开。
3. 发送 → 回答可引用；`/search/debug` 开图谱跑同题 → 通道表出现
   "community"。
4. 上传新文档后回到全局页 → 出现"图谱有更新，建议重建"。
