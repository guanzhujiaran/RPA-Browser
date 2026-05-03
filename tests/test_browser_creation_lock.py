"""
测试浏览器创建锁机制

这个测试验证针对 uid+browser_id 的锁是否能防止并发创建同一个浏览器实例
"""
import asyncio
import sys
import time
import uuid

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.services.RPA_browser.browser_session_pool.session_pool_model import (
    PluginedSessionInfo,
    _get_browser_creation_lock,
    _browser_creation_locks,
)


async def test_concurrent_browser_creation():
    """测试并发创建同一个浏览器实例"""
    mid = 123456789
    browser_id = 987654321
    
    print(f"测试并发创建浏览器: mid={mid}, browser_id={browser_id}")
    
    # 记录开始时间
    start_time = time.time()
    
    # 模拟多个并发请求尝试创建同一个浏览器
    async def create_browser_task(task_id: int):
        print(f"[Task {task_id}] 尝试获取锁...")
        lock = await _get_browser_creation_lock(mid, browser_id)
        print(f"[Task {task_id}] 获取到锁，开始创建浏览器...")
        
        # 模拟浏览器创建的耗时操作
        await asyncio.sleep(2)
        
        print(f"[Task {task_id}] 浏览器创建完成")
        return task_id
    
    # 创建5个并发任务
    tasks = [create_browser_task(i) for i in range(5)]
    
    # 并发执行所有任务
    results = await asyncio.gather(*tasks)
    
    elapsed = time.time() - start_time
    print(f"\n所有任务完成，总耗时: {elapsed:.2f}秒")
    print(f"如果没有锁保护，5个任务并发执行应该约2秒")
    print(f"如果有锁保护，5个任务串行执行应该约10秒")
    print(f"实际耗时: {elapsed:.2f}秒")
    
    if elapsed > 8:
        print("✅ 锁机制工作正常：任务被串行执行")
    else:
        print("❌ 锁机制可能有问题：任务似乎并发执行了")
    
    # 清理锁
    from app.services.RPA_browser.browser_session_pool.session_pool_model import _cleanup_browser_creation_lock
    await _cleanup_browser_creation_lock(mid, browser_id)
    
    return results


async def test_different_browsers_no_blocking():
    """测试不同的浏览器实例不会互相阻塞"""
    print("\n" + "="*60)
    print("测试不同浏览器实例的并发性")
    print("="*60)
    
    start_time = time.time()
    
    async def create_browser_task(mid: int, browser_id: int, task_id: int):
        print(f"[Task {task_id}] 尝试获取锁 (mid={mid}, browser_id={browser_id})...")
        lock = await _get_browser_creation_lock(mid, browser_id)
        print(f"[Task {task_id}] 获取到锁，开始创建浏览器...")
        
        # 模拟浏览器创建的耗时操作
        await asyncio.sleep(1)
        
        print(f"[Task {task_id}] 浏览器创建完成")
        
        # 清理锁
        from app.services.RPA_browser.browser_session_pool.session_pool_model import _cleanup_browser_creation_lock
        await _cleanup_browser_creation_lock(mid, browser_id)
        
        return task_id
    
    # 创建3个不同浏览器的任务（应该可以并发执行）
    tasks = [
        create_browser_task(111, 111, 1),
        create_browser_task(222, 222, 2),
        create_browser_task(333, 333, 3),
    ]
    
    results = await asyncio.gather(*tasks)
    
    elapsed = time.time() - start_time
    print(f"\n所有任务完成，总耗时: {elapsed:.2f}秒")
    print(f"如果不同浏览器可以并发，应该约1秒")
    print(f"如果被错误地串行化，应该约3秒")
    print(f"实际耗时: {elapsed:.2f}秒")
    
    if elapsed < 2:
        print("✅ 不同浏览器实例可以并发创建")
    else:
        print("❌ 不同浏览器实例被错误地串行化了")
    
    return results


async def main():
    print("="*60)
    print("浏览器创建锁机制测试")
    print("="*60)
    
    # 测试1：同一浏览器的并发创建
    await test_concurrent_browser_creation()
    
    # 测试2：不同浏览器的并发创建
    await test_different_browsers_no_blocking()
    
    print("\n" + "="*60)
    print("测试完成")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())
