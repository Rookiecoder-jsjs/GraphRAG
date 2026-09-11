# FEAT-019: URL 摄取

- 状态: 已完成（接口与页面在代码中存在）
- 页面: `/documents`（DocumentsPage.vue 头部「网页导入」切换 + UrlIngestForm.vue 表单）
- API: `POST /api/documents/ingest-url`（201；400/415/422/502/500）
- 数据表: documents（复用）；存储层: Chroma/Neo4j/BM25 经既有摄取管线复用

## 功能说明

粘贴一个网页链接即可入库：DocumentsPage 头部「网页导入」展开表单 → 提交 URL → 后端 `services/url_fetcher.py` 抓取并按 Content-Type 分派（html/xhtml → readability 提取正文 + html2text 转 Markdown；pdf → 字节落盘走 anydoc；text/plain、text/markdown → 落盘直读；其余 415）→ 建 `pending` 行 → 派发与文件上传完全相同的后台管线。摄取闸排队、SSE 进度弹窗、失败清理 `_cleanup_partial_document` 全部免费复用。

**SSRF 防护链**（url_fetcher.py，逐跳强制）：
1. scheme 白名单（`URL_ALLOWED_SCHEMES`，默认 http/https）、拒绝 userinfo、host 必填
2. `getaddrinfo` 解析**全部**地址，任一命中 private/loopback/link-local/reserved/multicast/unspecified 即拒（IPv4-mapped IPv6 按 IPv4 半段判定）
3. 禁自动重定向，每个 3xx Location 手动拼接后重跑 1+2，上限 `URL_FETCH_MAX_REDIRECTS`
4. 响应体流式读取，超过 `URL_FETCH_MAX_BYTES` 立即中断

## 边界与已知限制

- **DNS rebinding TOCTOU**（校验后连接时再解析）为接受已知的限制——ip pinning 破坏 TLS SNI/虚拟主机，得不偿失；docstring 已写明
- HTML 源不落原始盘：`file_path=NULL`、`file_type=html`，因此**不可 reprocess**（FEAT-017 会答 409）；删除已适配 NULL file_path
- 抓取失败语义：非法/被拒 URL→400，DNS/超时/HTTP 错误/超限/重定向循环→502，类型不支持→415，提取不出正文→422
- 大页面 readability 抽取 CPU 密集，走 `asyncio.to_thread`；字节上限先于解析
- 限流：每用户 10 次/分钟（`url_ingest_limiter`）

## 关联

- 测试: `backend/tests/test_url_ingest.py`（SSRF 单测 + MockTransport 抓取分派 + 端点）
- 依赖功能: FEAT-002（文档上传与解析——共享摄取管线）、FEAT-016（处理进度）
- 新依赖: readability-lxml==0.9、html2text==2025.4.15（根 requirements.txt）
