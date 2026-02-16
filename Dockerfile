FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /home/RPA-Browser

# 安装系统依赖（OpenCV 需要 libgl1）
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY pyproject.toml .python-version ./

RUN uv lock
RUN uv lock --index av=https://pyav.basswood-io.com/pypi/simple/
RUN uv sync

# 复制项目文件，这一步放到最后面，以避免缓存问题
COPY . .

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["uv","run","uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]