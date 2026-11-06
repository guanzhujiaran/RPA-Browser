# 内置动作补齐（第一批 + 第二批）与 MCP 同步实施计划书

> 用途：移交新会话执行。执行方只按本文件 + 文末提示词作业。
> 仓库：`/home/minato/BilibiliExplosion/RPA-Browser`（Python 3.13 / FastAPI / Playwright）。

---

## 1. 背景与目标

当前内置动作 26 个（`app/models/execution/builtin_action_types.py:161`），缺少「键盘输入、响应级等待、元素状态读取、下拉选择、URL 级等待、页面前进后退/刷新」这些**站点无关的通用机制**。

实测影响（2026-11-06 两轮运行）：

- 等待动作累计 2777s / 6321s（44%），大量固定 sleep 与「翻页 + 读评论区」推断；
- 评论是否真的提交、转发是否生效，只能靠「输入框是否清空 + 3 次刷新」间接判断；
- 已点赞/已关注只能靠 class 文本与选择器猜，`wait` 超时恒成功（`interaction.py:384`），状态判定全靠 if_else 兜底。

目标：补齐 5 个动作 + 1 个参数扩展，并同步 MCP 侧配置/文档/校验，使新动作在「动作面板 → 执行引擎 → MCP 调试通道」三处完全一致。

---

## 2. 范围与边界

### 2.1 本次要做（合并第一批 + 第二批）

| # | 动作 / 变更 | 类型 | 说明 |
|---|---|---|---|
| 1 | `press` | 新增动作 | 键盘按键 / 组合键，可先聚焦 selector |
| 2 | `wait_for_response` | 新增动作 | 等待匹配的 HTTP 响应，输出状态/是否成功/响应体文本 |
| 3 | `element_state` | 新增动作 | 读属性 / 数量 / 可见 / 可用 / 输入框值 |
| 4 | `select_option` | 新增动作 | 原生下拉选择（value / label / index） |
| 5 | `wait_for_url` | 新增动作 | 等待 URL 匹配（glob / regex） |
| 6 | `navigate` 扩展 `mode` | 参数扩展 | `goto`（默认）/ `reload` / `back` / `forward`，`url` 仅 goto 必填 |

### 2.2 明确不做（第三批 + 禁止项）

- ❌ `set_input_files`（文件上传）：涉及本地文件路径与权限边界，风险高。
- ❌ `frame` / iframe 内操作：与验证码/登录 iframe 强相关，暂不做。
- ❌ `download`（下载管理）：当前无业务需求，且涉及落盘路径。
- ❌ `evaluate` / `addScriptTag` / `setContent`：`actions/bili.py:8` 已明确禁止页面注入 JS。
- ❌ `route` / 请求拦截 / mock：非用户级 RPA 能力，误用会静默改坏请求。
- ❌ `tracing` / `video` / CDP / aria snapshot：属引擎可观测性，不做成用户动作。
- ❌ 不改任何业务动作 JSON（`actions/*.json`）与其数据库记录；本次只加内置动作能力。

### 2.3 硬约束（项目规则）

1. 内置动作必须**站点无关**：不得出现域名、站点选择器、错误页文案、资源类型分支。
2. 参数与结果一律 pydantic 模型；**类型注解禁止 `dict` / `Dict[str, Any]`**；Python 3.13 写法（`X | None`）。
3. 所有 import 写在文件头，禁止动态 import。
4. 每个动作：参数模型 + `VariableSpec` 变量契约 + `register_action_class` 注册 + 单测。
5. 不动 `.mcp.json` 之外的部署配置；不猜测网关地址，缺失则记录待办。

---

## 3. 交付物清单

