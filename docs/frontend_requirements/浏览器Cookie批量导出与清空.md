# 浏览器 Cookie 批量导出与批量清空

> 对应后端（**待新增**，当前不存在）：
> - `POST /api/v1/rpa/browser/control/operation/export_cookies/batch`
> - `POST /api/v1/rpa/browser/control/operation/clear_login_state/batch`
>
> 前端落点：`views/rpa-browser/BrowserFingerprintList.vue` 页头（与「批量导入 Cookie」并列）
> 复用组件模式：`components/rpa-browser/BatchImportCookieDialog.vue`
> 相关文档：`浏览器Cookie导出.md`、`浏览器Cookie导入.md`

## 1. 背景与现状

| 能力 | 单浏览器 | 批量 |
|---|---|---|
| 导入 cookie | ✅ `import_cookies` | ✅ `import_cookies/batch` |
| 导出 cookie | ✅ `export_cookies` | ❌ 无 |
| 清空登录态 | ✅ `clear_login_state` | ❌ 无 |

前端列表页已有「批量导入 Cookie」入口，但**没有批量导出 / 批量清空**，导致：

- 想给一批浏览器留档时必须逐个进详情页点导出（N 次跳转）；
- 换号 / 交付前清理凭证必须逐个点「清空登录态」（N 次二次确认）。

本需求补齐这两个批量入口。**不接受**「前端循环调单浏览器接口」的替代方案：
批量是后端的逐项隔离职责（部分失败要如实统计），前端 for 循环会导致失败处理散落两处、
请求风暴、且无法给出统一的结果汇总。

## 2. 共同约束（两条功能都适用）

1. **cookie 只能从运行中的 `BrowserContext` 读取**（profile 里的 `Default/Cookies` 是
   Chromium 加密的 SQLite，读不出明文）：
   - **批量接口一律纯只读，不启动浏览器**，未启动项返回 `not_running`。
     批量场景绝不能在一个请求里串行启动 N 个浏览器：单次启动 10~30s（内存不足还要排队），
     必然跑成分钟级长请求，撞上网关读超时后前端显示失败、后端仍在启动，最难排查。
   - 需要启动时由**前端逐项**调用 `POST /browser/control/create`（幂等），
     每项独立请求天然隔离超时与失败，还能展示「正在启动 2/5」进度；
     启动完成后再发起一次只读导出，把结果合并。
   - 批量清空**同样不在接口内启动**：未启动项返回 `not_running`；勾选
     「先启动再清空」时由前端逐项启动后重试清空（与导出同一套编排）。
     清空是破坏性操作，启动 + 清空整体仍然要先通过二次确认。
2. **逐项隔离**：`browser_ids` 第 i 项独立校验归属、独立执行，单项失败不影响其它项。
3. 单次上限由环境变量 **`COOKIE_BATCH_MAX_BROWSERS`** 决定（默认 `50`，配置项
   `app/config.py: cookie_batch_max_browsers`，三个批量接口共用同一个值）；超限由请求模型
   直接拒绝，不做静默截断。调大前先确认反向代理的读超时：批量越大越容易出现
   「前端报超时、后端仍在执行（已生效但显示失败）」。
4. 服务端不落库、不缓存任何 cookie 值；审计日志只记录条数与域名，**不记录 cookie 值**。
5. **僵尸会话自动清理**：entry 还在会话池、但浏览器进程已关闭（外部关闭 / 崩溃 /
   闲置回收）时，先做一次真实探测（`BrowserContext.cookies()`）；失败即
   `release_browser_session` 清掉 entry，按 `not_running` 处理，**不再抛
   `TargetClosedError`**。单个导出开启 `launch_if_not_running` 时会重建并重试一次；
   批量导出由前端逐项调 `POST /browser/control/create` 恢复。
   `create` 的「会话已存在」判断也改用同一探测 —— 否则僵尸 entry 会让用户点「启动」
   也恢复不了（永远返回「会话已存在」）。
