---
name: kg-launch
description: 启动本知识图谱系统（Neo4j + ChromaDB docker 容器 + FastAPI 后端 :8001 + Vue 前端 :5173）的跨平台流程与故障排查。当用户要求"启动项目 / 启动前后端 / 把系统跑起来 / 环境检查 / 换平台后怎么启动 / 启动失败排查"时使用；也用于新机器（Windows / macOS Intel / macOS Apple Silicon / Linux）首次搭环境。覆盖 docker compose、Python venv、backend/.env 配置、npm 一键启动、健康检查，以及已知坑（Apple Silicon Rosetta、双 .env 密码不一致、JWT_SECRET 占位符拒绝、端口冲突）。
---

# 知识图谱系统启动（跨平台）

目标：在任意平台跑起全栈——Neo4j + ChromaDB（docker）+ 后端（FastAPI :8001）+ 前端（Vue :5173）。

## 0. 环境检查（先跑）

```bash
node .claude/skills/kg-launch/scripts/check-env.mjs
```

脚本自动判定平台（含 Apple Silicon），检查 Python venv、Docker/Compose、容器状态、`backend/.env` 关键项（**只报状态，永不打印密钥**），输出 PASS/WARN/FAIL。有 FAIL 先修复再继续。

## 1. 快速启动（环境已就绪）

```bash
docker compose up -d        # Neo4j + ChromaDB
npm run dev                 # 后端 :8001 + 前端 :5173（concurrently）
```

启动脚本 `scripts/run-backend.mjs` 已按平台自动找 venv（win→`Scripts\python.exe`，mac/linux→`bin/python`）。

## 2. 新机器首次搭建

平台差异只在一处：**一次性环境准备**（venv 路径、Apple Silicon 的 Rosetta、.env）。按平台操作：

- [references/platform-setup.md](references/platform-setup.md) — 各平台完整搭环境命令

核心永远是三件事：**建 venv 装依赖 → 配 backend/.env → npm install**。
`.venv` 与 `data/` 已被 gitignore，克隆后全新生成，**不要**从别的机器拷贝 .venv（Windows venv 是 Windows 二进制）。

## 3. 启动后验证

| 目标 | 地址 |
|------|------|
| 后端 API 文档 | http://localhost:8001/docs |
| 前端 | http://localhost:5173 |
| Neo4j Browser | http://localhost:7474（账号 `neo4j`） |
| ChromaDB 心跳 | `curl http://localhost:8000/api/v1/heartbeat` |

## 4. 测试 / 评测

```bash
cd backend && ../.venv/bin/python -m pytest tests/ -q
# Windows 用：../.venv/Scripts/python.exe
cd backend && ../.venv/bin/python -m eval.runner --user-id 1 --no-llm --markdown
```

## 5. 已知坑速查（详见 troubleshooting.md）

- **Apple Silicon**：neo4j/chromadb 镜像都是 amd64，需 Rosetta 2：`softwareupdate --install-rosetta --agree-to-license`
- **双 .env 密码不一致**：compose 读仓库根 `.env`（默认 `12345678`），应用读 `backend/.env`；不一致则后端连不上 Neo4j
- **JWT_SECRET 占位符被拒**：`config.py` 启动即抛 RuntimeError，需随机生成
- **8000 端口被占**：ChromaDB 起不来，停掉占用方或换端口并同步 `CHROMA_PORT`

## 排查顺序

check-env 的 FAIL → [references/troubleshooting.md](references/troubleshooting.md) 已知坑 → `docker compose ps` 容器健康 → `backend/data/logs/app.log`。