| 交付物 | 位置 | 完成标准 |
|---|---|---|
| 本计划书 | `docs/动作补齐与MCP同步实施计划书.md` | 已存在且内容与代码一致 |
| 5 个动作 + 1 个参数扩展 | `app/services/execution/actions/*.py`、`app/models/execution/action_param_models.py` | 注册、可编译、有单测 |
| 枚举同步 | `app/models/execution/builtin_action_types.py` | Type/Name/Desc/Icon 四表对齐 |
| 一致性校验脚本 | `scripts/check_action_consistency.py` | 有差异即非零退出 |
| MCP 配置 | 仓库根 `.mcp.json`、`app/mcp/validator.py`、`docs/mcp动作调试计划书.md` | 与新动作一致 |
| 单测 | `test/actions/` | 新增 ≥ 12 用例，全量绿 |
| 验收报告 | 会话输出 | 含命令、输出、残留风险 |

---

## 4. 详细变更清单（执行依据）

### 4.1 `press`（action_id: `press`）

- 参数：`selector: str = ""`（提供则先 focus）、`key: str`（必填，≤50）、`modifiers: list[KeyboardModifierEnum] | None`、`timeout`（继承基类）。
- 行为：`locator.press(key, modifiers=...)`；无 selector 用 `page.keyboard.press`。
- 输出：`pressed: bool`。
- 失败：Playwright 异常 → `success=False`（可被 retry / on_error 接管）。
- 单测：①selector 聚焦后按 Enter 触发 keydown；②无 selector 全局按键；③缺 key 校验失败。

### 4.2 `wait_for_response`（action_id: `wait_for_response`）

- 参数：`url_pattern: str`（必填）、`pattern_type: glob|regex = glob`、`method: HttpMethodEnum | None`、`timeout`（默认 30000）、`include_body: bool = False`。
- 行为：`page.expect_response(predicate)`；命中即返回，超时 → **失败**（与 `wait` 的「超时也成功」语义相反，文档必须写明）。
- 输出：`response_status: int`、`response_ok: bool`、`response_url: str`、`response_text: str`（`include_body=true` 时截断到 20000 字符）。
- **禁止输出 dict**：响应体以字符串变量输出，需要结构化时由后续 `llm`/`if_else` 处理。
- 单测：①命中返回状态与文本；②超时失败；③method 过滤不匹配时继续等待。
- 风险：同 URL 多次请求 → 以「先命中先返回」为准，文档注明。

### 4.3 `element_state`（action_id: `element_state`）

- 参数：`selector`（必填）、`kind: attribute|count|visible|enabled|input_value`、`attribute: str = ""`（kind=attribute 时必填）、`missing: ignore|fail = ignore`、`timeout`。
- 行为与输出（单一变量 `state_value`）：
  - `attribute` → `str`（取不到为空串）
  - `input_value` → `str`
  - `count` → `int`
  - `visible` / `enabled` → `bool`
- `missing=fail` 时元素不存在直接失败；默认 `ignore` 返回空值，便于当探针。
- 单测：5 种 kind 各 1 例 + `missing` 两种策略 + `attribute` 缺参校验。

### 4.4 `select_option`（action_id: `select_option`）

- 参数：`selector`（必填）、`by: value|label|index = value`、`values: list[str]`（必填，≥1）、`timeout`。
- 行为：`locator.select_option(...)`，按 `by` 组装参数。
- 输出：`selected: list[str]`（实际选中值）、`selected_count: int`。
- 单测：value/label/index 三种选择 + 空 values 校验。

### 4.5 `wait_for_url`（action_id: `wait_for_url`）

- 参数：`url_pattern`（必填）、`pattern_type: glob|regex = glob`、`timeout`（默认 30000）。
- 行为：`page.wait_for_url`；超时 → 失败。
- 输出：`matched_url: str`。
- 单测：命中 / 超时失败 / regex 模式。

### 4.6 `navigate` 扩展

- 新增 `mode: goto|reload|back|forward = goto`。
- `url` 改为「mode=goto 时必填」，用 model validator 保证；其余 mode 忽略 url/wait_until。
- 输出保持 `status: int`（back/forward/reload 无法取 status 时返回 0 或沿用当前响应，需在模型里写明）。
- 单测：三种新 mode + 旧 JSON 全部编译通过（回归）。

