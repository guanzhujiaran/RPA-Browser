"""
测试 WebRTC 基于 page_index 的动态重建策略

验证移除缓存机制后，每次启动都会创建全新的流实例，
并且能够正确处理 "Screencast is already started" 错误。
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import Mock, AsyncMock, MagicMock

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger


async def test_video_frame_producer_error_handling():
    """测试 VideoFrameProducer 的错误处理机制"""
    
    logger.info("=" * 80)
    logger.info("测试 1: VideoFrameProducer Screencast 错误恢复机制")
    logger.info("=" * 80)
    
    from app.services.RPA_browser.webrtc.video_frame_producer import VideoFrameProducer
    from app.models.runtime.webrtc_models import WebRTCSessionConfig
    
    # 创建 mock page
    mock_page = Mock()
    mock_screencast = Mock()
    mock_page.screencast = mock_screencast
    
    # 模拟第一次调用 start 抛出 "already started" 错误
    call_count = [0]
    
    async def mock_start_side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            # 第一次调用抛出错误
            raise Exception("Screencast is already started")
        else:
            # 第二次调用成功
            mock_session = Mock()
            mock_session.stop = AsyncMock()
            return mock_session
    
    mock_screencast.start = AsyncMock(side_effect=mock_start_side_effect)
    mock_screencast.stop = AsyncMock()
    
    config = WebRTCSessionConfig(quality=80)
    producer = VideoFrameProducer(mock_page, config)
    
    # 测试启动（应该触发错误恢复流程）
    try:
        await producer.start()
        logger.info("✓ VideoFrameProducer 启动成功（触发了错误恢复）")
        
        # 验证 stop 被调用了一次（用于清理异常会话）
        assert mock_screencast.stop.called, "stop() 应该被调用以清理异常会话"
        logger.info(f"✓ screencast.stop() 被调用了 {mock_screencast.stop.call_count} 次")
        
        # 验证 start 被调用了两次（第一次失败，第二次成功）
        assert mock_screencast.start.call_count == 2, f"start() 应该被调用 2 次，实际 {mock_screencast.start.call_count}"
        logger.info(f"✓ screencast.start() 被调用了 {mock_screencast.start.call_count} 次")
        
        # 验证生产者状态
        assert producer.is_running, "生产者应该处于运行状态"
        assert producer.screencast_session is not None, "应该有有效的 session"
        logger.info("✓ Producer 状态正确")
        
    except Exception as e:
        logger.error(f"✗ 测试失败: {e}")
        raise
    
    # 测试停止
    await producer.stop()
    assert not producer.is_running, "生产者应该已停止"
    logger.info("✓ VideoFrameProducer 停止成功")
    
    logger.info("\n" + "=" * 80)
    logger.info("测试 1 完成！")
    logger.info("=" * 80)


async def test_stream_manager_page_index_based():
    """测试 StreamManager 基于 page_index 的管理"""
    
    logger.info("\n" + "=" * 80)
    logger.info("测试 2: WebRTCStreamManager 基于 page_index 的管理")
    logger.info("=" * 80)
    
    from app.services.RPA_browser.webrtc.stream_manager import WebRTCStreamManager
    from app.services.RPA_browser.webrtc.stream_session import WebRTCStreamSession
    
    # 创建 mock session
    mock_session = Mock()
    mock_playwright_instance = Mock()
    mock_playwright_instance.mid = 1
    mock_playwright_instance.browser_id = 174207413090521088
    mock_session.playwright_instance = mock_playwright_instance
    
    # 创建 mock pages
    mock_page_0 = Mock()
    mock_page_0._webrtc_page_id = "page-uuid-0"
    mock_screencast_0 = Mock()
    mock_session_obj = Mock()
    mock_session_obj.stop = AsyncMock()
    mock_screencast_0.start = AsyncMock(return_value=mock_session_obj)
    mock_page_0.screencast = mock_screencast_0
    
    mock_page_1 = Mock()
    mock_page_1._webrtc_page_id = "page-uuid-1"
    mock_screencast_1 = Mock()
    mock_session_obj_1 = Mock()
    mock_session_obj_1.stop = AsyncMock()
    mock_screencast_1.start = AsyncMock(return_value=mock_session_obj_1)
    mock_page_1.screencast = mock_screencast_1
    
    async def mock_get_all_pages():
        return [mock_page_0, mock_page_1]
    
    mock_session.get_all_pages = mock_get_all_pages
    
    # 创建 manager
    manager = WebRTCStreamManager(mock_session)
    
    # 测试 1: 启动第一个流
    logger.info("\n[步骤 1] 启动 page_index=0 的流")
    stream_0 = await manager.start_stream(0)
    assert 0 in manager.streams, "stream 应该被添加到字典中"
    assert stream_0.page_index == 0, "page_index 应该为 0"
    logger.info(f"✓ 流已创建: {stream_0.stream_key}, page_index={stream_0.page_index}")
    
    # 测试 2: 启动第二个流
    logger.info("\n[步骤 2] 启动 page_index=1 的流")
    stream_1 = await manager.start_stream(1)
    assert 1 in manager.streams, "stream 应该被添加到字典中"
    assert stream_1.page_index == 1, "page_index 应该为 1"
    logger.info(f"✓ 流已创建: {stream_1.stream_key}, page_index={stream_1.page_index}")
    
    # 测试 3: 重新启动同一个 page_index（应该关闭旧流并创建新流）
    logger.info("\n[步骤 3] 重新启动 page_index=0 的流（应该关闭旧流）")
    old_stream_0 = manager.streams[0]
    stream_0_new = await manager.start_stream(0)
    assert stream_0_new != old_stream_0, "应该创建新的流实例"
    assert stream_0_new.page_index == 0, "page_index 应该保持为 0"
    logger.info(f"✓ 旧流已关闭，新流已创建: {stream_0_new.stream_key}")
    
    # 测试 4: 验证流的唯一性
    logger.info("\n[步骤 4] 验证每个 page_index 只有一个活跃流")
    assert len(manager.streams) == 2, f"应该有 2 个流，实际 {len(manager.streams)}"
    assert 0 in manager.streams and 1 in manager.streams, "应该包含 page_index 0 和 1"
    logger.info(f"✓ 当前活跃流数量: {len(manager.streams)}")
    
    # 测试 5: 关闭流
    logger.info("\n[步骤 5] 关闭 page_index=0 的流")
    await manager.close_stream(0)
    assert 0 not in manager.streams, "流应该被移除"
    assert 1 in manager.streams, "另一个流应该仍然存在"
    logger.info("✓ 流已关闭并从字典中移除")
    
    # 测试 6: 关闭所有流
    logger.info("\n[步骤 6] 关闭所有流")
    await manager.close_all_streams()
    assert len(manager.streams) == 0, "所有流应该被关闭"
    logger.info("✓ 所有流已关闭")
    
    logger.info("\n" + "=" * 80)
    logger.info("测试 2 完成！")
    logger.info("=" * 80)


async def test_stream_session_with_page_index():
    """测试 StreamSession 使用 page_index"""
    
    logger.info("\n" + "=" * 80)
    logger.info("测试 3: WebRTCStreamSession 使用 page_index")
    logger.info("=" * 80)
    
    from app.services.RPA_browser.webrtc.stream_session import WebRTCStreamSession
    from app.models.runtime.webrtc_models import WebRTCSessionConfig
    
    # 创建 mock page
    mock_page = Mock()
    mock_page._webrtc_page_id = "test-page-uuid"
    mock_screencast = Mock()
    mock_session_obj = Mock()
    mock_session_obj.stop = AsyncMock()
    mock_screencast.start = AsyncMock(return_value=mock_session_obj)
    mock_page.screencast = mock_screencast
    
    config = WebRTCSessionConfig(quality=80, idle_timeout=300)
    
    # 测试 1: 创建不同 page_index 的 session
    logger.info("\n[步骤 1] 创建 page_index=0 的 session")
    session_0 = WebRTCStreamSession(
        stream_key="1:174207413090521088:page_0",
        page=mock_page,
        config=config,
        page_index=0
    )
    assert session_0.page_index == 0, "page_index 应该为 0"
    logger.info(f"✓ Session 创建成功: {session_0.stream_key}, page_index={session_0.page_index}")
    
    logger.info("\n[步骤 2] 创建 page_index=5 的 session")
    session_5 = WebRTCStreamSession(
        stream_key="1:174207413090521088:page_5",
        page=mock_page,
        config=config,
        page_index=5
    )
    assert session_5.page_index == 5, "page_index 应该为 5"
    logger.info(f"✓ Session 创建成功: {session_5.stream_key}, page_index={session_5.page_index}")
    
    # 测试 2: 验证 stream_info 包含正确的 page_index
    logger.info("\n[步骤 3] 验证 stream_info 包含正确的 page_index")
    info_0 = session_0.stream_info
    assert info_0.page_index == 0, "stream_info 应该包含正确的 page_index"
    logger.info(f"✓ stream_info.page_index = {info_0.page_index}")
    
    info_5 = session_5.stream_info
    assert info_5.page_index == 5, "stream_info 应该包含正确的 page_index"
    logger.info(f"✓ stream_info.page_index = {info_5.page_index}")
    
    logger.info("\n" + "=" * 80)
    logger.info("测试 3 完成！")
    logger.info("=" * 80)


async def main():
    """运行所有测试"""
    try:
        await test_video_frame_producer_error_handling()
        await test_stream_manager_page_index_based()
        await test_stream_session_with_page_index()
        
        logger.info("\n" + "=" * 80)
        logger.info("🎉 所有测试通过！")
        logger.info("=" * 80)
        logger.info("\n重构总结:")
        logger.info("1. ✓ 移除了 _page_screencast_cache 缓存机制")
        logger.info("2. ✓ 改用 page_index 作为流的唯一标识")
        logger.info("3. ✓ 实现了 Screencast 启动容错机制")
        logger.info("4. ✓ 简化了流生命周期管理")
        logger.info("=" * 80)
        
    except Exception as e:
        logger.error(f"\n✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
