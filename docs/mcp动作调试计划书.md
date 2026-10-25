# MCP 动作调试与工作流执行计划书（FastAPI 内嵌 + fastapi-mcp）

## 背景

调试复合动作（如 `action_104682832_参与B站抽奖.json`）的闭环很长：

1. 手工改 JSON；
2. 跑 `scripts/import_action_from_json.py` 落库；
3. 在该动作所属工作流里跑一次（`/workflows/run` 异步 + 前端看 SSE）；
4. 到「操作日志」里翻 `ActionLogRecord` 才能看到哪一步失败、失败原因、当次变量；
5. 回到第 1 步。

目标：让 AI 客户端通过 MCP 直接完成
「读动作 → 静态校验 → 试跑 → 看步骤日志 → 改 JSON → 落库 → 回读校验」。

## 方案选型

采用 **FastAPI 内嵌 + `fastapi-mcp`**：

- `fastapi-mcp` 把 FastAPI 路由按 OpenAPI 自动转成 MCP 工具，通过 streamable HTTP 挂载在
  `/api/v1/rpa/mcp`，MCP 客户端只需配一个 URL；
- 工具形态与前端接口完全同源，不存在第二条业务链路；
- **版本约束（实测）**：`fastapi-mcp 0.4.0` 调用 1.x 的 lowlevel API
  （`Server(name, description)`），在 `mcp 2.3.0` 下构造即 `TypeError`，
  因此依赖必须为 `mcp>=1.30.0,<2`。

被否决的方案：

- 独立 stdio 进程：工具能力与语义可控，但需要额外的 HTTP 客户端与文件白名单，
  且与「接口即能力」的既有约定重复；
- ~~只暴露既有路由不补专用路由~~：第一版按此做会缺静态校验、日志定位、落库回读这些
  **调试语义**。最终结论是**两者都要**：用户可用的真实路由全量暴露（不另写 MCP 接口），
  仅对没有等价路由的调试语义补 8 个专用工具。

## 设计

### 1. 挂载与暴露面

`app/mcp/mount.py` + `app/mcp/exposure.py`：

```python
install_schema_compat()                 # $ref 循环保护 + int|str 折叠（见 5.1）
source_app = build_user_app()           # 只 include 用户级 router（不含 admin）
mcp = FastApiMCP(
    source_app,
    exclude_operations=[*EXCLUDED_OPERATIONS, *settings.excluded_operation_ids],
    headers=["x-bili-mid", "x-bili-level", "x-bili-role"],  # 透传给内部调用
)
mcp.mount_http(app, mount_path=settings.controller_base_path + "/mcp")
```

- 只在 `create_app()` 末尾调用一次，**不进入 lifespan**，不改动执行引擎；
- 开关 `RPA_MCP_ENABLED`：未设置时 `RUNNING_MODE=dev` 开启、`prod` 关闭；
- 暴露面 = `browser` + `browser_control` 两个用户级 router 的全集（94 个工具）；
  `admin` **不 include**，因为 MCP 只开放用户能力；
- 管理员接口防线（`build_user_app()`）：
  - 主防线：只 include 用户级 router，`app/controller/v1/admin/**` 根本不进子应用；
  - 兜底 1：`find_admin_guarded_routes()` 递归展开 `include_router` 的惰性节点
    （FastAPI 0.148 的 `_IncludedRouter`），发现依赖
    `require_root` / `require_admin` / `require_permission` 的路由即 `logger.error`；
  - 兜底 2：`assert_no_admin_routes()` 从 OpenAPI 检查 `admin` 路径段，命中直接
    `RuntimeError` 拒绝挂载（fail-fast，宁可不启动也不静默暴露管理员能力）；
- 长连接语义的接口按 **operation_id** 精确排除（`EXCLUDED_OPERATIONS`）：
  运行进度 SSE、会话状态 SSE、WebRTC 信令 10 个；
  **不能按 tag 排除**——`pages/list|switch|close` 在既有代码里被打上了
  `WebRTC 视频流` tag，按 tag 排除会误伤页面管理。

### 2. 鉴权：全部走网关，RPA 只认内网可信头

