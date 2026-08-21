# AGENTS.md — 知识图谱系统：目标与实现方式

> 本文档记录**系统要处理的目标（What）以及实现该目标的方式（How）**。
> 开发约定与规范（编码约定、文档编号规则、决策记录格式、文档维护流程）见 [CLAUDE.md](./CLAUDE.md)。
> 两个文件共同构成项目知识库：**AGENTS.md 回答"做什么、怎么做"，CLAUDE.md 回答"按什么约定做"**。

---

## 一、系统目标（What）

**Knowledge Graph System** — 多用户知识图谱平台：用户上传文档，系统自动抽取实体与关系构建知识图谱，提供语义搜索与 AI 问答。

### 1.1 核心能力

| 能力 | 说明 | 对应功能条目 |
|------|------|-------------|
| 用户认证 | 注册/登录/JWT | FEAT-001 |
| 文档管理 | 上传解析、分块、标签、处理进度 | FEAT-002~006, FEAT-016 |
| 实体抽取 | LLM 抽取实体/关系写入 Neo4j | FEAT-002 |
| 图谱可视化 | 实体关系图、实体详情、聚类地图、时间线 | FEAT-007~009, 014 |
| 语义搜索 | 向量 + BM25 融合检索 + 重排序 | FEAT-013 |
| AI 问答 | 基于图谱上下文的 RAG 对话，支持消息反馈 | FEAT-010~012 |
| 仪表盘 | 系统数据总览 | FEAT-015 |

### 1.2 技术栈

| 层 | 技术栈 |
|----|--------|
| 后端 | FastAPI + Python 3.x |
| 图数据库 | Neo4j（实体/关系存储） |
| 向量数据库 | ChromaDB（Embedding 检索） |
| 关系数据库 | SQLite（用户/文档/会话等元数据） |
| 前端 | Vue 3 + Vite |
| LLM | 百炼 Qwen / Kimi / SiliconFlow（可切换） |
| Embedding | Qwen3-Embedding-8B（via SiliconFlow） |
| Reranker | Qwen3-Reranker-8B |

---

## 二、目标实现方式（How）

### 2.1 功能条目模型（文档组织核心）

**功能条目 = 功能 + 页面 + API + 数据表**。功能条目是系统的最小功能载体，也是文档组织的核心单位。

规范约束（新功能必须遵守）：

- **1 个功能条目 ↔ 1 个页面**：一个功能条目只能对接一个页面，页面与功能一一对应
- **1 个功能条目 ↔ 1 个 API**：一个功能条目只能对接一个 API 路由
- **1 个功能条目 ↔ 1 个数据表**：一个功能条目只能对接一个数据表（Neo4j 实体/Chroma 向量属存储层，不占 SQLite 数据表名额）
- **以功能条目为核心组织文档**：页面文档、API 文档、数据表文档都通过功能条目编号（FEAT-NNN）作为纽带关联
- **以功能条目为检索入口**：查功能、查页面、查 API、查数据表，一律从 FEAT-NNN 进入文档体系

> ⚠️ **存量现状**：本系统早期开发时未按该模型拆分，部分条目共享页面/数据表（见 §2.2 "现状"列）。**新功能一律遵守上述约束**；存量条目的拆分改造按 [CLAUDE.md 文档改造规则](./CLAUDE.md) 处理。

### 2.2 当前功能条目清单（存量现状，如实映射）

> 依据 `frontend/src/views/`、`backend/app/api/`、`backend/data/sqlite/app.db` 核对，整理日 2026-08-21。

