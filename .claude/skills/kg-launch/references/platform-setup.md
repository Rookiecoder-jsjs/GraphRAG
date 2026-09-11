# 各平台一次性环境搭建

> 前提：仓库已克隆。`.venv` 和 `data/` 在 .gitignore 中，克隆后全新生成；**不要**从别的机器拷贝 .venv（Windows 的 .venv 是 Windows 二进制，Mac/Linux 不可用）。

## 通用核心（三件事）

1. 建 venv 装依赖（`requirements.txt` 在仓库根）
2. 配 `backend/.env`（JWT_SECRET 随机值 + NEO4J_PASSWORD 与 compose 对齐 + 至少一个 LLM/embedding key）
3. `npm install`（仓库根 concurrent + `frontend/`）

## Windows

```powershell
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
npm install
cd frontend; npm install; cd ..
```

- venv 解释器：`.venv/Scripts/python.exe`（run-backend.mjs 自动找，亦可手动）
- 无 Rosetta 问题
- 生成 JWT_SECRET：`node -e "console.log(require('crypto').randomBytes(32).toString('base64url'))"`

## macOS Apple Silicon（M1/M2/M3）

```bash
# 0) Rosetta 2 —— 跑 amd64 镜像（neo4j 5.14 / chromadb 0.4.18）必须
softwareupdate --install-rosetta --agree-to-license

# 1) venv（Homebrew 官方 Python 3.11，自带可编译 SDK）
brew install python@3.11
python3.11 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt

# 2) 环境
cp backend/.env.example backend/.env
#   JWT_SECRET = 下面命令的输出
python3.11 -c "import secrets; print(secrets.token_urlsafe(48))"
#   NEO4J_PASSWORD = 12345678   ← 必须与 compose 默认一致（见 troubleshooting）
#   SILICON_FLOW_API_KEY / BAILIAN_API_KEY = 真实 key（至少一个）

# 3) 前端
npm install && (cd frontend && npm install)
```

- venv 解释器：`.venv/bin/python`
- pip 装 chromadb 传递依赖（hnswlib/onnxruntime）在 arm64 编译失败时 → 用 brew 的 `python@3.11` 重建 venv 重装

## macOS Intel

同上，**跳过第 0 步 Rosetta**（amd64 镜像原生运行）。

## Linux（Ubuntu/Debian 为例）

```bash
sudo apt install python3.11 python3.11-venv    # 或 pyenv 装 3.11
python3.11 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
npm install && (cd frontend && npm install)
```

- venv 解释器：`.venv/bin/python`
- docker：发行版包或 Docker Engine；`docker compose` v2 需单独安装
