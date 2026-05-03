"""
WebRTC 视频流服务测试

测试新的 OOP WebRTC 架构的基本功能。
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, MagicMock

from app.models.runtime.webrtc_models import WebRTCStreamState, WebRTCSessionConfig


class TestWebRTCModels:
    """测试 WebRTC 模型类"""
    
    def test_webrtc_stream_state_enum(self):
        """测试 WebRTCStreamState 枚举"""
        assert WebRTCStreamState.INITIALIZING.value == "initializing"
        assert WebRTCStreamState.ACTIVE.value == "active"
        assert WebRTCStreamState.CLOSED.value == "closed"
        assert WebRTCStreamState.ERROR.value == "error"
        
    def test_webrtc_session_config_defaults(self):
        """测试 WebRTCSessionConfig 默认值"""
        config = WebRTCSessionConfig()
        assert config.quality == 80
        assert config.max_fps == 30
        assert config.idle_timeout == 300
        assert config.frame_queue_size == 10
        
    def test_webrtc_session_config_custom(self):
        """测试 WebRTCSessionConfig 自定义值"""
        config = WebRTCSessionConfig(
            quality=90,
            max_fps=60,
            idle_timeout=600,
            frame_queue_size=20
        )
        assert config.quality == 90
        assert config.max_fps == 60
        assert config.idle_timeout == 600
        assert config.frame_queue_size == 20
        
    def test_webrtc_session_config_validation(self):
        """测试 WebRTCSessionConfig 参数验证"""
        # 测试无效的质量值
        with pytest.raises(ValueError):
            WebRTCSessionConfig(quality=-1)
            
        with pytest.raises(ValueError):
            WebRTCSessionConfig(quality=101)
            
        # 测试无效的 FPS
        with pytest.raises(ValueError):
            WebRTCSessionConfig(max_fps=0)
            
        # 测试无效的超时时间
        with pytest.raises(ValueError):
            WebRTCSessionConfig(idle_timeout=-1)
            
        # 测试无效的队列大小
        with pytest.raises(ValueError):
            WebRTCSessionConfig(frame_queue_size=0)


class TestVideoFrameProducer:
    """测试 VideoFrameProducer（需要 Playwright 环境）"""
    
    @pytest.mark.asyncio
    async def test_producer_initialization(self):
        """测试生产者初始化"""
        from app.services.RPA_browser.webrtc.video_frame_producer import VideoFrameProducer
        
        # 创建模拟页面对象
        mock_page = Mock()
        mock_page.screencast = Mock()
        mock_page.screencast.start = AsyncMock()
        
        config = WebRTCSessionConfig(quality=80)
        producer = VideoFrameProducer(mock_page, config)
        
        assert producer.page == mock_page
        assert producer.config == config
        assert producer.frame_queue.maxsize == 10
        assert not producer.is_running
        
    @pytest.mark.asyncio
    async def test_producer_start_stop(self):
        """测试生产者启动和停止"""
        from app.services.RPA_browser.webrtc.video_frame_producer import VideoFrameProducer
        
        # 创建模拟页面对象
        mock_page = Mock()
        mock_screencast_session = Mock()
        mock_screencast_session.stop = AsyncMock()
        
        mock_page.screencast = Mock()
        mock_page.screencast.start = AsyncMock(return_value=mock_screencast_session)
        
        config = WebRTCSessionConfig(quality=80)
        producer = VideoFrameProducer(mock_page, config)
        
        # 启动
        await producer.start()
        assert producer.is_running
        mock_page.screencast.start.assert_called_once()
        
        # 停止
        await producer.stop()
        assert not producer.is_running
        mock_screencast_session.stop.assert_called_once()


class TestWebRTCMediaTrack:
    """测试 WebRTCMediaTrack"""
    
    def test_track_kind(self):
        """测试轨道类型"""
        from app.services.RPA_browser.webrtc.media_track import WebRTCMediaTrack
        
        # 检查 kind 属性
        assert WebRTCMediaTrack.kind == "video"


class TestWebRTCStreamSession:
    """测试 WebRTCStreamSession"""
    
    @pytest.mark.asyncio
    async def test_stream_session_creation(self):
        """测试流会话创建"""
        from app.services.RPA_browser.webrtc.stream_session import WebRTCStreamSession
        
        mock_page = Mock()
        config = WebRTCSessionConfig()
        stream_key = "test:123:0"
        
        session = WebRTCStreamSession(stream_key, mock_page, config)
        
        assert session.stream_key == stream_key
        assert session.page == mock_page
        assert session.config == config
        assert session.state == WebRTCStreamState.INITIALIZING


class TestWebRTCStreamManager:
    """测试 WebRTCStreamManager"""
    
    def test_manager_initialization(self):
        """测试管理器初始化"""
        from app.services.RPA_browser.webrtc.stream_manager import WebRTCStreamManager
        
        mock_session = Mock()
        manager = WebRTCStreamManager(mock_session)
        
        assert manager.session == mock_session
        assert len(manager.streams) == 0
        assert manager.active_stream_count == 0
        assert manager.total_stream_count == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
