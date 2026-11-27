# 浏览器 Cookie 导出

> 对应后端：`POST /api/v1/rpa/browser/control/operation/export_cookies`
> 前端落点：浏览器操作面板（与「清空登录态（换号）」同一区块）
> 后端实现：`RPA-Browser/app/services/RPA_browser/session/browser_data_service.py` → `BrowserDataService.export_cookies`
> 后端模型：`app/models/runtime/browser_operation.py` → `ExportCookiesRequest`、`CookieData`、`ExportCookiesResponse`

## 1. 背景

浏览器的登录态只存在于 profile 磁盘目录（`{user_data_dir}/{mid}/{browser_id}`），
crawler / RPA 侧**不保存、不缓存、不落库**任何 cookie。

因此本功能定位是**一次性只读导出**，只用于两类场景：

1. 排查登录态问题（确认 SESSDATA 等是否存在、作用域与过期时间是否正确）；
2. 人工迁移 / 留档（换机器、换浏览器时由人手动处理）。

⚠️ **严禁**把导出结果做成「下次启动自动注入」：站点会随请求轮换 cookie
（如 B 站 `SESSDATA`），程序侧快照一定过期，注入会用已作废的旧值覆盖浏览器
刷新后的有效值，反而导致频繁掉登录。这条在评审时必须拦住。

## 2. 接口契约

### 2.1 请求

`POST /operation/export_cookies`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `domains` | `string[] \| null` | 否 | 站点过滤；不传 / `null` / `[]` = 导出全部 |
| `launch_if_not_running` | bool | 否 | 默认 `false`；为 `true` 时浏览器未启动会先启动再导出（占启动队列与时长配额） |

`domains` 的匹配语义是「**访问该站点时实际会携带的 cookie**」：

- 与目标站点相等、是其子域、或是其父域，都会导出；
- 例：传 `bilibili.com` → 命中 `bilibili.com`、`.bilibili.com`、`api.bilibili.com`；
- 例：传 `www.bilibili.com` → 额外带出父域 `.bilibili.com` 的 cookie（访问 www 时确实会发送）；
- 传 URL、带端口、带路径都可，后端归一化：`https://www.bilibili.com/video/BV1xx` → `www.bilibili.com`。

### 2.2 响应

`StandardResponse[ExportCookiesResponse]`

| 字段 | 类型 | 说明 |
|---|---|---|
| `cookie_count` | int | 导出的 cookie 条数 |
| `matched_domains` | string[] | 结果实际覆盖的域名（去重排序） |
| `cookies` | `CookieData[]` | 明细，结构可直接用于 Playwright `add_cookies` |
| `launched` | bool | 本次调用是否顺带启动了浏览器 |
| `message` | string | 结果说明（无匹配时也会返回 0 条，不报错） |

`CookieData`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | cookie 名 |
| `value` | string | cookie 值（**含 HttpOnly，属敏感凭证**） |
| `domain` | string | 作用域域名，可能带前导点（`.bilibili.com`） |
| `path` | string | 作用路径 |
| `expires` | number | 秒级时间戳；`-1` = 会话 cookie（关浏览器即失效） |
| `httpOnly` | bool | 是否 HttpOnly |
| `secure` | bool | 是否仅 HTTPS 发送 |
| `sameSite` | string | `Strict` / `Lax` / `None` |

### 2.3 错误

| 场景 | 业务码 | 前端表现 |
|---|---|---|
| `domains` 传了但一个有效域名都解析不出 | `400 INVALID_PARAM`（HTTP 200 承载） | 提示「站点格式不合法，请填写域名或 URL」 |
| 未启动且 `launch_if_not_running=false` | `1007 BROWSER_NOT_STARTED` | 提示「浏览器未启动」并从依赖链路统一处理 |
| 启动浏览器失败（开启顺带启动时） | `INTERNAL_ERROR` | 提示 `message` 里的原因，可重试 |
| 浏览器不属于当前用户 | `403` | 统一鉴权处理 |

注意：`domains` 全部非法时后端**不会**退化成「导出全部」，会直接报参数错误，避免静默放大导出范围。

## 3. 前端交互

**落点：`views/rpa-browser/BrowserDetail.vue`（指纹详情页）的「登录态（Cookie）」区块**，
与「导入 Cookie」「清空登录态」并列，不放运行态页面的页头（那里是启动/停止等主控操作）。

已实现（`BrowserDetail.vue` + `components/rpa-browser/ImportCookieDialog.vue`）：

- 区块内一个「导出站点」输入框（逗号 / 空格分隔，留空 = 全部）+「导出 Cookie」按钮；
  域名格式由后端归一化，前端不做转换。
- 导出为只读操作，不需要二次确认；成功后直接把响应里的 `cookies` 数组下载为
  `cookies_{browser_id}_{时间戳}.json`，并提示条数与覆盖域名数。
- 无匹配（`cookie_count == 0`）时只提示，不生成空文件。
- **未启动时自动启动**：详情页没有会话状态数据，无法可靠判断运行态，因此导出请求固定带
  `launch_if_not_running: true` —— 后端发现未启动会先启动浏览器再读 cookie（受启动队列约束，
  耗时可能较长，且占用时长配额）。按钮 loading 文案提示「未启动时会先启动，耗时较长」，
  仍不做「未启动时置灰」（前端判断不了运行态）。成功时若响应 `launched=true`，额外提示
  「本次启动了浏览器」。
- **浏览器被外部关闭也能恢复**：会话 entry 还在、进程已没了时会先探测
  （`BrowserContext.cookies()`），失败即清理僵尸会话再启动重试一次；前端无需特殊处理，
  最坏情况是这次导出多等一次启动时间。
- 点击后弹出小表单：
  - 「导出范围」单选：全部（默认）/ 指定站点；
  - 选「指定站点」时可输入多个域名，前端只做轻清洗（去空白、去重），不做格式转换，格式问题交给后端报错；
  - 提交按钮文案「导出」，导出期间 loading。
- 成功后：
  - 顶部提示 `已导出 N 条 cookie，覆盖 M 个域名`；
  - 直接触发浏览器下载 `cookies_{browser_id}_{时间戳}.json`，内容为响应中的 `cookies` 数组；
  - 下载前在弹窗中给出一次性提示：「导出的 cookie 等同于账号凭证，请勿上传或分享」。
- 无匹配（`cookie_count == 0`）：提示「未匹配到 cookie，可能尚未登录」，不生成空文件。

约束：

- 导出结果只在前端内存中处理并交给下载，**不得**调用任何后端接口做持久化；
- 不得把 `cookies` 写入 `localStorage` / `sessionStorage`；
- 不得提供「保存为常用登录态 / 下次自动注入」这类入口（见 §1 的禁用原因）。

## 4. 后端不做的事（前端也不要引导用户期待）

- 不导出 IndexedDB / Service Worker / localStorage 中的凭证 —— 若站点把登录态放在这些位置，本接口导出的 cookie 不足以恢复登录，需整目录迁移 profile；
- **不做「启动时自动注入」**：一次性写入见 `浏览器Cookie导入.md`，写入后由浏览器自己刷新与落盘，
  任何「把导出文件存起来、下次启动灌回去」的做法都是禁止的。
