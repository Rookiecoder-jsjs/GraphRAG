# FEAT-018: 反馈评测用例闭环

- 状态: 已完成（接口与页面在代码中存在）
- 页面: `/eval`（EvalCasesPage.vue，管理页）+ ChatPage.vue（👎 后「存为评测用例」入口）
- API: `/api/eval/cases`（GET 列表 / POST 手动新建 / POST `/from-message` 反馈转换 / PATCH 部分更新 / DELETE）
- 数据表: eval_cases（SQLite，含部分唯一索引 `uq_eval_cases_source`）

## 功能说明

把用户反馈变成活的评测集：对话中对回答点 👎 → 点「存为评测用例」→ 后端取该 assistant 消息**前一条 user 提问**作为 `query`、其 `message_sources`（按 rank 排序，NULL 靠后）作为 `expected_chunk_ids`、打 `user-feedback` 标签 → 落 `eval_cases` 表。管理页可查看/新建/启停/删除；`eval.runner` 默认把 DB 用例与静态 gold JSON 合并评测，`--no-db` 退回纯文件行为。触发 → 转换 → 门禁回归（军规②）用上真实用户痛点。

**幂等**：转换靠部分唯一索引 `uq_eval_cases_source (source_message_id) WHERE NOT NULL` 承载——并发双击时败者捕获 IntegrityError 回读胜者行，返回 200 + `created:false`，不是"先查后插"。

**合并规则**（eval/db_cases.py）：文件用例在前、db 用例在后；归一化 query（strip+lower）重复时 **db 优先**，被弃文件用例计入 `summary["skipped_duplicate_file_cases"]`；db 用例 id 前缀 `db-` 天然不与文件 id 冲突；表缺失/为空时行为与现状完全一致。

## 边界与已知限制

- `expected_chunk_ids` 引用的 chunk 可能随文档删除失效——runner 指标自然归 0，不做引用完整性校验（用例不随语料失效自动失效）
- 评测用例按 user_id 隔离，runner `--user-id N` 只加载该用户的用例
- 消息无前置 user 提问（如新会话首条即 assistant）→ 409

## 关联

- 测试: `backend/tests/test_eval_cases.py`、`backend/tests/test_eval_db_merge.py`
- 依赖功能: FEAT-012（消息反馈，👎 数据来源）、FEAT-001 检索服务、GUIDE-002（RAG 测评流程）