6. **前端统一走异步任务**：批量导入 / 批量清空不再是长 HTTP 请求 ——
   `POST /operation/tasks/import_cookies`、`POST /operation/tasks/clear_login_state`
   提交后立即返回 `task_id`，前端按 1~2s 轮询 `POST /operation/tasks/status`，
   拿到 `status` / `progress` / 逐项结果后停止。同步接口仅为 MCP / RPA 动作等内部
   调用方保留。任务在进程内存中：服务重启后轮询返回 `404`，前端必须按
   「任务不存在或已过期」提示，不能当成成功。

## 3. 批量导出 Cookie

### 3.1 请求

`POST /operation/export_cookies/batch`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `browser_ids` | `string[]` | 是 | 目标浏览器，上限见 `COOKIE_BATCH_MAX_BROWSERS` |
| `domains` | `string[] \| null` | 否 | 站点过滤，语义与单浏览器导出完全一致；空 = 全部 |

**没有 `launch_if_not_running`**：批量接口不负责启动（见 §2.1），启动由前端逐项调用
`POST /browser/control/create` 完成。`browser_ids` 上限 50、`domains` 非法一律整批 400。

`domains` 非法属于**请求级**参数错误：后端在循环前一次性校验，整批直接报
`400 INVALID_PARAM`，不会退化成「每个浏览器各返回一条 invalid」。

### 3.2 响应

`StandardResponse[ExportCookiesBatchResponse]`

| 字段 | 类型 | 说明 |
|---|---|---|
| `total_browsers` | int | 提交的浏览器数 |
| `succeeded` | int | `status == success` 的项数 |
| `failed` | int | 其余项数 |
| `total_cookie_count` | int | 所有成功项导出的 cookie 总条数 |
| `results` | `ExportCookiesBatchItemResult[]` | 逐项结果，顺序与 `browser_ids` 一致 |

`ExportCookiesBatchItemResult`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `browser_id` | string | 浏览器 ID |
| `status` | string | `success` / `not_running` / `forbidden` / `invalid` / `launch_failed` / `failed` |
| `launched` | bool | 批量导出恒为 `false`（批量接口不启动），字段为与单浏览器导出对齐保留 |
| `cookie_count` | int | 该项导出条数（非 success 时 0） |
| `matched_domains` | string[] | 该项实际覆盖的域名 |
| `cookies` | `CookieData[]` | 明细，结构同单浏览器导出；非 success 时为空 |
| `message` | string | 说明文案 |

状态含义：`not_running` 未启动（可按需开 `launch_if_not_running`）；`forbidden` 不存在 /
不属于当前用户；`invalid` 参数或内部校验失败；`launch_failed` 启动浏览器失败；
`failed` 读取 cookie 时出现内部异常（可重试）。

`status == success` 但 `cookie_count == 0` 是**合法结果**（未登录），不算失败，前端按「0 条」展示。

## 4. 批量清空登录态

语义与单浏览器 `clear_login_state` 完全一致：清 cookie（含 HttpOnly）+ 权限授权 +
已打开页面的 localStorage / sessionStorage，并可选刷新页面；**不关闭、不重建浏览器**。

**支持按站点隔离**（`domains`）：

- 不传 = 清空全部（换号，现行为不变）；
- 传了 = 只清「访问这些站点会携带的 cookie」（含子域 / 父域），以及 origin 匹配页面的
  web storage；其它站点不受影响；
- 权限授权**仍整体重置**（Playwright 只有 `clear_permissions()`，没有按 origin 的能力），
  响应 message 会明确说明；
- 不清理 IndexedDB / Service Worker / HTTP 缓存，所以按站点清空**不等于彻底退出该站点**。

### 4.1 请求

