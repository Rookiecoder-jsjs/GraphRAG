# GUIDE-003: Codex 参考改造总体设计

- 状态: 进行中（本文件同时是设计文档与进度看板，每完成一项在 §9 打勾）
- 日期: 2026-08-24
- 参考来源: openai/codex 仓库深读结论（本地克隆 `D:\NC\codex`，HEAD `e3609f2`）
- 关联: ADR-001（混合检索）、GUIDE-002（RAG 测评流程）

---

## 0. 文档用法

本文档是「向 Codex 学习」系列改造的**唯一设计来源与进度台账**。规则：

1. 每个任务条目（T1-1、T2-2 等）自成一体，含：目标 / Codex 参考 / 现状 / 设计 / 边界 / 验收 / 工作量。
2. **状态只允许三个值**：`未开始` / `进行中` / `已完成`，判定依据是代码与测试是否落地（与 CLAUDE.md §3.4 状态纪律一致）。
3. 改造完成一项，立即更新 §9 进度看板并在条目头部改状态——文档与代码永不脱节。
4. 实施中发现设计与现实冲突：先改设计（本文件），再改代码。禁止"代码走了文档没跟"。
5. 所有新配置项一律进 `backend/app/config.py`；所有新提示词一律进模板目录（见 T2-1，模板化落地前暂存原位并在迁移清单登记）。

## 1. 背景与目标

对 openai/codex（112 crate 的生产级编程 agent）做了四路深度阅读：agent 主循环、上下文工程、沙箱/扩展体系、会话持久化。结论：Codex 没有 NC 缺的检索评测框架（NC 反而更完整），但它有四件 NC 直接缺的东西——**提示词资产化管理、LLM 裁判提示词的严谨度、上下文注入的有界纪律、mock LLM 的集成测试**。

本设计的筛选标准不是"Codex 有什么"，而是**与 NC 当前阶段的匹配度**。据此分三梯队：

| 梯队 | 定位 | 内容 |
|------|------|------|
| 第一梯队 | 小、准、马上见效 | T1-1 judge 元规则、T1-2 注入预算熔断、T1-3 mock LLM 测试 |
| 第二梯队 | 等触发条件，条件已明 | T2-1 提示词模板化、T2-2 历史有界加载、T2-3 MCP server |
| 第三梯队 | 只借思想，不搬代码 | T3-1~T3-4，各带触发条件与设计草案 |

## 2. 总则：两条军规（落地到 CLAUDE.md §二）

> 状态: **已完成**（2026-08-24，已写入 CLAUDE.md §二"军规"小节）

Codex 的 AGENTS.md 把工程纪律写成机器可查的规则，其中两条对 NC 普适，原文采纳：

1. **一切注入物必须有界**——任何拼进 LLM prompt 的内容（检索 chunks、图谱三元组、对话历史、评测上下文）必须声明硬上限；超限时走统一截断/丢弃函数，禁止"相信模型自己会注意长度"。（Codex 出处：AGENTS.md "Model visible context" 第 3/4 条；实现参照 `codex-rs/utils/string/src/truncate.rs`、`unified_exec/head_tail_buffer.rs`）
2. **改提示词必须重跑评测**——`python -m eval.runner --user-id 1` 的检索+生成指标是提示词改动的回归门禁；没有数字背书的提示词改动不得合入。（Codex 出处：insta 快照测试文化——UI/文本输出的每次有意变更必须有快照 diff）

---

## 3. 第一梯队（立即实施）

### T1-2 上下文注入预算熔断器

> 状态: **已完成**（2026-08-24）
> 交付: `backend/app/services/context_budget.py` + `backend/tests/test_context_budget.py`（21 用例）+ `config.py` 4 个配置项 + `build_rag_system_prompt` 接线 + 全量 210 测试无回归
> 实施偏差记录：① `estimate_tokens` 对总数向上取整（ceil）——原设计的 int 截断会把单字 1.5 权重算成 1，违反"宁可高估"；② 装载前**预留说明行空间**（load_budget = budget − note_cost），实施中发现"先装满再驱逐"会让说明行挤掉本可保留的块；③ 首块腰斩的字符换算按最坏全-CJK 情况取 budget÷1.5，而非 ×2。验收清单中评测回归一项随 T1-1 完成后统一真跑。

#### 目标

消灭一整类隐性故障：任何来源的 `<context>` 内容（chunks 拼接、图谱三元组、未来新增的注入段）超长时不熔断，直接挤爆模型上下文窗口或稀释关键信息。

#### Codex 参考

- `codex-rs/utils/string/src/truncate.rs`：`approx_token_count`（4 bytes ≈ 1 token）、`truncate_middle_with_token_budget`（保头尾、中间替换 `…N tokens truncated…` 标记、UTF-8 边界安全）。
- `core/src/unified_exec/head_tail_buffer.rs`：1 MiB 硬顶，头尾各 50%，中间省略计数。
- AGENTS.md："No unbounded items - everything injected in the model context must have a bounded size and a hard cap."

#### 现状（已核实的注入链路与既有防线）

