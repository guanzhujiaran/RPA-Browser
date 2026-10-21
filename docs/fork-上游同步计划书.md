# RPA Fork「上游同步」计划书（v1）

> 状态：P0/P1 已实现（2026-10-17）；P2（依赖树级聚合 / 上游删除通知）后续可选。
> 涉及仓库：`RPA-Browser`（后端全部）、`Vue3FrontEndDemoExercise`（展示与开关）
> 关联实现：`ActionCrudService.sync_from_upstream`、`WorkflowCrudService.sync_from_upstream`（已存在的手动同步能力）

## 1. 背景与目标

Fork（`CompositeActionModel` / `UserWorkflow`）目前只有**手动**同步上游的接口
（`POST /custom_actions/sync`、`POST /workflows/sync`），存在两个缺口：

1. **用户不知道自己的 Fork 是否落后于上游**：列表/详情响应只有 `forks_count` / `forked_from_id`，
   没有任何「上游已更新」的标识，用户只能盲目地点同步。
2. **没有自动同步**：上游作者更新后，所有 Fork 副本永远停留在旧版本，除非用户主动同步。

本计划书目标：

- **G1 展示同步状态**：Fork 资源在列表/详情中返回「是否需要同步上游」及相关时间戳。
- **G2 自动同步（可选开关）**：为单个 Fork 提供 `auto_sync_upstream` 开关，开启后由定时任务
  自动同步上游更新，并发送站内通知。
- **安全边界**：自动同步**绝不覆盖用户本地修改过的内容**；手动同步允许覆盖，但明确告知。

## 2. 方案总览

| 能力 | 方案 | 分期 |
|---|---|---|
| 同步状态展示 | 新增 `last_synced_at` 字段，按时间戳对比上游 `updated_at` | P0 |
| 本地修改保护 | 新增 `local_steps_modified` 标记，自动同步遇到即跳过 + 通知 | P0/P1 |
| 自动同步 | 新增 `auto_sync_upstream` 开关 + 10 分钟定时轮询任务 | P1 |
| 依赖树级聚合展示 | 同步时递归处理（已有逻辑）；列表只展示顶层状态 | 后续可选 |
| 上游删除/私有化通知 | 自动同步扫描时顺带检测并通知 | 后续可选 |

### 2.1 核心判定口径（时间戳快照）

- Fork 创建时：`last_synced_at = 上游.updated_at`（快照语义，表示"此版本同步自哪个上游版本"）。
- 同步成功时：`last_synced_at = 上游.updated_at`。
- **`need_sync` = `forked_from_id != None` 且上游存在且 `is_public` 且 `上游.updated_at > last_synced_at`**
  （`last_synced_at` 为 NULL 时视为epoch，即需要同步）。
- 上游已删除或转私有：`need_sync = False`（保留当前副本，与手动同步语义一致，不打扰用户）。

时间戳判定的前提已满足：`ActionCrudService.update` / `sync_from_upstream` / workflow 更新路径
都会刷新 `updated_at`（`models.py:79`），即上游任何内容变更都会推进 `updated_at`。

### 2.2 本地修改检测（粗粒度）

- 新增 `local_steps_modified: bool`：
  - Fork 创建 / 同步成功 → `False`；
  - 用户通过 `update` 接口**传了 `steps`**（即有意修改步骤内容）→ `True`；
  - 仅改名 / 改图标 / 改描述等不影响（不触发）。
- 这是**粗粒度判定**（不比对内容差异）：只要用户动过 steps 就认为有本地修改。
  代价是可能误跳自动同步，收益是零开销、语义确定、无复杂 diff。
- 自动同步遇到 `local_steps_modified = True`：**跳过该资源**，发通知告知"存在本地修改，请手动同步"。
- 手动同步遇到 `True`：**允许覆盖**（用户主动行为），响应中标记 `local_overwritten = true` 告知。

## 3. 数据模型变更

`app/models/database/workflow/models.py` → `CommunityResourceBase`（line 64，两个表共用）新增：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `last_synced_at` | `datetime \| None` | `None` | 最近一次同步时上游的 `updated_at`；None = 需要同步 |
| `auto_sync_upstream` | `bool` | `False` | 上游自动同步开关（仅 Fork 资源有意义） |
| `local_steps_modified` | `bool` | `False` | 同步后用户是否修改过 steps |

