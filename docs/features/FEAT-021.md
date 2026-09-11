# FEAT-021: 评测报告落库与趋势

- 状态: 已完成（接口与页面在代码中存在）
- 页面: `/eval/runs`（EvalRunsPage.vue——趋势柱状图 + 运行记录表）
- API: `GET /api/eval/runs`（`?limit=50`，ge=1 le=200；端点在 `app/api/eval_runs.py`，与 eval.py 共享 `/api/eval` 前缀、零路径重叠）
- 数据表: `eval_runs`（写入口：`eval.runner --save` 经 `eval/db_runs.py`）

## 功能说明

`python -m eval.runner --user-id 1 --save [--label xxx]` 把运行的聚合指标（summary 全部 `<metric>_mean/_n` + elapsed + total_cases + skipped）与运行配置（use_graph_rag / k_values / gold_dir）作为一行 JSON 落入 `eval_runs`；`/eval/runs` 页以纯 CSS 柱状趋势（指标可切换，默认优先 recall@5→mrr→ndcg@5→hit@5）+ 运行记录表呈现，为军规②（改提示词必须重跑评测）提供历史证据链。

## 边界与设计取舍

- **逐 case 数据不落库**：drill-down 仍走 `--json` stdout，换取表精简（单行几 KB）
- **best-effort 保存**：sqlite 打开/写入失败仅 stderr 警告，退出码仍 0——记录历史绝不能让评测本身失败；对未初始化的库（独立跑 runner）保存为空操作
- **`--save` 与 `--no-db` 正交**：`--no-db` 只关闭 eval_cases 合并（读），不影响保存（写另一张表）
- **保存时机**：在 summary 定稿（含 skipped_duplicate_file_cases）之后、`--json` pop `retrieved` 之前，落库的是规范报告
- **只读 API**：运行记录是追加型证据，无 detail/update/delete 端点
- runner.py 存量已超 400 行（约 491 行），本次仅 +约 30 行（flag + 钩子），逻辑全部置于新模块 `eval/db_runs.py`

## 关联

- 依赖功能: FEAT-018（eval_cases 与 `/api/eval` 前缀、页面骨架先例）
- 支撑军规: CLAUDE.md 军规②（eval 门禁）