`POST /operation/clear_login_state/batch`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `browser_ids` | `string[]` | 是 | 目标浏览器，上限见 `COOKIE_BATCH_MAX_BROWSERS` |
| `domains` | `string[] \| null` | 否 | 只清这些站点；空 = 全部。语义与单浏览器清空一致，非法域名整批 400 |
| `reload_pages` | bool | 否 | 默认 `true`，清空后提交后台刷新已打开页面（不阻塞响应） |

**没有 `launch_if_not_running`**：与批量导出同一套架构，接口不负责启动。
需要「先启动再清空」时由前端逐项调用 `POST /browser/control/create`（幂等），
启动完成后再清空一次。

### 4.2 响应

`StandardResponse[ClearLoginStateBatchResponse]`

| 字段 | 类型 | 说明 |
|---|---|---|
| `total_browsers` | int | 提交的浏览器数 |
| `succeeded` | int | 成功项数 |
| `failed` | int | 其余项数 |
| `total_affected_domains` | int | 成功项清理范围内覆盖的站点数之和 |
| `total_cleared_cookie_count` | int | 成功项清理的 cookie 总条数 |
| `results` | `ClearLoginStateBatchItemResult[]` | 逐项结果，顺序与 `browser_ids` 一致 |

`ClearLoginStateBatchItemResult`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `browser_id` | string | 浏览器 ID |
| `status` | string | 见下表 |
| `cleared_cookie_count` | int | 该项已清理的 cookie 条数 |
| `matched_domains` | string[] | 该项实际清理的 cookie 域名（去重排序） |
| `cleared_pages` | int | 已清空 web storage 的页面数 |
| `reloaded_pages` | int | 已提交后台刷新的页面数（响应不等待刷新完成） |
| `affected_domains` | int | 该项清理范围内 cookie 覆盖的站点数 |
| `message` | string | 说明文案 |

`status` 取值：

| status | 含义 | 前端展示 |
|---|---|---|
| `success` | 已清空 | 成功（绿） |
| `not_running` | 浏览器未启动，无法清 | 未启动（灰） |
| `workflow_running` | 工作流执行中，拒绝清空 | 工作流执行中（黄） |
| `forbidden` | 浏览器不存在 / 不属于当前用户 | 无权访问（红） |
| `invalid` | 参数或内部校验失败 | 参数非法（红） |
| `failed` | 清理过程抛内部异常 | 失败（红，可重试） |

`workflow_running` 必须**跳过而不是排队**：清登录态会把正在跑的工作流打成「莫名其妙的登录失败」。

## 5. 前端交互

### 5.1 入口

`BrowserFingerprintList.vue` 页头按钮组，顺序：

```
[工作流管理] [批量导入 Cookie] [批量导出 Cookie] [批量清空登录态] [创建指纹]
```

「批量清空登录态」使用危险色（`type="danger" plain`），其余默认。

### 5.2 弹窗（两个独立组件，沿用批量导入的交互模型）

- `BatchExportCookieDialog.vue`：选浏览器 → 可选填 `domains` → 导出 → 汇总结果 + 下载。
  提交是**两阶段编排**（见 §2.1），用户仍然只点一次：
  1. 先调批量导出（秒回），已启动的浏览器立即拿到 cookie；
  2. 若勾选了「先启动再导出」（默认勾选）且存在 `not_running` 项，则**逐项**调用
     `POST /browser/control/create`，界面显示 `正在启动 2/5` 进度；超过 5 个先二次确认
     （启动耗时较长且占用时长配额）；
  3. 只对刚启动成功的浏览器再导出一次，按 `browser_id` 合并回第一次的结果后下载。
  逐项启动失败 / 内存不足排队不阻塞其它项，结果上方用 Alert 汇总
  `启动结果：3 个已启动；1 个内存不足在排队（稍后重新导出）；1 个启动失败`。
