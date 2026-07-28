# CLAUDE.md — Knowledge Graph System

## CodeGraph 使用规范

本项目已接入 CodeGraph MCP，遵循以下规则：

### 初次加载 / 全新仓库
```bash
# 克隆或首次打开项目时，必须先初始化索引
codegraph init
```

### 代码修改后
- **自动同步**：文件保存后 CodeGraph 监听器会在约 2 秒内自动更新索引，无需手动操作
- **批量修改后**（如 git pull、大规模重构）：运行一次手动同步确保一致
  ```bash
  codegraph sync
  ```
- **验证索引状态**：
  ```bash
  codegraph status
  ```

### 回答代码问题时
- 优先使用 `codegraph_explore` 工具，**不要**逐文件 grep / read
- 典型查询：调用链、模块依赖、改动影响范围、"X 是怎么工作的"
- CodeGraph 返回的代码片段视为**已读取**，无需再次打开文件
- 若索引中有 `⚠️` 过期标记，再用 Read 工具读取对应文件的最新内容

### CLI 速查
```bash
codegraph query <keyword>       # 搜索符号
codegraph callers <symbol>      # 谁调用了它
codegraph callees <symbol>      # 它调用了谁
codegraph impact <symbol>       # 改动影响范围
codegraph explore <question>    # 自然语言探索（等同 MCP 工具）
```

---

## 项目概览

**Knowledge Graph System** — 多用户知识图谱平台，支持文档上传、实体抽取、语义搜索和 AI 问答。

| 层 | 技术栈 |
|----|--------|
| 后端 | FastAPI + Python 3.x |
| 图数据库 | Neo4j（实体/关系存储）|
| 向量数据库 | ChromaDB（Embedding 检索）|
| 关系数据库 | SQLite（用户/文档元数据）|
| 前端 | Vue 3 + Vite |
| LLM | 百炼 Qwen / Kimi / SiliconFlow（可切换）|
| Embedding | Qwen3-Embedding-8B（via SiliconFlow）|
| Reranker | Qwen3-Reranker-8B |

---

## 目录结构

```
D:\NC/
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
│   │   │   ├── neo4j_client.py   # Neo4j 图操作
│   │   │   ├── chroma_client.py  # ChromaDB 向量检索
│   │   │   ├── embedding.py      # 向量化
│   │   │   ├── entity_extractor.py # LLM 实体抽取
│   │   │   ├── chunker.py        # 文档分块
│   │   │   ├── bm25.py           # 关键词检索
│   │   │   ├── fusion.py         # BM25 + 向量融合排序
│   │   │   ├── reranker.py       # 重排序
│   │   │   ├── llm.py            # LLM 调用封装
│   │   │   └── query_processor.py # 查询处理
│   │   └── auth/            # JWT 认证（security.py, jwt_handler.py）
│   ├── tests/               # pytest 测试
│   └── eval/                # 检索质量评估
├── frontend/
│   └── src/
│       ├── views/           # 页面（Dashboard, Documents, Chat, Graph,
│       │                    #   Search, Timeline, EntityDetail, ClusterMap）
│       └── components/      # UI 组件 + 布局
└── .codegraph/              # CodeGraph 索引（勿手动修改）
```

---

## 开发环境

### 必需服务
| 服务 | 默认地址 | 说明 |
|------|----------|------|
| Neo4j | bolt://localhost:7687 | 图数据库 |
| ChromaDB | localhost:8000 | 向量数据库 |

### 环境变量（`.env`）
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

### 启动
```bash
# 后端
cd backend
.venv/Scripts/activate        # Windows
uvicorn app.main:app --reload --port 8001

# 前端
cd frontend
npm install
npm run dev                   # 默认 http://localhost:5173
```

---

## 测试

```bash
cd backend
pytest tests/ -v
pytest tests/ --cov=app --cov-report=term-missing   # 带覆盖率
```

测试文件与功能模块对应：`test_graph_rag.py`, `test_embedding.py`, `test_tags.py`, `test_timeline.py`, `test_dashboard.py` 等。

---

## 编码约定

- **Python**：PEP 8，所有函数加类型注解，用 `logging` 不用 `print`
- **错误处理**：API 层统一返回结构化错误，服务层抛出有意义的异常，不吞掉错误
- **不可变优先**：数据对象用 `@dataclass(frozen=True)` 或 Pydantic model，避免原地修改
- **配置集中**：所有配置项在 `app/config.py`，不在业务代码里读 `os.environ`
- **安全**：密钥只走环境变量，JWT_SECRET 禁止使用占位符，CORS 不用 `*`
- **文件体量**：单文件不超过 400 行，超出则拆分为子模块
