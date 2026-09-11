# FEAT-024: 检索调试台

- 状态: 已完成（接口与页面在代码中存在）
- 页面: `/search/debug`（SearchDebugPage.vue）
- API: `POST /api/search/debug`（另 `SearchRequest` 增可选字段 `use_graph_rag`）
- 数据表: 无新表（只读诊断，豁免三件套中的表——复用检索管线内存态）

## 功能说明

调参者在调试页输入查询（可选 top_k、图谱通道开关）→ 后端以 `debug=True`
跑一次与 `/api/chat`、`/api/search` 完全相同的检索管线（改写→多查询→三通道
召回→RRF→重排序→扩展→富集）→ 页面按六个阶段卡片展示全部中间产物：改写
对照、每通道命中与得分、融合排名与来源通道、重排序前后、扩展来源（种子/
邻居/同节）、耗时条与降级事件。用于回答"哪个通道把答案顶掉了""改写是否
生效"这类调参问题，与军规②的端到端指标互补。

## 关联

- 依赖功能: FEAT-013（语义搜索共用管线）、FEAT-010（AI 对话共用管线）
- 决策记录: ADR-001/002/004/005/006（管线各阶段）、ADR-009（查询闸）
- 实现要点（改代码前必读）：
  - debug 数据由 `app/services/retrieval/debug.py` 的 `DebugCollector`
    收集；`retrieve(..., debug=True)` 时 `cache_key=None`——**缓存读跳过**
    （计时真实）且**写被守卫**：调试快照绝不进入被缓存共享的结果对象
    （`retriever.py` 返回值按引用共享给缓存命中方）。
  - 三个早退分支（嵌入失败 / 融合为空 / 正常终点）全部合并 debug。
  - 通道命中快照截 25 条/通道、preview ≤160 字符；扩展来源记在旁路 map，
    不污染 chunk dict。
  - 一次调试 = 全价真实运行：绕过缓存，占查询闸槽位 + `search-debug` 限流
    配额（与 `search` 分开计键）。
  - `titles` 由 API 层从 debug 各结构收集 `document_id` 后单次 IN 查询得到；
    BM25-only 命中无 metadata，titles 容忍不全，前端兜底显示 chunk_id。
  - 测试: `tests/test_search_debug.py`（快照形状/截断/缓存双向旁路/早退分支/
    端点 titles/429/模型兼容）。

## 手工冒烟清单

1. 普通搜索 `/search` 契约不变（老客户端不传 `use_graph_rag` 行为不变）。
2. `/search/debug` 输入查询 → 六段卡片齐全；开图谱通道后出现 graph 通道。
3. 搜索页"调试模式"按钮带当前查询跳转并自动运行。
