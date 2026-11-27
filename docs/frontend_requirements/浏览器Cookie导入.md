# 浏览器 Cookie 导入

> 对应后端：
> `POST /api/v1/rpa/browser/control/operation/import_cookies`（单个）
> `POST /api/v1/rpa/browser/control/operation/import_cookies/batch`（批量）
> 前端落点：浏览器操作面板（单个） / 浏览器列表多选（批量）
> 后端实现：`app/services/RPA_browser/session/cookie_import_service.py` → `CookieImportService`
> 写入实现：`app/services/RPA_browser/session/browser_data_service.py` → `BrowserDataService.import_cookies`
> 后端模型：`app/models/runtime/browser_operation.py` → `ImportCookiesRequest`、`ImportCookiesBatchRequest` 等

## 1. 背景与边界

导入是**一次性写入**：把外部 cookie 写进运行中的浏览器 context，写完后服务端立即放弃入参，
不缓存、不落库。之后的 cookie 刷新、续期、落盘全部由浏览器自己负责。

为什么必须是运行态：`context.add_cookies()` 是唯一有序的注入点；离线改 profile 里的
`Cookies` SQLite 会与浏览器自身的合并逻辑冲突，而且磁盘数据本身是滞后快照。

⚠️ 禁止把导入做成「每次启动自动注入」：站点会随请求轮换 cookie（如 B 站 `SESSDATA`），
存下来的快照必然过期，回灌会用已作废的旧值覆盖浏览器刷新后的有效值，导致频繁掉登录。
导入只能由用户**显式触发一次**。

### 1.1 异步任务与前端轮询

导入 / 清空这类接口**不再同步等待浏览器操作完成**：

- 提交接口立即返回 `task_id`，真正的执行交给 FastAPI `BackgroundTasks`（响应发出后调度）；
- 前端按 1~2s 轮询 `POST /operation/tasks/status`，拿到 `status` / `progress` / 逐项结果后停止；
- 为什么任务化：慢站点 / 多页面时刷新与串行启动能跑几十秒，超过 nginx 默认
  `proxy_read_timeout` 60s → 前端收到 **504 Gateway Time-out**，但操作其实已生效，
  用户会误以为失败并重试；
- 页面刷新仍由任务内部执行，且是**后台步骤**（`reloaded_pages` 表示「已提交刷新」，
  不等待 reload 完成，每页最多 15s，失败只记 debug 日志）；
- 任务只存在**进程内存**：服务重启会丢任务，轮询返回 `404`，前端必须提示
  「任务不存在或已过期」，**不能当成成功**；
- 同步接口（`/operation/import_cookies/batch`、`/operation/clear_login_state/batch`）继续保留，
  供 MCP / RPA 动作等内部调用方使用；前端统一走任务接口。

任务接口：

```
POST /operation/tasks/import_cookies       body 同 import_cookies/batch
POST /operation/tasks/clear_login_state    body 同 clear_login_state/batch
POST /operation/tasks/status               body { task_id }
```

## 2. 单个导入

### 2.1 请求

`POST /operation/import_cookies?browser_id={id}`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `cookies` | `CookieData[]` | 是 | 至少 1 条；结构与导出结果完全一致，可直接把导出文件内容填入 |
| `launch_if_not_running` | bool | 否 | 默认 `false`。`true` 时若浏览器未启动会先启动再导入 |
| `reload_pages` | bool | 否 | 默认 `true`，导入后提交后台刷新已打开页面（不阻塞响应） |

`CookieData` 字段同《浏览器Cookie导出.md》§2.2（`name` / `value` / `domain` 必填）。

后端会做最小归一化，前端不必预处理：

- `domain` 可带协议或路径，会被清洗；**前导点保留**（`.bilibili.com` 表示对子域生效）；
- `sameSite` 大小写不敏感（`lax` → `Lax`）；
- `sameSite=None` 时自动补 `secure=true`（否则 Chromium 直接拒收该条）；
- `expires = -1` 表示会话 cookie；
- `expires` 的合法范围是 `-1` 或 `0 < expires <= 253402300799`（秒级）：
  - `null`（扩展导出的会话 cookie 常见）、`0`、`< -1`、`NaN` / `Inf` → 归一化成 `-1`；
  - 13 位毫秒时间戳（JS `Date.now()` / 部分扩展）→ 自动除以 1000 换算成秒；
  - 换算后仍超上限 → 按会话 cookie（`-1`）处理。
  不归一化会让 Playwright 抛 `Cookie should have a valid expires`，导致**整批**写入失败；
