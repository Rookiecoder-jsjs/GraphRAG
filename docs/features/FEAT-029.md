# FEAT-029: 评测 judge 提示词回归模板单一来源

- 状态: 已完成（接口与页面在代码中存在）
- 页面: （无新页面——EvalRunsPage 早已自动展示 judge 指标）
- API: （无新 API）
- 数据表: （无新表）

## 功能说明

能力盘点时误判"缺 LLM-judge"——实际上 `eval/judge.py` 早已产出
faithfulness / hallucination_rate / answer_relevance / citation_accuracy /
answer_correctness（judge_confidence），且随 `eval_runs.summary` 落库、在
EvalRunsPage 趋势选择器自动出现。真实欠账只有一处：`judge()` 加载
`templates/judge.md` 后**立即被内联字符串覆盖**——两者恰好逐字节一致所以
从未爆雷，但任何对 judge.md 的后续修改都会被静默无视（templates/README
规则 1"提示词唯一存放处"被违反）。

修缮：删除内联覆盖，模板生效。行为零变化（已逐字节核对），此后 judge.md
的修改才真正可生效。

## 关联

- 依赖功能: FEAT-021（评测报告）、GUIDE-002（两段式评估）、GUIDE-003
  （judge v2 引入处）
- 实现要点：
  - 军规②视角：本次是提示词**来源**重构而非内容变更（文本零变化），
    但本批 FEAT-026/028 动了检索路径，eval 门禁整体重跑对比（见
    ADR-010/提交记录的 eval 数字）。
  - `test_judge_v2.py` 的锚点断言引用模块属性，自动指向模板文本，未改。
  - 测试: `tests/test_judge_template.py`（模板渲染锚点/模块属性等于模板
    渲染/源码无内联副本/`$` 安全）。

## 手工冒烟清单

1. `python -m eval.runner --user-id 1`（LLM 模式）→ 报告含
   faithfulness 等生成指标；`--save` 后 EvalRunsPage 趋势可选。