> 范围：仅 `CompositeActionModel` 与 `UserWorkflow`（社区 Fork 功能实际使用的两张表）。
> `WorkflowRecord` 是另一套历史结构，不纳入本期。

## 4. 业务规则

### 4.1 Fork 时（`action_crud.fork_deep` / `workflow_crud.fork`）

- 新副本三个字段：`last_synced_at = 上游.updated_at`、`auto_sync_upstream = 请求参数`、`local_steps_modified = False`。
- `_sync_child` 自动新建的子 Fork 同样初始化（`last_synced_at = 上游.updated_at`）。

### 4.2 手动同步时（`sync_from_upstream`）

- 每个成功同步的副本：`last_synced_at = 上游.updated_at`、`local_steps_modified = False`。
- 同步前若 `local_steps_modified = True`，在响应中返回 `local_overwritten = true`。
- 上游不可用的副本：保持不动，不改 `last_synced_at`。

### 4.3 用户更新时（`update`）

- 收到非空 `steps` → `local_steps_modified = True`。
- 支持通过 update 接口改 `auto_sync_upstream`（`None = 不修改`，兼容旧请求）。
- 更新自身**不影响** `need_sync`（判定只看上游时间戳与 `last_synced_at`）。

### 4.4 自动同步（P1，`ForkAutoSyncService`）

- **入口**：`app/setup.py` 注册 `add_interval_job(..., minutes=10, id="fork_upstream_auto_sync")`。
- **扫描 SQL 口径**（Action 与 Workflow 各一遍）：
  `forked_from_id IS NOT NULL AND auto_sync_upstream = 1 AND 上游存在 AND 上游.is_public AND 上游.updated_at > COALESCE(last_synced_at, '1970-01-01')`。
- **逐条处理**（单条异常不影响整体，每轮上限 200 条）：
  - `local_steps_modified = True` → 跳过 + 通知「存在本地修改，已跳过自动同步，请手动同步」；
  - 否则调 `sync_from_upstream`（同步成功由 service 内部维护字段）+ 通知同步结果摘要。
- Workflow 的自动同步同时覆盖外壳配置（复用 `WorkflowCrudService.sync_from_upstream` 的递归逻辑）。

### 4.5 通知

复用现有站内通知通道 `app/services/mq/rpc_notify.publish_notify`（工作流运行通知同款）：
- 标题携带资源唯一标识（`action_id` / `workflow_id` + 同步时间），借助 be-message 侧
  `(target_value, title)` 幂等，避免重复推送；
- 通知内容：同步结果摘要（更新了几个动作 / 新增几个 / 跳过原因）；
- RPC 未连接/失败只打日志，不影响同步主流程。

## 5. API 变更

### 5.1 响应模型（`app/models/workflow/models.py`）

以下响应新增字段（均为**可选带默认值**，向后兼容）：

| 响应模型 | 新增字段 |
|---|---|
| `CompositeActionListItemResponse` / `CompositeActionDetailResponse` | `has_upstream_update: bool = False`、`upstream_updated_at: int \| None`（秒级）、`last_synced_at: int \| None`（秒级）、`auto_sync_upstream: bool = False`、`local_steps_modified: bool = False` |
| `WorkflowListItemResponse` / `WorkflowDetailResponse` | 同上 |
| `ActionSyncResponse` / `WorkflowSyncResponse` | `local_overwritten: bool = False` |

> 时间对外一律**秒级 int32**（遵循 `.codebuddy/rules/unix-timestamp.mdc`），NULL 表示无。

### 5.2 请求模型

| 请求模型 | 变更 |
|---|---|
| `ActionForkRequest` / `WorkflowForkRequest` | 新增 `auto_sync_upstream: bool = False` |
| `CompositeActionUpdateRequest` / `WorkflowUpdateRequest` | 新增 `auto_sync_upstream: bool \| None = None`（None=不改，仅所有者可用） |

### 5.3 Controller（`action_router.py` / `workflow_router.py`）

