# FEAT-017: 文档失败重试

- 状态: 已完成（接口与页面在代码中存在）
- 页面: `/documents`（DocumentsPage.vue——failed 行的状态徽标 + 重试按钮）
- API: `POST /api/documents/{doc_id}/reprocess`（202；401/404/409/422/500）
- 数据表: documents（复用，status 状态机）+ progress_history（复用，重试时清空）

## 功能说明

处理失败的文档可以一键重新处理：用户在文档列表看到红色「处理失败」徽标（悬停显示原因）→ 点重试按钮确认 → 后端校验（属主 → status=='failed' → 原文件存在 → 重新转换出非空文本）→ `reset_for_retry` 复位为 `pending` → 清空该文档旧进度事件 → 派发与上传一致的摄取管线（切块/embed/图谱/实体提取），前端进度弹窗从头跟踪。

**关键设计**：reset 成功后必须删除该文档的 `progress_history` 旧行——进度 SSE 从 id 0 全量重放并在首条终态帧（complete/error）关流，不清旧 error 帧则新打开的进度弹窗会重放上一次的失败并挂断。

## 边界与已知限制

- 仅 `failed` 状态可重试（`ready` 走删除重传；与 `doc_status.reset_for_retry` 语义一致）
- URL 摄取文档（`file_path=NULL`）无法重试 → 409「原文件丢失」
- 转换在请求内同步执行（大文件秒级阻塞该请求），与 upload 行为一致
- 失败清理是 best-effort：若 Neo4j 清理曾失败，重跑靠 UNWIND+MERGE 幂等覆盖
- 双击重试由状态前置校验 + `reset_for_retry` 异常双保险兜底为 409

## 关联

- 测试: `backend/tests/test_document_retry.py`
- 依赖功能: FEAT-002（文档上传与解析）、FEAT-016（处理进度）