| 注入物 | 位置 | 现有限制 | 缺口 |
|--------|------|---------|------|
| 检索 chunks → context_str | `api/chat.py::_build_citation_context`（L185） | `max_chunks=8`、`per_chunk_chars=600`（字符级） | 无**总量**熔断；参数被调大时无兜底 |
| 图谱 facts | `services/llm.py::build_graph_context`（L146） | triples≤15、entities≤20（条数级，无字符级） | 单条 triple/description 超长无防护 |
| 默认上下文路径 | `llm.py::generate_rag_response` L707 的 `[Document N]` 分支 | `context_chunks[:5]`，**chunk 内容完全不截断** | 最薄弱：eval runner 走这条路 |
| 对话历史 | chat.py 双路径 | 最近 10 条取库、生成用 5 条（条数级） | 单条消息超长无防护（T2-2 解决，此处不管） |
| judge 上下文 | `eval/judge.py::_clip` | 800 字符 | 已达标 |

#### 设计

**新模块 `backend/app/services/context_budget.py`**，四个纯函数 + 一个入口封装：

```python
def estimate_tokens(text: str) -> int:
    """混合文字的保守 token 估算。

    CJK 字符按 settings.CONTEXT_ESTIMATE_CJK_WEIGHT（默认 1.5）计，
    其余字符按 bytes/settings.CONTEXT_ESTIMATE_ASCII_BYTES_PER_TOKEN（默认 4）计。
    为什么 CJK 用 1.5：qwen tokenizer 中文实测约 1.0–1.6 token/字；
    熔断器的职责是宁可高估不可漏放，故取保守上界而非均值。
    （对照：Codex 的 4 bytes/token 对 UTF-8 中文=0.75 token/字，会系统性低估，
    不能照抄——这是本次设计中唯一一处"参考但不照抄"的点。）
    """

def truncate_middle(text: str, max_chars: int, marker_fmt: str = "…[已省略 {n} 字]…") -> str:
    """保头尾各半、中间替换为省略标记；标记内含被省略字符数。
    语义对齐 Codex truncate_middle：短于上限原样返回；
    max_chars 过小（<20）时退化为纯头部截断，保证标记本身不被截。"""

@dataclass(frozen=True)
class BudgetFitResult:
    fitted: str          # 装配后的最终字符串
    used_tokens: int     # 实际占用估算
    dropped_blocks: int  # 整块丢弃数
    truncated_blocks: int # 腰斩块数
    over_budget: bool    # 是否触发了任何裁剪

def fit_blocks_to_budget(
    context_str: str,
    budget_tokens: int,
    separator: str = "\n\n",
) -> BudgetFitResult:
    """按块贪心装载，不做全文腰斩。

    context_str 以 separator 切块（与 _build_citation_context 的 [Context N]
    块结构天然对齐）。顺序遍历（检索结果已按相关性排序，丢尾部损失最小）：
    - 块能装入 → 保留；
    - 第一个块自己就超预算 → 对它单独 truncate_middle 到预算内（保底有内容）；
    - 装不下 → 丢弃，累计计数；
    - 若发生了丢弃，在末尾追加一行
      "…[因上下文长度限制，另省略 N 个资料块]…"（该行计入预算）。
    """

def enforce_rag_context_budget(context_str: str) -> str:
    """build_rag_system_prompt 的接线封装。
    settings.CONTEXT_BUDGET_ENABLED=False 时直通（逃生门）；
    否则 fit_blocks_to_budget(context_str, settings.CONTEXT_BUDGET_TOKENS)，
    并 logger.info 一行观测日志（原始/裁剪后估算 tokens、丢弃数）。"""
```

**配置项（`config.py`）**：

```python
CONTEXT_BUDGET_ENABLED: bool = True      # 逃生门：出问题可一键关
CONTEXT_BUDGET_TOKENS: int = 8000        # <context> 区总预算
CONTEXT_ESTIMATE_CJK_WEIGHT: float = 1.5 # 每 CJK 字符折算 token
CONTEXT_ESTIMATE_ASCII_BYTES_PER_TOKEN: int = 4
```

预算取 8000 的依据：现行满载 = 8 块 × 600 字 ≈ 4800 汉字 ≈ 7200 估算 token < 8000，**正常流量永不触发**；只有异常膨胀（如未来把 per_chunk_chars 调到 2000、或 eval 默认路径喂入整章文档）才熔断。

**接线点（唯一咽喉）**：`llm.py::build_rag_system_prompt` 开头对 `context_str` 参数执行 `enforce_rag_context_budget`。理由：流式 chat、非流式 chat、eval runner 三条路径都经过此函数（已核实 6 个调用方），一处接线全覆盖；且此处截断不影响 `sources`/引用编号映射（编号块要么完整保留要么整体丢弃，不会出现"[Context 3]"被腰斩导致引用悬空的情形）。

#### 边界与失败模式

| 场景 | 行为 |
|------|------|
| context_str 为空 | 直通返回（fit 结果为空串，无日志噪音） |
| 单块超预算（如一整章） | 该块 middle-truncate，标记含省略字数 |
| 全部块都装不下（极端） | 保留首块截断版 + 省略说明行——prompt 永不为空 |
| estimate 高估导致误裁 | 逃生门 `CONTEXT_BUDGET_ENABLED=False`；日志可见 used vs budget |
| separator 在正文中合法出现 | 块切分只是启发式；最坏情况多切几刀、语义不变（块序保持） |

