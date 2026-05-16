"""
数据库迁移脚本：添加用户级别名称唯一性约束

类似 GitHub 仓库命名模式：
- 同一用户下，plugin/action/workflow 的名称必须唯一
- 不同用户之间可以重名
"""
import asyncio
from sqlalchemy import text
from app.utils.depends.session_manager import DatabaseSessionManager


async def migrate():
    """执行迁移"""
    async with DatabaseSessionManager.async_session() as session:
        try:
            # 1. 为 custom_action 表添加复合唯一索引
            print("Creating index for custom_action...")
            try:
                await session.exec(text("""
                    CREATE UNIQUE INDEX idx_user_action_name_unique 
                    ON custom_action (mid, name)
                """))
                print("  ✓ Index created for custom_action")
            except Exception as e:
                if "Duplicate key name" in str(e):
                    print("  ⊘ Index already exists for custom_action")
                else:
                    raise
            
            # 2. 为 user_workflow 表添加复合唯一索引
            print("Creating index for user_workflow...")
            try:
                await session.exec(text("""
                    CREATE UNIQUE INDEX idx_user_workflow_name_unique 
                    ON user_workflow (mid, name)
                """))
                print("  ✓ Index created for user_workflow")
            except Exception as e:
                if "Duplicate key name" in str(e):
                    print("  ⊘ Index already exists for user_workflow")
                else:
                    raise
            
            # 3. 为 user_plugin 表添加复合唯一索引
            print("Creating index for user_plugin...")
            try:
                await session.exec(text("""
                    CREATE UNIQUE INDEX idx_user_plugin_name_unique 
                    ON user_plugin (mid, name)
                """))
                print("  ✓ Index created for user_plugin")
            except Exception as e:
                if "Duplicate key name" in str(e):
                    print("  ⊘ Index already exists for user_plugin")
                else:
                    raise
            
            await session.commit()
            print("\n✅ Migration completed successfully!")
            print("\n已添加以下唯一性约束：")
            print("  - custom_action: 同一用户下 action 名称必须唯一")
            print("  - user_workflow: 同一用户下 workflow 名称必须唯一")
            print("  - user_plugin: 同一用户下 plugin 名称必须唯一")
            
        except Exception as e:
            await session.rollback()
            print(f"\n❌ Migration failed: {e}")
            raise


if __name__ == "__main__":
    print("Starting database migration...")
    print("=" * 60)
    asyncio.run(migrate())
    print("=" * 60)