- `domain` 带端口（`skr.skr2.cc:666`）会被清洗成 `skr.skr2.cc` —— cookie 作用域本身
  不包含端口，这是规范行为，不是数据丢失；
- 过期字段兼容 `expirationDate` / `expiration_date`（Chrome 扩展 EditThisCookie、
  Cookie-Editor 等的常见字段名，秒级数字），归一化为 `expires`。
  只认 `expires` 会把持久 cookie 静默降级成会话 cookie，表现为
  「导入成功、重启浏览器登录态没了」。

#### 落盘与恢复（本项目 Chromium 实测结论）

`add_cookies` 之后，cookie 由 Chromium 自己的 `SQLitePersistentCookieStore` 写入
`{user_data_dir}/{mid}/{browser_id}/Default/Cookies`，不是 Playwright 另存一份。实测行为：

| 场景 | 实测结果 |
|---|---|
| 持久 cookie（`expires > 0`） | 写入磁盘，正常关闭后**重启可恢复** |
| 会话 cookie（`expires = -1`） | 会临时写进磁盘（`expires_utc = 0`），但**重启时被 Chromium 丢弃并从库中删除** |
| 远期 `expires`（如 2030 年） | 被**截断到距今 400 天**（Chrome M104+ 的 cookie 上限策略），不是导入失败 |
| 写入磁盘的时机 | 异步批量 commit，不是每条立即 fsync；**SIGKILL 可能丢掉最近写入**，正常 `context.close()` 会落盘 |

因此：

- 想让登录态跨重启保留，导入的数据必须是**带过期时间的持久 cookie**；
- 「导出 → 再导入」闭环里，如果原浏览器里那条本来就是会话 cookie，导入后重启仍会失效；
- 会话 cookie 与 400 天截断都是浏览器自身策略，后端不会改写 `expires` 去绕开。

**「导入后重启又没了」排查顺序**：

1. 看导入响应的 `session_cookie_count`：若等于 `imported_count`，整批都是会话 cookie。
   常见原因是导出工具用的是 `expirationDate` 而文件里没有 `expires`（现已兼容，
   用同一份文件重新导入即可）；
2. 确认浏览器是**正常关闭**（`context.close()`）：强杀进程（SIGKILL）会丢掉最近写入，
   磁盘只是滞后快照；
3. 确认关闭前没有调用「清空登录态」，也没有走「删除浏览器重建」。

### 2.2 响应

`StandardResponse[ImportCookiesResponse]`

| 字段 | 类型 | 说明 |
|---|---|---|
| `browser_id` | string | 目标浏览器 id |
| `launched` | bool | 本次调用是否顺带启动了浏览器 |
| `imported_count` | int | 写入条数 |
| `reloaded_pages` | int | 已提交后台刷新的页面数（响应不等待刷新完成） |
| `session_cookie_count` | int | 其中会话 cookie（`expires = -1`）条数，**Chromium 重启时丢弃** |
| `message` | string | 结果说明（含会话 cookie 提示） |

### 2.3 错误

| 场景 | 业务码 | 前端表现 |
|---|---|---|
| 浏览器未启动且未允许启动 | `1007 BROWSER_NOT_STARTED` | 提示「请先启动浏览器」；若用户勾选了「顺带启动」则不该出现此错 |
| 浏览器已被手动关闭（entry 仍在会话池） | `1007`（按未启动处理） | 后端先做存活探测并清理僵尸会话；勾选「顺带启动」会重建后导入。**不会**再返回 `Target page, context or browser has been closed` 这类写入失败 |
| 工作流执行中 | `2016 BROWSER_WORKFLOW_RUNNING` | 提示「工作流正在执行，请稍后再导入」 |
| cookie 缺 `name`/`domain` | `400 INVALID_PARAM` | 提示后端返回的 `msg` |
| 启动失败 / 写入失败 | `500 INTERNAL_ERROR` 系 | 提示失败原因，不要重试风暴 |

