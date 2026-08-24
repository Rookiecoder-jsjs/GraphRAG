# NC Knowledge-Base MCP Server

把本系统的检索/图谱能力暴露为标准 MCP 工具，任何 MCP 客户端零前端投入直接查询知识库（GUIDE-003 T2-3）。

## 工具面（v1 四个，全部只读）

| 工具 | 签名 | 说明 |
|------|------|------|
| `search_knowledge` | `(query, user_id, top_k=5)` | 混合检索（BM25+向量+RRF+重排），返回 chunk 预览与来源标题 |
| `search_graph` | `(entity_or_query, user_id, depth=1)` | 实体名搜索 + N 跳邻域展开（depth 限 1–3） |
| `get_entity_detail` | `(name, user_id)` | 实体详情 envelope（统计/文档/关联实体/样例 chunk） |
| `list_documents` | `(user_id,)` | 文档清单（状态 + 时间戳，最近 100 条） |

所有读取按 `user_id` 显式隔离——MCP 层没有 JWT 会话，调用方声明读谁的库；持有 token 即访问凭证。

## 启动

```bash
# 生成 token
python -c "import secrets; print(secrets.token_urlsafe(32))"

# stdio 模式（由 MCP 客户端作为子进程拉起，不要手动挂前台跑）
KG_MCP_TOKEN=<token> python mcp_server/server.py
```

token 缺失或为占位符时进程拒绝启动（exit 2），对齐 JWT_SECRET 纪律。

日志只写 **stderr**——stdout 是 stdio JSON-RPC 协议通道。

## 客户端接入

### Claude Desktop（`claude_desktop_config.json`）

```json
{
  "mcpServers": {
    "nc-knowledge-base": {
      "command": "D:/NC/.venv/Scripts/python.exe",
      "args": ["D:/NC/backend/mcp_server/server.py"],
      "env": {
        "KG_MCP_TOKEN": "<your-token>",
        "PYTHONPATH": "D:/NC/backend"
      }
    }
  }
}
```

### Claude Code（项目 `.mcp.json` 或 `claude mcp add`）

```json
{
  "mcpServers": {
    "nc-knowledge-base": {
      "type": "stdio",
      "command": "D:/NC/.venv/Scripts/python.exe",
      "args": ["mcp_server/server.py"],
      "env": { "KG_MCP_TOKEN": "<your-token>" }
    }
  }
}
```

（在 `backend/` 目录下启动 claude 时无需 PYTHONPATH——server.py 自行把 backend 根加入 sys.path。）

### codex（`~/.codex/config.toml`）

```toml
[mcp_servers.nc-knowledge-base]
command = "D:/NC/.venv/Scripts/python.exe"
args = ["D:/NC/backend/mcp_server/server.py"]
env = { "KG_MCP_TOKEN" = "<your-token>" }
```

## 前置条件

- SQLite 数据文件存在（默认 `backend/data/sqlite/app.db`，由主服务初始化）
- Neo4j / Chroma 已运行（`search_knowledge`、图谱两工具需要；`list_documents` 只依赖 SQLite）
- 与 FastAPI 主服务可并行运行：SQLite WAL 允许多进程并发读