### 4.7 枚举与元数据

`builtin_action_types.py` 需同时补：

1. `BuiltinActionDesc`：一句中文描述；
2. `BuiltinActionName`：动作面板显示名；
3. `BuiltinActionIconId`：唯一编号（现有已占 `{0,3,7,11,14,23,28,32,35,41,50,53,65,68,71,75,84,87,90,101,112,115,118,121}`，新编号取未占用值，建议从 124 起递增）。

> 注意：`open_page` / `close_page` / `parallel` 历史缺 Icon 成员（前端回落），新动作**不允许**缺，一致性脚本会拦。

---

## 5. 执行阶段（按顺序，不可跳步）

### P0 准备与基线（先做，产出基线数字）

```bash
cd /home/minato/BilibiliExplosion/RPA-Browser
git status --short            # 必须干净或明确记录在途改动
RPA_SELECTOR_E2E=0 .venv/bin/python -m pytest test/actions test/mcp test/execution_engine -q
.venv/bin/python - <<'EOF'
from app.models.execution.builtin_action_types import BuiltinActionType
from app.services.execution.actions.all_actions import get_all_actions_metadata
print("types=", len(list(BuiltinActionType)), "meta=", len(get_all_actions_metadata()))
EOF
```

基线（当前实测）：`types=26`、`meta=26`、`已注册类=25`（`composite` 无注册类，属正常）、`Icon=23`。

### P1 参数模型

`app/models/execution/action_param_models.py`：新增 5 组 `*Params` + `*Result`，补 `VariableSpec`；`NavigateParams` 增 `mode`。所有数值字段给 `ge/le`，字符串给 `max_length`。

### P2 枚举与注册

`builtin_action_types.py` 三张表 + `BuiltinActionType`；动作类文件（建议 `interaction.py` 放 press/element_state/select_option；`navigation.py` 放 wait_for_url；`wait_for_response.py` 新文件）+ `all_actions.py` 导出。

### P3 实现动作

按第 4 节规格逐个实现；每个动作 `_execute` 内先 `validate_params_with_model`，异常统一转 `ActionResult(success=False, error=...)`。

### P4 单测

`test/actions/test_press.py`、`test_element_state.py`、`test_select_option.py`、`test_wait_for_url.py`、`test_wait_for_response.py`、`test_navigation.py`（补 mode 用例）。用真实 Playwright page + `page.set_content` 本地 HTML，不联网。

### P5 MCP 同步

1. 仓库根 `.mcp.json`：登记 `rpa` server（`type: http`，`url: <gateway>/api/v1/rpa/mcp`）。**地址必须取自现有网关配置，不得猜测**；拿不到就保留空并在报告里标注待办。
2. `app/mcp/validator.py`：`_hint_for` 的动作名示例补上新动作（`press/wait_for_response/element_state/select_option/wait_for_url`）。
3. `docs/mcp动作调试计划书.md`：新增「新动作的 MCP 冒烟」小节（`rpa_debug_actions_list` 能列出、`rpa_debug_steps_validate` 能校验内联步骤）。
4. 工具清单不变：`EXPECTED_TOOLS` 不得增删（新增动作不新增 MCP 工具）。

### P6 一致性校验（门禁）

新增 `scripts/check_action_consistency.py`，断言：

1. `len(BuiltinActionType) == len(Name) == len(Desc)`，且每个 type 都有 Name/Desc；
2. 每个 type 有 Icon（历史 3 个例外 allowlist，脚本里显式列出）；
3. Icon 编号唯一；
4. `get_all_actions_metadata()` 条目数 == type 数，且每条 `name/desc/params_schema` 非空；
5. `set(type) - set(BUILTIN_ACTION_MAP) == {"composite"}`；
6. 所有 `actions/*.json` 能通过 `workflow_step_adapter.validate_python` + `PipelineBuilder.build`；
7. 输出 MCP 侧期望动作清单（供人工比对 `rpa_debug_actions_list`）。

