# FastAPI Browser Automation Project

这是一个基于 FastAPI 的浏览器自动化项目，支持指纹浏览器和远程控制功能。

## 使用 Docker 部署

### 前置要求

- Docker
- Docker Compose

### 依赖管理

项目使用 `uv` 工具进行依赖管理，相比传统的 pip，它提供了更快的安装速度。
Docker 镜像构建过程中也会使用 `uv` 来安装 Python 依赖，以加快构建速度。

### 部署步骤

1. 构建并启动服务：
```bash
docker-compose up -d
```

2. 访问应用：
   - API 文档: http://localhost:8000/docs
   - API 地址: http://localhost:8000

### 故障排除

如果在构建 Docker 镜像时遇到网络连接问题，请尝试以下解决方案：

1. 检查网络连接是否正常
2. 如果在公司网络环境下，可能需要配置代理
3. Dockerfile 中已配置阿里云镜像源，通常可以解决大部分网络问题
4. 如果仍然遇到问题，可以尝试在本地先下载浏览器文件，然后修改 Dockerfile 使用本地文件

### 开发环境

对于开发环境，可以使用以下命令启动服务，支持热重载：
```bash
docker-compose -f docker-compose.yml -f docker-compose.override.yml up -d
```

### 环境变量

项目使用 `.env.dev` 文件配置环境变量：
- `chromium_executable_path`: Chromium 浏览器路径
- `mysql_browser_info_url`: MySQL 数据库连接 URL
- `controller_base_path`: API 控制器基础路径

默认使用定制的指纹浏览器 [ungoogled-chromium](https://github.com/adryfish/fingerprint-chromium/releases/download/139.0.7258.154/ungoogled-chromium-139.0.7258.154-1-x86_64.AppImage)，提供更好的反检测能力。

### 目录说明

- `user_data_dir`: 存放浏览器用户数据
- `logs`: 存放应用日志

### 数据库

项目使用 MySQL 8.0 数据库，默认会自动创建 `browser_info` 数据库和相关表。