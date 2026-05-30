#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════
#  DeepAgents 一键启动脚本 (Bash / Git Bash / Linux / macOS)
#  用法: bash run.sh
# ═══════════════════════════════════════════════════════
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

_ok()  { echo -e "${GREEN}[√]${NC} $1"; }
_info() { echo -e "${CYAN}[*]${NC} $1"; }
_warn() { echo -e "${YELLOW}[!]${NC} $1"; }
_err()  { echo -e "${RED}[✗]${NC} $1"; }

echo ""
echo "  DeepAgents — 多智能体深度研究系统"
echo "  ═══════════════════════════════════════════"
echo ""

# ---------- 1. 环境变量 ----------
if [ ! -f ".env" ]; then
    _warn "未找到 .env，从 .env.example 复制..."
    cp .env.example .env
    _warn "请编辑 .env 填入你的 API 密钥后重新运行"
    exit 1
fi
_ok ".env 已就绪"

# ---------- 2. Docker 服务 (PostgreSQL + Redis) ----------
DOCKER_RUNNING=false
if command -v docker &> /dev/null; then
    if docker info &> /dev/null; then
        _ok "Docker 已就绪"
        DOCKER_RUNNING=true
    else
        _warn "Docker 未运行，正在尝试启动 Docker Desktop..."
        if [[ "$OSTYPE" == "darwin"* ]]; then
            open -a Docker 2>/dev/null &
        elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
            start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe" 2>/dev/null &
        fi
        _info "等待 Docker 启动 (最多 60 秒)..."
        for i in $(seq 1 20); do
            if docker info &> /dev/null; then
                DOCKER_RUNNING=true
                break
            fi
            sleep 3
        done
    fi
fi

if $DOCKER_RUNNING; then
    _info "启动 PostgreSQL + Redis ..."
    cd docker
    docker compose up -d postgres redis 2>&1
    if [ $? -ne 0 ]; then
        _err "Docker 服务启动失败"
        cd ..
        exit 1
    fi
    cd ..

    _info "等待数据库就绪 ..."
    for i in $(seq 1 30); do
        if docker compose -f docker/docker-compose.yaml exec -T postgres pg_isready -U deepagents > /dev/null 2>&1; then
            break
        fi
        sleep 2
    done
    _ok "PostgreSQL + Redis 已就绪"
else
    _warn "Docker 不可用，跳过持久化服务 (将使用内存模式运行)"
fi

# ---------- 3. Python 包管理器 ----------
PKG_MGR=""
if command -v uv &> /dev/null; then
    PKG_MGR="uv"
    _ok "uv 已就绪"
elif command -v pip &> /dev/null; then
    PKG_MGR="pip"
    _ok "pip 已就绪（建议安装 uv 提速: https://docs.astral.sh/uv/）"
else
    _err "未找到 uv 或 pip，请先安装 Python"
    exit 1
fi

# ---------- 4. 后端依赖 ----------
_info "同步 Python 依赖..."
if [ "$PKG_MGR" = "uv" ]; then
    uv sync
fi
_ok "Python 依赖已同步"

# ---------- 5. Node 包管理器 ----------
NODE_PKG=""
if command -v pnpm &> /dev/null; then
    NODE_PKG="pnpm"
elif command -v npm &> /dev/null; then
    NODE_PKG="npm"
elif command -v yarn &> /dev/null; then
    NODE_PKG="yarn"
else
    _err "未找到 pnpm / npm / yarn，请安装 Node.js: https://nodejs.org/"
    exit 1
fi
_ok "前端包管理器: $NODE_PKG"

# ---------- 6. 前端依赖 ----------
if [ ! -d "frontend/node_modules" ]; then
    _info "安装前端依赖..."
    cd frontend && $NODE_PKG install && cd ..
    _ok "前端依赖已安装"
else
    _ok "前端依赖已就绪"
fi

# ---------- 7. 清理函数 ----------
cleanup() {
    echo ""
    _info "正在关闭服务..."
    [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null && _ok "后端已停止"
    [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null && _ok "前端已停止"
    if $DOCKER_RUNNING; then
        _info "停止 Docker 服务..."
        cd "$SCRIPT_DIR/docker"
        docker compose stop postgres redis 2>/dev/null && _ok "Docker 服务已停止"
        cd "$SCRIPT_DIR"
    fi
    _ok "所有服务已关闭"
    exit 0
}
trap cleanup SIGINT SIGTERM

# ---------- 8. 启动服务 ----------
echo ""
echo "  ═══════════════════════════════════════════"
echo "    API 文档:    http://localhost:8000/docs"
echo "    前端页面:    http://localhost:5173"
echo "  ═══════════════════════════════════════════"
echo ""
_info "正在启动服务... (Ctrl+C 停止所有服务)"
echo ""

# 启动后端
if [ "$PKG_MGR" = "uv" ]; then
    uv run python -m app.api.server &
    BACKEND_PID=$!
else
    python -m app.api.server &
    BACKEND_PID=$!
fi

# 等后端就绪
_info "等待后端就绪..."
for i in $(seq 1 30); do
    if curl -s http://localhost:8000/api/kb/list > /dev/null 2>&1; then
        break
    fi
    sleep 2
done
_ok "后端已就绪: http://localhost:8000"

# 启动前端
cd frontend
$NODE_PKG run dev &
FRONTEND_PID=$!
cd ..
sleep 4
_ok "前端已就绪: http://localhost:5173"

echo ""
_ok "全部就绪，打开 http://localhost:5173 开始使用"
echo ""

# 等待任一子进程退出
wait