- 列表/详情共 **10 处响应构造点**（action 5 处：create/get/update/list/forks；workflow 5 处：create/get/update/list/admin list）补齐字段；
  其中 list 接口用**批量查询**（一次 `IN` 拿所有上游 `id → updated_at/is_public`）避免 N+1。
- fork 接口透传 `auto_sync_upstream`；
- update 接口透传 `auto_sync_upstream`；
- sync 接口返回 `local_overwritten`。

## 6. 服务层变更

| 文件 | 变更 |
|---|---|
| `app/services/execution/crud_service/action_crud.py` | `fork_deep` / `_sync_child` 初始化三字段；`sync_from_upstream` 维护 `last_synced_at` / `local_steps_modified` 并计算 `local_overwritten`；`update` 支持 `auto_sync_upstream` 与 `local_steps_modified`；新增批量方法 `attach_upstream_update_flags(models)` |
| `app/services/execution/crud_service/workflow_crud.py` | 同上（`fork` / `sync_from_upstream` / `update` / `attach_upstream_update_flags`） |
| `app/services/execution/upstream_sync_service.py`（新增） | `ForkAutoSyncService.scan_and_sync_all()`：扫描 + 逐条同步 + 通知 |

`attach_upstream_update_flags` 输出结构（内部 dataclass，禁止裸 Dict）：

```python
@dataclass(slots=True)
class UpstreamSyncFlag:
    has_upstream_update: bool
    upstream_updated_at: int | None  # 秒
```

## 7. 迁移与存量数据

- 新增 alembic 迁移：`compositeactionmodel`、`userworkflow` 各加 3 列
  （`last_synced_at DATETIME NULL`、`auto_sync_upstream BOOLEAN NOT NULL DEFAULT 0`、
  `local_steps_modified BOOLEAN NOT NULL DEFAULT 0`）。
- 存量数据不回填：`last_synced_at = NULL` → 存量 Fork 首次展示为「需要同步」，
  用户手动同步一次后进入正常快照语义（存量 Fork 本就没有同步能力，提示一次可接受）。
- 启动时 Schema 一致性校验（`check_schemas`）自动覆盖新列。

## 8. 前端（Vue3FrontEndDemoExercise）

需求细节按规则另写 `docs/frontend_requirements/` 文档，要点：

1. **操作/工作流列表**：Fork 项（`forked_from_id != null`）展示「可同步」徽标（`has_upstream_update`），
   鼠标悬浮/详情展示 `upstream_updated_at` / `last_synced_at`。
2. **同步入口**：列表项/详情页「同步上游」按钮，调已有 `/custom_actions/sync`、`/workflows/sync`；
   `local_overwritten = true` 时提示"已覆盖本地修改"。
3. **Fork 对话框 / 编辑表单**：`auto_sync_upstream` 开关（默认关，附说明文案）。
4. **自动同步结果**：站内通知已覆盖；前端无需额外处理；可选在列表用 `local_steps_modified` 提示"有本地修改，自动同步已跳过"。

## 9. 分期实施

| 阶段 | 内容 | 验收 |
|---|---|---|
| P0 | §3 字段 + §7 迁移 + §4.1~4.3 规则 + §5 API 响应字段与开关透传 | ✅ 已实现 |
| P1 | §4.4 自动同步任务 + §4.5 通知 + 前端 §8 | ✅ 后端已实现；前端见 `docs/frontend_requirements/Fork上游同步.md` |
| 后续 | 依赖树级 need_sync 聚合、上游删除/私有化通知 | — |

## 10. 风险与取舍

1. **时间戳判定的边界**：同秒内的更新可能漏判（MySQL DATETIME 秒级精度；同步与上游编辑同秒的极端情况可接受）；
   上游"空改动"（只碰 updated_at 不改内容）会产生一次误报，用户点同步显示"已是最新版本"，无副作用。
2. **本地修改粗判**：用户改 steps 后即使改动很小，自动同步也会跳过——策略上"宁可不自动，不可丢用户数据"。
3. **自动同步覆盖风险**：仅对 `auto_sync_upstream = True` 且无本地修改的副本生效；手动同步始终是用户主动行为。
4. **通知依赖 be-message 可用**：RPC 不可用时降级为日志，不影响同步。
5. **存量首次误报**：`last_synced_at = NULL` 语义即"未知版本"，展示为需要同步符合直觉。
