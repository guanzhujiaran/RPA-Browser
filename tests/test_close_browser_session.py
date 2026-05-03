"""
测试关闭浏览器会话功能
"""
import asyncio
import sys
import os

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.services.RPA_browser.live_service import LiveService
from app.models.runtime.control import CreateSessionRequest


async def test_close_session():
    """测试关闭会话功能"""
    
    # 模拟用户ID和浏览器ID
    mid = 123
    browser_id = 456
    
    print("=== 开始测试关闭浏览器会话功能 ===")
    
    # 1. 手动创建一个会话条目（模拟真实场景）
    print("\n1. 手动创建会话条目...")
    session_key = f"{mid}_{browser_id}"
    
    # 导入必要的类
    from app.models.runtime.live_service import BrowserSessionEntry
    from unittest.mock import Mock
    
    # 创建一个模拟的 plugined_session
    mock_session = Mock()
    mock_session.close = asyncio.coroutine(lambda: None)
    
    # 创建会话条目
    entry = BrowserSessionEntry(
        mid=mid,
        browser_id=browser_id,
        plugined_session=mock_session,
        last_activity=int(asyncio.get_event_loop().time()),
        last_heartbeat=int(asyncio.get_event_loop().time()),
    )
    
    # 添加到会话字典中
    LiveService.browser_sessions[session_key] = entry
    print(f"   会话键: {session_key}")
    print(f"   会话已创建")
    
    # 2. 检查会话是否存在
    print(f"\n2. 检查会话是否存在...")
    print(f"   会话存在: {session_key in LiveService.browser_sessions}")
    
    if session_key in LiveService.browser_sessions:
        entry = LiveService.browser_sessions[session_key]
        print(f"   会话状态: {entry.status}")
        print(f"   生命周期状态: {entry.lifecycle_state}")
        print(f"   最后活动时间: {entry.last_activity}")
        print(f"   最后心跳时间: {entry.last_heartbeat}")
    
    # 3. 获取会话状态
    print(f"\n3. 获取会话状态...")
    status_data = LiveService.get_browser_session_status(mid, browser_id)
    print(f"   会话存在: {status_data.session_exists}")
    print(f"   浏览器运行: {status_data.browser_running}")
    print(f"   生命周期状态: {status_data.lifecycle_state}")
    print(f"   活跃连接数: {status_data.active_connections}")
    print(f"   消息: {status_data.message}")
    
    # 4. 关闭会话
    print(f"\n4. 关闭浏览器会话...")
    success = await LiveService.release_browser_session(mid, browser_id)
    print(f"   关闭结果: {success}")
    
    # 5. 再次检查会话是否存在
    print(f"\n5. 再次检查会话是否存在...")
    print(f"   会话键: {session_key}")
    print(f"   会话存在: {session_key in LiveService.browser_sessions}")
    
    # 6. 再次获取会话状态
    print(f"\n6. 再次获取会话状态...")
    status_data_after = LiveService.get_browser_session_status(mid, browser_id)
    print(f"   会话存在: {status_data_after.session_exists}")
    print(f"   浏览器运行: {status_data_after.browser_running}")
    print(f"   生命周期状态: {status_data_after.lifecycle_state}")
    print(f"   活跃连接数: {status_data_after.active_connections}")
    print(f"   消息: {status_data_after.message}")
    
    # 7. 验证结果
    print(f"\n7. 验证结果...")
    if not status_data_after.session_exists and not status_data_after.browser_running:
        print("   ✅ 测试通过: 会话已成功关闭并清理")
        return True
    else:
        print("   ❌ 测试失败: 会话未被正确清理")
        print(f"      - session_exists: {status_data_after.session_exists} (期望: False)")
        print(f"      - browser_running: {status_data_after.browser_running} (期望: False)")
        return False


if __name__ == "__main__":
    try:
        result = asyncio.run(test_close_session())
        if result:
            print("\n🎉 所有测试通过!")
            sys.exit(0)
        else:
            print("\n💥 测试失败!")
            sys.exit(1)
    except Exception as e:
        print(f"\n❌ 测试过程中发生错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)