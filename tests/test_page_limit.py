"""
页面数量限制测试脚本

测试浏览器上下文页面数量限制功能
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.services.RPA_browser.browser_session_pool.playwright_pool import get_default_session_pool
from app.models.runtime.session import SessionCreateParams
from app.config import settings


async def test_page_limit():
    """测试页面数量限制功能"""
    print("=" * 80)
    print("开始测试页面数量限制功能")
    print("=" * 80)
    print(f"\n配置信息:")
    print(f"  - 最大页面数限制: {settings.browser_max_pages_per_context}")
    print("=" * 80)
    
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
        print(f"  - 当前最大页面数限制: {session.max_pages}")
        
        # 2. 获取初始页面
        print("\n[步骤2] 获取初始页面...")
        current_page = await session.get_current_page()
        print(f"✓ 初始页面URL: {current_page.url}")
        
        # 3. 创建多个页面，测试限制功能
        print(f"\n[步骤3] 创建 {session.max_pages + 2} 个页面（超过限制）...")
        created_pages = []
        
        for i in range(session.max_pages + 2):
            try:
                page = await session.browser_context.new_page()
                await page.goto(f"https://www.example.com/{i}")
                created_pages.append(page)
                
                all_pages = await session.get_all_pages()
                print(f"  ✓ 创建页面 {i+1}: {page.url}")
                print(f"    当前页面总数: {len(all_pages)}/{session.max_pages}")
                
                # 稍微等待一下，让页面加载
                await asyncio.sleep(0.5)
                
            except Exception as e:
                print(f"  ✗ 创建页面 {i+1} 失败: {e}")
        
        # 4. 检查最终页面数量
        print("\n[步骤4] 检查最终页面数量...")
        final_pages = await session.get_all_pages()
        print(f"✓ 最终页面总数: {len(final_pages)}")
        print(f"  - 预期最大值: {session.max_pages}")
        print(f"  - 是否符合限制: {'✓ 是' if len(final_pages) <= session.max_pages else '✗ 否'}")
        
        # 5. 显示所有页面信息
        print("\n[步骤5] 显示所有存活页面信息:")
        for i, page in enumerate(final_pages):
            try:
                title = await page.title()
                print(f"  - 页面 {i}: {page.url[:60]}... (标题: {title[:30]})")
            except Exception as e:
                print(f"  - 页面 {i}: 获取信息失败 - {e}")
        
        # 6. 再创建一个页面，验证自动清理
        print(f"\n[步骤6] 再创建一个新页面（触发自动清理）...")
        new_page = await session.browser_context.new_page()
        await new_page.goto("https://www.final-test.com")
        
        final_pages_after = await session.get_all_pages()
        print(f"✓ 创建后页面总数: {len(final_pages_after)}")
        print(f"  - 是否符合限制: {'✓ 是' if len(final_pages_after) <= session.max_pages else '✗ 否'}")
        print(f"  - 新页面URL: {new_page.url}")
        
        print("\n" + "=" * 80)
        print("测试完成！")
        print("=" * 80)
        
        # 验证结果
        if len(final_pages_after) <= session.max_pages:
            print("\n✅ 页面数量限制功能工作正常！")
        else:
            print(f"\n❌ 页面数量限制功能异常！当前页面数 {len(final_pages_after)} 超过限制 {session.max_pages}")
        
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
    asyncio.run(test_page_limit())
