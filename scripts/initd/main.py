from scripts.initd.install_ungoogled_chromium import install_chromium
import asyncio


async def init_dependencies():
    """初始化依赖项（仅安装 Chromium）
    
    注意：数据库表创建现在完全由 Alembic 管理
    请使用 alembic_manage.sh 脚本进行数据库迁移
    """
    await install_chromium()


if __name__ == "__main__":
    asyncio.run(init_dependencies())
