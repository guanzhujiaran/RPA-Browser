# LiveService 清理逻辑重构总结

## 重构目标

将 LiveService 中分散的清理逻辑整合为一个统一的状态机，提高代码的可维护性和可预测性。

## 问题分析

### 原有问题

1. **逻辑分散**: 清理判断分布在多个方法中：
   - `_check_heartbeat_timeouts()` - 心跳超时检查
   - `_check_live_stream_timeouts()` - 直播流超时检查
   - `cleanup_idle_browsers()` - 闲置浏览器清理
   - `cleanup_expired_sessions()` - 过期会话清理

2. **判断条件混乱**: 
   - 混合使用 heartbeat、expired、idle、live_stream_timeouts 等多种条件
   - 没有明确的优先级顺序
   - 存在重复的判断逻辑

3. **状态管理不清晰**:
   - 缺少明确的生命周期状态转换
   - 状态更新分散在各个方法中

## 重构方案

### 1. 引入状态机核心方法

#### `_evaluate_session_cleanup(entry, current_time)`
统一的会话清理评估方法，返回 `CleanupDecision` 对象：

```python
@dataclass
class CleanupDecision:
    should_cleanup: bool      # 是否应该清理
    reason: str               # 清理原因
    next_state: SessionLifecycleState  # 下一个状态
    priority: int             # 优先级（数字越小优先级越高）
```

**优先级顺序**（从高到低）:
1. **优先级 1**: 过期时间检查 (expires_at)
2. **优先级 2**: 心跳超时检查 (heartbeat timeout)
3. **优先级 3**: 闲置超时检查 (idle timeout)
4. **优先级 99**: 状态转换（不清理）

### 2. 重构清理方法

#### `_check_heartbeat_timeouts()`
- 使用状态机评估每个会话
- 根据评估结果决定是否清理
- 自动处理状态转换（ACTIVE ↔ IDLE）

#### `_check_live_stream_timeouts()`
- 简化逻辑，专注于直播流超时检测
- 超时时标记直播流为非活跃
- 触发 `_cleanup_live_stream()` 进行清理

#### `cleanup_idle_browsers()`
- 使用统一的闲置判断逻辑
- 基于 `cleanup_policy.max_idle_time` 配置
- 只清理真正闲置的会话

#### `cleanup_expired_sessions()`
- 基于 `entry.expires_at` 字段判断
- 保护人工操作模式下的会话
- 记录过期时长信息

### 3. 新增辅助方法

#### `_execute_cleanup_strategy(entry, decision)`
根据不同的清理原因执行不同的清理策略：

- **心跳超时**: 先恢复自动化，再清理
- **闲置超时**: 直接清理
- **过期**: 检查保护条件后清理
- **直播流超时**: 清理直播流，可能触发动画会清理

## 清理判断流程

```
开始清理检查
    ↓
遍历所有会话
    ↓
调用 _evaluate_session_cleanup()
    ↓
优先级 1: 检查 expires_at
    ├─ 已过期 → 返回 TERMINATING（优先级 1）
    └─ 未过期 ↓
优先级 2: 检查心跳超时
    ├─ 无活跃客户端且超时 → 返回 TERMINATING（优先级 2）
    ├─ 无活跃客户端但未超时 → 恢复自动化（不清理）
    └─ 有活跃客户端 ↓
优先级 3: 检查闲置超时
    ├─ IDLE + 无连接 + 超时 → 返回 TERMINATING（优先级 3）
    └─ 其他情况 ↓
状态转换判断
    ├─ IDLE → ACTIVE（有新活动）
    ├─ ACTIVE → IDLE（进入闲置）
    └─ 保持当前状态
    ↓
收集需要清理的会话
    ↓
执行清理策略
    ↓
释放浏览器会话
```

## 状态转换图

```
INITIALIZING
    ↓
ACTIVE ←→ IDLE
    ↓       ↑
TERMINATING
    ↓
TERMINATED
```

