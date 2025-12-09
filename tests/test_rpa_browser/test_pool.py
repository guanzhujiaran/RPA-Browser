import asyncio
import uuid
from app.services.RPA_browser.browser_session_pool.playwright_pool import PlaywrightSessionPool, get_default_session_pool


async def test_session_pool():
    """测试会话池基本功能"""
    pool = PlaywrightSessionPool(max_sessions=3)
    
    # 测试浏览器token列表
    browser_tokens = [
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4()
    ]
    
    try:
        # 测试创建会话
        print("测试创建会话...")
        session1 = await pool.get_session(browser_tokens[0])
        print(f"成功创建会话1: {browser_tokens[0]}")
        
        session2 = await pool.get_session(browser_tokens[1])
        print(f"成功创建会话2: {browser_tokens[1]}")
        
        # 测试获取已存在的会话
        print("测试获取已存在的会话...")
        same_session1 = await pool.get_session(browser_tokens[0])
        print(f"成功获取已存在的会话1: {browser_tokens[0]}")
        
        # 验证是同一个会话
        assert session1[1] == same_session1[1], "应该返回相同的会话"
        print("验证通过：返回了相同的会话")
        
        # 测试获取页面
        print("测试获取页面...")
        page = await pool.get_page(browser_tokens[0])
        print(f"成功获取页面，URL: {page.url}")
        await page.close()
        
        # 测试超过最大会话数
        print("测试超过最大会话数...")
        session3 = await pool.get_session(browser_tokens[2])
        print(f"成功创建会话3: {browser_tokens[2]}")
        
        print("测试完成，清理资源...")
        
    except Exception as e:
        print(f"测试过程中出现错误: {e}")
    finally:
        # 清理所有会话
        await pool.cleanup_all_sessions()
        print("所有会话已清理")


async def test_default_pool():
    """测试默认会话池单例"""
    print("测试默认会话池单例...")
    pool1 = get_default_session_pool()
    pool2 = get_default_session_pool()
    
    assert pool1 is pool2, "应该返回相同的实例"
    print("单例测试通过")


if __name__ == "__main__":
    print("开始测试Playwright会话池...")
    asyncio.run(test_session_pool())
    asyncio.run(test_default_pool())
    print("所有测试完成")