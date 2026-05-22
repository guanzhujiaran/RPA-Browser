"""Alembic environment configuration - delegates to app/utils/alembic_migration.py"""
from logging.config import fileConfig

from alembic.config import Config
from alembic import context

# 从主迁移工具导入所有必要的组件
from app.utils.alembic_migration import (
    get_alembic_config_for_env,
    target_metadata,
    run_migrations_offline,
    run_migrations_online,
)

# 获取配置
config = get_alembic_config_for_env()

# 设置日志
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 执行迁移
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
