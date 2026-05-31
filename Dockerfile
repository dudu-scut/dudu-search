# ── 构建阶段 ──
FROM python:3.12-slim AS builder

WORKDIR /app

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 先复制依赖文件，利用 Docker 缓存
COPY pyproject.toml uv.lock .python-version ./

# 安装生产依赖
RUN uv sync --frozen --no-dev --no-install-project

# 复制源码
COPY . .

# 安装项目本身
RUN uv sync --frozen --no-dev

# ── 运行阶段 ──
FROM python:3.12-slim AS runtime

WORKDIR /app

# 安装运行时系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 从构建阶段复制虚拟环境
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/app /app/app
COPY --from=builder /app/pyproject.toml /app/

# 设置 PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# 创建非 root 用户
RUN useradd --create-home --shell /bin/bash appuser
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/live || exit 1

ENTRYPOINT ["uv", "run", "uvicorn", "app.api.server:app", "--host", "0.0.0.0", "--port", "8000", "--no-reload"]