#### 验收清单

- [ ] `estimate_tokens`：纯中文 / 纯 ASCII / 混合 / 空串 / 多字节 emoji 五组断言
- [ ] `truncate_middle`：短文直通 / 长文头尾保留且含标记 / 标记数字正确 / 极小 max_chars 不崩
- [ ] `fit_blocks_to_budget`：全装入零改动 / 尾块丢弃含说明行 / 首块超预算腰斩 / 全丢弃仍有首块
- [ ] `build_rag_system_prompt` 集成：超长 context_str 输出 ≤ 预算对应规模、含省略标记、`[Context 1]` 完整
- [ ] 逃生门关闭后输出与现状逐字节一致
- [ ] `pytest tests/test_prompt_helpers.py tests/test_context_budget.py` 全绿
- [x] 评测回归：数字与基线可比（军规②，REPORT-2026-08-24-regression.md）

#### 工作量

约 0.5 天（模块+测试 200 行内，接线 5 行）。

---

### T1-1 Judge 元规则升级（裁判提示词 v2）

> 状态: **已完成**（2026-08-24；军规②回归已真跑，见 REPORT-2026-08-24-regression.md）
> 交付: `eval/judge.py` 提示词重写（可执行判据/双置信度/反谄媚/反投机）+ `judge_confidence` 聚合指标 + v1 输出容错 + `tests/test_judge_v2.py`（14 用例）+ 全量 224 测试无回归

#### 目标

让 LLM-as-judge 的分数更可信：压制谄媚性宽松与投机性严格，给每个判断附置信度供报告降权展示。

#### Codex 参考

`codex-rs/prompts/templates/review/rubric.md` 的五个手法：

1. 判据前置且可执行（"只标记本 commit 引入的问题"级别的 yes/no 定义）；
2. 反谄媚条款（禁 "Great job..."、禁夸大严重度）；
3. 宁缺毋滥（"没有确定 finding 就输出零 findings"）；
4. 置信度双字段（每条 finding 的 confidence_score 0–1 + 整体 overall_confidence_score）；
5. 输出 schema 写死在提示词里并声明 MUST MATCH exactly。

#### 现状

`eval/judge.py` 已有 claims 式 faithfulness + 三个 0-1 指标 + keyword_coverage 兜底（架构正确）。缺口：

- 无置信度——低质量判断与高质量判断在聚合里同权；
- 判据模糊——"是否被 context 支持"没有可执行的判定规则，模型靠感觉；
- 无反谄媚约束——回答写得流畅礼貌时倾向拉高分。

#### 设计

**提示词 v2 结构**（保持中文，保持"只输出 JSON"契约）：

```
角色段（保留）
├─ 输出 schema v2（MUST MATCH exactly）：
│    {
│      "claims": [{"claim","supported","confidence"}],   ← 新增 confidence
│      "answer_relevance", "citation_accuracy",
│      "answer_correctness",
│      "overall_confidence",                             ← 新增 0-1
│      "notes"                                           ← 新增一句话主依据
│    }
├─ 判据段（可执行化）：
│    - supported=true 当且仅当 context 存在能直接推出该陈述的原文；
│      需要借助外部知识补全才能成立 → supported=false
│    - 数字/日期/专名须与 context 逐字一致才算 supported
│    - 引用标记 [N] 判据：所指片段确含该陈述 → 达标；指错片段 → 计入失准
├─ 元规则段：
│    - 不确定时降低 confidence，而不是猜测 supported
│    - 不要因为回答流畅/礼貌/结构清晰而放松事实判断（反谄媚）
│    - 不要为了显得严格而给出资料不支持的扣分（反投机）
└─ 边界段（保留 v1 的空 context / 拒答规则）
```

**解析端改动（`judge.py::judge`）**：

- `confidence` 容错读取：缺失/非法 → 按 `"medium"` 处理（旧式输出不炸）；
- 新指标 `judge_confidence = mean(overall_confidence)`（0-1，`_to_float` 归一）进入返回 dict；
- **faithfulness 算法 v1 不变**（supported 占比）——保证与 GUIDE-002 基线数字可比；置信度加权留待积累一轮基线后再议（记录为开放项）；
- 兜底路径（LLM 失败/JSON 解析失败）不产 `judge_confidence` 字段——下游以"字段缺失=低可信"理解。

**runner/report 兼容**：`run_evaluation` 的 generation 指标聚合是动态 dict 合并（已核实 `Judge = Callable[..., Awaitable[Dict]]` 契约），新字段自动流入 summary 与 markdown 报告，**零改动**。

#### 边界与失败模式

| 场景 | 行为 |
|------|------|
| 模型仍输出 v1 格式（无 confidence/notes） | 全部按 medium/None 容错，指标照出 |
| confidence 给出越界值 | `_to_float` 截断到 [0,1] |
| notes 含敏感内容 | 仅进评测报告，不入产品库 |
| 新旧提示词分数漂移 | 军规②：跑两轮评测对比，漂移写入 GUIDE-002 附录 |

#### 验收清单

