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
| `SERVER_NAME` / `SERVER_ADDRESS` | 推送服务标识（来自 `bili_common` 的 `PushNotifySettingsMixin`） |
| `PUSHME_URL` / `PUSHPLUS_URL` / `HITOKOTO_API_URL` | 渠道默认端点 / 一言接口（同上，与其它服务共用默认值） |
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

## RPA MCP（用户能力全量暴露）

`app/mcp` 把**用户可用的全部 RPA 接口**以 MCP（streamable HTTP）挂在服务上，
工具由 OpenAPI 自动生成（`fastapi-mcp`），不额外维护第二套 MCP 接口：

- MCP 端点：`http://<host>:<RPA_BROWSER_PORT>/api/v1/rpa/mcp`
- 工具 = 用户级路由全集（动作 / 工作流 / 浏览器会话与页面 / 操作日志 / 动作调试 …），
  共 94 个；其中 `rpa_*` 是调试专用工具（静态校验 / 试跑 / 日志定位 / 落库回读）
- SSE 与 WebRTC 信令类接口按 `operation_id` 排除（长连接不是请求-响应语义），清单见
  `app/mcp/exposure.py` 的 `EXCLUDED_OPERATIONS`，可用 `RPA_MCP_EXCLUDE_OPERATIONS` 追加
- 开关：`RPA_MCP_ENABLED`（默认 dev 开、prod 关）
- **鉴权走网关**：MCP 客户端配的是网关地址（`http(s)://<gateway>/api/v1/rpa/mcp`），
  请求带 `Cookie: bili_jwt=<token>`；网关校验（含登出黑名单）后清空并重写
  `x-bili-*` 再转发，RPA 侧只透传这些内网可信头，不解析 Cookie、不接受配置写死 mid。
  因此登出/封禁由网关直接挡掉；**RPA 端口必须只在内网可达**
- 注意：网关对非 SSE 请求有 180s 超时，`rpa_run_workflow` 等待超过 3 分钟会被断开

```bash
# 离线自检（不联网）：打印开关/路径/工具清单 + 排除清单有效性校验
.venv/bin/python -m app.mcp --selftest
```

MCP 客户端配置（Claude Desktop / Cursor 等）：

```json
{
  "mcpServers": {
    "rpa": {
      "url": "http://<gateway>/api/v1/rpa/mcp",
      "headers": { "Cookie": "bili_jwt=<浏览器里的 bili_jwt Cookie 值>" }
    }
  }
}
```

设计与工具清单见 `docs/mcp动作调试计划书.md`。