MCP 客户端配置的是**网关地址**（`http(s)://<gateway>/api/v1/rpa/mcp`），
网关 `ProxyEndPort.js` 的 `/api/v1/rpa` 代理链已完成鉴权：

```text
客户端 Cookie: bili_jwt  →  网关 jwtAuth / userInfoPreFetch（含登出黑名单）
  →  setUserHeaders 清空客户端自带 x-bili-* 并注入可信身份头
  →  转发到 RPA /api/v1/rpa/mcp
```

因此 RPA 侧：

- **不解析 Cookie、不调 /identify、不校验 JWT**（避免与网关口径漂移：
  黑名单在网关 Redis，RPA 查不到）；
- **不接受配置写死 mid**（`RPA_MCP_MID` 已删除，那等于免鉴权后门）；
- 只把网关已注入的 `x-bili-mid / level / role` 通过 `fastapi-mcp` 的 `headers`
  白名单透传给工具内部 ASGI 调用；

登录态失效（登出 / 封禁 / token 过期）由网关直接挡掉，RPA 侧收到空 `x-bili-mid`
即按未登录处理。

前置条件：**RPA 端口只在内网可达**。`x-bili-*` 是内网可信头，绕过网关直连
RPA 端口即可伪造身份 —— 这与其它业务接口的既有假设一致，MCP 不额外开门。

客户端配置（Claude Desktop / Cursor 等）：

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

> 直连 `RPA:28000` 仅用于本地调试，需要自行带 `x-bili-mid`，且不要把该端口暴露到公网。

### 3. 工具来源：用户路由全集 + 调试专用路由（tag = `MCP 调试`）

普通能力直接复用真实路由（CRUD / 执行 / 日志查询），工具名即路由的 `operation_id`；
只有「没有等价路由」的调试语义才落到 `tag = "MCP 调试"` 的专用路由上。
service 层 `app/services/execution/debug_service.py` 直接调用既有服务
（`action_crud_svr` / `execution_engine` / `workflow_crud_svr` / `launch_workflow_run` /
`action_log_crud_svr`），不重复实现数据层。

| 工具（operation_id） | 路由 | 作用 |
| --- | --- | --- |
| `rpa_list_actions` | `POST /debug/actions/list` | 按名称模糊搜索动作，拿 `action_id` |
| `rpa_get_action` | `POST /debug/actions/get` | 导出动作全量定义（含 `steps`），DB 真值基线 |
| `rpa_validate_steps` | `POST /debug/actions/validate` | 静态校验（`validate_python` + `PipelineBuilder.build`），不联网 |
| `rpa_execute_action` | `POST /debug/actions/execute` | 执行已保存动作或内联 `steps`，默认校验拦截 |
| `rpa_save_action` | `POST /debug/actions/save` | 落库（同名 upsert）+ 回读比对 |
| `rpa_run_workflow` | `POST /debug/workflows/run-and-wait` | 运行工作流并等待终态（服务端轮询） |
| `rpa_get_run_logs` | `POST /debug/runs/logs` | 步骤链路 + 失败定位（`steps[i]` 候选） |
| `rpa_get_action_logs` | `POST /debug/action-logs/recent` | 某动作最近 N 条日志 |

### 4. 调试闭环的语义约定

- **静态校验先行**：`validate` 用 `workflow_step_adapter.validate_python(_ensure_action_type(step))`
  逐层校验，pydantic 的 `loc` 转成 `steps[2].children[0].params.timeout` 路径；
  再对顶层跑 `PipelineBuilder.build`，暴露循环/分支参数错误。
- **执行前拦截**：`execute` 默认先校验，未通过直接返回 `blocked_by_validation=true` 与问题清单，
  不发执行请求；`force=true` 可跳过（内置动作名如 `click` 不做校验）。
- **日志定位**：`ActionLogRecord` 只有 `depth` 与写入顺序，没有绝对 step 路径，
  因此用「写入顺序 + depth + 动作名」还原调用树，输出 `recovery_hints`（候选 `steps[i]`），
  循环体内 / 同名重复一律标 `ambiguous=true`，不假装能唯一定位。
