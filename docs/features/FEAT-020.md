# FEAT-020: 引用溯源跳转

- 状态: 已完成（页面行为在代码中存在）
- 页面: `/chat`（ChatPage.vue——来源卡片标题可点击跳转）
- API: 无新端点（复用既有 sources 数据与 `/documents/:id` 路由）
- 数据表: 无新表（复用 message_sources / SSE sources 帧）

> ⚠️ **模型例外条目**：本条目按其性质豁免「1 功能 = 1 页面 + 1 API + 1 表」约束——它是纯前端交互增强，与 FEAT-012 共享 ChatPage（存量共享先例）。引用 [N] 点击展开来源卡片在此前已存在，本条目补齐最后一跳。

## 功能说明

阅读 RAG 回答时可溯源到原文档：展开的来源卡片中，标题可点击（有 hover 下划线与「查看文档详情」提示）→ `router.push` 到 `/documents/{document_id}` 文档详情页。`document_id` 缺失（历史会话 sources 未持久化）时不可点；文档已被删除由 DocumentDetailPage 既有 404 错误态兜底，不做预检请求。

## 关联

- 依赖功能: FEAT-012（消息反馈与引用来源展示）、FEAT-004（文档详情页）
