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

`rpa-browser` 使用 **host 网络**（`network_mode: host`），直接监听宿主机 `28000`，不再做端口映射。原因：外网手机（IPv6）看 WebRTC 直播时，`aiortc` 的 offer 必须带宿主机公网 IPv6 的 host 候选；bridge 网络只能给出 `172.x` 容器地址，ICE 会超时。

host 网络下的注意事项：

- 容器没有 Docker DNS，所有依赖地址都必须写 `127.0.0.1:<宿主机映射端口>`（compose 已注入）：
  `mysql` → `127.0.0.1:${MYSQL_PORT:-10000}`、`rabbitmq` → `127.0.0.1:${RABBITMQ_PORT:-5672}`、
  `casdoor` → `127.0.0.1:${CASDOOR_PORT:-10011}`、`be-message-service` → `127.0.0.1:${MESSAGE_SERVICE_PORT:-10013}`。
  `depends_on` 只保证启动顺序，不能替代 DNS。
- 网关通过 `RPA_SERVICE_URI=http://host.docker.internal:28000` 访问（bridge 网内的 host-gateway）。
- `28000` 直接暴露在宿主机上，必须用 iptables 限制来源（仅 `127.0.0.1` 与 Docker 网段 `172.19.0.0/16`），
  规则脚本见 `scripts/docker/rpa-host-firewall.sh`；媒体 UDP 端口段（`32768:60999`）需按需放行。
  该脚本会安装开机自恢复的 `rpa-host-firewall.service`；**在 1Panel 里修改过防火墙规则后，
  需要重跑一次 `sudo scripts/docker/rpa-host-firewall.sh apply`**（或重启该 service），
  因为 1Panel 重建 INPUT 链时会丢掉自定义跳转。
- **Docker Desktop（Windows / macOS）不支持 host 网络**，本配置只能在 Linux 宿主机（或 WSL2 + 原生 Docker Engine）上运行。
- 部署前确认宿主机有公网 IPv6 地址，且路由器 / 光猫的 IPv6 防火墙已放行宿主机高位 UDP 入站（需人工确认）。

## 配置

通过 `docker-compose.yml` 注入：

| 变量 | 说明 |
| --- | --- |
| `mysql_browser_info_url` | `mysql+aiomysql://127.0.0.1:10000/BiliRPADB?...`（host 网络，端口取 `MYSQL_PORT`） |
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
- OpenAPI：`http://<host>:28000/docs`
- 健康检查：`/health`（依赖 RabbitMQ 连通性）

## RPA MCP（用户能力全量暴露）

`app/mcp` 把**用户可用的全部 RPA 接口**以 MCP（streamable HTTP）挂在服务上，
工具由 OpenAPI 自动生成（`fastapi-mcp`），不额外维护第二套 MCP 接口：

- MCP 端点：`http://<host>:28000/api/v1/rpa/mcp`
- 工具 = 用户级路由全集（动作 / 工作流 / 浏览器会话与页面 / 操作日志 / 动作调试 …），
  共 96 个；其中 `rpa_debug_*` 是调试专用工具（静态校验 / 试跑 / 日志定位 / 落库回读），
  其余 `rpa_*` 是与前端页面同款接口的镜像（工具名映射见 `app/mcp/exposure.py` 的
  `TOOL_NAME_OVERRIDES`）
- SSE 与 WebRTC 信令类接口按 `operation_id` 排除（长连接不是请求-响应语义），清单见
  `app/mcp/exposure.py` 的 `EXCLUDED_OPERATIONS`，可用 `RPA_MCP_EXCLUDE_OPERATIONS` 追加
- 开关：`RPA_MCP_ENABLED`（默认 dev 开、prod 关）
- **鉴权走网关**：MCP 客户端配的是网关地址（`http(s)://<gateway>/api/v1/rpa/mcp`），
  请求带 `Cookie: bili_jwt=<token>`；网关校验（含登出黑名单）后清空并重写
  `x-bili-*` 再转发，RPA 侧只透传这些内网可信头，不解析 Cookie、不接受配置写死 mid。
  因此登出/封禁由网关直接挡掉；**RPA 端口必须只在内网可达**
- 注意：网关对非 SSE 请求有 180s 超时，`rpa_debug_workflow_run` 等待超过 3 分钟会被断开

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