- **落库回读**：`save` 后用 `action_crud_svr.get_by_action_id` 回读，比对 `action_id` 序列与步骤数，
  返回 `verified` 与差异，落实「JSON 与 DB 必须一致」。
- **可见性**：调试通道新建动作一律 `is_public=false`；已有动作的可见性保持不变，
  需要「公开到社区」仍走前端的 publish 审批，避免绕过审核。

### 5. 本地文件

文件读写不放服务端（MCP 客户端侧本来就有文件工具）：

- AI 用自身的文件工具读写 `action_*.json`；
- 把 `steps` 传给 `rpa_validate_steps` 校验，把完整内容传给 `rpa_save_action` 落库。

### 5.1 实测踩到的两个硬约束（实现必须遵守）

**（1）`$ref` 展开没有循环保护 → 必须打补丁才能暴露含自引用模型的路由**

`fastapi-mcp 0.4.0` 在 **tag 过滤之前**就会全量展开 `$ref`
（`fastapi_mcp/openapi/convert.py` 的 `resolve_schema_references`），而本项目存在自引用模型
（`WorkflowStepRequest.children: list[WorkflowStepRequest]`），构造时直接
`RecursionError: maximum recursion depth exceeded`。
实测：`自定义操作管理` / `操作日志管理` tag 能生成工具，
`工作流管理` / `执行引擎` tag 必崩——所以「按 tag 直接暴露真实路由」在原生库上做不到。

解决（`app/mcp/exposure.py`）：给展开函数加「同一引用链上已展开的模型不再展开」的保护，
自引用位置退化成 `{"type": "object"}`（顶层字段仍完整，深层结构由后端 pydantic 校验）。
另外仍保留轻量 `schema_app`（`build_user_app()`）作为 schema 与内部调用目标，
MCP 端点挂在主 app：

```python
source_app = build_user_app()                     # 只 include 用户 router
mcp = FastApiMCP(source_app, exclude_operations=[...], ...)
mcp.mount_http(app, mount_path="/api/v1/rpa/mcp")  # 对外入口在主 app
```

`source_app` 上补注册业务异常处理器（错误以可读 JSON 返回）和
`BanGuardMiddleware`（MCP 不过网关，封禁用户不能借道）。

**（2）`int | str` 在 MCP 客户端会被按 integer 校验**

- `int | str` 在 OpenAPI 里是 `anyOf[integer, string]`，部分 MCP 客户端按第一个类型
  校验 → 雪花 id 用字符串传会被拒；
- 真实路由大量使用该写法（`verify_browser_ownership` 的 `browser_id`、`action_id` 等），
  不改业务模型的前提下，在暴露层把 `anyOf[integer, string]` **折叠成 `string`**；
  后端 pydantic 的 `int | str` 本就接受字符串，语义不变（实测传 `"104660032"` 可正常调用）。
- 调试路由仍保留显式 `str` 声明（`verify_debug_browser`）与 `LoosePayload`
  （`JsonValue` 的 schema 会退化成 `string`/`array`，对象入参在参数校验阶段就被拒）。

**（3）长连接接口必须排除**

SSE（`workflows/runs/events`、`browser_control/events`）与 WebRTC 信令是长连接 / 双向实时，
MCP 工具调用会挂住，按 operation_id 排除；排除清单在 `--selftest` 里校验
（不存在 / 泄漏都会打印告警）。

### 5.2 debug 模式：用户级全量日志采集

背景：排查「关着日志的运行为什么没干活」时，五级三态开关链上任何一级显式关闭
都会让 `ActionLogRecord` 缺字段；且失败时 `error_message` 只带 `str(e)`，
调用栈只进 loguru stderr，MCP 从库里看不到炸点。

**debug 是用户级开关，不是系统级配置**（曾误做成 env `ACTION_LOG_DEBUG_ENABLED`，
已回退）：用户在工作流 / 复合动作的「执行日志」设置里选择是否开启，
数据模型为 `UserWorkflow.log_debug` / `CompositeActionModel.log_debug`
（三态 `bool | None`，迁移 `20261025_log_debug`），设计见
`docs/rpa-workflow-调度外壳计划书.md` §9.8。

采集优先级（`resolve_log_option`，任一级带 `debug=True` 即归一化）：

