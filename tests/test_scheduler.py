"""测试后台调度器功能"""
import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.scheduler_manager import scheduler_manager, interval_job, cron_job


# 测试任务
test_count = 0


@interval_job(seconds=5, job_id="test_interval_task")
async def test_interval_task():
    """测试间隔任务"""
    global test_count
    test_count += 1
    print(f"[Interval Task] 执行次数: {test_count}")


@cron_job("*/10 * * * *", job_id="test_cron_task")
async def test_cron_task():
    """测试 Cron 任务 - 每 10 秒"""
    print(f"[Cron Task] 执行于: {asyncio.get_event_loop().time()}")


async def test_scheduler():
    """测试调度器功能"""
    print("=" * 50)
    print("开始测试后台调度器")
    print("=" * 50)

    # 启动调度器
    scheduler_manager.start()
    print("\n✅ 调度器已启动")

    # 查看已注册的任务
    print("\n📋 已注册的任务:")
    jobs = scheduler_manager.get_jobs()
    for job in jobs:
        print(f"  - ID: {job.id}")
        print(f"    Name: {job.name}")
        print(f"    Next Run: {job.next_run_time}")
        print()

    # 测试手动添加任务
    print("\n➕ 手动添加新任务...")

    async def manual_task():
        print("[Manual Task] 这是一个手动添加的任务")

    scheduler_manager.add_interval_job(
        func=manual_task,
        seconds=8,
        id="manual_task",
        name="手动任务"
    )
    print("✅ 手动任务已添加")

    # 暂停和恢复任务测试
    print("\n⏸️ 暂停任务: test_interval_task")
    scheduler_manager.pause_job("test_interval_task")

    await asyncio.sleep(3)

    print("\n▶️ 恢复任务: test_interval_task")
    scheduler_manager.resume_job("test_interval_task")

    # 运行一段时间观察
    print("\n" + "=" * 50)
    print("观察任务执行 (运行 30 秒)...")
    print("=" * 50)

    await asyncio.sleep(30)

    # 移除任务测试
    print("\n🗑️ 移除任务: manual_task")
    scheduler_manager.remove_job("manual_task")
    print("✅ 任务已移除")

    # 最终状态
    print("\n📊 最终任务状态:")
    jobs = scheduler_manager.get_jobs()
    for job in jobs:
        print(f"  - {job.name} (ID: {job.id})")

    # 关闭调度器
    print("\n🛑 关闭调度器...")
    scheduler_manager.shutdown(wait=True)
    print("✅ 调度器已关闭")

    print("\n" + "=" * 50)
    print("测试完成!")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(test_scheduler())
