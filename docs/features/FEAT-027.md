# FEAT-027: 证据兜底（CRAG-lite）

- 状态: 已完成（接口与页面在代码中存在）
- 页面: `/chat`（ChatPage.vue 证据不足徽标）
- API: `/api/chat`、`/api/chat/stream` 响应加性键 `evidence_level: "low"`（normal 不带键）；`done` SSE 帧同
- 数据表: （无新表）

## 功能说明

CRAG 的洞见收敛到本管线可零成本行动的部分：交叉编码重排已经为每个
expanded chunk 算出 `relevance_score`。当**全部**命中低于地板
（`EVIDENCE_FLOOR=0.30`，刻意低于 medium 带 0.40，只拦 hopeless 档）时，
chat 不再调用 LLM 硬答，而是返回 `insufficient_evidence.md` 的静态文案，
防幻觉；低相关参考（sources）照常返回与落库，用户能看到"找到了什么、
为什么不够"。前端在消息上打"证据不足"徽标。

## 关联

- 依赖功能: FEAT-003（重排序产生 relevance_score）、FEAT-013（检索管线）
- 实现要点（改代码前必读）：
  - `services/evidence.py::grade_evidence` 三态：空 chunks → `low`；
    **无数值 relevance_score（rerank 失败回退 RRF 序）→ `unknown` 走现
    行为**——把 provider 抖动误判成低证据会打断正常聊天，这是 load-bearing
    的设计；max ≥ floor → `normal`。
  - guard 只在 `fact_retrieval 且 include_context=True` 时判定——
    `include_context=False` 是用户显式不要检索，chitchat/should_reject
    有各自分支，都不许被兜底拦下。
  - 流式插入点在 sources 帧**之后**、prompt 构建之前（检索已完整 await）；
    静态文本走一个 `chunk` 帧，保存后 done 帧带 `evidence_level`。
  - **不做二次检索**：管线已含改写+多查询，立即重跑全价买不到什么——
    本版本只做"分级 + 诚实兜底"（性价比约束）；阈值可配置、可整体关闭。
  - eval 不经 chat handler（runner 直连 build_rag_context +
    generate_rag_response），门禁数字可比。
  - 测试: `tests/test_evidence_guard.py`（三态纯函数/双路径帧序/不调 LLM/
    sources 照常/落库/开关关闭/unknown 不触发）。

## 手工冒烟清单

1. 问一个语料外问题（如"量子计算的原理解释一下"若库内无此内容）→
   静态兜底文案 + 参考列表 + "证据不足"徽标。
2. 正常语料内问题 → 回答行为与之前逐字段一致，无徽标。