| 优先级 | 开关 | 说明 |
| --- | --- | --- |
| 1 | 执行参数 `log.debug` | 单次执行某步骤；JSON 内联 |
| 2 | 父操作透传配置（`debug` 随 `log_config` 继承） | 复合/循环/分支子步骤 |
| 3 | **工作流级** `UserWorkflow.log_debug` | 优先于工作流 `log_enabled`；debug 隐含「开启」 |
| 4 | 工作流开关 `UserWorkflow.log_enabled` | 本次运行总闸 |
| 5 | **动作级** `CompositeActionModel.log_debug` / `log_enabled` | 单操作执行（`/actions/execute`）走这一级 |
| 6 | 项目兜底 `settings.action_log_default_enabled` | 全局默认 |

归一化（`action_logger._normalize_debug`）：强制 `enabled=True`、
`record_params / record_result / record_variables=True`、`only_on_error=False`、
`max_payload_length=0`（不截断，保证 traceback 等排障信息完整可查）；
保留期与体积策略和普通日志完全一致（仍走 `action_log_default_retention_days`
与周期清理任务），不做单独管理。

**失败 traceback 入库**：`engine.py` 的 `except Exception` 分支把
`traceback.format_exc()` 追加进 `result.logs`，随 `ActionLogRecord.logs`
落库，`rpa_get_action_logs` / `rpa_get_run_logs` 直接可读。

**MCP 读取侧零改动**：不加过滤维度，MCP 客户端按 `execution_id` 自行查询；
`rpa_save_action` 的载荷（`ActionSavePayload`）已支持 `log_debug`，
可用 MCP 直接给动作开调试模式。

不做的事：

- 不新建日志表（复用 `ActionLogRecord`，仅新增两列 `log_debug`）；
- 不单独管理 debug 日志的保留期与清理策略；
- 不改变脱敏规则（`_redact_sensitive` 照常生效，`llm_api_key` 等仍打码）。

### 6. 配置（`RPA_MCP_*` 与日志采集 `ACTION_LOG_*`，全部可选）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `RPA_MCP_ENABLED` | dev=true / prod=false | 是否挂载 MCP 端点 |
| `RPA_MCP_MAX_WAIT_SECONDS` | `300` | `run-and-wait` 的服务端等待上限（注意网关 180s 超时） |
| `RPA_MCP_POLL_INTERVAL` | `1.5` | 等待运行的轮询间隔（秒） |
| `RPA_MCP_EXCLUDE_OPERATIONS` | 空 | 追加排除的 operationId（逗号分隔），叠加在内置 `EXCLUDED_OPERATIONS` 之上 |

> debug 模式是**用户级开关**（工作流 / 动作的 `log_debug`），没有 env 配置，
> 见 §5.2。

## 涉及文件

- 新增 `app/mcp/mount.py`、`app/mcp/exposure.py`（暴露面 + schema 兼容补丁）、
  `app/mcp/models.py`、`app/mcp/validator.py`、`app/mcp/logs.py`、
  `app/mcp/save_payload.py`、`app/mcp/__main__.py`（离线自检）；
  新增 `test/mcp/test_exposure.py`
- 新增 `app/services/execution/debug_service.py`
- 新增 `app/controller/v1/browser_control/execution/debug_router.py`
- 新增 `app/services/RPA_browser/session/page_resolver.py`（`_resolve_page` 下沉，路由与调试服务共用）
- 修改 `app/models/router/{router_tag,router_prefix,all_routes}.py`、`app/controller/v1/browser_control/{base.py,execution/__init__.py}`
- 修改 `main.py`（挂载）、`pyproject.toml`（`fastapi-mcp` + `mcp<2`）
- 删除上一版 stdio 实现（`app/mcp/server.py` / `client.py` / `file_store.py`）
- debug 模式（见 5.2，用户级开关）：新增迁移
  `alembic/versions/20261025_log_debug.py`；修改
  `app/models/database/workflow/models.py`（两表加 `log_debug`）、
  `app/models/execution/action_param_models.py`（`ActionLogOption.debug`）、
  `app/services/execution/action_logger.py`（归一化 + 工作流级解析）、
  `app/services/execution/engine.py`（透传 `workflow_log_debug` +
  异常 traceback 入 `result.logs`）、`app/services/execution/workflow/run_workflow.py`、
  `app/models/workflow/{workflow_requests,workflow_responses,composite_action_models}.py`、
  `app/services/execution/crud_service/{workflow_crud,action_crud}.py`、
  `app/controller/v1/browser_control/execution/{workflow_router,action_router}.py`、
  `app/controller/v1/admin/workflow_admin_router.py`、`app/mcp/save_payload.py`、
  `app/services/execution/debug_service.py`、`scripts/import_action_from_json.py`
  （`log_debug` 透传）；新增 `test/execution_engine/test_action_log_debug.py`