- [ ] 提示词含五要素：可执行判据 / confidence 字段 / overall_confidence / 反谄媚 / 反投机
- [ ] v1 形态输出（无新字段）解析不炸，faithfulness 数值与 v1 逻辑一致
- [ ] v2 形态输出正确产出 `judge_confidence`
- [ ] 兜底路径无 `judge_confidence`
- [x] 真跑对比完成（REPORT-2026-08-24-regression.md；单轮 + 定向复跑诊断）
- [ ] `pytest eval 相关测试` 全绿

#### 工作量

约 0.5 天（提示词重写 + 解析 + 测试）。

---

### T1-3 Mock LLM 集成测试链路

> 状态: **已完成**（2026-08-24）
> 交付: `tests/mock_llm.py`（MockTransport 工厂，扁平模块——site-packages 有同名 `tests` 包抢解析，不能用子包）+ `tests/test_llm_transport.py`（14 用例）+ 全量 238 测试无回归
> 实施偏差记录：① 工厂放 `tests/mock_llm.py` 扁平位置而非 `tests/helpers/` 子包；② 400 剥参重试与超时重试的断言用传输层计数器——被拒/超时的首次请求不经过 mock handler，recorder 只能看到成功请求。

#### 目标

补齐单元 mock（`AsyncMock` 替换整个服务）与真跑评测（烧真 key）之间的断层：**HTTP 层**的确定性测试，让 `LLMService` 的流式解析、重试、参数降级逻辑离线可回归。

#### Codex 参考

`codex-rs/core/tests/common/responses.rs` + `streaming_sse.rs`：wiremock 起 mock SSE 服务器模拟 `/responses` 完整事件流，127 个集成测试全程零真实 API。要点：mock 层放在**传输协议层**（HTTP/SSE），而不是业务对象层——这样连客户端解析代码一起被测。

#### 现状

- pytest 8.3.3 + pytest-asyncio 0.24.0；conftest 已做 JWT/SQLite 隔离（`tests/conftest.py`）；
- 现有 mock 手法是 `mock.patch.object(chat, "get_llm_service", AsyncMock(return_value=llm))`——**绕过**了 LLMService 内部；
- `LLMService` 用 `httpx.AsyncClient`（requirements 锁 httpx==0.27.0），**httpx 自带 `MockTransport`**——零新依赖即可做传输层 mock（因此本任务**不引入 respx**，少一个依赖面）。

#### 设计

**新文件 `backend/tests/helpers/mock_llm.py`**：

```python
def make_mock_llm_service(
    *,
    content: str = "测试回答 [1]",
    thinking: str | None = None,
    finish_reason: str = "stop",
    status_sequence: list[int] = [],   # 非空时先依次返回这些状态码，最后一次成功
    sse_frames: list[dict] | None = None,  # 完全自定义流帧（高级用法）
) -> tuple[LLMService, MockCallRecorder]:
    """构造挂了 httpx.MockTransport 的 LLMService。

    recorder 记录每次请求的 (url, payload)，供断言 enable_thinking /
    messages 结构 / stream 标志。覆盖三类响应：
    1. 非流式 JSON：{"choices":[{"message":{"content",...},"finish_reason"}]}
    2. SSE 流：data: {...delta...}\n\n 序列 + usage-only 空 choices 帧 +
       data: [DONE]，含 reasoning_content（thinking 帧）与 content:null 帧
    3. 错误注入：status_sequence 先抛 500/429 再成功，验证单次重试逻辑
    """
```

**fixture（conftest 注册）**：

```python
@pytest.fixture
def mock_llm():          # 默认 happy-path 实例
def mock_llm_factory():  # 需要自定义行为的测试用工厂
```

**首批测试矩阵（`tests/test_llm_transport.py`）**：

| # | 用例 | 断言 |
|---|------|------|
| 1 | 非流式 happy path | 返回 content；payload 无 enable_thinking |
| 2 | content=null 归一 | 返回 "" 且 warning |
| 3 | finish_reason=length + truncation_marker | 尾部含 `…（已截断，输出达到上限）` |
| 4 | finish_reason=length 无 marker | 原样返回 |
| 5 | SSE 正常流 | ("content", ...) 增量序列完整拼回原文 |
| 6 | SSE thinking 流 | reasoning_content → ("thinking", ...) 先于 content |
| 7 | SSE content:null / usage-only 帧 | 不产出空元组、不 IndexError |
| 8 | SSE [DONE] 终止 | 流正常结束 |
| 9 | 500 → 重试成功 | 两次请求，最终成功 |
| 10 | 连续两次 500 | 抛出且 __cause__ 保留首次错误 |
| 11 | 400 + enable_thinking → 剥参重试一次 | 第二次 payload 无该键，成功 |
| 12 | 404（不可重试） | 立即抛，无第二次请求 |
| 13 | 传输超时 → 重试一次 | sleep 被 patch，两次请求 |
| 14 | api_key 缺失 | ValueError 快速失败 |

后续扩展（不在本任务内）：`retrieve()` 全链路 mock（需同时 mock chroma/bm25/neo4j，属集成测试范畴，另行立项）。

#### 边界与失败模式

