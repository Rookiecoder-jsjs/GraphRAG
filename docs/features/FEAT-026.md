# FEAT-026: 检索过滤（tag/document 限定检索范围）

- 状态: 已完成（接口与页面在代码中存在）
- 页面: `/search`（SearchPage.vue 增标签范围下拉；SearchDebugPage 透传）
- API: `/api/search`、`/api/search/debug`、`/api/chat`、`/api/chat/stream` 请求体加性字段 `document_ids` / `tag`（无新端点）
- 数据表: （无新表，复用 `document_tags`）

## 功能说明

文档一多，"只在这几篇/这个标签里找答案"是刚需。请求带 `tag`（标签名，
大小写/`#` 不敏感）或 `document_ids`（显式列表，≤50），检索的**全部通道**
（向量、BM25、图谱、社区）只在范围内召回；两者同时给取交集。范围解析为
空（未知标签/交集为空）→ 直接空结果，不跑计费管线、不占准入闸。

## 关联

- 依赖功能: FEAT-001（MCP server 调 retrieve 自动受益）、FEAT-003（检索管线）
- 实现要点（改代码前必读）：
  - **缓存键第 5 位**：`None`（无过滤）或 `sha1(sorted ids)`。`None` 与
    `[]` 语义不同（全库 vs 空），绝不能碰撞——原设计用 `""` 哨兵会让两者
    同哈希（评审发现，未上线即修）。
  - chroma `_where_for`：`$and`+`$in` 组合；**空列表必须 raise**——chroma
    `$in: []` 本身抛错，且静默放宽为"无过滤"会泄全库。调用方（API 层解析
    scope、retrieve 对空白列表短路）保证空范围永不进 chroma。
  - bm25：三个构建入口（build/add/remove）都维护 `chunk_doc` 来源映射；
    `search` **先 mask 后取 top_k**（分数全量算出，范围外的命中不许挤掉
    范围内的）；无来源映射的索引在过滤下返回空（过滤永不放宽）。
  - 图通道在 chroma metadata 上过滤（Neo4j Chunk 节点无 document_id 属性
    ——见 neo4j_client.py 模式说明）；扩展阶段天然同文档，零改动。
  - `search_scope.resolve_document_ids_for_filter`：**未知 tag = 空列表而
    非 None**（把未知标签放宽成全库是最坏失败）；`normalize_tag` 镜像
    documents API 语义，注释声明两者必须同步。
  - 测试: `tests/test_search_scope.py`、`test_bm25.py::TestDocumentFilter`、
    `test_retriever_cache.py`（5 元组）、`test_search_debug.py`（线程化）。

## 手工冒烟清单

1. SearchPage 选标签 → 搜索结果全部来自该标签文档；头部显示"范围「x」"。
2. 切回"全部文档" → 行为与改造前一致。
3. 调试模式带标签跳转 → debug config.document_filter 显示范围。
4. Chat 带 document_ids 请求（API 层）→ 引用仅来自范围内文档。
