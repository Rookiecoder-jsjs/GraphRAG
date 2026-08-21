# GUIDE-002: RAG 测评流程——检索与生成两段式评估

- 状态: 已完成
- 日期: 2026-08-21
- 相关: [ADR-001](../adr/ADR-001.md)（混合检索）、[ADR-005](../adr/ADR-005.md)（多查询）、[ADR-006](../adr/ADR-006.md)（图谱引导检索）

## 背景

RAG 产品的质量评估必须拆成两段：**检索**（资料找没找对）与**生成**（基于资料答得好不好）。只测最终答案无法定位问题出在知识库、检索链路还是模型生成。本指南定义本项目的测评流程、指标与运行方式。

## 指标框架

### 检索侧（资料有没有找对）

| 指标 | 含义 | 实现 |
|------|------|------|
| Hit@K | 前 K 条是否命中至少一条正确资料 | `backend/eval/metrics.py` |
| Recall@K | 该找的资料是否被召回 | 同上 |
| Precision@K | 找出来的资料里有多少真相关 | 同上 |
| MRR | 正确答案排得靠不靠前 | 同上 |
| nDCG@K | 综合精度与排序的单值总结 | 同上 |

### 生成侧（基于资料答得好不好）

| 指标 | 含义 | 实现 |
|------|------|------|
| Faithfulness | 回答是否忠实于检索资料（claims 支持比例） | `backend/eval/judge.py`（LLM-as-judge） |
| Hallucination Rate | 1 − Faithfulness，编造内容的比例 | 同上 |
| Answer Relevance | 回答是否切题 | 同上 |
| Citation Accuracy | [N] 引用是否真的支撑结论 | 同上 |
| Answer Correctness | 对照期望答案判断正误 | 同上 |

生成侧采用 **LLM-as-judge**：把「问题 + 回答 + 检索到的资料 + 期望答案」交给 LLM 裁判，一次调用返回结构化 JSON 打分（JSON 解析失败降级为 `keyword_coverage`，不中断流程）。

## Gold 用例（真实数据）

基于 user-1 知识库（AI 智能体书籍内容）构造 6 个真实用例，见 `backend/eval/gold/`：

| 用例 | 问题 | 类型 | 难度 |
|------|------|------|------|
| factual-01 | 什么是智能体？ | 单文档事实 | easy |
| factual-02 | 智能体应用有哪两种协作模式？ | 单文档事实 | easy |
| synthesis-01 | Workflow 和 Agent 有什么区别？ | 跨 chunk 综合 | medium |
| entity-01 | Trae 是什么？ | 实体锚定（graph-rag） | medium |
| compare-01 | Copilot 和 Trae 有什么不同？ | 对比 | medium |
| refuse-01 | 如何配置 Kubernetes 集群？ | 拒答（知识库外） | hard |

## 运行方式

```bash
cd backend
# 检索侧（不调生成 LLM，快）
../.venv/Scripts/python.exe -m eval.runner --user-id 1 --no-llm --markdown
# 完整流程（检索 + 生成 + LLM 打分）
../.venv/Scripts/python.exe -m eval.runner --user-id 1 --markdown
# 对比实验：图谱引导检索开/关
../.venv/Scripts/python.exe -m eval.runner --user-id 1 --use-graph-rag --json
```

前置：Neo4j + ChromaDB 已启动（`docker compose up -d`）、后端可用、`.env` 已配置 LLM API key。

## 基准结果（2026-08-21，user-1 知识库）

### 基线（混合检索，graph-rag 关）

| 指标 | 值 | 说明 |
|------|-----|------|
| Hit@1 / Hit@5 | 1.000 / 1.000 | 首条即命中正确答案（5/5 用例） |
| MRR | 1.000 | 正确 chunk 均排第一 |
| Recall@5 | 0.833 | 部分多 chunk 用例未全召回 |
| Precision@5 | 0.280 | 前 5 条约 28% 相关（知识库小、噪音占比高） |
| nDCG@5 | 0.863 | |
| Faithfulness / 幻觉率 | 1.000 / 0.000 | LLM 忠实作答，无编造 |
| Answer Relevance / Citation Accuracy / Correctness | 全部 1.000 | |

### 对比实验：graph-rag 开 vs 关（完整流程）

| 指标 | 关（基线） | 开 | 差异 |
|------|-----------|-----|------|
| Hit@1 | 1.000 | 0.800 | ↓ factual-02 掉到 0 |
| MRR | 1.000 | 0.800 | ↓ |
| Recall@5 | 0.833 | 0.633 | ↓ |
| nDCG@5 | 0.863 | 0.676 | ↓ |
| Faithfulness | 1.000 | 1.000 | 持平（答案仍忠实） |

> ⚠️ **结论修正（2026-08-21 诊断）**：上表差异**不能归因于 graph-rag 开关**，是**单次运行的统计假象**。
>
> 诊断证据（全部实测）：
> 1. 实体抽取对「协作模式」类问题 **5 次全部返回空 `[]`** → 图谱通道未激活（retriever 要求实体非空才进图谱分支），graph 开关对该次检索结果**无影响**；
> 2. query rewrite 对同一查询 **5 次输出全部漂移**，且方向性偏向「多智能体协作 vs 单智能体」（1.4.2 节），而 gold chunk 属于 1.4 节「工具型 vs 协作者型」——一次坏漂移即可让 embedding 检索整体带偏；
> 3. graph ON 单独复现 **5 次 4 次 hit@1=1.0**，与 OFF 无显著差异。
>
> **根因**：检索质量对 LLM 预处理（rewrite/variants）的输出高度敏感，评测单次运行撞上坏漂移就得到误导性结论。
> **正确做法**：多次运行取均值（runner 增加 `--repeat N` 参数），或 rewrite/variants 用 `temperature=0` 降低漂移。
> **不受影响的结论**：Precision@5=0.28 偏低为固有特性（召回窗口 RERANK_RECALL_K=25 × 多查询 × 扩展步骤 + 知识库主题同质），非 graph-rag 所致。

## 问题定位速查

| 现象 | 看哪个指标 | 迭代方向 |
|------|-----------|---------|
| 答案完全错 | Hit@K / Recall@K 低 | 优化知识库、chunk、召回 |
| 找到了文档但答错 | Faithfulness 低 | 强化 Prompt、加引用校验 |
| 引用错文档 | Citation Accuracy 低 | 引用定位校验 |
| 答非所问 | Answer Relevance 低 | 意图路由、Prompt |
| 编造内容 | Hallucination Rate 高 | 收紧拒答边界、加置信度 |
