# CustomAction 与 Workflow 概念边界说明

## 📋 概述

本文档明确区分 RPA-Browser 系统中 **原子动作（Atomic Actions）**、**组合动作（Custom Actions）** 和 **工作流（Workflows）** 的概念边界，避免架构混淆。

---

## 🎯 三层动作架构

### 1️⃣ 原子动作 (Atomic Actions)

**定义**：系统预注册的、不可再分的基础操作。

**特点**：
- ✅ 由系统在 `action_registry` 中注册
- ✅ 直接调用 Playwright API
- ✅ 无内部步骤，是执行的最小单元
- ✅ 全局共享，所有用户可用

**示例**：
- `click` - 点击元素
- `input` - 输入文本
- `navigate` - 导航到 URL
- `screenshot` - 截图
- `wait` - 等待
- `scroll` - 滚动

**代码位置**：
```python
# app/services/execution/actions/
├── interaction.py   # ClickAction, InputAction, ScrollAction, WaitAction
├── navigation.py    # NavigateAction, NewPageAction
├── screenshot.py    # ScreenshotAction
├── llm.py          # LLMAction
└── control_flow.py # LoopAction, IfElseAction
```

**注册方式**：
```python
# app/services/execution/action_registry.py
action_registry.register_action(ClickAction())
action_registry.register_action(InputAction())
# ...
```

---

### 2️⃣ 组合动作 (Custom Actions / Composite Actions)

**定义**：用户定义的、可复用的动作组合（类似函数）。

**特点**：
- ✅ 包含多个步骤（`steps` 字段）
- ✅ 有明确的输入参数定义（`parameters_schema`）
- ✅ 可以被 Workflow 引用和调用
- ✅ 用户级隔离（通过 `mid` 字段）
- ✅ 可以公开给社区（`is_public` 字段）
- ✅ 支持 Fork 功能

**与 Workflow 的区别**：
| 特性 | Custom Action | Workflow |
|------|--------------|----------|
| 复杂度 | 轻量级 | 重量级 |
| 用途 | 可复用的动作片段 | 完整的业务流程 |
| 控制流 | 简单序列 | 支持循环、条件分支 |
| 错误处理 | 基础重试 | 高级错误恢复策略 |
| 嵌套深度 | 较浅 | 较深（可配置） |

**数据模型**：
```python
# app/models/core/workflow/models.py
class CustomActionModel(SQLModel, table=True):
    __tablename__ = "custom_action"
    
    id: int                           # 数据库主键
    action_id: str                    # 系统生成的唯一标识（格式：ca_xxx）
    name: str                         # 用户可编辑的显示名称
    version: str                      # 版本号
    action_type: str = "composite"    # 操作类型
    parameters_schema: List[Dict]     # 输入参数定义
    steps: List[Dict]                 # 步骤列表（引用原子动作或其他组合动作）
    is_composite: bool = True         # 标记为组合动作
    mid: int                          # 用户ID
    is_public: bool                   # 是否公开
    forks_count: int                  # 被 Fork 次数
    forked_from_id: int | None        # Fork 来源
    # ... 其他字段
```

**关键设计决策**：

#### ✅ `action_id` 由系统生成
```python
# ❌ 错误：用户不能自定义 action_id
action_id = request.action_id  # 不允许

# ✅ 正确：系统自动生成
action_id = f"ca_{uuid.uuid4().hex[:12]}"  # 格式：ca_a1b2c3d4e5f6
```

**原因**：
1. 保证全局唯一性
2. 避免命名冲突
3. 便于系统内部管理
4. 用户通过 `name` 字段进行业务层面的命名

#### ✅ `steps` 字段保留
```python
steps: List[Dict[str, Any]] = Field(
    default_factory=list, 
    sa_column=Column(JSON), 
    description="步骤列表JSON（引用原子动作或其他组合动作的执行序列）"
)
```

**原因**：
1. CustomAction 本质就是简化的 Workflow
2. 需要存储执行序列
3. 每个步骤引用一个原子动作或其他组合动作

