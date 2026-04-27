"""
LiveService 清理状态机测试

测试状态机的核心逻辑，包括：
1. 清理决策评估
2. 优先级判断
3. 状态转换
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import time
from unittest.mock import Mock, MagicMock
from app.models.runtime.control import (
    BrowserStatusEnum,
    SessionLifecycleState,
    BrowserCleanupPolicy,
)
from app.models.runtime.live_service import BrowserSessionEntry


def test_evaluate_session_cleanup_expired():
    """测试过期会话清理"""
    from app.services.RPA_browser.live_service import LiveService
    
    # 创建已过期的会话
    current_time = int(time.time())
    entry = BrowserSessionEntry(
        mid=123,
        browser_id=456,
        plugined_session=Mock(),
        last_activity=current_time - 100,
        last_heartbeat=current_time - 100,
        status=BrowserStatusEnum.RUNNING,
        is_manual_mode=False,
        expires_at=current_time - 10,  # 已过期10秒
        cleanup_policy=BrowserCleanupPolicy(
            max_idle_time=1800,
            max_no_heartbeat_time=300,
            cleanup_interval=300
        )
    )
    
    decision = LiveService._evaluate_session_cleanup(entry, current_time)
    
    assert decision.should_cleanup == True
    assert decision.priority == 1
    assert "过期" in decision.reason
    assert decision.next_state == SessionLifecycleState.TERMINATING
    print("✅ 测试通过: 过期会话清理")


def test_evaluate_session_cleanup_heartbeat_timeout():
    """测试心跳超时清理"""
    from app.services.RPA_browser.live_service import LiveService
    
    current_time = int(time.time())
    entry = BrowserSessionEntry(
        mid=123,
        browser_id=456,
        plugined_session=Mock(),
        last_activity=current_time - 400,
        last_heartbeat=current_time - 400,  # 超过300秒
        status=BrowserStatusEnum.IDLE,
        is_manual_mode=False,
        heartbeat_clients={},  # 无活跃客户端
        cleanup_policy=BrowserCleanupPolicy(
            max_idle_time=1800,
            max_no_heartbeat_time=300,
            cleanup_interval=300
        )
    )
    
    decision = LiveService._evaluate_session_cleanup(entry, current_time)
    
    assert decision.should_cleanup == True
    assert decision.priority == 2
    assert "心跳超时" in decision.reason
    assert decision.next_state == SessionLifecycleState.TERMINATING
    print("✅ 测试通过: 心跳超时清理")


def test_evaluate_session_cleanup_idle_timeout():
    """测试闲置超时清理"""
    from app.services.RPA_browser.live_service import LiveService
    
    current_time = int(time.time())
    entry = BrowserSessionEntry(
        mid=123,
        browser_id=456,
        plugined_session=Mock(),
        last_activity=current_time - 2000,  # 超过1800秒
        last_heartbeat=current_time - 100,  # 心跳正常
        status=BrowserStatusEnum.IDLE,
        is_manual_mode=False,
        active_connections=set(),  # 无活跃连接
        heartbeat_clients={"client1": current_time - 100},
        cleanup_policy=BrowserCleanupPolicy(
            max_idle_time=1800,
            max_no_heartbeat_time=300,
            cleanup_interval=300
        )
    )
    
    decision = LiveService._evaluate_session_cleanup(entry, current_time)
    
    assert decision.should_cleanup == True
    assert decision.priority == 3
    assert "闲置超时" in decision.reason
    assert decision.next_state == SessionLifecycleState.TERMINATING
    print("✅ 测试通过: 闲置超时清理")


def test_evaluate_session_cleanup_with_active_clients():
    """测试有活跃客户端时不清理"""
    from app.services.RPA_browser.live_service import LiveService
    
    current_time = int(time.time())
    entry = BrowserSessionEntry(
        mid=123,
        browser_id=456,
        plugined_session=Mock(),
        last_activity=current_time - 400,
        last_heartbeat=current_time - 400,
        status=BrowserStatusEnum.RUNNING,
        is_manual_mode=False,
        heartbeat_clients={"client1": current_time - 50},  # 有活跃客户端
        cleanup_policy=BrowserCleanupPolicy(
            max_idle_time=1800,
            max_no_heartbeat_time=300,
            cleanup_interval=300
        )
    )
    
    decision = LiveService._evaluate_session_cleanup(entry, current_time)
    
    # 有活跃客户端，不应该清理
    assert decision.should_cleanup == False
    print("✅ 测试通过: 有活跃客户端时不清理")


def test_evaluate_session_state_transition_active_to_idle():
    """测试状态转换: ACTIVE → IDLE"""
    from app.services.RPA_browser.live_service import LiveService
    
    current_time = int(time.time())
    entry = BrowserSessionEntry(
        mid=123,
        browser_id=456,
        plugined_session=Mock(),
        last_activity=current_time - 100,
        last_heartbeat=current_time - 100,
        status=BrowserStatusEnum.IDLE,
        lifecycle_state=SessionLifecycleState.ACTIVE,
        is_manual_mode=False,
        active_connections=set(),
        heartbeat_clients={},
        cleanup_policy=BrowserCleanupPolicy(
            max_idle_time=1800,
            max_no_heartbeat_time=300,
            cleanup_interval=300
        )
    )
    
    decision = LiveService._evaluate_session_cleanup(entry, current_time)
    
    # 应该从 ACTIVE 转换为 IDLE
    assert decision.should_cleanup == False
    assert decision.next_state == SessionLifecycleState.IDLE
    print("✅ 测试通过: 状态转换 ACTIVE → IDLE")


def test_evaluate_session_state_transition_idle_to_active():
    """测试状态转换: IDLE → ACTIVE"""
    from app.services.RPA_browser.live_service import LiveService
    
    current_time = int(time.time())
    entry = BrowserSessionEntry(
        mid=123,
        browser_id=456,
        plugined_session=Mock(),
        last_activity=current_time - 100,
        last_heartbeat=current_time - 100,
        status=BrowserStatusEnum.RUNNING,
        lifecycle_state=SessionLifecycleState.IDLE,
        is_manual_mode=False,
        active_connections={"conn1"},
        heartbeat_clients={"client1": current_time - 10},
        cleanup_policy=BrowserCleanupPolicy(
            max_idle_time=1800,
            max_no_heartbeat_time=300,
            cleanup_interval=300
        )
    )
    
    decision = LiveService._evaluate_session_cleanup(entry, current_time)
    
    # 应该从 IDLE 转换为 ACTIVE
    assert decision.should_cleanup == False
    assert decision.next_state == SessionLifecycleState.ACTIVE
    print("✅ 测试通过: 状态转换 IDLE → ACTIVE")


def test_evaluate_manual_mode_protection():
    """测试人工操作模式保护"""
    from app.services.RPA_browser.live_service import LiveService
    
    current_time = int(time.time())
    entry = BrowserSessionEntry(
        mid=123,
        browser_id=456,
        plugined_session=Mock(),
        last_activity=current_time - 400,
        last_heartbeat=current_time - 400,
        status=BrowserStatusEnum.PAUSED,
        is_manual_mode=True,
        active_connections={"conn1"},
        heartbeat_clients={},  # 无心跳但有人工操作
        cleanup_policy=BrowserCleanupPolicy(
            max_idle_time=1800,
            max_no_heartbeat_time=300,
            cleanup_interval=300
        )
    )
    
    decision = LiveService._evaluate_session_cleanup(entry, current_time)
    
    # 人工操作模式下，应该先恢复自动化而不是直接清理
    assert decision.should_cleanup == False
    assert "恢复自动化" in decision.reason
    print("✅ 测试通过: 人工操作模式保护")


def test_priority_order():
    """测试优先级顺序"""
    from app.services.RPA_browser.live_service import LiveService
    
    current_time = int(time.time())
    
    # 测试1: 过期优先级最高
    entry1 = BrowserSessionEntry(
        mid=123, browser_id=456, plugined_session=Mock(),
        last_activity=current_time - 400,
        last_heartbeat=current_time - 400,
        status=BrowserStatusEnum.IDLE,
        is_manual_mode=False,
        expires_at=current_time - 10,  # 已过期
        active_connections=set(),
        heartbeat_clients={},
        cleanup_policy=BrowserCleanupPolicy(
            max_idle_time=1800, max_no_heartbeat_time=300, cleanup_interval=300
        )
    )
    decision1 = LiveService._evaluate_session_cleanup(entry1, current_time)
    assert decision1.priority == 1
    
    # 测试2: 心跳超时优先级次之
    entry2 = BrowserSessionEntry(
        mid=123, browser_id=456, plugined_session=Mock(),
        last_activity=current_time - 400,
        last_heartbeat=current_time - 400,
        status=BrowserStatusEnum.IDLE,
        is_manual_mode=False,
        expires_at=None,  # 未过期
        active_connections=set(),
        heartbeat_clients={},
        cleanup_policy=BrowserCleanupPolicy(
            max_idle_time=1800, max_no_heartbeat_time=300, cleanup_interval=300
        )
    )
    decision2 = LiveService._evaluate_session_cleanup(entry2, current_time)
    assert decision2.priority == 2
    
    # 测试3: 闲置超时优先级最低
    entry3 = BrowserSessionEntry(
        mid=123, browser_id=456, plugined_session=Mock(),
        last_activity=current_time - 2000,
        last_heartbeat=current_time - 100,  # 心跳正常
        status=BrowserStatusEnum.IDLE,
        is_manual_mode=False,
        expires_at=None,
        active_connections=set(),
        heartbeat_clients={"client1": current_time - 100},
        cleanup_policy=BrowserCleanupPolicy(
            max_idle_time=1800, max_no_heartbeat_time=300, cleanup_interval=300
        )
    )
    decision3 = LiveService._evaluate_session_cleanup(entry3, current_time)
    assert decision3.priority == 3
    
    print("✅ 测试通过: 优先级顺序正确 (1 > 2 > 3)")


if __name__ == "__main__":
    print("开始运行 LiveService 清理状态机测试...\n")
    
    try:
        test_evaluate_session_cleanup_expired()
        test_evaluate_session_cleanup_heartbeat_timeout()
        test_evaluate_session_cleanup_idle_timeout()
        test_evaluate_session_cleanup_with_active_clients()
        test_evaluate_session_state_transition_active_to_idle()
        test_evaluate_session_state_transition_idle_to_active()
        test_evaluate_manual_mode_protection()
        test_priority_order()
        
        print("\n" + "="*50)
        print("🎉 所有测试通过！")
        print("="*50)
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        raise
    except Exception as e:
        print(f"\n💥 测试异常: {e}")
        raise
