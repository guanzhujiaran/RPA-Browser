FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app

# 更换 apt 源为阿里云镜像以提高下载速度
RUN echo "deb http://mirrors.aliyun.com/debian bookworm main non-free non-free-firmware" > /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian-security bookworm-security main" >> /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian bookworm-updates main" >> /etc/apt/sources.list


# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    wget \
    unzip \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    && rm -rf /var/lib/apt/lists/*
# 复制依赖文件
COPY pyproject.toml .python-version uv.lock ./
# 安装Python依赖
RUN uv sync --locked

# 安装并配置指定的Chromium浏览器
RUN mkdir -p /usr/local/chromium && \
    cd /usr/local/chromium && \
    wget --tries=3 --no-check-certificate https://github.com/adryfish/fingerprint-chromium/releases/download/139.0.7258.154/ungoogled-chromium-139.0.7258.154-1-x86_64.AppImage -O chromium.AppImage && \
    chmod +x chromium.AppImage && \
    mkdir -p /usr/share/desktop-directories/

# 复制项目文件
COPY . .

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["uv","run","uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]