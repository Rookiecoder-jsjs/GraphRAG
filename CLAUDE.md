# CLAUDE.md — 知识图谱系统：开发约定与规范

> 本文档记录**项目的开发约定与规范**（编码约定、文档编写规范、维护流程、验收标准）。
> 系统要处理的目标与实现方式（功能条目模型、当前功能条目清单、文档目录结构、CodeGraph、skill 管理）见 [AGENTS.md](./AGENTS.md)。
> 两个文件共同构成项目知识库：**AGENTS.md 回答"做什么、怎么做"，CLAUDE.md 回答"按什么约定做"**。

---

## 一、开发环境

### 1.1 必需服务

| 服务 | 默认地址 | 说明 |
|------|----------|------|
| Neo4j | bolt://localhost:7687 | 图数据库 |
| ChromaDB | localhost:8000 | 向量数据库 |

### 1.2 环境变量（`.env`）

```bash
# 必填 — 启动时会校验，占位符会导致 RuntimeError
JWT_SECRET=<用 python -c "import secrets; print(secrets.token_urlsafe(48))" 生成>

# LLM（至少配置一个）
BAILIAN_API_KEY=...
# SILICON_FLOW_API_KEY=...
# KIMI_API_KEY=...

# 数据库（有默认值，按需覆盖）
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=...
CHROMA_HOST=localhost
CHROMA_PORT=8000
```

### 1.3 启动

> ⚠️ **虚拟环境位置**：后端 Python 环境在**仓库根 `.venv`**（依赖齐全，清单在根 `requirements.txt`）。
> `backend/.venv` 是空壳（只有 pip/setuptools，无依赖），**不要**在它里面激活或装包。
> 首次部署：`python -m venv .venv` 建在根目录，然后
> `./.venv/Scripts/python.exe -m pip install -r requirements.txt`（Windows；mac/Linux 为 `bin/python`）。
> 一键启动推荐 `npm run dev` / `npm run backend` —— 走 `scripts/run-backend.mjs`，会**自动按优先级**找到根 `.venv`（根 → 上级 → backend），无需手动指定 Python。

```bash
# 推荐：一键启动前后端（根目录执行）
npm run dev                   # concurrently 同时起后端(8001)+前端(5173)

# 或分开启动：
npm run backend               # 仅后端（launcher 自动发现根 .venv）
npm run frontend              # 仅前端

# 手动启动后端（显式用根 .venv，CWD 必须是 backend 以锚定 ./data 与 .env）
cd backend
../.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8001

# 前端
cd frontend
npm install
npm run dev                   # 默认 http://localhost:5173
```

### 1.4 测试

```bash
cd backend
pytest tests/ -v
pytest tests/ --cov=app --cov-report=term-missing   # 带覆盖率
```

测试文件与功能模块对应：`test_graph_rag.py`, `test_embedding.py`, `test_tags.py`, `test_timeline.py`, `test_dashboard.py` 等。

---

## 二、编码约定

- **Python**：PEP 8，所有函数加类型注解，用 `logging` 不用 `print`
- **错误处理**：API 层统一返回结构化错误，服务层抛出有意义的异常，不吞掉错误
- **不可变优先**：数据对象用 `@dataclass(frozen=True)` 或 Pydantic model，避免原地修改
- **配置集中**：所有配置项在 `app/config.py`，不在业务代码里读 `os.environ`
- **安全**：密钥只走环境变量，JWT_SECRET 禁止使用占位符，CORS 不用 `*`
- **文件体量**：单文件不超过 400 行，超出则拆分为子模块
- **前后端统一**：同一页面既在前端编码也在后端编码，**一个页面 = 一个功能**，开发时前后端同步推进，不拆开交付

---

## 三、文档编写规范（文档体系）

### 3.1 总原则

- **短**：内部文档尽量短，只保留有效信息；一页讲不完就拆分，不追求"全面"
- **高信息密度**：每个条目必须回答"触发 → 做什么 → 反馈/结果"，禁止抽象描述、禁止"太虚"的套话
- **可维护**：维护成本可控；宁可删减也不要维护一堆过期文档
- **一致结构**：所有编号文档统一用"决策记录"式结构与编号规则（见 3.2 / 3.3）
- **DRY**：文档内容与代码保持一致；若状态（已完成/计划中）无法从代码识别，不写入文档，避免人工维护失真

