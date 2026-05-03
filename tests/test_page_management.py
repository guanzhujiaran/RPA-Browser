"""
页面管理功能测试脚本

测试页面列表获取、页面切换、页面关闭等功能
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.services.RPA_browser.browser_session_pool.playwright_pool import get_default_session_pool
from app.models.runtime.session import SessionCreateParams


async def test_page_management():
    """测试页面管理功能"""
    print("=" * 60)
    print("开始测试页面管理功能")
    print("=" * 60)
    
    # 配置参数
    mid = 38
    browser_id = 130734684681277440
    
    pool = get_default_session_pool()
    
    try:
        # 1. 创建会话
        print("\n[步骤1] 创建浏览器会话...")
        session_params = SessionCreateParams(
            mid=mid,
            browser_id=browser_id,
            headless=False,
        )
        session = await pool.get_session(session_params)
        print(f"✓ 会话创建成功")
        
        # 2. 获取初始页面
        print("\n[步骤2] 获取当前页面...")
        current_page = await session.get_current_page()
        print(f"✓ 当前页面URL: {current_page.url}")
        
        # 3. 打开新页面
        print("\n[步骤3] 打开新页面...")
        page1 = await session.browser_context.new_page()
        await page1.goto("https://www.baidu.com")
        print(f"✓ 新页面URL: {page1.url}")
        
        page2 = await session.browser_context.new_page()
        await page2.goto("https://www.bilibili.com")
        print(f"✓ 第二个新页面URL: {page2.url}")
        
        # 4. 获取所有页面列表
        print("\n[步骤4] 获取所有页面列表...")
        all_pages = await session.get_all_pages()
        print(f"✓ 总共有 {len(all_pages)} 个页面:")
        for i, page in enumerate(all_pages):
            title = await page.title()
            print(f"  - 页面 {i}: {page.url} (标题: {title})")
        
        # 5. 切换到第一个新页面
        print("\n[步骤5] 切换到页面索引 1 (百度)...")
        switched_page = await session.switch_to_page(1)
        print(f"✓ 已切换到: {switched_page.url}")
        
        # 6. 再次获取当前页面，确认切换成功
        print("\n[步骤6] 验证当前页面...")
        current_page = await session.get_current_page()
        print(f"✓ 当前页面URL: {current_page.url}")
        
        # 7. 关闭第二个新页面
        print("\n[步骤7] 关闭页面索引 2 (B站)...")
        success = await session.close_page(2)
        print(f"✓ 关闭结果: {'成功' if success else '失败'}")
        
        # 8. 再次获取所有页面列表
        print("\n[步骤8] 获取关闭后的页面列表...")
        all_pages = await session.get_all_pages()
        print(f"✓ 剩余 {len(all_pages)} 个页面:")
        for i, page in enumerate(all_pages):
            title = await page.title()
            print(f"  - 页面 {i}: {page.url} (标题: {title})")
        
        # 9. 测试关闭不存在的页面
        print("\n[步骤9] 测试关闭不存在的页面索引 10...")
        try:
            success = await session.close_page(10)
            print(f"✗ 应该抛出异常但没有")
        except IndexError as e:
            print(f"✓ 正确抛出异常: {e}")
        
        # 10. 测试切换到不存在的页面
        print("\n[步骤10] 测试切换到不存在的页面索引 10...")
        try:
            page = await session.switch_to_page(10)
            print(f"✗ 应该抛出异常但没有")
        except IndexError as e:
            print(f"✓ 正确抛出异常: {e}")
        
        print("\n" + "=" * 60)
        print("所有测试完成！")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n✗ 测试过程中出现错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 清理资源
        print("\n[清理] 关闭会话...")
        try:
            await session.close()
            print("✓ 会话已关闭")
        except Exception as e:
            print(f"✗ 关闭会话失败: {e}")


if __name__ == "__main__":
    asyncio.run(test_page_management())
