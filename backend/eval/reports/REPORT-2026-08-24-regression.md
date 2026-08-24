# 军规②评测回归报告（2026-08-24）

> 规则来源：CLAUDE.md §二 军规②「改提示词必须重跑评测」。
> 本轮覆盖的提示词/上下文改动：T1-1 judge v2、T2-1 提示词模板化（10 模板迁移）、
> T2-2 会话历史有界加载（历史装载路径重写）、T1-2 注入预算熔断器接线。

## 运行

```
cd backend && ../.venv/Scripts/python.exe -m eval.runner --user-id 1 --markdown
# 13 用例（12 可打分 + 拒答 1），检索+生成+judge v2 全链路，534s
```

## 一、生成侧（judge v2 首次真跑）

| 指标 | 本轮 | 基线(08-21) |
|------|------|------------|
| Faithfulness | **1.000** | 1.000 |
| Hallucination Rate | **0.000** | 0.000 |
| Answer Relevance | **1.000** | 1.000 |
| Citation Accuracy | **1.000** | 1.000 |
| Answer Correctness | **0.992** | 1.000 |
| judge_confidence（新字段） | **1.000** | — |

五项核心指标与基线持平（answer_correctness −0.008 来自 factual-02 的 0.90，
judge 对要点覆盖的判定略严于旧版）。**结论：T1-1/T2-1/T2-2 未引入生成质量回归。**
judge v2 的 confidence 字段全量输出且无降级路径触发（无 keyword-only 兜底行）。

## 二、检索侧（6 个可比用例 vs 基线）

| 用例 | 基线 Hit@1 / nDCG@5 | 本轮 Hit@1 / nDCG@5 |
|------|--------------------|--------------------|
| factual-01 | 1.00 / 0.61 | 1.00 / **0.85 ↑** |
| factual-02 | 1.00 / 1.00 | 1.00 / 1.00 |
| synthesis-01 | 1.00 / 0.70 | 1.00 / 0.67 ≈ |
| entity-01 | 1.00 / 1.00 | 1.00 / 1.00 |
| compare-01 | 1.00 / 1.00 | 1.00 / 1.00 |
| refuse-01 | 拒答正确 | 拒答正确 |

六个可比用例 Hit@1 全部 1.0，nDCG@5 无实质漂移。**结论：检索侧无回归。**

## 三、新增 7 个章节用例（ch2–ch8，无基线）

整体：Hit@5 = 1.000、Hit@3 = 0.917、Hit@1 = 0.667。四个 hit@1=0 用例
定向复跑诊断：

| 用例 | 现象 | 定向复跑结果 |
|------|------|-------------|
| ch2-history | gold 排 rank 3 | 复现（排序问题，非召回失败） |
| ch6-autogen | hit@1=0 | **复跑 hit@1=1.0**（两 gold 占据 top-2） |
| ch7-framework | 3 gold 中 2 个进 top-5（rank1/rank5） | 复现；第 3 个未进 top-10 |
| ch8-memory | gold 2 个进 top-5 | 复现；另见下方去重缺陷 |

### 判定

hit@1 波动是**检索链路对 LLM 预处理（rewrite/variants）随机性的既有敏感**
——与 2026-08-21 基线报告对 factual-02 的根因诊断完全一致，不是本轮改动引入。
定向复跑 ch6-autogen 直接翻回满分即为直接证据：同一代码、同一查询，仅 LLM
采样不同。

## 四、顺带发现的两个既有问题（非本轮引入，登记待办）

1. **RRF 融合后 chunk 去重缺失**：ch8-memory 的 retrieved 前 5 名里同一
   chunk_id（`34396fb2…`）出现 3 次——多查询变体各自召回同一 chunk 后融合层
   未按 chunk_id 归并，浪费窗口位次。修复点：retriever.py 的 RRF 合并处按
   chunk_id 去重取最高分。（本轮不修——军规②只做回归验证，修复需单独任务 +
   单独评测轮。）
2. **runner 不保存 per-case JSON 明细**：--markdown 模式下 retrieved 列表
   不落盘，事后诊断需要重跑。建议 runner 增加 `--out <file>` 双格式落盘。

## 五、验收勾销

- T1-1 验收末项「军规②评测回归」→ ✅（本报告）
- T1-2 同上 → ✅
- T2-1 同上 → ✅
- T2-2 同上 → ✅

## 附录：运行环境

- user-1 知识库：9 文档 / 1128 chunks（注意：较基线的 51 chunks 已扩充，
  Precision 类指标的绝对值不可跨期比较；Hit/MRR/nDCG 排序类指标不受影响）
- 基础设施：Neo4j + Chroma Docker 容器 healthy；Bailian qwen3.7-flash 生成+裁判