### 3.2 文档编号规则

所有内部文档必须编号，格式：`类型前缀-3位序号`，序号从 `001` 起、递增、**永不重用**（删除的编号作废不补）。

| 前缀 | 含义 | 存放目录 | 示例 |
|------|------|---------|------|
| `ADR` | 决策记录（Architecture Decision Record） | `docs/adr/` | `ADR-001.md` |
| `FEAT` | 功能条目（功能+页面+API+数据表） | `docs/features/` | `FEAT-001.md` |
| `GUIDE` | 通用指南（依赖、优化、FAQ 等） | `docs/guides/` | `GUIDE-001.md` |

规则：
- 编号出现在**文档标题**前缀：`# ADR-001: 标题`
- 新文档创建时在 `docs/00-INDEX.md` 登记编号与路径，**编号从索引领取，不自行猜测**
- 引用方式：文档正文直接写 `ADR-001` / `FEAT-013`，无需完整路径

### 3.3 决策记录格式（统一模板）

**适用**：一切"为什么这样做"的技术决策（架构、依赖选型、算法方案）。每个 ADR 是一份决策记录，前后结构一致。

```markdown
# ADR-NNN: 标题

- 状态: 提议 | 已接受 | 已废弃
- 日期: YYYY-MM-DD
- 相关: 关联的 FEAT-NNN / ADR-NNN（可选）

## 背景
为什么需要这个决策（现状与问题，具体到能复现）

## 决策
决定是什么（一条一句话，可执行，不含模糊措辞）

## 后果
- 正面: ...
- 负面: ...
- 取舍: 放弃的替代方案及原因

## 落地（可选）
改了什么代码/文档，编号指向
```

### 3.4 功能条目文档格式

```markdown
# FEAT-NNN: 功能条目名

- 状态: 已完成（接口与页面在代码中存在） | 草稿（新规划、尚无代码）
- 页面: <路由路径>（对应 .vue 文件）
- API: <路由前缀>（端点列表）
- 数据表: <表名>（或 存储层: Neo4j/Chroma）

## 功能说明
一句话说明该功能做什么（触发 → 做 → 反馈）

## 关联
- 决策记录: ADR-NNN（若有）
- 依赖功能: FEAT-NNN
```

> ⚠️ **状态字段纪律（DRY）**：`状态` 只能取"已完成"或"草稿"两个值——判断依据是**代码中接口/页面是否存在**（存在即"已完成"），不依赖人工进度判断。禁止出现"计划中""部分完成"等无法从代码验证的状态，避免文档与代码脱节。`草稿` 条目在文档维护（§3.8）时核对代码：已实现的转"已完成"，长期无进展的删除。

### 3.5 目录结构

```
docs/
├── 00-INDEX.md      # 文档总索引（唯一入口，登记全部编号文档）
├── adr/             # 决策记录（ADR-NNN.md）
├── features/        # 功能条目（FEAT-NNN.md，核心）
└── guides/          # 通用指南（GUIDE-NNN.md）
```

### 3.6 新文档创建规则

1. 判断类型：技术决策 → `ADR`；功能/页面/API/数据表 → `FEAT`；其他（依赖、优化、FAQ）→ `GUIDE`
2. 从 `docs/00-INDEX.md` 领取下一个可用编号
3. 按 3.3 / 3.4 模板创建，套用统一结构
4. 在 `docs/00-INDEX.md` 登记（编号、标题、路径、日期）

### 3.7 文档改造规则（重写 / 合并 / 拆分 / 删除）

| 操作 | 规则 |
|------|------|
| 重写 | 保留原编号；标题可改；`docs/00-INDEX.md` 更新日期与摘要 |
| 合并 | 保留编号较小者的编号；被合并方在索引中标记"并入 ADR-NNN"，编号作废不重用 |
| 拆分 | 原文档保留最相关部分与编号；拆出的新文档领取新编号 |
| 删除 | 索引中标记"已废弃"，编号作废不重用；文件删除或移入归档 |

**红线**：任何改造不得改动外部契约（API 路径、配置键、表结构、前端路由）。出现此类改动立即回退并报"变更不合理"（见 AGENTS.md §六）。

### 3.8 文档维护流程（创建 → 改造 → 迁移 → 维护）