- MockTransport 不走网络——CI/离线环境均可跑；
- SSE 帧手工构造须与 DashScope 实际抓包对齐（实施时以 `_stream_completions` 的解析分支为准绳反向构造，覆盖它每一个 continue/yield 分支）；
- 时间相关（重试 sleep）patch `asyncio.sleep`，不引入真实等待。

#### 验收清单

- [ ] 14 个用例全绿，全程无网络
- [ ] `pytest tests/` 整体无回归（新增文件不影响存量）
- [ ] requirements 零新增

#### 工作量

约 1 天（工厂 + 14 用例 + 边界打磨）。

---

## 4. 第二梯队（触发条件已明确的按序实施）

### T2-1 提示词模板化

> 状态: **已完成**（2026-08-24）
> 交付: `app/prompts/loader.py` + `templates/` 10 模板 + README + registry + 全调用方切换 + 启动自检 + `tests/test_prompt_loader.py`（12 用例）+ 全量 250 测试无回归
> 实施偏差记录：占位符语法用 `string.Template` 的 `$name` 而非 `.format` 的 `{name}`——提示词正文充满 JSON 花括号，`$` 风格免转义；原设计的"字面花括号写 {{}}"规范随之作废。

#### 目标

提示词从 Python 字符串常量迁为 markdown 模板文件，措辞迭代不再触碰业务代码；diff 干净、评审聚焦文案本身。

#### Codex 参考

`codex-rs/prompts/templates/<功能>/<模板>.md` + `include_str!` 编译期嵌入；代码只负责填槽（`.format`/占位符替换），`SUMMARIZATION_PROMPT` 即典型。

#### 现状（迁移清单，已逐一核实）

| # | 常量/内联串 | 位置 | 模板名 |
|---|------------|------|--------|
| 1 | `_RAG_SYSTEM_PROMPT_TEMPLATE` | services/llm.py:135 | `rag_system.md` |
| 2 | `_CITATION_INSTRUCTION` | services/llm.py:37 | `citation_instruction.md` |
| 3 | comparison_mode 内联指令 | services/llm.py:201 | `comparison_instruction.md` |
| 4 | `_CHITCHAT_SYSTEM_PROMPT` | api/chat.py:88 | `chitchat_system.md` |
| 5 | `_REJECTION_TEMPLATE` | api/chat.py:78 | `rejection_template.md` |
| 6 | intent 分类提示词 `_SYSTEM_PROMPT` | services/intent.py:24 | `intent_classify.md` |
| 7 | query rewrite f-string 提示词 | services/query_processor.py:41 | `query_rewrite.md` |
| 8 | query variants f-string | services/query_processor.py:95 附近 | `query_variants.md` |
| 9 | entity extraction system_prompt | services/llm.py:535（extract_entities_batch） | `entity_extract.md` |
| 10 | `_JUDGE_SYSTEM_PROMPT`（T1-1 产物） | eval/judge.py | `judge.md` |

#### 设计

**目录**：`backend/app/prompts/`

```
backend/app/prompts/
├── __init__.py
├── loader.py            # 唯一加载器
└── templates/
    ├── rag_system.md
    ├── chitchat_system.md
    ├── rejection_template.md
    ├── intent_classify.md
    ├── query_rewrite.md
    ├── query_variants.md
    ├── entity_extract.md
    ├── judge.md
    └── README.md         # 每个模板一行：用途+占位符+消费方（防孤儿模板）
```

**加载器 `loader.py`**：

```python
TEMPLATES_DIR = Path(__file__).parent / "templates"

@lru_cache(maxsize=None)
def load_prompt(name: str, **placeholders) -> str:
    """读 templates/{name}.md，.strip() 后 .format(**placeholders)。
    - lru_cache：文件在进程生命周期内视为不可变（改模板需重启——
      与 Codex include_str! 语义一致，换来零 IO 与绝对一致性）
    - KeyError/TemplateError 向上抛：模板缺失或占位符不匹配是部署期
      错误，fail fast，不允许静默回退到空提示词
    """
def assert_templates_exist(names: Iterable[str]) -> None:
    """启动自检钩子（main.py lifespan 调用）：任一模板缺失立即拒绝启动。"""
```

**兼容策略**：调用方**直接改引 `load_prompt`**，不留常量别名（NC 是单人项目，一步到位优于过渡层）；`_TRUNCATION_MARKER` 这类运行时符号（非提示词文案）**不迁移**，留在代码里。

**测试**：每个模板存在性 + 占位符渲染冒烟 + 渲染产物包含关键锚点（如 rag_system.md 必含 `<context>` 与 DATA 指令）——锚点断言同时是军规②的轻量守卫。

#### 边界与失败模式

| 场景 | 行为 |
|------|------|
| 模板文件被误删 | lifespan 启动自检 fail fast |
| `.format` 遇到文案中的 `{` 字面量 | 模板规范：字面花括号写 `{{`/`}}`（README 写明）|
| 热改模板不生效 | 设计使然（缓存）；README 注明"改模板需重启后端" |

#### 验收清单

- [x] 十个模板落位且 README 登记齐全
- [x] 全部调用方切换，原常量删除，grep 无残留引用
- [x] pytest 全绿（新增模板测试 + 存量 prompt 测试改造）
- [x] 军规②：评测回归数字可比（REPORT-2026-08-24-regression.md）

