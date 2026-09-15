# 启动故障排查

按 症状 → 原因 → 修复 组织。先跑 `node .claude/skills/kg-launch/scripts/check-env.mjs`，多数问题它直接指出。

## Apple Silicon 上镜像拉取/运行报 exec format 错误

- **原因**：`neo4j:5.14-community` 与 `chromadb/chroma:0.4.18` 都是 linux/amd64 镜像，M 芯片需要 Rosetta 2 模拟
- **修复**：`softwareupdate --install-rosetta --agree-to-license`，重启 Docker Desktop

## 后端启动即抛 RuntimeError（JWT_SECRET …）

- **原因**：`app/config.py` 拒绝占位符 JWT_SECRET（`""`、`your-secret-key-change-this-in-production`、`replace-me-with-a-strong-random-value`），防止 JWT 伪造
- **修复**：`python -c "import secrets; print(secrets.token_urlsafe(48))"` 生成，写入 `backend/.env`

## 后端连不上 Neo4j（认证失败 / 拒绝连接）

- **原因**：两个 `.env` 的 `NEO4J_PASSWORD` 不一致——`docker compose` 读**仓库根** `.env`（没有则默认 `12345678`），FastAPI 应用读 **backend/.env**
- **修复**：让两处相等；最简单是 `backend/.env` 也设为 `12345678`

## ChromaDB 8000 端口被占（容器起不来 / heartbeat 失败）

- **原因**：别的服务栈占用了 8000
- **修复**：停掉占用方；或给容器换端口并同步后端 `CHROMA_PORT`

## npm run dev 找不到 Python

- **原因**：`scripts/run-backend.mjs` 按优先级找 venv（仓库根 → 上级 → backend），都没有则回退 PATH 上的 python
- **修复**：把 venv 建在仓库根 `.venv`；mac/Linux 用 `.venv/bin/python`，Windows 用 `.venv/Scripts/python.exe`

## 前端白屏 / 5173 打不开

- **修复**：先 `cd frontend && npm install`，再 `npm run dev`；单独起前端用 `npm run frontend`
- 注意 `npm run dev` 是 concurrently 同时起后端 + 前端，后端若起失败前端仍会开

## 检索/聊天报 provider 错误

- **原因**：`backend/.env` 缺 LLM/embedding key
- **修复**：至少配 `SILICON_FLOW_API_KEY` 或 `BAILIAN_API_KEY`；多 key 池用 `SILICON_FLOW_API_KEYS` 逗号分隔多把，留空回退单 key（ADR-009）

## 换机器 / 迁移数据

- `data/` 目录（Neo4j / Chroma / SQLite 数据）可整体拷贝，跨平台通用
- `.venv` 不能拷，换平台必须重建（Windows venv 是 Windows 二进制）