| 编号 | 功能条目 | 页面 | API | 数据表 | 现状 |
|------|---------|------|-----|--------|------|
| FEAT-001 | 用户认证 | Home.vue（登录） | `/api/auth`（register/login/me） | users | ✅ 合规 |
| FEAT-002 | 文档上传与解析 | DocumentsPage.vue（上传区） | `POST /api/documents/upload` | documents | ⚠️ 与 FEAT-003/016 共享页面 |
| FEAT-003 | 文档列表 | DocumentsPage.vue（列表区） | `GET /api/documents` | documents | ⚠️ 与 FEAT-002/016 共享页面与表 |
| FEAT-004 | 文档详情与分块 | DocumentDetailPage.vue | `GET /api/documents/{id}/detail`、`/chunks` | chunks | ✅ 合规 |
| FEAT-005 | 文档标签 | DocumentDetailPage.vue（标签区） | `GET/POST/DELETE /api/documents/{id}/tags`、`GET /api/tags` | document_tags | ⚠️ 与 FEAT-004 共享页面 |
| FEAT-006 | 文档聚类地图 | ClusterMapPage.vue | `GET /api/documents/cluster-map` | （Chroma） | ✅ 合规 |
| FEAT-007 | 知识图谱浏览 | GraphPage.vue | `GET /api/graph/entities`、`/visualization`、`POST /api/graph/query` | （Neo4j） | ✅ 合规 |
| FEAT-008 | 实体详情 | EntityDetailPage.vue | `GET /api/graph/entities/{name}/detail` | （Neo4j） | ✅ 合规 |
| FEAT-009 | 实体时间线动画 | EntityTimelineAnimationPage.vue | `GET /api/timeline` | （Neo4j） | ✅ 合规 |
| FEAT-010 | AI 对话 | ChatPage.vue | `POST /api/chat`、`/stream` | conversations, messages | ⚠️ 涉及多表 |
| FEAT-011 | 对话历史管理 | ConversationHistoryPage.vue | `GET/DELETE /api/chat/conversations` | conversations | ⚠️ 与 FEAT-010 共享表 |
| FEAT-012 | 消息反馈 | ChatPage.vue（反馈区） | `GET/POST/DELETE /api/chat/messages/{id}/feedback` | message_feedback, message_sources | ⚠️ 与 FEAT-010 共享页面 |
| FEAT-013 | 语义搜索 | SearchPage.vue | `POST /api/search` | （Chroma + chunks） | ✅ 合规 |
| FEAT-014 | 实体时间线 | TimelinePage.vue | `GET /api/timeline` | （Neo4j） | ⚠️ 与 FEAT-009 共用 API |
| FEAT-015 | 仪表盘 | DashboardPage.vue | `GET /api/dashboard/summary` | （聚合查询，无专属表） | ⚠️ 无数据表 |
| FEAT-016 | 文档处理进度 | DocumentsPage.vue（进度区） | `GET /api/progress/{doc_id}`、`/history` | progress_history | ⚠️ 与 FEAT-002/003 共享页面 |

> 数据表完整清单（12 张）：`users`、`documents`、`chunks`、`document_tags`、`conversations`、`messages`、`message_feedback`、`message_sources`、`progress_history`、`embedding_cache`、`schema_version`（`sqlite_sequence` 为 SQLite 内部表）。

### 2.3 以功能条目为核心检索

- **入口**：查功能 → 定位 FEAT-NNN → 该条目文档内含页面路由、API 端点、数据表名，及其关联的决策记录（ADR-NNN）
- **全表索引**：功能条目清单维护在 `docs/features/00-INDEX.md`（见 §三 目录结构）
- **规则**：新功能先编 FEAT-NNN → 再写页面/API/数据表文档 → 全部挂在条目下

---

## 三、文档体系组织方式

### 3.1 目录结构

```
D:\NC/
├── AGENTS.md            # 本文件：目标 + 实现方式（how & what）
├── CLAUDE.md            # 开发约定与文档规范
├── README.md            # 对外项目介绍（用户/新人入口，不参与内部编号）
└── docs/                # 内部文档体系
    ├── 00-INDEX.md      # 文档总索引（唯一入口，登记全部编号文档）
    ├── adr/             # 决策记录（ADR-NNN.md）
    ├── features/        # 功能条目（FEAT-NNN.md，核心）
    └── guides/          # 通用指南（GUIDE-NNN.md：依赖、优化方案、FAQ）
```

### 3.2 查找规则

| 想查什么 | 去哪 |
|---------|------|
| 某个功能怎么实现的 | `docs/features/FEAT-NNN.md`（从 §2.2 清单定位编号） |
| 某个页面对应什么功能 | §2.2 清单按页面列反查 |
| 某个 API / 数据表归属 | §2.2 清单按 API/数据表列反查 |
| 为什么做某个技术决策 | `docs/adr/ADR-NNN.md`（索引见 `docs/adr/00-INDEX.md`） |
| 环境依赖、优化方案等 | `docs/guides/GUIDE-NNN.md` |

