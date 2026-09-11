# FEAT-022: 粘贴文本入库

- 状态: 已完成（接口与页面在代码中存在）
- 页面: `/documents`（DocumentsPage.vue 头部「粘贴入库」+ PasteTextForm.vue）
- API: `POST /api/documents/ingest-text`（201；422 空白/超长；500 行写入失败；端点在 `app/api/url_ingest.py`——摄取变体集中模块）
- 数据表: documents（复用，无新表）

## 功能说明

第三条摄取通道（文件 / URL / 剪贴板粘贴）：粘贴 Markdown 或纯文本（可带可选标题），后端 `clean_markdown` 清洗后写入 `UPLOAD_DIR/{doc_id}.md` 并以 `file_type='md'` 建 pending 文档行，派发与上传完全相同的后台管线（切块/向量/实体抽取 + SSE 进度 + 摄取闸）。

## 边界与设计取舍

- **落盘 .md → FEAT-017 重试可用**：`file_path` 非空，与 URL-HTML 文档（file_path=NULL、reprocess 409）形成对照；磁盘字节与派发给管线的字符串一致（clean_markdown 幂等，reprocess 重跑字节不变）
- **标题回退链**：显式标题 > 首个 `#` H1 > 「粘贴文档」；H1 回退无界，统一截 200 字
- **大小上限**：`TEXT_INGEST_MAX_CHARS`（默认 200000 字符）在 handler 内校验（pydantic Field 绑不了 settings，import 期读值会让测试的 cache_clear 失效）；字节级另有 15MB RequestBodyLimitMiddleware 兜底
- **限流**：独立 `SlidingWindowLimiter(10, 60)` 实例（key `text-ingest:{user_id}`）——URL 与粘贴各自 10/min，均为计费管线，故意不共享预算
- 全局粘贴监听（DocumentsPage onPaste）只认 `clipboardData.files`，与文本粘贴无冲突（已核实）
- clean_markdown 的既有顺序（先折叠 LF 空行、后 CRLF→LF）保持不动——共享工具，不在本轮回改

## 关联

- 依赖功能: FEAT-019（端点模式与表单镜像）、FEAT-017（重试）、FEAT-016（进度）
