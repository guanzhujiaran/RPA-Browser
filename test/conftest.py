"""
测试配置文件 - 使用 pytest + unittest + playwright-asyncio
浏览器由 pytest-playwright-asyncio 提供 page/browser fixtures
参考: https://playwright.dev/python/docs/test-runners#using-with-unittesttestcase
"""
import pytest
import os
import sys
from pathlib import Path

# 添加项目根目录到 sys.path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

os.environ.setdefault("mysql_browser_info_url", "sqlite+aiosqlite:///./test.db")
os.environ.setdefault("RUNNING_MODE", "dev")


@pytest.fixture(autouse=True)
def mock_database():
    """Mock 数据库连接"""
    from unittest.mock import AsyncMock, MagicMock, patch
    
    mock_session = AsyncMock()
    mock_manager = MagicMock()
    mock_manager.async_session.return_value.__aenter__.return_value = mock_session
    
    mock_session_module = MagicMock()
    mock_session_module.DatabaseSessionManager = mock_manager
    
    with patch.dict(sys.modules, {
        'app.utils.depends.session_manager': mock_session_module,
    }):
        yield mock_manager