脚本必须：有差异打印差异并以退出码 1 结束；无差异退出 0。

### P7 回归与真实验证

```bash
RPA_SELECTOR_E2E=0 .venv/bin/python -m pytest test -q
.venv/bin/python scripts/check_action_consistency.py
```

- 真跑（二选一）：
  - MCP：`rpa_debug_actions_list` → 应返回 31 个内置动作；`rpa_debug_steps_validate` 校验一段含新动作的内联 steps；
  - 本地：写一个临时 `test/actions/test_smoke_new_actions.py`，用 `page.set_content` 串起 `press → element_state → select_option → wait_for_url → wait_for_response`（`wait_for_response` 断言超时失败路径）。
- 生效需重启 RPA-Browser 进程（当前 debugpy 会话不会热加载）。

### P8 回滚预案

- 代码：所有改动集中在 6 个文件 + 1 个新动作文件 + 测试 + 脚本，`git revert <commit>` 即可；**无数据库变更、无动作 JSON 变更**，不需要回滚落库。
- MCP：`.mcp.json` 与文档同 commit，revert 同步生效；服务重启后恢复旧工具集（工具数不变）。
- 若新动作引发既有工作流异常（理论上不会，既有 JSON 不引用新动作）：先 `git revert`，再用 P0 基线命令确认 `types=26`。

---

## 6. 一致性要求（合并内容 ↔ MCP 配置 完全对应）

1. **单一数据源**：`BuiltinActionType` 是唯一动作清单；Name/Desc/Icon、元数据、MCP 动作列表全部由它派生，禁止另建白名单。
2. **四处同名同数**：`BuiltinActionType` / `BuiltinActionName` / `BuiltinActionDesc` / `BuiltinActionIconId` 的新增条目同名（`PRESS`、`WAIT_FOR_RESPONSE`、`ELEMENT_STATE`、`SELECT_OPTION`、`WAIT_FOR_URL`）。
3. **注册一致**：5 个新动作各有一个 `register_action_class`，`all_actions.py` 显式 import（漏 import 会导致 `未找到操作`）。
4. **MCP 一致**：`.mcp.json` 的 URL 必须等于 `MCPSettings.mount_path`（默认 `/api/v1/rpa/mcp`）；`EXPECTED_TOOLS` 保持 8 项不变；`rpa_debug_actions_list` 返回数 = 31。
5. **文档一致**：`docs/mcp动作调试计划书.md` 与 `docs/动作补齐与MCP同步实施计划书.md` 中的动作清单、数量、命令与实际代码一致。
6. **门禁**：P6 脚本必须进 CI/验收命令；任何一处漏改都会被脚本或单测拦住。

---

## 7. 验收标准（逐条勾选）

- [ ] `scripts/check_action_consistency.py` 退出码 0，打印 `types=31 meta=31`。
- [ ] `pytest test -q` 全绿（skip 仅限需真实浏览器的 E2E）。
- [ ] 每个新动作至少 2 个单测；`navigate` 旧行为用例全部保留通过。
- [ ] 所有 `actions/*.json` 编译通过（未修改 JSON）。
- [ ] `rpa_debug_actions_list`（或等价的 `get_all_actions_metadata()`）含 5 个新动作，且 name/desc/icon 齐全。
- [ ] `.mcp.json` 要么填入真实网关地址并通过连通性检查，要么明确标注「待填 + 原因」。
- [ ] 验收报告包含：改动文件清单、命令与输出、真跑/冒烟结果、剩余风险。

---

