# RPA-Browser

基于 **FastAPI + Playwright（Chromium）** 的 RPA 浏览器自动化服务。接收来自网关 / `be-bilibili-crawler` 的浏览器任务指令，在受控浏览器实例中执行登录态续期、滑块/验证码处理、Cookie 维护、页面自动化等操作，并把结果回传。内置浏览器指纹管理、任务调度、RabbitMQ RPC 与统一消息推送。

> 与 `puppeteer_Bili`（前端网关 + Puppeteer）区别：RPA-Browser 是**后端 RPA 执行引擎**，专注服务端受控浏览器自动化。

## 功能

- 浏览器实例的创建 / 复用 / 销毁
- 登录态续期与 Cookie 维护
- 验证码 / 滑块自动化处理
- 浏览器指纹生成与管理
- 任务调度（APScheduler）
- 通过 RabbitMQ RPC 回调 `be-bilibili-crawler`（`auto_attach_auth` 模式）
- 对接 `be-message-service` 统一推送

## 技术栈

| 类别 | 技术 |
| --- | --- |
| 语言 | Python 3.12+ |
| Web 框架 | FastAPI（uvicorn） |
| 浏览器 | Playwright（Chromium，存于 `app/chrome`） |
| 消息队列 | RabbitMQ（RPC） |
| 数据库 | MySQL（`BiliRPADB`）/ Redis |
| 其他 | Gemini API（AI 辅助）、APScheduler |
| 依赖管理 | uv |

## 目录结构

```
RPA-Browser/
├── pyproject.toml / uv.lock
├── main.py                  # 入口：FastAPI(lifespan) + 路由 + RabbitMQ RPC 消费
├── Dockerfile
└── app/
    ├── config.py            # 环境变量配置
    ├── routes.py            # 路由聚合
    ├── scheduler_manager.py # 任务调度
    ├── setup.py
    ├── controller/v1/       # API 控制器（按业务拆分）
    ├── services/
    │   ├── RPA_browser/     # 核心 RPA 浏览器执行
    │   ├── execution/       # 任务执行 / 编排
    │   ├── broswer_fingerprint/  # 指纹管理
    │   ├── message/         # 消息推送（对接 message-service）
    │   └── mq/              # RabbitMQ 客户端 / RPC
    ├── models/  utils/  exceptions/
    └── data/  logs/  chrome/  # 数据 / 日志 / 浏览器二进制
```

## 安装与启动

### 本地（uv）

```bash
cd RPA-Browser
uv sync
uv run python main.py
# 或
uv run uvicorn main:app --host 0.0.0.0 --port 28000
```

### Docker（推荐）

```bash
cd /home/minato/BilibiliExplosion
docker compose up -d rpa-browser
```

容器内端口 `28000`，由 `docker-compose.yml` 的 `RPA_BROWSER_PORT` 映射；依赖 `postgres` / `redis` / `rabbitmq` / `casdoor`。

## 配置

通过 `docker-compose.yml` 注入：

| 变量 | 说明 |
| --- | --- |
| `mysql_browser_info_url` | `mysql+aiomysql://mysql/BiliRPADB?...` |
| `controller_base_path` | 控制器基础路径，默认 `/api/v1/rpa` |
| `proxy_server_url` | 代理地址，如 `http://host.docker.internal:10809` |
| `GEMINI_API_KEY` | Gemini API Key |
| `RABBITMQ_URL` | RabbitMQ 连接串（`auto_attach_auth` RPC 依赖） |
| `MESSAGE_CONFIG` | 统一推送渠道配置（JSON，无 per-user 配置时兜底） |
| `SERVER_NAME` / `SERVER_ADDRESS` | 推送服务标识 |
| `TZ` | 时区 |

## 与其它服务的关系

```
puppeteer_Bili (网关)
      │  HTTP
      ▼
  RPA-Browser  ──RabbitMQ RPC──▶  be-bilibili-crawler
      │                                │
      └──── be-message-service ◀───────┘   (统一推送)
```

## API

- 基础路径：`/api/v1/rpa`
- OpenAPI：`http://<host>:<RPA_BROWSER_PORT>/docs`
- 健康检查：`/health`（依赖 RabbitMQ 连通性）