## 不做的事

- 不改既有路由的 `operation_id`（避免前端 SDK 生成结果变化）；因此工具名就是路由的
  默认 operationId（`xxx_api_v1_rpa_browser_control_..._post`），长但可追溯到接口；
- 不新写 MCP 专用接口来复制 CRUD（能复用真实路由的一律复用）；
- 不暴露管理端（`admin` router 不进 `build_user_app()`）；
- 不暴露 SSE / WebRTC（长连接语义），清单在 `EXCLUDED_OPERATIONS`；
- 不绕过审批与归属校验（`assert_approved`、`verify_browser_ownership` 照常）。

## 已知代价

- **工具表很大**：94 个工具的 schema 序列化约 130k 字符（≈ 40k tokens），
  AI 客户端每次请求都要吃这份成本；需要精简时用 `RPA_MCP_EXCLUDE_OPERATIONS` 裁，
  或按域拆多个 MCP 端点；
- **必须经网关访问**：MCP 客户端配的是网关 URL；绕过网关直连 RPA 端口等于绕过
  登录与登出校验（`x-bili-*` 是内网可信头）；
- **网关 180s 超时**：`/api/v1/rpa` 代理对非 SSE 请求用 `req.setTimeout(180000)`，
  而 `rpa_run_workflow` 最长等 300s —— 超过 3 分钟的等待会被网关断开。
  需要更长时把 `/mcp` 加进网关的 `isSseRequest()`，或把
  `RPA_MCP_MAX_WAIT_SECONDS` 调到 180 以内；
- prod 默认关闭，需显式 `RPA_MCP_ENABLED=true`；
- 工具名可读性差（受「不改 operation_id」约束，见上）。

## 验证

1. `.venv/bin/python -m app.mcp --selftest`：打印开关/挂载路径/工具数、
   `unknown_excluded`（清单里不存在的 id）/ `leaked`（该排除却出现的 id）/
   `admin_leaks`（管理端接口泄漏）/ 调试工具是否齐备，并跑静态校验样例；
2. `pytest test/mcp`：校验路径定位、噪音压缩、日志树与候选路径、
   `$ref` 循环保护与 `int|str` 折叠；
3. 鉴权（经网关 `http://<gateway>/api/v1/rpa/mcp`）：
   - 不带 Cookie → 网关不注入身份 → 工具返回 401 `未登录`；
   - 只带伪造 `x-bili-mid` → 网关 `setUserHeaders` 清空 → 401；
   - 带有效 `Cookie: bili_jwt=...` → `code: 0`；
   - 登出（JWT 进网关黑名单）后同一 Cookie → 网关按匿名处理 → 401；
4. 起服务后用 MCP 客户端（URL：`http://<gateway>/api/v1/rpa/mcp`）：
   - `list_custom_actions` / `list_workflows` 拿到本人真实数据（验证头注入与内部调用链路）；
   - `browser_launch_queue_status` 传**字符串** `browser_id` 能成功（验证 `int|str` 折叠）；
   - `rpa_list_actions` → `rpa_get_action` 拿基线；
   - 改错一个必填参数 → `rpa_validate_steps` 返回定位到 `steps[i]` 的问题；
   - 修好后 `rpa_execute_action`（活跃浏览器）拿到结果；
   - `rpa_run_workflow` + `rpa_get_run_logs` 拿到失败步骤与参数；
   - `rpa_save_action`（先 dry_run）→ 落库 → 返回 `verified=true`。