- `BatchClearLoginStateDialog.vue`：选浏览器 → 可选填 `domains` → 二次确认 → 执行 → 汇总结果。
  与导出同样两阶段：先清空（未启动项 `not_running`）→ 勾选「先启动再清空」时
  逐项启动并显示 `正在启动 2/5` 进度 → 只对启动成功的项再清空一次并合并结果。
  二次确认文案必须说明「未启动的浏览器会先逐个启动（耗时较长）」或「会被跳过」，
  并带上清空范围（「全部站点」/「指定站点 xxx」）。
- 站点输入语义：留空 = 全部站点；多个用逗号 / 空格分隔，前端只做轻清洗，
  格式由后端归一化。按站点清空时提示「权限授权仍会整体重置；IndexedDB / SW 不在清理范围」。
- `BrowserDetail.vue` 的「登录态（Cookie）」区块同步增加「清空站点」输入框，
  与「导出站点」并列，留空 = 清空全部（换号）。

浏览器列表由弹窗自己拉取（与 `BatchImportCookieDialog` 一致，不依赖列表页行选择），
多选展示 `browser_id + 名称`，并显示已选数量 / 上限。

前端用一个常量（如 `COOKIE_BATCH_LIMIT = 50`）做交互层拦截，**与后端默认值对齐即可**，
不要硬编码在多处；后端用 `COOKIE_BATCH_MAX_BROWSERS` 调整后，前端同步该常量。
即使前端漏改，超限也会被后端请求模型拒绝，不会静默截断。

### 5.3 批量导出：下载格式

**一次请求、一次下载**，不按浏览器拆多个文件（多文件下载会被浏览器拦截）。
文件名：`cookies_batch_{yyyyMMddHHmmss}.json`，内容：

```json
[
  { "browser_id": "1001", "browser_name": "小号A", "cookies": [ /* CookieData */ ] },
  { "browser_id": "1002", "browser_name": "小号B", "cookies": [ /* CookieData */ ] }
]
```

**顶层必须是数组**，不能包一层 `{ "groups": [...] }`：现存 `parseCookieGroups` 只接受
`[[cookie...]]` 或 `[{ cookies: [...] }]`，包对象会让「批量导出 → 批量导入」直接解析失败。
`exported_at` / `domains` 这类元信息不写进文件（文件名已含时间戳，cookie 自带 domain）。

约束：

- 数组只包含 `status == success && cookie_count > 0` 的项，**顺序与勾选顺序一致**；
- 该结构可直接被现存 `utils/rpa/cookieJson.ts` 的 `parseCookieGroups` 解析
  （`[{ cookies: [...] }, ...]` 已被支持），即「批量导出文件」能原样喂给「批量导入」，形成闭环；
- 全部项都是 0 条时提示「未匹配到 cookie」并**不下载空文件**。

### 5.4 批量导出：结果展示

汇总条：`共 N 个：成功 X（Y 条 cookie）/ 未成功 Z`；
下方逐项表格：浏览器 + 状态 Tag + cookie 条数 + 覆盖域名数 + `message` 里的原因，
未启动项标「未启动」（需先在浏览器画面页启动才能导出）。

### 5.5 批量清空：二次确认

必须 `ElMessageBox.confirm`，文案示例：

```
将清空选中的 N 个浏览器全部站点 cookie（含 HttpOnly）与页面登录缓存，用于换号。
工作流执行中的浏览器会被跳过。该操作不可撤销，确定继续？
```

确认按钮文案「清空」，危险色。弹窗内**不提供**「全选所有页」这类一次选中成百上千的入口。

### 5.6 批量清空：结果展示

汇总条：`共 N 个：成功 X（影响 Y 个站点）/ 未启动 Z / 工作流执行中 W`；
逐项列表用 Tag 区分 `success` / `not_running` / `workflow_running` / `forbidden` /
`invalid` / `failed`，失败项给出 `message` 里的原因，**不自动重试**。

## 6. 前端实现约束

