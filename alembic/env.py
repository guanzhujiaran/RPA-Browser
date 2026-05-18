"""Alembic 环境配置 - 支持 SQLModel 和项目配置"""
import sys
from pathlib import Path
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# 添加项目根目录到 Python 路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

# 导入项目配置和所有模型
from app.config import settings

# 延迟导入模型，避免触发初始化代码
def import_models():
    """导入所有需要迁移管理的模型"""
    # workflow 相关模型
    from app.models.database.workflow.models import (
        CustomActionModel,
        UserPluginModel,
        UserWorkflowModel,
        WorkflowPluginLink,
        ActionPluginLink,
        WorkflowExecutionLogModel,
    )
    
    # browser 相关模型
    from app.models.database.browser.info import (
        UserBrowserInfo,
        UserBrowserDefaultSetting,
    )
    
    # notification 相关模型
    from app.models.database.notify.models import (
        NotificationConfig,
    )
    
    # 返回第一个模型的 metadata（所有模型共享同一个 metadata）
    return CustomActionModel.metadata

# Alembic Config object
config = context.config

# 设置日志
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 收集所有模型的 metadata（延迟导入）
target_metadata = import_models()


def get_url():
    """
    从项目配置获取数据库 URL
    
    Alembic 不支持异步驱动，因此需要将异步 URL 转换为同步 URL：
    - mysql+aiomysql:// -> mysql+pymysql://
    - postgresql+asyncpg:// -> postgresql://
    - sqlite+aiosqlite:// -> sqlite://
    """
    url = settings.mysql_browser_info_url
    
    # MySQL: aiomysql -> pymysql
    if url.startswith("mysql+aiomysql://"):
        url = url.replace("mysql+aiomysql://", "mysql+pymysql://")
    # PostgreSQL: asyncpg -> psycopg2
    elif url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    # SQLite: aiosqlite -> default
    elif url.startswith("sqlite+aiosqlite://"):
        url = url.replace("sqlite+aiosqlite://", "sqlite://")
    
    return url


def run_migrations_offline() -> None:
    """离线模式运行迁移"""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式运行迁移"""
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = get_url()
    
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # 启用比较服务器默认值
            compare_server_default=True,
            # 启用比较类型
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
