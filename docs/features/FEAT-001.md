# FEAT-001: 知识库 MCP Server

- 状态: 已完成（接口在代码中存在：`backend/mcp_server/server.py`）
- 页面: 无（stdio 进程，无前端页面）
- API: MCP 工具面 `search_knowledge` / `search_graph` / `get_entity_detail` / `list_documents`（stdio JSON-RPC，非 HTTP 端点）
- 数据表: documents（SQLite 只读）；存储层: Neo4j / Chroma（只读）

## 功能说明

任何 MCP 客户端（Claude Desktop / Claude Code / codex）经 stdio 协议直接查询知识库：语义检索、图谱探索、实体详情、文档清单。触发 → 客户端拉起子进程并握手 → 四个只读工具返回结果。`KG_MCP_TOKEN` 缺失或为占位符时进程拒绝启动。所有工具显式携带 `user_id` 做跨用户隔离。

## 关联

- 决策记录: GUIDE-003 T2-3（设计来源，Codex mcp-server/ 参考）
- 依赖功能: 检索服务（retriever）、图谱服务（neo4j_client）