- 不得把任何 `cookies` 写入 `localStorage` / `sessionStorage` / IndexedDB，只在弹窗内存中流转并交给下载；
- 不得为了「能导出」而隐式调用启动接口；
- 关闭弹窗即丢弃结果与选择；
- 两个弹窗共用 `components/rpa-browser/` 下的状态 Tag 映射，避免与批量导入的文案不一致。

## 7. 后端实现（已完成）

| 内容 | 位置 |
|---|---|
| 请求 / 响应模型、状态枚举 | `app/models/runtime/browser_operation.py`（`ExportCookiesBatch*` / `ClearLoginStateBatch*`） |
| 批量上限配置 | `app/config.py` → `cookie_batch_max_browsers`（环境变量 `COOKIE_BATCH_MAX_BROWSERS`） |
| 两个路由 + 逐项归属校验 | `app/controller/v1/browser_control/operation/router.py`（`export_cookies_batch` / `clear_login_state_batch`） |
| 逐项编排（取会话 → touch → 执行 → 转状态） | `app/services/RPA_browser/session/cookie_batch_service.py` |
| 站点过滤预校验 | `BrowserDataService.validate_domains`（循环前一次性校验，非法整批 400） |

注意事项：

- 会话获取不走 `require_active_browser_session` 依赖（它只能覆盖单个浏览器），
  而是在 service 内按 `browser_id` 从会话池取；取不到即 `not_running`；
- 批量导入的 `browser_ids` 上限也改用同一个配置项，三个批量接口口径一致；
- 审计日志（`logger.warning`）记录 `mid`、浏览器数、成功/失败数、cookie 总条数、过滤域名，
  **不打印 cookie 值**。

## 8. 验收清单

- [ ] 2 个运行中 + 1 个未启动、勾选「先启动再导出」→ 只点一次：出现 `正在启动 1/1` 进度，最终 3 项都 success，下载 JSON 含 3 组；
- [ ] 同上但取消勾选 → 未启动项为 `not_running`，下载 JSON 只含 2 组，提示明确指出「1 个浏览器未启动」，且不触发任何启动；
- [ ] 未启动项启动后因内存不足进入排队（`queued`）→ 该项不出现在下载文件里，结果上方的 Alert 提示「N 个内存不足在排队」，不阻塞其它项；
- [ ] 上述导出文件直接粘贴进「批量导入 Cookie」→ 能解析出与成功项数量一致的组且顺序一致；
- [ ] 勾选含工作流运行中的浏览器 → 批量清空：该项为 `workflow_running`，其它项正常清空，汇总数量正确；
- [ ] 1 个运行中 + 1 个未启动、勾选「先启动再清空」→ 只点一次：出现 `正在启动 1/1` 进度，最终 2 项都 success，未启动那个重新打开页面后确认已退出登录；
- [ ] 同上但取消勾选 → 未启动项为 `not_running`，结果上方提示可勾选「先启动再清空」；
- [ ] 指定 `domains=["bilibili.com"]` 清空 → 只清 B 站相关域名（`.bilibili.com` / `api.bilibili.com`），同浏览器里 `.other.com` 的 cookie 仍在、重新打开该站点仍是登录态；
- [ ] 指定站点清空后，无关站点的页面没有被清 web storage、也没有被刷新；
- [ ] `domains=["", "  "]` → 整批 `400 INVALID_PARAM`，不退化成清空全部；
- [ ] 返回的 `cleared_cookie_count` / `matched_domains` 与实际清理内容一致；
- [ ] 传入超过 `COOKIE_BATCH_MAX_BROWSERS` 的 `browser_ids` → 后端直接报参数错误，不截断执行；
- [ ] 传入不属于自己的 `browser_id` → 该项 `forbidden`，不影响其它项；
- [ ] `domains` 全非法 → 整批 `400 INVALID_PARAM`，不返回逐项结果；
- [ ] 日志中检索不到任何 cookie 值。
