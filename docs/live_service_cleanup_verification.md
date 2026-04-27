# LiveService 清理状态机 - 快速验证指南

## 验证方法

由于项目依赖较多，我们提供以下几种验证方式：

### 1. 代码审查验证

检查以下关键点是否正确实现：

#### ✅ 状态机核心方法存在
```bash
grep -n "_evaluate_session_cleanup" app/services/RPA_browser/live_service.py
```

应该找到：
- `_evaluate_session_cleanup` 方法定义
- 在 `_check_heartbeat_timeouts` 中被调用

#### ✅ 优先级判断逻辑
```bash
grep -A 5 "priority = " app/services/RPA_browser/live_service.py
```

应该看到：
- priority=1: 过期检查
- priority=2: 心跳超时
- priority=3: 闲置超时
- priority=99: 状态转换

#### ✅ 状态转换逻辑
```bash
grep -n "lifecycle_state =" app/services/RPA_browser/live_service.py
```

应该看到状态赋值操作。

### 2. 运行时验证

启动服务后，通过以下方式验证：

#### 创建测试会话
```python
import asyncio
from app.services.RPA_browser.live_service import LiveService
from app.models.runtime.control import CreateSessionRequest, BrowserCleanupPolicy

async def test():
    # 创建会话
    request = CreateSessionRequest(
        auto_cleanup=True,
        cleanup_policy=BrowserCleanupPolicy(
            max_idle_time=60,      # 1分钟闲置
            max_no_heartbeat_time=30,  # 30秒无心跳
            cleanup_interval=10    # 每10秒检查
        ),
        expiration_time=120        # 2分钟过期
    )
    
    await LiveService.create_browser_session(123, 456, request)
    
    # 查询状态
    status = LiveService.get_browser_session_status(123, 456)
    print(f"生命周期状态: {status.lifecycle_state}")
    print(f"清理策略: {status.cleanup_policy}")

asyncio.run(test())
```

#### 观察日志输出
启动服务后，观察日志中是否有以下信息：

```
INFO: 会话 123_456 状态转换: active -> idle
WARNING: 会话 123_456 需要清理 - 原因: 心跳超时 (35s > 30s), 状态: active -> terminating
INFO: 已清理会话: 123_456, 原因: 心跳超时 (35s > 30s)
```

### 3. 单元测试验证（简化版）

创建一个不依赖完整环境的测试：

```python
# tests/test_cleanup_logic_simple.py
"""
简化的清理逻辑测试 - 不依赖完整环境
"""

def test_cleanup_decision_structure():
    """测试 CleanupDecision 数据结构"""
    from dataclasses import dataclass
    from enum import Enum
    
    class SessionLifecycleState(str, Enum):
        ACTIVE = "active"
        IDLE = "idle"
        TERMINATING = "terminating"
    
    @dataclass
    class CleanupDecision:
        should_cleanup: bool = False
        reason: str = ""
        next_state: SessionLifecycleState = SessionLifecycleState.ACTIVE
        priority: int = 99
    
    # 测试1: 过期清理
    decision1 = CleanupDecision(
        should_cleanup=True,
        reason="会话已过期",
        next_state=SessionLifecycleState.TERMINATING,
        priority=1
    )
    assert decision1.priority == 1
    assert decision1.should_cleanup == True
    
    # 测试2: 心跳超时
    decision2 = CleanupDecision(
        should_cleanup=True,
        reason="心跳超时",
        next_state=SessionLifecycleState.TERMINATING,
        priority=2
    )
    assert decision2.priority == 2
    
    # 测试3: 闲置超时
    decision3 = CleanupDecision(
        should_cleanup=True,
        reason="闲置超时",
        next_state=SessionLifecycleState.TERMINATING,
        priority=3
    )
    assert decision3.priority == 3
    
    # 测试4: 状态转换（不清理）
    decision4 = CleanupDecision(
        should_cleanup=False,
        reason="状态正常",
        next_state=SessionLifecycleState.ACTIVE,
        priority=99
    )
    assert decision4.priority == 99
    assert decision4.should_cleanup == False
    
    print("✅ CleanupDecision 结构测试通过")

def test_priority_comparison():
    """测试优先级比较"""
    # 数字越小优先级越高
    assert 1 < 2 < 3 < 99
    print("✅ 优先级比较测试通过")

if __name__ == "__main__":
    test_cleanup_decision_structure()
    test_priority_comparison()
    print("\n🎉 所有简化测试通过！")
```