#### 工作量

约 1 天。

### T2-2 会话历史有界加载（压缩即边界）

> 状态: 已完成（2026-08-24）
> 触发条件（满足其一即启动）：① 单会话消息数常态超过 30 条；② 聊天 token 成本肉眼可见上升；③ 出现"长对话后半程回答变差"的用户反馈。
>
> **实施记录**（2026-08-24）：落地为 `app/services/history.py`（加载器 `load_chat_history` + 压缩器 `maybe_compact_history` + 后台任务派生 `spawn_history_compaction`）。与设计的四处偏差：
> 1. **折叠候选集 = 全部普通消息**（设计草案是"最后一条 summary 之后的 segment"）。原因：上一轮刻意保留在 summary 行**之前**的 KEEP_RECENT 原始消息会被 segment 定义漏掉，二次压缩生成的新摘要就不再代表"其之前全部历史"，不变式被破坏；测试 `test_second_compaction_merges_old_summary` 实测确认。旧摘要文本进 transcript、旧行删除的设计保持不变。
> 2. `spawn_history_compaction` 用模块级 `_pending_tasks: set` 持强引用 + done-callback 自清理——asyncio 只持弱引用，裸 create_task 可能在首个 await 被 GC 静默取消（同 main.py 已修过的坑）。
> 3. 新增配置 `HISTORY_COMPACT_ENABLED / HISTORY_WINDOW_LIMIT(=10) / HISTORY_COMPACT_THRESHOLD(=24) / HISTORY_KEEP_RECENT(=6) / HISTORY_SUMMARY_MAX_CHARS(=400)`；两处硬编码 `LIMIT 10` 由 WINDOW_LIMIT 取代。查询一律按 `id` 排序（AUTOINCREMENT 单调），不再用 created_at（同秒插入会并列）。
> 4. 测试环境 conftest 默认 `HISTORY_COMPACT_ENABLED=false`（否则每个 chat 单测都触发真实 DB 的后台压缩）；压缩专项测试显式开启。
>
> 新模板 `templates/history_compact.md`（Codex 交接骨架四节 + 反补充/逐字一致规则）已注册 TEMPLATE_NAMES 并纳入启动自检；12 个新测试（tests/test_history.py）。

#### 目标

多轮对话发给模型的成本与质量不随历史线性恶化：老历史折叠成一条摘要，之后每次只送"摘要 + 其后消息"。

#### Codex 参考

rollout 的恢复机制（`rollout/src/model_context.rs::ModelContextScan`）：从文件尾反向扫描，遇到压缩检查点（`CompactedItem` 带 replacement_history）即停——**压缩摘要本身就是持久化的恢复边界**。以及 compact.rs 的交接摘要骨架：进度与关键决策 / 约束与偏好 / 待办 / 继续所需数据。

#### 现状

- `messages` 表：`(id, conversation_id, role TEXT NOT NULL, content, created_at)`——**role 无 CHECK 约束，是自由 TEXT，加 'summary' 角色零 schema 迁移**；
- 加载路径三处（chat.py 非流式 L377、流式 L524、conversations 详情接口），均为 `ORDER BY created_at DESC LIMIT 10` 再 reverse；
- `database.py` 有 `schema_version` 迁移机制（tests/test_migrations.py 在管）。

#### 设计

**存储约定**（不动表结构）：

```
role = 'summary' 的行代表其 created_at 之前全部历史的折叠。
不变式：一个 conversation 至多一条未过期 summary 行；
        summary 行之后不存在 created_at 更早的普通消息（追加单调性由 AUTOINCREMENT id 保证）。
```

**写入侧**：`api/chat.py::_save_assistant_message` 之后挂钩 `maybe_compact_history(conversation_id)`：

```python
async def maybe_compact_history(conversation_id: str) -> None:
    """消息数超过 HISTORY_COMPACT_THRESHOLD（默认 24）且尚无 summary 行时，
    异步（asyncio.create_task，失败只 log）生成摘要：
    - 取 summary 行之后（或全量）除最新 6 条外的消息；
    - 用 LLM 按 Codex 交接骨架生成 ≤300 字摘要（enable_thinking=False）;
    - INSERT (conversation_id, 'summary', 摘要文本)；
    - 幂等：已有 summary 则跳过（下一轮阈值翻倍再压，见下）。
    二次压缩：当 summary 之后的普通消息再次达到阈值，生成"合并摘要"
    （旧摘要+新区间 → 新 summary 行，旧行删除）——保持至多一行的不变式。
    """
```

**读取侧**（三处加载统一收敛为一个 helper，顺手消除重复）：

```python
async def load_chat_history(db, conversation_id, limit: int) -> list[dict]:
    """SELECT ... WHERE conversation_id=? AND role!='summary' ORDER BY id DESC LIMIT ? UNION
       SELECT 'summary' 行（若有且其后的普通消息不足 limit）
    组装顺序：[summary(若有), ...近期消息]。role='summary' 映射为
    {'role':'system','content':'<此前对话的摘要>…'} 进入 prompt；
    映射为 'assistant' 会污染轮替交替。"""
```

