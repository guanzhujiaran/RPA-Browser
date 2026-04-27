# LiveService 会话清理状态机设计文档

## 概述

LiveService 使用状态机模式来管理浏览器会话的生命周期和清理逻辑。该设计将原本分散的清理判断逻辑整合到一个统一的状态机中，提高了代码的可维护性和可预测性。

## 状态机核心组件

### 1. 会话生命周期状态 (SessionLifecycleState)

```python
class SessionLifecycleState(str, Enum):
    INITIALIZING = "initializing"  # 初始化中
    ACTIVE = "active"              # 活跃状态
    IDLE = "idle"                  # 闲置状态
    SUSPENDING = "suspending"      # 暂停中
    TERMINATING = "terminating"    # 终止中
    TERMINATED = "terminated"      # 已终止
```

### 2. 清理策略 (BrowserCleanupPolicy)

```python
class BrowserCleanupPolicy(SQLModel):
    max_idle_time: int = 1800          # 最大闲置时间（秒）
    max_no_heartbeat_time: int = 300   # 最大无心跳时间（秒）
    cleanup_interval: int = 300        # 清理检查间隔（秒）
```

## 清理判断优先级

状态机按照以下优先级顺序评估会话是否需要清理：

### 优先级 1: 过期时间检查 (expires_at)
- **条件**: `current_time > entry.expires_at`
- **动作**: 立即清理会话
- **例外**: 如果处于人工操作模式且有活跃连接，暂不清理
- **原因**: 明确的过期时间是最高优先级的清理信号

### 优先级 2: 心跳超时检查 (heartbeat timeout)
- **条件**: 
  - 没有活跃的心跳客户端 (`len(heartbeat_clients) == 0`)
  - 距离上次心跳时间超过阈值 (`time_since_last_heartbeat > max_no_heartbeat_time`)
- **动作**: 
  - 如果处于人工操作模式，先恢复自动化
  - 然后清理会话
- **原因**: 心跳是客户端存活的主要指标

### 优先级 3: 闲置超时检查 (idle timeout)
- **条件**:
  - 会话状态为 IDLE
  - 没有活跃连接 (`len(active_connections) == 0`)
  - 距离上次活动时间超过阈值 (`time_since_last_activity > max_idle_time`)
- **动作**: 清理会话
- **原因**: 长时间闲置的会话占用资源

### 优先级 4: 直播流超时检查 (live stream timeout)
- **条件**: 直播流最后心跳时间超过阈值 (`DEFAULT_LIVE_STREAM_TIMEOUT = 60s`)
- **动作**: 清理直播流，可能触发会话清理
- **原因**: 直播流超时表明客户端已断开

## 状态转换规则

### ACTIVE → IDLE
- **触发条件**: 
  - 会话状态变为 IDLE
  - 没有活跃连接
- **说明**: 会话进入闲置状态，等待进一步操作或清理

### IDLE → ACTIVE
- **触发条件**:
  - 有新的活跃心跳客户端
  - 或会话状态变为 RUNNING
- **说明**: 会话从闲置恢复为活跃状态

### ACTIVE/IDLE → TERMINATING
- **触发条件**: 任何清理条件满足（过期、心跳超时、闲置超时）
- **说明**: 会话进入终止流程，执行清理策略

### TERMINATING → TERMINATED
- **触发条件**: 清理完成，会话被释放
- **说明**: 会话完全终止，资源被回收

## 清理策略执行

### _execute_cleanup_strategy 方法

根据不同的清理原因执行不同的清理动作：

1. **心跳超时清理**:
   - 如果处于人工操作模式，先尝试恢复自动化
   - 清理关联的直播流
   - 释放浏览器会话

2. **闲置超时清理**:
   - 直接释放浏览器会话

3. **过期清理**:
   - 检查是否有人工操作保护
   - 如果没有保护，释放浏览器会话

4. **直播流超时清理**:
   - 停止视频流
   - 从直播流管理中移除
   - 如果没有其他活跃连接，恢复自动化或释放会话

## 后台任务

### _cleanup_task_loop
定期执行以下清理检查：
1. `_check_heartbeat_timeouts()` - 心跳超时检查（包含状态机评估）
2. `_check_live_stream_timeouts()` - 直播流超时检查
3. `cleanup_idle_browsers()` - 闲置浏览器清理
4. `cleanup_expired_sessions()` - 过期会话清理

### _heartbeat_monitor_loop
监控心跳状态，确保及时检测客户端断开。

## 优势

1. **统一的清理逻辑**: 所有清理判断都通过状态机进行，避免了逻辑分散
2. **明确的优先级**: 清理条件有明确的优先级顺序，避免冲突
3. **可预测的状态转换**: 状态转换规则清晰，便于调试和维护
4. **灵活的清理策略**: 支持自定义清理策略（max_idle_time, max_no_heartbeat_time等）
5. **保护机制**: 人工操作模式下的会话有特殊保护，避免误清理

## 使用示例

### 创建会话时设置清理策略

```python
request = CreateSessionRequest(
    auto_cleanup=True,
    cleanup_policy=BrowserCleanupPolicy(
        max_idle_time=1800,           # 30分钟闲置后清理
        max_no_heartbeat_time=300,    # 5分钟无心跳后清理
        cleanup_interval=300          # 每5分钟检查一次
    ),
    expiration_time=3600              # 1小时后过期
)
await LiveService.create_browser_session(mid, browser_id, request)
```

### 查询会话状态

```python
status = LiveService.get_browser_session_status(mid, browser_id)
print(f"生命周期状态: {status.lifecycle_state}")
print(f"清理策略: {status.cleanup_policy}")
print(f"过期时间: {status.expires_at}")
```

## 注意事项

1. **心跳机制**: 客户端应定期发送心跳，否则会话可能被清理
2. **人工操作保护**: 处于人工操作模式的会话有更长的保护时间
3. **清理延迟**: 清理不是实时的，取决于 cleanup_interval 配置
4. **资源释放**: 清理时会释放所有相关资源（浏览器、视频流、WebRTC连接等）

## 未来改进

1. 添加更细粒度的状态（如 WARNING 状态，在清理前发出警告）
2. 支持动态调整清理策略
3. 添加清理历史记录和统计信息
4. 支持会话暂停和恢复功能
