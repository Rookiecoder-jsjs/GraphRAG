# Neo4j + ChromaDB 项目依赖文档

## 一、当前环境依赖版本

### 1. Python 依赖（本地虚拟环境）

| 包名 | 版本 | 说明 |
|------|------|------|
| neo4j | 6.1.0 | Python Neo4j 驱动 |
| chromadb | 0.4.18 | 向量数据库客户端 |
| numpy | 1.26.4 | 注意：必须 < 2.0 |

**注意**：chromadb 0.4.18 不兼容 NumPy 2.0，必须使用 NumPy 1.x

### 2. Docker 镜像版本

| 服务 | 镜像 | 端口 |
|------|------|------|
| Neo4j | neo4j:5.14-community | 7474, 7687 |
| ChromaDB | chromadb/chroma:0.4.18 | 8000 |

---

## 二、环境安装步骤

### 1. 安装 Python 依赖

```bash
# 创建虚拟环境（如需要）
uv venv .venv
source .venv/Scripts/activate  # Windows

# 安装兼容版本的依赖
uv pip install "numpy<2.0"
uv pip install neo4j
uv pip install chromadb==0.4.18
```

### 2. 启动 Docker 服务

```bash
# 启动 Neo4j
docker run -d --name neo4j-neo4j-1 \
  -p 7474:7474 -p 7687:7687 \
  neo4j:5.14-community

# 启动 ChromaDB
docker run -d --name neo4j-chroma-1 \
  -p 8000:8000 \
  chromadb/chroma:0.4.18
```

---

## 三、版本匹配注意事项

### ⚠️ 关键原则：客户端版本必须与服务端匹配

| 场景 | 客户端版本 | 服务端版本 | 结果 |
|------|------------|------------|------|
| 本地 NumPy 2.0 + Chroma 0.4.18 | ❌ | ✅ | 运行失败 |
| 本地 Chroma 1.5.1 + Docker Chroma 0.4.18 | ✅ | ❌ | 运行失败 |
| 本地 Chroma 0.4.18 + NumPy 1.x + Docker Chroma 0.4.18 | ✅ | ✅ | ✅ 正常运行 |

### 版本对应关系

- **Docker ChromaDB 0.4.18** → 需要本地 `chromadb==0.4.18` + `numpy<2.0`
- **Docker ChromaDB latest** → 需要本地 `chromadb>=0.5.0` + `numpy>=1.26`（兼容 2.0）

---

## 四、切换项目使用时的检查清单

### 如果在新机器上部署：

1. **检查 Docker 镜像版本**
   ```bash
   docker images | grep -E "neo4j|chroma"
   ```

2. **安装匹配的 Python 包**
   ```bash
   # 方案 A：匹配现有的 Docker 0.4.18
   uv pip install "numpy<2.0" chromadb==0.4.18 neo4j

   # 方案 B：升级 Docker 到最新版本
   docker pull chromadb/chroma:latest
   uv pip install "chromadb>=0.5.0" neo4j
   ```

3. **验证连接**
   ```bash
   python test_db.py
   ```

### 如果要升级 ChromaDB 版本：

```bash
# 1. 停止并删除旧容器
docker stop neo4j-chroma-1
docker rm neo4j-chroma-1

# 2. 拉取新镜像
docker pull chromadb/chroma:latest

# 3. 启动新容器
docker run -d --name neo4j-chroma-1 -p 8000:8000 chromadb/chroma:latest

# 4. 升级本地客户端
uv pip install "chromadb>=0.5.0"
```

---

## 五、关于 Embedding 模型

当前项目配置：

- **默认**：使用 ChromaDB 内置的 `all-MiniLM-L6-v2` ONNX 模型（需要下载约 82MB）
- **计划使用**：Qwen 嵌入模型（通过 API 调用）

### 使用自定义 Embedding 的优势

1. 不依赖本地 ONNX 模型下载
2. 可以使用更强大的嵌入模型
3. 跨设备共享（API 方式）

---

## 六、常见问题

### Q1: 报错 `AttributeError: np.float_ was removed in NumPy 2.0`

**原因**：本地 NumPy 版本过高  
**解决**：`uv pip install "numpy<2.0"`

### Q2: 报错 `HTTPStatusError: 404 Not Found`

**原因**：ChromaDB 服务未启动或版本不匹配  
**解决**：检查 Docker 容器状态，确保版本一致

### Q3: 报错 `WinError 10054 远程主机强迫关闭了连接`

**原因**：客户端与服务端版本不匹配  
**解决**：确保本地 chromadb 版本与 Docker 镜像版本一致

### Q4: 下载 ONNX 模型失败

**原因**：网络不稳定  
**解决**：
- 使用代理
- 手动下载后放置到 `C:\Users\Administrator\.cache\chroma\onnx_models\all-MiniLM-L6-v2\`
- 或使用自定义 embedding（推荐）

---

## 七、相关文件

- `test_db.py` - 数据库连接测试脚本
- `download_onnx.py` - ONNX 模型下载脚本（如需使用内置 embedding）
- `.env` - 环境变量（API 密钥等）