**前端兼容**：`GET /chat/conversations/{id}/messages` 过滤 role='summary'（用户不需要看见摘要行）；ChatPage.vue 零改动。

**失败模式**：

| 场景 | 行为 |
|------|------|
| 摘要 LLM 调用失败 | log warning，本轮不压缩，下轮重试（无副作用） |
| 用户删光 summary 后的消息 | 摘要行成为孤儿 → maybe_compact 的二次压缩自然吸收 |
| 检索 rewrite 的 history 窗口 | 同一 helper 供给，摘要行同样进入 rewrite 上下文（改善指代消解） |

#### 验收清单

- [x] 单测：阈值触发 / 幂等（重复调用只一行 summary）/ 二次压缩合并 / 读取组装顺序 / 前端接口过滤
- [x] 手工验证：chat 路径冒烟（非流式两轮 + 流式 + 闲聊路由，历史指代正确、SSE 帧完整、transcript 无 summary 泄漏）
- [x] 军规②评测回归（REPORT-2026-08-24-regression.md）

#### 工作量

约 1.5 天。

### T2-3 知识库 MCP Server

> 状态: 已完成（2026-08-24）
> 触发条件（满足其一即启动）：① 需要在 Claude Code/Codex/Claude Desktop 里直接查询知识库；② 需要 PM 演示"MCP 生态接入"能力。
>
> **实施记录**（2026-08-24）：落地为 `backend/mcp_server/`（server.py + auth.py + README.md + __init__.py）。与设计的偏差：
> 1. **依赖版本**：设计写"mcp>=1.x"，实际锁死 `mcp==1.29.0`——mcp 2.0 会拖入 starlette>=1.0，与 fastapi 0.115（需要 starlette<0.39）不兼容，实测 `Router.__init__() got an unexpected keyword argument 'on_startup'` 崩溃。requirements.txt 同步固定 httpx==0.28.1 / starlette==0.38.6 / pydantic==2.13.4 / pydantic-settings==2.11.0。
> 2. **鉴权形态**：stdio 传输本身是进程级隔离（只有持有 env 的父进程能对话），故 token 是启动门禁（缺失/占位符 → exit 2）而非每调用校验；auth.verify 预留给未来 SSE/HTTP 传输。日志只写 stderr（stdout 是协议通道）。
> 3. `search_knowledge` 调 retriever 时 `enable_rewrite=False`（跳过 LLM 重写，工具调用要快）；top_k 上限 20、depth 白名单 1–3（get_related_entities 内部同样钳制）。
> 4. **实测修复两个 bug**（2026-08-25）：① server 进程 CWD 由 MCP 客户端决定，相对路径 `SQLITE_PATH=./data/...` 会在错误位置建空库（"no such table: documents"）→ 启动时 `os.chdir(_BACKEND_ROOT)` 锚定数据根；② 首次 `from neo4j import ...` 在 anyio 运行中的事件循环里挂死（裸 asyncio 进程 <1s 完成，FastMCP 工具内永久卡住；二分定位到 import 期而非 driver 连接期）→ 模块顶层预导入全部 app 服务模块。另修 search_graph 返回键名与 get_related_entities 实际返回（center_nodes/related_nodes）不匹配的 bug。
>
> 端到端冒烟已过：stdio 握手 + list_tools 四工具 + list_documents 真实数据返回。9 个离线单测（tests/test_mcp_server.py）。FEAT-001 已建档登记。

#### 目标

把 NC 的检索/图谱能力包成标准 MCP server，任何 MCP 客户端零前端投入直接消费。

#### Codex 参考

`mcp-server/`（把 codex 自己暴露为 MCP server 的 stdio JSON-RPC 实现）与 `rmcp-client`（消费端）；connectors 经宿主 MCP server 化为普通工具的模式——"能力工具化，工具即入口"。

#### 设计

**位置**：`backend/mcp_server/`（独立进程，复用根 .venv；与 FastAPI 主服务解耦，直接读同一套 SQLite/Chroma/Neo4j）。

```
backend/mcp_server/
├── server.py        # FastMCP 入口，4 个 tool + 1 个 resource
├── auth.py          # KG_MCP_TOKEN 环境变量校验（工具级守卫）
└── README.md        # Claude Desktop / Codex / claude code 三种接入配置示例
```

**工具面（v1 四个，全部只读）**：

| 工具 | 签名 | 底层复用 |
|------|------|---------|
| `search_knowledge` | `(query: str, top_k: int = 5) -> list[ChunkResult]` | `retriever.retrieve`（use_graph_rag=False） |
| `search_graph` | `(entity_or_query: str, depth: int = 1) -> GraphView` | neo4j.search_entities + get_related_entities |
| `get_entity_detail` | `(name: str) -> EntityDetail` | `neo4j.get_entity_detail`（复用 graph.py 的 hydration 逻辑抽公共函数） |
| `list_documents` | `() -> list[DocSummary]` | documents 表直查 |

**鉴权**：`KG_MCP_TOKEN` 必填（启动校验，占位符拒绝——对齐 JWT_SECRET 的既有纪律）；stdio 模式下 token 由父进程经 env 注入。

**依赖**：`requirements.txt` 增加 `mcp>=1.x`（FastMCP 已并入官方 mcp 包）。这是本设计**唯一的新增运行时依赖**。