## 3. 批量导入

### 3.1 请求

`POST /operation/import_cookies/batch`

```json
{
  "browser_ids": ["1234567890", "1234567891"],
  "cookie_groups": [
    [ { "name": "SESSDATA", "value": "...", "domain": ".bilibili.com" } ],
    [ { "name": "SESSDATA", "value": "...", "domain": ".bilibili.com" } ]
  ],
  "launch_if_not_running": false,
  "reload_pages": true
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `browser_ids` | string[] | 是 | 1~50 个浏览器；**顺序决定配对**，第 i 个配第 i 组 |
| `cookie_groups` | `CookieData[][]` | 是 | 第 i 组写入第 i 个浏览器；每组至少 1 条（空组直接 422） |
| `launch_if_not_running` | bool | 否 | 批次级；默认 `false`。`true` 会**逐项串行启动**，可能很慢 |
| `reload_pages` | bool | 否 | 默认 `true` |

**数量不匹配不报错，按「尽力导入」处理**：

- 组数 > 浏览器数：多出的整组不导入，响应给出 `unused_cookie_group_count` 与 `unused_cookie_count`；
- 浏览器数 > 组数：多出的浏览器不写入任何 cookie，状态为 `unassigned` 并在 `unassigned_browser_ids` 中列出；
- 空组例外：它会让后续组整体错位，所以直接 422 拒绝。

### 3.2 响应

`StandardResponse[ImportCookiesBatchResponse]`

| 字段 | 类型 | 说明 |
|---|---|---|
| `total_browsers` | int | 提交的浏览器数 |
| `assigned` | int | 实际分配到 cookie 的浏览器数（浏览器数与组数的较小值） |
| `succeeded` | int | 已分配项中成功的数量 |
| `failed` | int | 已分配项中失败的数量（不含未分配） |
| `unassigned_browser_ids` | string[] | 分不到 cookie 的浏览器（组数不足） |
| `unused_cookie_group_count` | int | 多出而未使用的组数（浏览器数不足） |
| `unused_cookie_count` | int | 未使用的 cookie 总条数（多出各组之和） |
| `results` | `ImportCookiesBatchItemResult[]` | 逐项结果：已分配项按配对顺序在前，`unassigned` 在后 |

`ImportCookiesBatchItemResult`：`browser_id`、`status`、`launched`、`imported_count`、`reloaded_pages`、`session_cookie_count`、`message`。

`status` 取值：

| status | 含义 | 前端展示 |
|---|---|---|
| `success` | 写入成功 | 绿色「成功」 |
| `not_running` | 浏览器未启动或已关闭（僵尸会话已清理），未导入 | 灰色「未启动」，提示可先启动 |
| `workflow_running` | 工作流执行中，已跳过 | 橙色「工作流执行中」 |
| `forbidden` | 浏览器不存在或不属于当前用户 | 红色「无权访问」 |
| `invalid` | browser_id 非法 / cookie 缺必填字段 | 红色，展示 `message` |
| `launch_failed` | 顺带启动失败 | 红色「启动失败」 |
| `injection_failed` | 写入过程异常 | 红色「写入失败」 |
| `unassigned` | 没有分到 cookie（组数不足），未写入 | 灰色「未分配」 |

批量接口**不会因为单项失败整体失败**，HTTP 与顶层 `code` 仍为成功；前端必须按 `results` 逐项展示。

## 4. 前端交互

实现文件：

| 文件 | 职责 |
|---|---|
| `views/rpa-browser/BrowserDetail.vue` | 详情页「登录态（Cookie）」区块：导出 / 导入入口 / 清空 |
| `components/rpa-browser/ImportCookieDialog.vue` | 单个导入弹窗 |
| `components/rpa-browser/BatchImportCookieDialog.vue` | 批量导入弹窗与逐项结果 |
| `utils/rpa/cookieJson.ts` | 单个 / 批量 JSON 的解析与轻校验 |
| `api/browser/cookie_api.ts` | 接口封装（统一走生成的 `自动化控制Service`） |

类型说明：SDK 的 `hey-api/index.ts` 未重新导出新增的 cookie 类型，
业务层按项目约定从 `@/api/browser/hey-api/types.gen` 按需 `import type`。

### 4.1 单个导入（指纹详情页）

落点：`BrowserDetail.vue` 的「登录态（Cookie）」区块，与「导出 Cookie」「清空登录态」并列。
- 弹窗内容：JSON 文本框 + 文件选择（读取后填入文本框），支持直接粘贴导出文件内容。
- 提交前在前端做**格式校验**：必须是数组，且每项含非空 `name` / `value` / `domain`；不做域名归一化（后端会做）。
- 弹窗内固定提示：「导入会覆盖同名 cookie，但不会删除其它旧 cookie；要彻底换号请先清空登录态」。
- 「顺带启动浏览器」默认**不勾选**，勾选时提示「会启动浏览器，占用启动队列与时长配额」。
- 成功后提示 `已写入 N 条 cookie`，并按 `reloaded_pages` 说明「已提交后台刷新 N 个页面」
  （不要写「刷新完成」，刷新是异步的）。
- 若 `session_cookie_count > 0`，必须额外给出警示：「其中 N 条为会话 cookie，浏览器重启后会失效」——
  这类 cookie 即使写入成功也无法跨重启保留，不能让用户误以为登录态已长期就绪。
- 未启动（`1007`）时，提示文案要给出两条明确出路：「先启动浏览器」或「勾选顺带启动后重试」；**不得**自动重试。

### 4.2 批量导入（指纹列表页头）

- 入口：`BrowserFingerprintList.vue` 页头「批量导入 Cookie」按钮；浏览器由弹窗自行拉取并多选。
- 顺序：
  1. **按配对顺序勾选浏览器**（多选、可搜索，tag 顺序即配对顺序）；
  2. **给多组 cookie**：选择多个 JSON 文件（一个文件 = 一组）或粘贴二维数组
     `[[cookie...], [cookie...]]`（也接受 `[{ cookies: [...] }]`）；
  3. 预览「将导入 N 个浏览器」+ 配对表（第 # 个浏览器 ← 第 # 组、条数、会话 cookie 数）；
  4. 点「开始导入」。
- **数量不匹配必须提前提示**，且不阻止导入：
  - 组数多于浏览器 → 黄条「浏览器不够：将忽略 N 组 cookie（共 M 条），这些不会导入」；
  - 浏览器多于组数 → 黄条「cookie 组数不够：有 N 个浏览器分不到 cookie，会标记为未分配」。
- 前端只做结构轻校验（每组非空、`name`/`value`/`domain` 必填）；域名归一化、归属校验、
  数量统计全部由后端完成，前端展示后端返回的统计，不自己算最终结果。
- 导入完成后展示 `succeeded / assigned`、忽略组数与条数、未分配数量，以及逐项状态表；
  失败项支持「仅重试失败项」（`unassigned` 不计入可重试项）。
- 浏览器列表一次拉取 200 个（`per_page=200`）；超过时当前弹窗内搜索只能覆盖已加载部分。
- 提交后以表格逐项展示 `results`：浏览器名 / 状态 / 写入条数 / 会话 cookie 条数 / 说明；失败项支持「仅重试失败项」。
  `session_cookie_count > 0` 的项在「会话 cookie」列标黄，hover 提示「重启浏览器后失效」。
- 「顺带启动」默认不勾选；勾选时二次确认，明确写出「会逐个启动浏览器，可能耗时较长，且占用时长配额」。

## 5. 安全约束（前端必须遵守）

- cookie 内容是明文账号凭证：只在内存中处理，**不得**写入 `localStorage` / `sessionStorage` / IndexedDB；
- **不得**把导入内容提交到任何自建的持久化接口；
- **不得**提供「保存为常用登录态 / 开机自动注入 / 定时同步」这类入口；
- 导入完成后的文本框内容应清空（避免停留在页面上被截图或误复制）；
- 不要在日志、埋点、错误上报里带上 cookie 的 `value`。

## 6. 后端不做的事

- 不提供「离线写 profile」：导入必须落在运行中的 context 上；
- 不做「导入前自动清空」：默认 upsert，换号场景请先调 `clear_login_state`，两步显式操作更可控；
- 不校验 cookie 是否真的能登录：导入成功只代表写入成功，是否真的登录成功需要在页面上验证。
