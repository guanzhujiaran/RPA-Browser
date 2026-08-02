# -*- coding: utf-8 -*-
"""
RPA-Browser Alembic 异步环境配置
参照 FastapiApp 的 alembic/env.py 写法
"""
import asyncio
import sys
from pathlib import Path

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

# 确保项目根目录在 path 中，以便导入 app 模块
_current_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_current_dir.parent))

from app.config import settings
from sqlmodel import SQLModel

# 导入所有数据库模型，确保它们注册到 SQLModel.metadata
from app.models.database.workflow.models import *  # noqa: F401, F403
from app.models.database.browser.info import *  # noqa: F401, F403
from app.models.database.notify.models import *  # noqa: F401, F403
from app.models.database.log.models import *  # noqa: F401, F403

target_metadata = SQLModel.metadata

_DB_URL: str = settings.mysql_browser_info_url

config = context.config
config.set_main_option("sqlalchemy.url", _DB_URL)


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():
    connectable = create_async_engine(_DB_URL)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_offline() -> None:
    url = _DB_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()