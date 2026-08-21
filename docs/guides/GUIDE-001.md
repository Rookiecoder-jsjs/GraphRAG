# GUIDE-001: 环境依赖——Neo4j + ChromaDB 版本与安装

- 状态: 已完成
- 日期: 2026-08-21（自原根目录 `Neo4j+Chroma依赖.md` 迁移并更新过时内容）
- 相关: [ADR-001](../adr/ADR-001.md)（混合检索依赖 ChromaDB 客户端）

## 背景

后端同时依赖 Neo4j（图数据库）与 ChromaDB（向量数据库）。**客户端版本必须与服务端版本匹配**，版本不匹配是本项目最常见的部署失败原因。

## 当前环境依赖版本

### Python 依赖（根 `.venv`，清单见根 `requirements.txt`）

| 包名 | 版本 | 说明 |
|------|------|------|
| neo4j | 6.1.0 | Python Neo4j 驱动 |
| chromadb | 0.4.18 | 向量数据库客户端（**必须 < NumPy 2.0**） |
| numpy | 1.26.4 | 必须 < 2.0（chromadb 0.4.18 不兼容 NumPy 2.0） |

### Docker 镜像版本

| 服务 | 镜像 | 端口 |
|------|------|------|
| Neo4j | neo4j:5.14-community | 7474, 7687 |
| ChromaDB | chromadb/chroma:0.4.18 | 8000 |

## 安装步骤

```bash
# Python 依赖（在根 .venv 内）
uv pip install "numpy<2.0"
uv pip install neo4j
uv pip install chromadb==0.4.18

# 启动 Docker 服务
docker run -d --name neo4j-neo4j-1 -p 7474:7474 -p 7687:7687 neo4j:5.14-community
docker run -d --name neo4j-chroma-1 -p 8000:8000 chromadb/chroma:0.4.18
```

## 版本匹配规则

| 场景 | 客户端 | 服务端 | 结果 |
|------|--------|--------|------|
| 本地 NumPy 2.0 + Chroma 0.4.18 | ❌ | ✅ | 运行失败 |
| 本地 Chroma 1.5.1 + Docker Chroma 0.4.18 | ✅ | ❌ | 运行失败 |
| 本地 Chroma 0.4.18 + NumPy 1.x + Docker Chroma 0.4.18 | ✅ | ✅ | ✅ 正常运行 |

- Docker ChromaDB **0.4.18** → 本地 `chromadb==0.4.18` + `numpy<2.0`
- Docker ChromaDB **latest** → 本地 `chromadb>=0.5.0` + `numpy>=1.26`（兼容 2.0）

## Embedding 模型（当前真实状态）

- **当前使用**：Silicon Flow 的 Qwen3-Embedding-8B（API 调用，经 `app/services/embedding.py`），生成 768 维向量
- 不使用 ChromaDB 内置 ONNX 模型（`all-MiniLM-L6-v2` 方案已弃用）

## 常见问题

| 报错 | 原因 | 解决 |
|------|------|------|
| `AttributeError: np.float_ was removed in NumPy 2.0` | 本地 NumPy 过高 | `uv pip install "numpy<2.0"` |
| `HTTPStatusError: 404 Not Found` | ChromaDB 未启动或版本不匹配 | 检查容器状态，确保版本一致 |
| `WinError 10054 远程主机强迫关闭` | 客户端/服务端版本不匹配 | 对齐版本 |
