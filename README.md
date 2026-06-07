# 🧠 知识图谱系统 (Knowledge Graph System)

> 一个支持多用户的知识图谱系统，整合 Neo4j 图数据库和 ChromaDB 向量数据库，实现文档知识管理、可视化图谱与 RAG 对话。
> 让散落各处的知识连成一张可探索的网 ✨

## ⚠️ 安全提醒

> 🚨 **本项目的 `.env`（根目录与 `backend/`）包含真实 API 密钥和 JWT 签名密钥。任何 fork、镜像或截图前必须：**
> 1. 在硅基流动 / Moonshot / 阿里云百炼控制台**轮换（撤销并重新签发）**这些 API 密钥
> 2. 用 `python -c "import secrets; print(secrets.token_urlsafe(48))"` 生成新 `JWT_SECRET` 并替换
> 3. 🙅 **永远不要**把 `.env` 提交到 git（已在 `.gitignore` 中，但仍请小心处理历史记录）

详见 [安全配置](#安全配置) 一节。

## 核心功能

1. 📚 **文档知识库**：支持上传 PDF/Word/TXT/MD 格式，自动转换为 Markdown 并按层级切块
2. 🔍 **混合检索**：基于硅基流动 Qwen3-Embedding-8B 向量检索 + BM25 关键词 + Qwen3-Reranker 重排序 + RRF 融合
3. 🕸️ **知识图谱可视化**：d3 + Vue 3 实现的交互式力导向图谱，支持节点拖拽、实体编辑、合并与删除
4. 💬 **大模型对话**：基于 Kimi / 百炼 (qwen-flash) 的 RAG 问答，支持流式 / 非流式、图谱增强 RAG、对比模式、消息反馈
5. 📊 **仪表盘与时间线**：文档 / 实体 / 标签统计、月度增长、近期活动、实体首现时间线
6. 🗺️ **文档聚类地图**：2D PCA 投影可视化所有文档的语义分布
7. 🔐 **用户隔离**：JWT 账号密码认证，SQLite 存储用户数据，Neo4j/ChromaDB 通过 `user_id` 标签隔离
8. 🛡️ **健壮性**：统一 logging、批量写入（Neo4j UNWIND）、输入校验、4xx 不重试、防 401 重定向循环

## 技术架构

| 层级 | 技术 |
|------|------|
| 🎨 前端 | Vue 3.5 + Vite 7 + Pinia + Vue Router + d3.js · 学术雅致派双主题（Fraunces + Plus Jakarta Sans + JetBrains Mono） |
| ⚙️ 后端 API | FastAPI 0.115 + Python 3.11 + uvicorn |
| 🕸️ 图数据库 | Neo4j 5.14 (Docker, APOC 插件) |
| 🧬 向量数据库 | ChromaDB 0.4.18 (Docker) |
| 💾 用户数据 | SQLite + SQLAlchemy 2.0 + aiosqlite (单文件) |
| 🧩 嵌入模型 | 硅基流动 Qwen3-Embedding-8B (API) |
| 🎯 重排序 | 硅基流动 Qwen3-Reranker-8B (API) |
| 🤖 大模型 | Kimi API (Moonshot) / 百炼 qwen-flash (阿里云 DashScope) / 硅基流动 Qwen3-8B |
| 🔑 密码哈希 | bcrypt 3.2.2（原生） |
| 📝 日志 | Python `logging` + RotatingFileHandler（统一在 `app/logger.py`） |

## 项目目录结构

```
D:/NC/
├── docker-compose.yml           # Neo4j + ChromaDB 服务定义
├── .env / .env.example          # 部署环境变量（example 为模板）
├── package.json                 # 根级 npm 脚本（concurrently 一键启动）
├── start-dev.bat / start-dev.sh # Windows / Bash 一键启动脚本
├── backend/
│   ├── .env / .env.example      # 后端实际加载的 .env
│   ├── app/
│   │   ├── api/                 # API 端点
│   │   │   ├── auth.py          # 注册/登录/me
│   │   │   ├── documents.py     # 文档上传/列表/详情/删除/切块/标签/聚类
│   │   │   ├── chat.py          # RAG 对话（流式 + 非流式 + 反馈）
│   │   │   ├── graph.py         # 实体/图谱查询/可视化/编辑/合并
│   │   │   ├── search.py        # 语义检索
│   │   │   ├── progress.py      # 文档处理进度（SSE + 历史）
│   │   │   ├── tags.py          # 用户级标签聚合
│   │   │   ├── timeline.py      # 时间线聚合数据
│   │   │   └── dashboard.py     # 仪表盘汇总
│   │   ├── auth/                # JWT 鉴权与 bcrypt 密码哈希
│   │   ├── models/              # Pydantic 数据模型（含字段校验）
│   │   ├── services/            # 核心服务
│   │   │   ├── embedding.py     # 硅基流动嵌入（限流 + JSON 缓存）
│   │   │   ├── llm.py           # 百炼 / Kimi / 硅基流动 多 LLM
│   │   │   ├── chunker.py       # Markdown 层级切块
│   │   │   ├── entity_extractor.py  # 实体 + 关系提取（LLM 模式）
│   │   │   ├── neo4j_client.py  # Neo4j 封装（含 UNWIND 批量）
│   │   │   ├── chroma_client.py # ChromaDB 封装
│   │   │   ├── bm25.py          # BM25 关键词检索（per-user 索引）
│   │   │   ├── fusion.py        # RRF / 加权融合
│   │   │   ├── reranker.py      # 硅基流动 Rerank
│   │   │   ├── query_processor.py  # 查询改写 / 变体 / 实体抽取
│   │   │   └── progress_tracker.py  # SSE 进度跟踪
│   │   ├── utils/md_parser.py   # Markdown 解析（markitdown 防御性封装）
│   │   ├── config.py            # 配置管理（含 CORS 白名单 + 生产校验）
│   │   ├── database.py          # SQLite 初始化
│   │   ├── logger.py            # 统一 logging 配置
│   │   └── main.py              # FastAPI 入口
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/                     # Vue 3 + d3 前端（学术雅致派双主题）
│   ├── src/
│   │   ├── components/
│   │   │   ├── GraphPanel.vue   # 图谱核心面板（d3 力导向）
│   │   │   ├── layout/          # Layout / Sidebar
│   │   │   ├── decor/           # 装饰层（Decor / shapes）
│   │   │   └── ui/              # 通用 UI（Button/Card/Tag/Stat/Switch/...）
│   │   ├── views/
│   │   │   ├── Home.vue                  # 登录 / 注册
│   │   │   ├── DocumentsPage.vue         # 文档列表 + 上传
│   │   │   ├── DocumentDetailPage.vue    # 文档详情（标签 / 实体 / 关联）
│   │   │   ├── ClusterMapPage.vue        # 2D PCA 聚类地图
│   │   │   ├── GraphPage.vue             # 图谱主页（搜索 / 可视化）
│   │   │   ├── EntityDetailPage.vue      # 实体详情页
│   │   │   ├── EntityTimelineAnimationPage.vue  # 实体时间线动画
│   │   │   ├── DashboardPage.vue         # 仪表盘
│   │   │   ├── TimelinePage.vue          # 时间线
│   │   │   ├── ChatPage.vue              # RAG 对话
│   │   │   └── SearchPage.vue            # 语义检索
│   │   ├── composables/
│   │   │   └── useTheme.js       # 亮/暗主题切换 + localStorage 持久化
│   │   ├── api/                  # axios 客户端封装（auth/documents/chat/graph/...）
│   │   ├── router/index.js       # 路由 + 鉴权守卫
│   │   ├── store/auth.js         # Pinia auth store
│   │   ├── utils/                # categorize / timelineAnim 工具
│   │   ├── styles/variables.css  # 学术雅致派设计令牌（墨蓝+琥珀双主题）
│   │   ├── App.vue
│   │   └── main.js
│   ├── index.html
│   ├── package.json
│   └── vite.config.js
└── data/                         # 数据目录 (gitignore)
    ├── sqlite/                  # SQLite 数据库
    ├── uploads/                 # 上传的原文件
    ├── logs/                    # 应用日志（RotatingFileHandler）
    ├── neo4j/                   # Neo4j 数据卷
    └── chromadb/                # ChromaDB 数据卷
```

## 🚀 快速开始

### 1. 🐳 启动基础设施服务

```bash
docker-compose up -d
```

这将启动：
- 🕸️ Neo4j: http://localhost:7474 (浏览器界面), bolt://localhost:7687
- 🧬 ChromaDB: http://localhost:8000

### 2. 🔑 配置环境变量（**从模板复制**，不要直接编辑 `.env.example`）

```bash
# 根目录（用于 IDE / 工具）
cp .env.example .env

# 后端实际加载的 .env
cd backend
cp .env.example .env
```

**必填项：**
- `SILICON_FLOW_API_KEY` — 硅基流动 API 密钥（用于 Embedding + Rerank + 备用 LLM）
- `BAILIAN_API_KEY` — 阿里云百炼 API 密钥（用于 LLM）
- `KIMI_API_KEY` — Moonshot Kimi API 密钥（备用 LLM）
- `JWT_SECRET` — **生产环境必须**用 `python -c "import secrets; print(secrets.token_urlsafe(48))"` 生成

可选调整：
- `CORS_ALLOWED_ORIGINS` — 逗号分隔的允许来源（默认 `http://localhost:5173`）
- `APP_ENV` — `development`（默认）或 `production`（生产环境会拒绝默认 JWT_SECRET 启动）
- `UPLOAD_DIR` / `SQLITE_PATH` / `LOG_DIR` — 数据与日志目录
- `ENABLE_LLM_EXTRACTION` / `USE_RULE_EXTRACTION` — 实体提取策略（默认纯 LLM 模式）
- `ENTITY_BATCH_SIZE` — 实体提取批大小（默认 200）

### 3. 📦 安装后端依赖

```bash
# 推荐：使用根目录 .venv
cd ..
.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
.venv/Scripts/python.exe -m spacy download zh_core_web_sm
```

### 4. ▶️ 运行后端服务

```bash
cd backend
../.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

或使用一键启动脚本（Windows / Bash 双版本 / 根级 npm 脚本）：

```bash
# Windows
start-dev.bat

# Bash (Git Bash / WSL)
./start-dev.sh

# 或：根目录 npm 脚本（需要先 npm install）
npm run dev          # concurrently 同时启动前后端
npm run backend      # 仅后端
npm run frontend     # 仅前端
```

API 文档地址：http://localhost:8001/docs
健康检查：    http://localhost:8001/health

### 5. 🎨 安装并运行前端

```bash
cd frontend
npm install
npm run dev
```

前端访问地址：http://localhost:5173
Vite 已配置 `/api` 代理到 `http://localhost:8001`。

## 🔌 API 接口设计

所有需要鉴权的接口都要求 `Authorization: Bearer <jwt>` header。SSE 进度接口也通过 header 鉴权，**不接受** URL `?token=` 参数。

### 🔐 认证 `/api/auth`
- `POST /api/auth/register` — 用户注册（用户名 3-50 字符 `[A-Za-z0-9_.-]`、密码 ≥ 8 字符且必须含字母+数字）
- `POST /api/auth/login` — 用户登录（OAuth2 form-data，返回 JWT）
- `GET  /api/auth/me` — 获取当前用户信息

### 📄 文档 `/api/documents`
- `POST   /api/documents/upload` — 上传文档（multipart/form-data，支持 .pdf/.docx/.doc/.txt/.md/.markdown，≤ 10MB）
- `GET    /api/documents` — 列出用户文档（分页：`?skip=0&limit=100`；可选 `?tag=xxx` 过滤）
- `GET    /api/documents/{id}/detail` — 文档详情（metadata + 标签 + 切块统计 + 关键实体 + 关联文档）
- `GET    /api/documents/{id}/chunks` — 文档切块列表
- `GET    /api/documents/cluster-map` — 2D PCA 聚类地图（所有文档的语义投影）
- `DELETE /api/documents/{id}` — 删除文档（联动清理 SQLite + ChromaDB + Neo4j）
- `GET    /api/documents/{id}/tags` — 文档标签列表
- `POST   /api/documents/{id}/tags` — 添加文档标签（幂等，返回最新标签列表）
- `DELETE /api/documents/{id}/tags/{tag:path}` — 移除文档标签（返回最新标签列表）

### 🔍 检索 `/api/search`
- `POST /api/search` — 语义检索（向量 + BM25 + Rerank + 图谱关联）

### 🕸️ 图谱 `/api/graph`
- `GET    /api/graph/entities?query=xxx` — 实体名称模糊搜索
- `POST   /api/graph/query` — 语义图谱查询（按 `query` 在向量空间找相关 chunk，再展开图谱）
- `GET    /api/graph/visualization` — 获取当前用户的全量图谱可视化数据
- `GET    /api/graph/entities/{name:path}/detail` — 实体详情（实体 + 统计 + 文档 + 关联实体 + 示例 chunk）
- `PATCH  /api/graph/entities/{name:path}` — 更新实体的类型 / 描述
- `DELETE /api/graph/entities/{name:path}` — 删除实体（清理 MENTIONS / RELATES_TO）
- `POST   /api/graph/entities/merge` — 合并实体（`{source, target}`，source 被删除并重新指向 target）

### 💬 对话 `/api/chat`
- `POST   /api/chat` — 发送消息（非流式 RAG 问答，支持 `use_graph_rag` / `compare_mode` / `with_followups`）
- `POST   /api/chat/stream` — 发送消息（Server-Sent Events 流式）
- `GET    /api/chat/conversations` — 获取对话列表
- `GET    /api/chat/conversations/{id}/messages` — 获取对话历史
- `DELETE /api/chat/conversations/{id}` — 删除对话
- `POST   /api/chat/messages/{id}/feedback` — 提交消息反馈（`{rating, note?}`）
- `GET    /api/chat/messages/{id}/feedback` — 获取消息反馈
- `DELETE /api/chat/messages/{id}/feedback` — 删除消息反馈

### 🏷️ 标签 `/api/tags`
- `GET /api/tags?q=xxx` — 用户级标签聚合（按使用频次倒排，可选模糊搜索）

### 🕒 时间线 `/api/timeline`
- `GET /api/timeline` — 文档月度分布 + 近期文档 + 实体首现时间线

### 📊 仪表盘 `/api/dashboard`
- `GET /api/dashboard/summary` — 仪表盘汇总（统计 + 近期活动 + 热门实体 + 热门标签 + 月度增长）

### ⏳ 进度 `/api/progress`
- `GET /api/progress/{doc_id}` — SSE 流式进度事件（30 秒 keepalive，完成/错误自动关闭）
- `GET /api/progress/{doc_id}/history` — 历史进度事件列表

### 💓 健康检查
- `GET /health` — 返回 `{"status": "healthy"}`
- `GET /` — 返回 API 元信息

## 🌊 数据流架构

### 📤 文档上传流程
```
PDF/Word/TXT/MD → markitdown → Markdown → 层级解析 → 语义切块
    → 硅基流动 Embedding (Qwen3-Embedding-8B) → ChromaDB 存储
    → Neo4j 实体关系提取 (BM25 索引同步) → SSE 进度推送
```

### 🔍 检索对话流程
```
用户 Query → 查询改写 (LLM) → Embedding
    → 向量检索 + BM25 → RRF 融合 → Qwen3-Reranker
    → 上下文扩展 (前/后 chunk) → Neo4j 图谱关联
    → 构建 Prompt → Kimi / 百炼 LLM → 流式返回结果
```

## 🧬 核心数据模型

### 📦 Chunk 数据结构
```python
{
    "chunk_id": "uuid",
    "document_id": "doc_uuid",
    "user_id": "user_uuid",
    "content": "文本内容",
    "hierarchy": {
        "level": 2,                    # 标题层级
        "path": ["标题1", "标题1.1"],    # 层级路径
        "parent_id": "parent_chunk_id"
    },
    "position": {
        "start_line": 10,
        "end_line": 25,
        "prev_chunk_id": "uuid",       # 前一块（用于上下文召回）
        "next_chunk_id": "uuid"        # 后一块
    }
}
```

### 🕸️ Neo4j 图谱数据模型
```cypher
// 节点
(:User {user_id, username, password_hash, created_at})
(:Document {doc_id, title, user_id, file_path, created_at})
(:Chunk {chunk_id, content, embedding_id, user_id, position, hierarchy_path})
(:Entity {name, type, description, user_id})              // 从文本提取

// 关系
(:User)-[:OWNS]->(:Document)
(:Document)-[:CONTAINS]->(:Chunk)
(:Chunk)-[:NEXT]->(:Chunk)                               // 文档顺序
(:Chunk)-[:MENTIONS]->(:Entity)                          // 提及实体
(:Entity)-[:RELATES_TO {relation_type}]->(:Entity)       // 实体关系
```

## 🔐 安全配置

| 项 | 状态 | 备注 |
|----|------|------|
| 🔑 JWT 签名 | **必须替换** | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| 🌐 CORS 来源 | 白名单 | 通过 `CORS_ALLOWED_ORIGINS` 配置，禁用通配符 |
| 🔒 密码哈希 | bcrypt | 72 字节硬截断；不 mutate 调用方入参 |
| 🗝️ API 密钥 | 环境变量 | **勿**硬编码到代码；`.env` 已 gitignore |
| 🚦 生产模式 | `APP_ENV=production` | 默认 JWT_SECRET 启动时直接 `RuntimeError` |
| 🚪 401 处理 | 拦截器去重 | 防重入 + 派发 `auth:logout` 事件 |
| 📡 进度 SSE | Authorization header | 不接受 `?token=` URL 参数（避免日志泄露） |
| 💾 嵌入缓存 | JSON 序列化 | 取代 `pickle`（防反序列化漏洞） |
| ✅ 注册校验 | 强校验 | 用户名 `[A-Za-z0-9_.-]`、密码 ≥ 8 字符含字母+数字 |
| 👥 Neo4j 删除 | 跨用户隔离 | `delete_document` step 4 强制 `user_id` 过滤 |
| ⚡ 批量写入 | UNWIND | 实体/关系/MENTIONS 由 N 次往返降为 1 次 |

## ⚙️ 配置说明

### 🛠️ 环境变量

| 变量名 | 说明 | 默认值 | 必填 |
|--------|------|--------|------|
| `APP_ENV` | 运行环境 | `development` | 否 |
| `NEO4J_URI` | Neo4j 连接地址 | `bolt://localhost:7687` | 否 |
| `NEO4J_USER` | Neo4j 用户名 | `neo4j` | 否 |
| `NEO4J_PASSWORD` | Neo4j 密码 | `12345678` | 否 |
| `CHROMA_HOST` / `CHROMA_PORT` | ChromaDB 主机端口 | `localhost` / `8000` | 否 |
| `SQLITE_PATH` | SQLite 数据库路径 | `./data/sqlite/app.db` | 否 |
| `SILICON_FLOW_API_KEY` | 硅基流动 API 密钥（Embedding + Rerank + 备用 LLM） | - | **是** |
| `SILICON_FLOW_BASE_URL` | 硅基流动 base URL | `https://api.siliconflow.cn/v1` | 否 |
| `KIMI_API_KEY` | Moonshot Kimi 密钥（备用 LLM） | - | 否 |
| `KIMI_BASE_URL` | Moonshot base URL | `https://api.moonshot.cn/v1` | 否 |
| `BAILIAN_API_KEY` | 阿里云百炼 LLM 密钥 | - | **是** |
| `BAILIAN_BASE_URL` | 百炼 base URL | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 否 |
| `BAILIAN_MODEL` | 百炼模型 | `qwen-flash` | 否 |
| `JWT_SECRET` | JWT 签名密钥 | 占位符（生产必须替换） | **是** |
| `JWT_ALGORITHM` | JWT 算法 | `HS256` | 否 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Token 有效期（分钟） | `60` | 否 |
| `UPLOAD_DIR` | 上传文件目录 | `./data/uploads` | 否 |
| `MAX_FILE_SIZE` | 最大文件大小（字节） | `10485760`（10MB） | 否 |
| `EMBEDDING_MODEL` | 嵌入模型名 | `Qwen/Qwen3-Embedding-8B` | 否 |
| `EMBEDDING_DIM` | 嵌入维度 | `1024` | 否 |
| `RERANK_MODEL` | Rerank 模型 | `Qwen/Qwen3-Reranker-8B` | 否 |
| `CORS_ALLOWED_ORIGINS` | 允许的 CORS 来源（逗号分隔） | localhost 开发地址 | 否 |
| `ENABLE_LLM_EXTRACTION` | 启用 LLM 实体提取 | `True` | 否 |
| `USE_RULE_EXTRACTION` | 同时使用规则提取 | `False` | 否 |
| `ENTITY_BATCH_SIZE` | 实体提取批大小 | `200` | 否 |
| `ENTITY_EXTRACTION_DELAY` | 实体提取批间延迟（秒） | `0` | 否 |
| `LOG_DIR` / `LOG_LEVEL` | 日志目录与级别 | `./data/logs` / `INFO` | 否 |

## 🧭 前端路由总览

| 路径 | 页面 | 说明 |
|------|------|------|
| `/login` | `Home.vue` | 登录 / 注册 |
| `/documents` | `DocumentsPage.vue` | 文档列表与上传 |
| `/documents/:id` | `DocumentDetailPage.vue` | 文档详情 |
| `/documents/map` | `ClusterMapPage.vue` | 2D PCA 聚类地图 |
| `/graph` | `GraphPage.vue` | 图谱主页（搜索 + 可视化） |
| `/graph/timeline-animation` | `EntityTimelineAnimationPage.vue` | 实体时间线动画 |
| `/entities/:name` | `EntityDetailPage.vue` | 实体详情 |
| `/dashboard` | `DashboardPage.vue` | 仪表盘 |
| `/timeline` | `TimelinePage.vue` | 时间线 |
| `/chat` | `ChatPage.vue` | RAG 对话 |
| `/search` | `SearchPage.vue` | 语义检索 |

> 除 `/login` 外所有路由均需要登录，由 `router/index.js` 的 `beforeEach` 守卫统一拦截。

## 🛠️ 开发指南

### 📋 环境要求

- Python 3.11+
- Node.js 18+
- Docker & Docker Compose

### 🧪 测试

```bash
# 冒烟测试（无需 Neo4j/ChromaDB）
cd backend
../.venv/Scripts/python.exe -c "
from app.main import app
from fastapi.testclient import TestClient
c = TestClient(app)
assert c.get('/health').status_code == 200
print('OK')
"

# 集成测试（需启动 Docker 服务）
docker-compose up -d
../.venv/Scripts/python.exe -m pytest backend/tests
```

### 🐳 Docker 部署

```bash
# 构建并运行所有服务
docker-compose up -d

# 单独构建后端镜像
cd backend
docker build -t kg-backend .
docker run -p 8001:8001 --env-file .env kg-backend
```

## 📦 关键依赖版本

```
# backend
fastapi==0.115.0
uvicorn==0.32.0
python-jose[cryptography]==3.3.0
bcrypt==3.2.2
python-multipart==0.0.17
neo4j==6.1.0
chromadb==0.4.18
numpy==1.26.4
httpx==0.27.0
aiofiles==24.1.0
markitdown==0.0.1a3
markdown-it-py==3.0.0
jieba==0.42.1
spacy==3.7.5
rank-bm25==0.2.2
python-dotenv==1.0.0
pydantic==2.9.2
pydantic-settings==2.6.0
sqlalchemy==2.0.36
aiosqlite==0.20.0

# dev / test only
pytest==8.3.3
pytest-asyncio==0.24.0
```

```
# frontend
vue ^3.5.24
vue-router ^4.6.3
pinia ^2.3.1
d3 ^7.9.0
axios ^1.7.9
@tanstack/vue-virtual ^3.13.28
vite ^7.2.4
```

> **注意**：原 `passlib[bcrypt]==1.7.4` 已移除，改为原生 `bcrypt==3.2.2`（passlib 与新版 bcrypt 存在兼容问题）。

## 📌 注意事项

1. 🗝️ **API 密钥保护**：`.env` 中的密钥若已泄露，**立即**在控制台轮换
2. 🔑 **JWT_SECRET**：生产环境 (`APP_ENV=production`) 拒绝默认占位符启动
3. ⚡ **硅基流动限速**：嵌入服务实现了批量处理、异步队列和并发控制（Semaphore=5）
4. 🧮 **NumPy 版本**：必须使用 NumPy 1.x（<2.0）以保证 ChromaDB 兼容性
5. 🧬 **ChromaDB 版本**：客户端和服务端必须都使用 0.4.18 版本
6. 🐳 **Docker 内存**：Neo4j 需要充足内存，建议 4GB+
7. 🛡️ **markitdown 防御**：`utils/md_parser.py` 兼容新旧 API（`text_content` / `markdown` / `title`），失败时回退到 `txt` 解析
8. 🧩 **Neo4j APOC**：docker-compose 启用了 APOC 插件，UNWIND 批量写入依赖其函数
9. ⚠️ **实体合并**：`POST /api/graph/entities/merge` 会硬删 source 并将所有引用指向 target，操作不可逆

## 📜 许可证

MIT