**转换条件**:
- **ACTIVE → IDLE**: 会话变为 IDLE 状态且无活跃连接
- **IDLE → ACTIVE**: 有新的活跃心跳或会话变为 RUNNING
- **ACTIVE/IDLE → TERMINATING**: 任何清理条件满足
- **TERMINATING → TERMINATED**: 清理完成

## 优势

### 1. 代码质量提升
- ✅ 统一的清理逻辑入口
- ✅ 明确的优先级顺序
- ✅ 减少代码重复
- ✅ 提高可读性

### 2. 可维护性提升
- ✅ 状态转换规则清晰
- ✅ 易于添加新的清理条件
- ✅ 便于调试和测试
- ✅ 日志更详细

### 3. 功能增强
- ✅ 支持自定义清理策略
- ✅ 保护人工操作模式
- ✅ 自动状态转换
- ✅ 更精确的清理时机

### 4. 可扩展性
- ✅ 易于添加新的生命周期状态
- ✅ 支持动态调整清理策略
- ✅ 可以添加清理历史记录
- ✅ 便于实现监控和告警

## 配置示例

### 默认清理策略
```python
BrowserCleanupPolicy(
    max_idle_time=1800,           # 30分钟闲置后清理
    max_no_heartbeat_time=300,    # 5分钟无心跳后清理
    cleanup_interval=300          # 每5分钟检查一次
)
```

### 自定义清理策略
```python
request = CreateSessionRequest(
    auto_cleanup=True,
    cleanup_policy=BrowserCleanupPolicy(
        max_idle_time=600,         # 10分钟闲置后清理
        max_no_heartbeat_time=120, # 2分钟无心跳后清理
        cleanup_interval=60        # 每1分钟检查一次
    ),
    expiration_time=7200           # 2小时后过期
)
```

## 日志改进

### 清理前
```
WARNING: 清理无心跳会话: 123_456
WARNING: 清理闲置会话: 123_456
WARNING: 清理过期会话: 123_456
```

### 清理后
```
INFO: 会话 123_456 需要清理 - 原因: 心跳超时 (350s > 300s), 状态: active -> terminating
INFO: 已清理会话: 123_456, 原因: 心跳超时 (350s > 300s)
INFO: 会话 123_456 状态转换: active -> idle
INFO: 会话 123_456 闲置超时: 1900s > 1800s
INFO: 已清理闲置会话: 123_456
```

## 兼容性

- ✅ 完全向后兼容
- ✅ 保留所有原有接口
- ✅ 不影响现有功能
- ✅ 默认行为保持一致

## 测试建议

### 单元测试
1. 测试状态机评估逻辑
2. 测试不同优先级的清理条件
3. 测试状态转换规则
4. 测试清理策略执行

### 集成测试
1. 测试完整的清理流程
2. 测试并发清理场景
3. 测试异常情况处理
4. 测试资源释放完整性

### 性能测试
1. 测试大量会话的清理性能
2. 测试清理对系统资源的影响
3. 测试后台任务的执行效率

## 后续优化方向

1. **添加 WARNING 状态**: 在清理前发出警告，给客户端反应时间
2. **清理历史记录**: 记录每次清理的原因和时间
3. **动态策略调整**: 支持运行时调整清理策略
4. **监控和告警**: 添加清理统计和异常告警
5. **优雅关闭**: 实现更优雅的会话关闭流程
6. **会话恢复**: 支持从 TERMINATING 状态恢复

## 相关文件

- `/app/services/RPA_browser/live_service.py` - 主要实现
- `/app/models/runtime/control.py` - 模型定义
- `/app/models/runtime/live_service.py` - 数据模型
- `/docs/live_service_cleanup_state_machine.md` - 详细设计文档

## 总结

本次重构将 LiveService 的清理逻辑从分散的多处判断整合为一个统一的状态机，通过明确的优先级顺序和状态转换规则，提高了代码的可维护性和可预测性。同时保留了所有原有功能，确保了向后兼容性。
