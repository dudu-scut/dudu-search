@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

:: ═══════════════════════════════════════════════════════
::  DeepAgents 一键启动脚本 (Windows CMD)
::  用法: 双击 run.bat 或在终端中运行 run.bat
:: ═══════════════════════════════════════════════════════

cd /d "%~dp0"
set "PROJECT_ROOT=%~dp0"

echo.
echo   DeepAgents — 多智能体深度研究系统
echo   ═══════════════════════════════════════════
echo.

:: ---------- 1. 环境变量 ----------
if not exist ".env" (
    echo [!] 未找到 .env，从 .env.example 复制...
    copy .env.example .env >nul
    echo [!] 请编辑 .env 填入你的 API 密钥后重新运行
    pause
    exit /b 1
)
echo [✓] .env 已就绪

:: ---------- 2. Docker 服务 (PostgreSQL + Redis) ----------
where docker >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] 未找到 Docker，请安装 Docker Desktop
    echo     持久化存储需要 PostgreSQL + Redis，跳过...
    goto skip_docker
)

docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Docker 未运行，正在尝试启动...
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe" 2>nul
    echo [*] 等待 Docker 启动 (最多 60 秒)...
    set /a docker_wait=0
    :wait_docker
    docker info >nul 2>&1
    if not %errorlevel% equ 0 (
        timeout /t 3 /nobreak >nul
        set /a docker_wait+=3
        if !docker_wait! lss 60 goto wait_docker
        echo [!] Docker 启动超时，请手动启动 Docker Desktop 后重试
        pause
        exit /b 1
    )
)
echo [✓] Docker 已就绪

echo [*] 启动 PostgreSQL + Redis ...
cd docker
docker compose up -d postgres redis 2>&1
if %errorlevel% neq 0 (
    echo [!] Docker 服务启动失败
    cd ..
    pause
    exit /b 1
)
cd ..

echo [*] 等待数据库就绪 ...
:wait_pg
docker compose -f docker/docker-compose.yaml exec -T postgres pg_isready -U deepagents >nul 2>&1
if %errorlevel% neq 0 (
    timeout /t 2 /nobreak >nul
    goto wait_pg
)
echo [✓] PostgreSQL + Redis 已就绪
:skip_docker

:: ---------- 3. 检查 Python/uv ----------
where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] 未找到 uv，请先安装: https://docs.astral.sh/uv/
    pause
    exit /b 1
)
echo [✓] uv 已就绪

:: ---------- 4. 后端依赖 ----------
echo [*] 同步 Python 依赖...
uv sync 2>&1
if %errorlevel% neq 0 (
    echo [!] 依赖同步失败，请检查网络连接
    pause
    exit /b 1
)
echo [✓] Python 依赖已同步

:: ---------- 5. 前端包管理器 ----------
set "PKG_MGR="
where pnpm >nul 2>&1 && set "PKG_MGR=pnpm"
if not defined PKG_MGR (
    where npm >nul 2>&1 && set "PKG_MGR=npm"
)
if not defined PKG_MGR (
    echo [!] 未找到 pnpm 或 npm，请安装 Node.js: https://nodejs.org/
    pause
    exit /b 1
)
echo [✓] 前端包管理器: %PKG_MGR%

:: ---------- 6. 前端依赖 ----------
if not exist "frontend\node_modules" (
    echo [*] 安装前端依赖...
    cd frontend
    call %PKG_MGR% install
    if %errorlevel% neq 0 (
        echo [!] 前端依赖安装失败
        cd ..
        pause
        exit /b 1
    )
    cd ..
    echo [✓] 前端依赖已安装
) else (
    echo [✓] 前端依赖已就绪
)

:: ---------- 7. 启动服务 ----------
echo.
echo   ═══════════════════════════════════════════
echo    后端 API:    http://localhost:8000
echo    API 文档:    http://localhost:8000/docs
echo    前端页面:    http://localhost:5173
echo   ═══════════════════════════════════════════
echo.
echo [*] 正在启动服务...

:: 启动后端 (新窗口)
start "DeepAgents-后端" cmd /c "cd /d "%PROJECT_ROOT%" && uv run python -m app.api.server 2>&1 && pause"

:: 等后端先启动
echo [*] 等待后端启动...
timeout /t 8 /nobreak >nul

:: 启动前端 (新窗口)
start "DeepAgents-前端" cmd /c "cd /d "%PROJECT_ROOT%frontend" && %PKG_MGR% run dev && pause"

echo.
echo [✓] 服务已启动，打开 http://localhost:5173 开始使用
echo [!] 关闭此窗口不会停止服务，请在对应的后端/前端窗口按 Ctrl+C 停止
echo.
pause