#### 验收清单

- [ ] stdio 模式在 Claude Desktop 配置后四个工具可调通（端到端 stdio 冒烟已过；Claude Desktop 实配待用户操作）
- [x] 无 token 启动被拒
- [x] 只读验证：全部工具无写路径（代码评审 + 静态守卫测试 + 端到端冒烟）
- [x] FEAT 建档（首个功能条目文档，按 CLAUDE.md §3.4 从索引领号）

#### 工作量

约 2 天。

---

## 5. 第三梯队（只借思想；各自带触发条件与草案）

### T3-1 WorldState 式差量注入
> 触发：聊天请求需要携带"页面状态"（选中过滤器、图谱开关组合等）且这些状态在多轮间高度稳定。

草案：把易变状态建模为 sections dict，每 section 渲染后 SHA1 指纹（Codex: `world_state/mod.rs::WorldStateHash`）；请求组装时与上一轮快照求 RFC 7386 merge patch，只把差量作为新的 user fragment 注入。NC 当前无此需求形态，仅登记。

### T3-2 Steering 统一输入队列
> 触发：NC 从"单轮问答"演进为"多步 agent 任务"（如自动研究规划），需要在模型运行中插话。

草案：turn 维度的 input_queue（Codex: `session/input_queue.rs`）；插话与普通输入同队列，仅在采样完成的固定时机合入历史。禁止旁路通道。

### T3-3 Stop-hook 反惰性阀门
> 触发：同 T3-2，agent 任务出现"提前收工"。

草案：模型声明完成后，hook 校验任务清单；未完则注入 continuation prompt 强制续跑；`stop_hook_active` 单次保护防死循环（Codex: `session/turn.rs` L502-538 的 should_block/should_stop 分型）。

### T3-4 节流周期注入
> 触发：长任务需要周期性提醒（时间/预算/进度）进入模型上下文。

草案：Codex `time_reminder.rs` 三条件（新窗口 OR 间隔到期 OR 紧随用户/工具输出边界）+ "边界即使未触发也消费"防积压。

---

## 6. 实施顺序与依赖

```
军规入 CLAUDE.md ──┐
                   ├─→ T1-2 预算熔断（最独立）─→ T1-1 judge v2 ──→ T2-1 模板化（judge.md 收编 v2 产物）
T1-3 mock 测试 ────┘（为以上提供回归网）                │
                                                      ↓
                              T2-2 历史有界（独立，随时可插队）   T2-3 MCP（独立）
第三梯队：仅登记，不排期
```

原则：T1-3 先行或并行——它让后续每一步都有离线回归网；T1-2 其次（纯增量、零行为变化风险）；T1-1 涉及提示词语义，必须在 T2-1 之前定稿（否则模板化要迁两次）；T2-1 之后所有提示词迭代走模板。

## 7. 验证与回归策略

1. **每任务收尾**：`cd backend && pytest tests/ -v` 全绿。
2. **提示词类改动**（T1-1/T2-1/T2-2）：军规②——`python -m eval.runner --user-id 1` 前后对比，检索指标应不动，生成指标漂移需可解释并记录进 GUIDE-002 附录。
3. **涉及 chat 路径**（T1-2/T2-2）：本地起服务，curl 流式+非流式各一轮冒烟（UTF-8 文件 body，规避 GBK 终端坑）。
4. **每完成一项**：更新本文件状态与 §9 看板（文档与代码同步是本文件的最高纪律）。

## 8. 明确不做的事

- 不抄 112 crate 拆分粒度（单人项目，"单文件 ≤400 行"已够）；
- 不引入 JSONL 事件溯源存储（SQLite 在 NC 规模完全正确）；
- 不做沙箱/code-mode/V8（NC 不执行模型生成的代码）；
- 不为第三梯队写任何代码，除非触发条件命中并先回到本文件补充正式设计。

## 9. 进度看板

| 任务 | 状态 | 完成日期 | 备注 |
|------|------|---------|------|
| 军规两条入 CLAUDE.md | ✅ 已完成 | 2026-08-24 | §二末尾"军规"小节 |
| T1-2 注入预算熔断 | ✅ 已完成 | 2026-08-24 | context_budget.py + 21 测试 + 接线；210 全量绿 |
| T1-3 mock LLM 测试 | ✅ 已完成 | 2026-08-24 | mock_llm.py + 14 用例；238 全量绿 |
| T1-1 judge v2 | ✅ 已完成 | 2026-08-24 | 提示词+judge_confidence+14 测试；军规②回归已真跑通过 |
| T2-1 提示词模板化 | ✅ 已完成 | 2026-08-24 | loader+10 模板+启动自检；250 全量绿 |
| T2-2 历史有界加载 | ✅ 已完成 | 2026-08-24 | services/history.py + history_compact 模板 + 12 测试；271 全量绿；冒烟+军规②回归通过 |
| T2-3 MCP server | ✅ 已完成 | 2026-08-24 | mcp_server/ 四工具 + FEAT-001 建档；mcp==1.29.0 锁版；Claude Desktop 实配待用户 |
| T3-1~T3-4 | 📋 仅登记 | — | 各带触发条件，见 §5 |