完整流程的结束条件：

| 阶段 | 动作 | 完成标准 |
|------|------|---------|
| 创建 | 按 3.6 创建文档 | 编号登记、模板完整、`docs/00-INDEX.md` 已更新 |
| 改造 | 按 3.7 处理存量文档 | 无未编号文档、索引与实际文件一致 |
| 迁移 | 旧文档迁移到新体系 | 所有旧文档已分类落位、无根目录散落 `.md`（`README.md` 除外） |
| 维护 | 每次代码变更后核对 | 文档与代码一致；信息密度达标（无过期/冗余段落） |

**结束条件（什么样才算完成）**：`docs/00-INDEX.md` 登记的全部文档均存在、编号无冲突、无散落未编号文档、无过期内容。满足即本轮维护结束。

### 3.9 迁移方案（旧体系 → 新体系）

| 现有文件 | 处置 | 落位 |
|---------|------|------|
| `README.md` | 保留（对外项目介绍，不编号，是新人/用户入口） | 根目录不动 |
| `RAG_检索优化指南.md` | 拆分：检索架构现状 → 归入 `FEAT-013` 关联文档；各优化方案（混合检索/重排序/图谱引导等）→ 逐条转为 `ADR-NNN` 决策记录 | `docs/adr/` |
| `Neo4j+Chroma依赖.md` | 归入通用指南 | `docs/guides/GUIDE-001.md`（环境依赖） |
| `AGENTS.md` / `CLAUDE.md` | 保持职责分工（本文件已重写为规范文档） | 根目录 |

迁移顺序：① 建 `docs/` 目录与 `00-INDEX.md` → ② 按 3.9 表逐文件分类 → ③ 按 3.3/3.4 模板改写落位 → ④ 执行 3.8 结束条件校验。

### 3.10 验收标准与验收流程

**验收入口**：`docs/00-INDEX.md`（从总索引进入，逐项核对）。

**验收标准（逐项打勾）**：
1. 每个编号文档：存在、可读、模板结构一致（标题含编号、含状态/日期）
2. 每个 FEAT 条目：页面/API/数据表三字段齐全，且与代码核对一致（前端路由、API 端点、表名）
3. 每个 ADR：有背景/决策/后果三段，决策可执行、不含模糊措辞
4. 索引 `docs/00-INDEX.md`：无缺号、无重复、无已删文档残留
5. 外部契约文档：无内部结构引用被改动（见 AGENTS.md §六）
6. 信息密度：每个条目能回答"触发 → 做什么 → 反馈"，无抽象套话

**验收执行**：逐项跑一遍上面的核对，输出通过/不通过清单。不通过项转"验收反馈"。

### 3.11 验收反馈的收集与整合

1. **收集**：验收不通过的条目，记录在 `docs/00-INDEX.md` 对应文档行的"待整改"备注，或新建 `docs/guides/` 下反馈清单
2. **整理**：反馈按 创建 / 改造 / 迁移 / 维护 四个阶段归类
3. **整合**：按 3.6/3.7 规则整改对应文档 → 更新索引 → 重新执行 3.10 验收 → 循环直到全绿

---

## 四、目录结构（全貌）

```
D:\NC/
├── AGENTS.md            # 目标 + 实现方式（见 AGENTS.md）
├── CLAUDE.md            # 本文件：开发约定与文档规范
├── README.md            # 对外项目介绍
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI 入口，lifespan 管理 Neo4j/Chroma 连接
│   │   ├── config.py        # pydantic-settings，所有环境变量集中管理
│   │   ├── database.py      # SQLite 初始化
│   │   ├── logger.py        # 日志配置
│   │   ├── api/             # 路由层（auth, documents, search, graph, chat,
│   │   │                    #   progress, tags, timeline, dashboard）
│   │   ├── models/          # Pydantic 数据模型
│   │   ├── services/        # 业务逻辑层
│   │   ├── auth/            # JWT 认证
│   ├── tests/               # pytest 测试
│   └── eval/                # 检索质量评估
├── frontend/
│   └── src/
│       ├── views/           # 页面（与 FEAT 条目一一对应）
│       └── components/      # UI 组件 + 布局
├── docs/                    # 内部文档体系（见 §三）
└── .codegraph/              # CodeGraph 索引（勿手动修改）
```