## 8. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| `wait_for_response` 时序抖动、同 URL 多次命中 | 判定错乱 | 必须给 `url_pattern` + 可选 `method`；文档写明「先命中先返回」；超时失败交由 on_error |
| `element_state` 变成"万能动作" | 参数膨胀、难维护 | 用 `kind` 枚举分流，输出固定 `state_value` |
| `press` 在 contenteditable 上不触发预期 | 评论提交失败 | 文档要求先 `selector` 聚焦；必要时配合 `element_state(input_value)` 校验 |
| `navigate.url` 改可选破坏旧校验 | 旧 JSON 报错 | model validator 仅在 `mode=goto` 要求 url；P7 全量 JSON 编译回归 |
| 图标编号与现有冲突 | 前端图标错乱 | 脚本断言编号唯一；新编号从 124 起 |
| `.mcp.json` 填错地址 | 本地调试连不上（不影响服务） | 地址取自现有网关配置；拿不到就留空并标注待办 |
| MCP 工具清单被误改 | 客户端工具缺失 | 断言 `EXPECTED_TOOLS` 不变 |

---

## 9. 新会话执行提示词（直接复制）

```text
你在 /home/minato/BilibiliExplosion/RPA-Browser 工作。请严格执行 docs/动作补齐与MCP同步实施计划书.md。

【任务】只做「第一批 + 第二批」内置动作补齐与 MCP 同步：
1) 新增动作 press / wait_for_response / element_state / select_option / wait_for_url；
2) NavigateParams 增加 mode=goto|reload|back|forward（url 仅 goto 必填）；
3) 同步更新 MCP 相关配置/文档/校验脚本。
严格按计划书的 P0→P8 顺序执行，不得跳步。

【绝对不做】
- 不做 set_input_files / frame / download（第三批，风险过高）；
- 不做 evaluate/addScriptTag/setContent/route/tracing 等页面注入或网络拦截能力；
- 不修改任何 actions/*.json 业务动作与其数据库记录；
- 不猜测 MCP 网关地址；拿不到就留空并记录待办，不得编造。

【硬性技术约束】
- Python 3.13；类型注解禁止 dict / Dict[...]，参数与结果一律 pydantic 模型；
- import 全部写在文件头，禁止动态 import；
- 动作必须站点无关（不得出现域名/站点选择器/错误页文案/资源类型分支）；
- 每个动作必须：参数模型 + VariableSpec 变量契约 + register_action_class + all_actions 导出 + 单测；
- 每个新动作补齐 BuiltinActionType/Desc/Name/IconId 四张表，图标编号唯一（现有占用 0,3,7,11,14,23,28,32,35,41,50,53,65,68,71,75,84,87,90,101,112,115,118,121，新编号从 124 起）。

【执行要点】
- 先跑 P0 基线并把数字记下来（当前应为 types=26 / meta=26 / 注册类=25，composite 无注册类属正常）；
- wait_for_response 超时必须失败（与 wait 超时成功的语义相反），响应体只输出字符串变量，禁止 dict；
- element_state 用 kind 枚举（attribute/count/visible/enabled/input_value），输出固定 state_value，missing=ignore|fail 默认 ignore；
- 单测用真实 Playwright page + page.set_content 本地 HTML，不联网；
- 新增 scripts/check_action_consistency.py 作为门禁（有差异退出码 1）。

【验证（必须执行并把输出贴进结果）】
  RPA_SELECTOR_E2E=0 .venv/bin/python -m pytest test -q
  .venv/bin/python scripts/check_action_consistency.py
并做一次新动作冒烟：get_all_actions_metadata() 或 rpa_debug_actions_list 必须返回 31 个动作；若 RPA 服务在跑，说明需重启进程才生效。

【停下条件】
- 发现计划书与实际代码冲突（文件/函数/枚举名对不上）时，先停下说明差异，不擅自改设计；
- 需要数据库迁移、需要改业务 JSON、或需要第三批能力时，停下询问。

【输出格式】
1) 改动文件清单（路径 + 一句话说明）；
2) 5 个动作的参数/输出/失败语义简述；
3) 一致性校验与全量测试的结果（命令 + 关键输出）；
4) MCP 同步项完成情况（.mcp.json/validator/文档/脚本）；
5) 未完成项与风险（如网关地址待填、图标素材待补）。
```