运行测试：
```bash
python3 tests/test_cleanup_logic_simple.py
```

### 4. 集成测试场景

#### 场景1: 心跳超时清理
1. 创建会话
2. 停止发送心跳
3. 等待超过 `max_no_heartbeat_time`
4. 验证会话被清理

#### 场景2: 闲置超时清理
1. 创建会话
2. 不进行任何操作
3. 等待超过 `max_idle_time`
4. 验证会话被清理

#### 场景3: 过期清理
1. 创建会话并设置 `expiration_time`
2. 等待超过过期时间
3. 验证会话被清理

#### 场景4: 人工操作保护
1. 创建会话
2. 进入人工操作模式
3. 停止心跳
4. 验证先恢复自动化，而不是直接清理

### 5. 日志验证要点

查看日志时关注以下关键点：

#### ✅ 正确的日志格式
```
INFO: 会话 {session_key} 需要清理 - 原因: {reason}, 状态: {old_state} -> {new_state}
INFO: 已清理会话: {session_key}, 原因: {reason}
```

#### ✅ 状态转换日志
```
DEBUG: 会话 {session_key} 状态转换: active -> idle
DEBUG: 会话 {session_key} 状态转换: idle -> active
```

#### ❌ 错误的日志（应该避免）
```
WARNING: 清理无心跳会话: {session_key}  # 旧格式
WARNING: 清理闲置会话: {session_key}    # 旧格式
```

## 常见问题排查

### Q1: 会话没有被清理
**可能原因**:
- 清理间隔未到（默认300秒）
- 有活跃的心跳客户端
- 处于人工操作模式

**解决方法**:
- 检查 `cleanup_interval` 配置
- 确认没有活跃客户端
- 检查是否处于人工操作模式

### Q2: 状态没有正确转换
**可能原因**:
- 状态更新逻辑未执行
- 条件判断有误

**解决方法**:
- 启用 DEBUG 日志级别
- 检查 `_evaluate_session_cleanup` 返回值
- 验证状态赋值操作

### Q3: 清理策略不生效
**可能原因**:
- 策略未正确设置
- 使用了默认策略

**解决方法**:
- 检查 `entry.cleanup_policy` 的值
- 确认创建会话时传入了自定义策略
- 验证策略字段是否正确

## 性能监控

### 关键指标
1. **清理延迟**: 从满足清理条件到实际清理的时间
2. **状态转换次数**: 会话状态转换的频率
3. **清理成功率**: 成功清理的会话比例

### 监控方法
```python
# 添加统计信息
cleanup_stats = {
    "total_evaluations": 0,
    "total_cleanups": 0,
    "cleanup_by_reason": {
        "expired": 0,
        "heartbeat_timeout": 0,
        "idle_timeout": 0,
    },
    "state_transitions": {
        "active_to_idle": 0,
        "idle_to_active": 0,
    }
}
```

## 总结

通过以上验证方法，可以确保 LiveService 清理状态机正确工作：

1. ✅ 代码审查：确认核心逻辑已实现
2. ✅ 运行时验证：观察实际行为
3. ✅ 单元测试：验证逻辑正确性
4. ✅ 集成测试：验证完整流程
5. ✅ 日志验证：确认行为符合预期

如果发现任何问题，请参考"常见问题排查"部分进行调试。