---

## 四、CodeGraph 使用规范

本项目已接入 CodeGraph MCP。**回答代码问题优先用 `codegraph_explore`，不要逐文件 grep/read。**

### 4.1 初次加载 / 全新仓库

```bash
# 克隆或首次打开项目时，必须先初始化索引
codegraph init
```

### 4.2 代码修改后

- **自动同步**：文件保存后监听器约 2 秒内自动更新索引，无需手动操作
- **批量修改后**（git pull、大规模重构）：手动同步一次
  ```bash
  codegraph sync
  ```
- **验证索引状态**：
  ```bash
  codegraph status
  ```

### 4.3 回答代码问题时

- 优先使用 `codegraph_explore` 工具，**不要**逐文件 grep / read
- 典型查询：调用链、模块依赖、改动影响范围、"X 是怎么工作的"
- CodeGraph 返回的代码片段视为**已读取**，无需再次打开文件
- 若索引中有 `⚠️` 过期标记，再用 Read 工具读取对应文件的最新内容

### 4.4 CLI 速查

```bash
codegraph query <keyword>       # 搜索符号
codegraph callers <symbol>      # 谁调用了它
codegraph callees <symbol>      # 它调用了谁
codegraph impact <symbol>       # 改动影响范围
codegraph explore <question>    # 自然语言探索（等同 MCP 工具）
```

---

## 五、项目级 Skill 管理

### 5.1 全局与项目 Skill 的区分与联动

| 维度 | 全局 Skill | 项目级 Skill |
|------|-----------|-------------|
| 存放位置 | `~/.claude/skills/` | `.claude/skills/`（本项目仓库内，随 git 走） |
| 适用面 | 所有项目通用的能力（文档、PDF、浏览器等） | 仅本项目需要的专用流程 |
| 维护 | 全局配置中心统一管理 | 随项目代码库版本管理 |
| 安装 | 一次性，改全局 | 按项目需要，通过 marketplace 安装 |

**联动规则：**
- 项目能用到全局 skill 时**优先复用**，不在项目内重复安装
- 项目专属流程（如本项目"文档体系维护"）才装为项目级 skill
- 状态与维护：全局 skill 由全局 settings 控制启停；项目级 skill 通过 `.claude/settings.json` 的 `enabledPlugins` 控制

### 5.2 安装方式

**路径与清单结构：**

```
.claude/settings.json      # enabledPlugins 控制启用的插件/skill
.claude/skills/            # 项目级 skill 目录
```

**安装步骤（推荐走 marketplace 安装，不手工拷贝）：**

1. 打开 Claude Code marketplace / 插件市场，搜索所需 skill
2. 安装到项目（`enabledPlugins` 写入 `.claude/settings.json`）
3. 验证：
   ```bash
   # 确认 skill 出现在可用列表
   claude --list-skills 2>/dev/null || cat .claude/settings.json
   ```

**验证清单：** ① 安装路径正确（项目级在 `.claude/`）② `enabledPlugins` 已登记 ③ skill 触发词可用

> 注意：全局 settings 中已禁用的插件，不应再以项目级方式重新启用，除非确有项目需要并知悉维护成本（全局状态以 `~/.claude/settings.json` 为准，项目状态以 `.claude/settings.json` 为准，两处可不同）。

---

## 六、兼容性约束（外部接口不感知内部文档调整）

**原则：内部文档调整不得改变任何外部契约。**

- **外部契约清单**：对外 API 路径与响应结构、配置格式（`.env` 键名）、数据库表结构、前端路由。这些由代码定义，文档只记录、不修改
- **内部文档调整边界**：文档编号、目录位置、措辞、拆分合并——调整只在 `docs/`、`AGENTS.md`、`CLAUDE.md` 内部进行
- **变更自检**：调整内部文档后，若任何外部契约文档（API 文档、配置说明）出现了指向内部结构的引用变化（例如 "检索结构参考:xxx" 或 "检索结构参考:TOML" 这类原本应保持不变的文本被改动），**立即判定为"变更不合理"并报错**——说明内部调整越界污染了外部接口文档，需回退
- **强制执行**：外部契约文档中凡是标注"内部实现参考"的段落，只允许指向，不允许被内部调整反向改写