**示例步骤结构**：
```json
{
  "action_id": "click",
  "params": {
    "selector": "#submit-button"
  }
}
```

---

### 3️⃣ 工作流 (Workflows)

**定义**：完整的业务流程，支持复杂的控制流和错误处理。

**特点**：
- ✅ 包含多个步骤（`steps` 字段）
- ✅ 支持循环（`loop_count`, `loop_while`, `loop_until`）
- ✅ 支持条件分支（`condition`）
- ✅ 支持错误处理策略（`error_handling`, `max_retries`）
- ✅ 可以调用原子动作和组合动作
- ✅ 用户级隔离
- ✅ 支持公开和 Fork

**数据模型**：
```python
# app/models/core/workflow/models.py
class UserWorkflowModel(SQLModel, table=True):
    __tablename__ = "user_workflow"
    
    id: int                           # 数据库主键
    workflow_id: str                  # 系统生成的唯一标识（格式：wf_xxx）
    name: str                         # 用户可编辑的显示名称
    steps: List[WorkflowStepRequest]  # 步骤列表（支持控制流）
    error_handling: str               # 错误处理策略
    max_retries: int                  # 最大重试次数
    timeout: int                      # 超时时间
    mid: int                          # 用户ID
    is_public: bool                   # 是否公开
    forks_count: int                  # 被 Fork 次数
    forked_from_id: int | None        # Fork 来源
    # ... 其他字段
```

**步骤结构（支持控制流）**：
```json
{
  "action_id": "click",
  "params": {"selector": "#button"},
  "loop_count": 5,                    // 循环 5 次
  "condition": "{{variable}} > 0",    // 条件执行
  "retry": 3,                         // 失败重试 3 次
  "children": [...]                   // 子步骤（用于循环体或分支）
}
```

---

## 🔄 三者关系图

```
┌─────────────────────────────────────────────┐
│           工作流 (Workflow)                  │
│  ┌─────────────────────────────────────┐    │
│  │  Step 1: 调用原子动作 (click)       │    │
│  │  Step 2: 调用组合动作 (login_action)│    │
│  │  Step 3: 条件分支 (if-else)         │    │
│  │  Step 4: 循环 (loop 5 times)        │    │
│  └─────────────────────────────────────┘    │
└─────────────────────────────────────────────┘
                    ▲
                    │ 调用
                    │
┌─────────────────────────────────────────────┐
│      组合动作 (Custom Action)                │
│  ┌─────────────────────────────────────┐    │
│  │  Step 1: click("#username")         │    │
│  │  Step 2: input("admin")             │    │
│  │  Step 3: click("#password")         │    │
│  │  Step 4: input("123456")            │    │
│  │  Step 5: click("#login-btn")        │    │
│  └─────────────────────────────────────┘    │
└─────────────────────────────────────────────┘
                    ▲
                    │ 调用
                    │
┌─────────────────────────────────────────────┐
│         原子动作 (Atomic Actions)            │
│  • click                                    │
│  • input                                    │
│  • navigate                                 │
│  • screenshot                               │
│  • wait                                     │
│  • scroll                                   │
│  • ...                                      │
└─────────────────────────────────────────────┘
```

---

## 📝 使用场景对比

### 场景 1：简单的登录操作

**推荐**：Custom Action

```python
# 创建一个名为 "网站登录" 的组合动作
CustomAction(
    name="网站登录",
    steps=[
        {"action_id": "navigate", "params": {"url": "https://example.com/login"}},
        {"action_id": "input", "params": {"selector": "#username", "text": "{{username}}"}},
        {"action_id": "input", "params": {"selector": "#password", "text": "{{password}}"}},
        {"action_id": "click", "params": {"selector": "#login-btn"}},
    ],
    parameters_schema=[
        {"name": "username", "type": "string", "required": True},
        {"name": "password", "type": "string", "required": True},
    ]
)
```

**优点**：
- ✅ 可复用（在多个 Workflow 中调用）
- ✅ 有明确的参数接口
- ✅ 易于维护和测试

---

### 场景 2：完整的自动化流程

