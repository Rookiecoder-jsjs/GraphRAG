# 提示词模板目录

所有 LLM 提示词的唯一存放处（GUIDE-003 T2-1）。业务代码通过
`app.prompts.loader.load_prompt(name, **placeholders)` 加载，禁止在代码里新写提示词字符串。

## 规则

1. 占位符用 `$name`（string.Template 语法）——正文里的 JSON 花括号**无需转义**；
   需要 `$` 字面量时写 `$$`。
2. 改动任何模板后必须重启后端才生效（进程内缓存，与 codex 的 include_str! 同语义）。
3. **改提示词必须重跑评测**（CLAUDE.md 军规②）：`python -m eval.runner --user-id 1`，
   检索+生成指标有对比记录方可合入。
4. 新增模板必须在下表登记（用途 + 占位符 + 消费方），防孤儿模板。

## 模板清单

| 文件 | 用途 | 占位符 | 消费方 |
|------|------|--------|--------|
| `rag_system.md` | RAG 回答系统提示词（含 `<context>` 注入防护） | `context_str` `graph_context` `citation_block` `comparison_block` | `services/llm.py::build_rag_system_prompt` |
| `citation_instruction.md` | 引用标记指令（有 sources 时拼入 RAG 提示词） | — | 同上 |
| `comparison_instruction.md` | 对比模式指令（compare_mode 时拼入） | — | 同上 |
| `chitchat_system.md` | 闲聊意图的轻量系统提示词 | — | `api/chat.py` 双路径 |
| `rejection_template.md` | should_reject 意图的固定拒答文案 | — | `api/chat.py` 双路径 |
| `intent_classify.md` | 意图分类 system prompt | — | `services/intent.py` |
| `query_rewrite.md` | 检索查询改写（含多轮 standalone 指令插槽） | `standalone_instruction` `query` `history_block` | `services/query_processor.py` |
| `query_variants.md` | 多查询变体生成 | `num_variants` `query` | 同上 |
| `entity_extract.md` | 实体抽取 system prompt | `entity_types` | `services/llm.py::extract_entities_batch` |
| `judge.md` | LLM-as-judge 评估员提示词（v2，含置信度与元规则） | — | `eval/judge.py` |