**推荐**：Workflow

```python
# 创建一个名为 "每日数据抓取" 的工作流
Workflow(
    name="每日数据抓取",
    steps=[
        # 步骤 1：登录
        {"action_id": "ca_login_action", "params": {...}},
        
        # 步骤 2：循环抓取多页数据
        {
            "action_id": "navigate",
            "params": {"url": "https://example.com/data"},
            "loop_count": 10,
            "children": [
                {"action_id": "screenshot", "params": {...}},
                {"action_id": "click", "params": {"selector": ".next-page"}},
            ]
        },
        
        # 步骤 3：条件判断
        {
            "action_id": "llm",
            "params": {"prompt": "分析数据..."},
            "condition": "{{data_quality}} > 0.8"
        },
    ],
    error_handling="retry",
    max_retries=3,
    timeout=300000,  # 5 分钟
)
```

**优点**：
- ✅ 支持复杂的控制流
- ✅ 高级错误处理
- ✅ 可配置超时和重试

---

### 场景 3：基础交互操作

**推荐**：直接使用原子动作

```python
# 在工作流或组合动作中直接调用
{"action_id": "click", "params": {"selector": "#button"}}
{"action_id": "input", "params": {"selector": "#field", "text": "hello"}}
```

**优点**：
- ✅ 无需额外定义
- ✅ 系统预注册，开箱即用
- ✅ 性能最优

---

## 🔑 关键设计原则

### 1️⃣ `action_id` vs `name`

| 字段 | 用途 | 谁设置 | 示例 |
|------|------|--------|------|
| `action_id` | 系统内部唯一标识 | **系统自动生成** | `ca_a1b2c3d4e5f6` |
| `name` | 用户可见的业务名称 | **用户自定义** | "网站登录" |

**规则**：
- ✅ 用户只能通过 `name` 字段命名
- ❌ 用户不能指定 `action_id`
- ✅ `action_id` 格式固定为 `ca_xxx`（12位十六进制）

---

### 2️⃣ `steps` 字段的归属

**问题**：为什么 CustomAction 和 Workflow 都有 `steps`？

**答案**：因为它们都是"动作序列"，只是复杂度不同。

| 模型 | steps 内容 | 控制流支持 |
|------|-----------|-----------|
| CustomAction | 简单的动作序列 | ❌ 不支持 |
| Workflow | 复杂的动作序列 | ✅ 支持循环、条件、分支 |

**类比**：
- CustomAction 的 steps ≈ 函数的语句序列
- Workflow 的 steps ≈ 程序的语句序列（含控制流）

---

### 3️⃣ 命名规范

**ID 格式**：
- 原子动作：小写字母 + 下划线，如 `click`, `input_text`
- 组合动作：`ca_` + 12位十六进制，如 `ca_a1b2c3d4e5f6`
- 工作流：`wf_` + 12位十六进制，如 `wf_1a2b3c4d5e6f`
- 插件：`plugin_` + 8位十六进制，如 `plugin_a1b2c3d4`

**好处**：
1. 通过前缀快速识别类型
2. 避免 ID 冲突
3. 便于调试和日志追踪

---

## 🚀 迁移指南

如果你的代码中还在使用旧的 `user_xxx` 格式的 action_id，请更新为 `ca_xxx` 格式：

### 修改前
```python
action_id = f"user_{uuid.uuid4().hex[:12]}"
```

### 修改后
```python
action_id = f"ca_{uuid.uuid4().hex[:12]}"
```

**影响范围**：
- ✅ `app/controller/v1/browser_control/execution/action_router.py` - 已更新
- ✅ `app/services/execution/crud_service.py` - 已更新（Fork 方法）

---

## 📚 相关文档

- [Action Registry 设计](../app/services/execution/action_registry.py)
- [Execution Engine 实现](../app/services/execution/execution_engine.py)
- [前端分页迁移指南](./FRONTEND_PAGINATION_MIGRATION_GUIDE.md)

---

**最后更新**: 2026-05-13  
**维护者**: RPA-Browser 团队
