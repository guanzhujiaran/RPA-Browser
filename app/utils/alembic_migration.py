"""Alembic 数据库迁移工具"""
from pathlib import Path
from alembic.config import Config
from alembic import command
from alembic.autogenerate import compare_metadata
from sqlalchemy.exc import OperationalError
from alembic.script import ScriptDirectory
from alembic.runtime.migration import MigrationContext
from app.models.database.workflow.models import (
    CustomActionModel,
    UserPluginModel,
    UserWorkflowModel,
    WorkflowPluginLink,
    ActionPluginLink,
    WorkflowExecutionLogModel,
)
from app.models.database.browser.info import (
    UserBrowserInfo,
    UserBrowserDefaultSetting,
)
from app.models.database.notify.models import NotificationConfig
import pymysql
from urllib.parse import urlparse, parse_qs, urlencode
# 检查当前版本
from alembic.script import ScriptDirectory
from alembic.runtime.migration import MigrationContext
from app.config import settings
from alembic.script import ScriptDirectory
from alembic.runtime.migration import MigrationContext
from app.config import settings
from loguru import logger

def _ensure_database_exists(db_url: str) -> None:
    """
    确保数据库存在,如果不存在则创建
    
    Args:
        db_url: 数据库连接 URL
    """

    
    # 解析 URL
    parsed = urlparse(db_url)
    
    # 提取数据库名称
    db_name = parsed.path.lstrip('/')
    if not db_name:
        logger.warning("⚠️  无法从 URL 中提取数据库名称")
        return
    
    # 提取连接参数
    query_params = parse_qs(parsed.query)
    
    # ⚠️ 关键:移除 auth_plugin 参数,避免使用 mysql_native_password
    if 'auth_plugin' in query_params:
        logger.info("🔧 检测到 URL 中包含 auth_plugin 参数,已移除以避免认证问题")
        del query_params['auth_plugin']
    
    # 从 URL 或查询参数中提取用户名和密码
    # 支持两种格式:
    # 1. mysql://user:pass@host/db (标准格式)
    # 2. mysql://host/db?user=xxx&password=yyy (查询参数格式)
    username = parsed.username or query_params.get('user', ['root'])[0]
    password = parsed.password or query_params.get('password', [''])[0]
    
    # 构建连接到 MySQL 服务器的参数(不包含数据库名)
    connection_params = {
        'host': parsed.hostname or '127.0.0.1',
        'port': parsed.port or 3306,
        'user': username,
        'password': password,
        # 不使用 auth_plugin,让 PyMySQL 自动协商
    }
    
    logger.info(f"🔍 检查数据库是否存在: {db_name} (用户: {username})")
    
    try:
        # 直接使甤 PyMySQL 连接(不通过 SQLAlchemy)
        connection = pymysql.connect(**connection_params)
        with connection.cursor() as cursor:
            # 检查数据库是否存在
            cursor.execute(
                "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = %s",
                (db_name,)
            )
            
            if cursor.fetchone():
                logger.info(f"✅ 数据库已存在: {db_name}")
            else:
                # 数据库不存在,创建它
                logger.info(f"🚀 数据库不存在,正在创建: {db_name}")
                cursor.execute(
                    f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
                connection.commit()
                logger.info(f"✅ 数据库创建成功: {db_name}")
        
        connection.close()
    except Exception as e:
        logger.error(f"❌ 检查/创建数据库失败: {e}")
        raise


def run_alembic_migrations(
    alembic_ini_path: str | None = None,
    upgrade_to: str = "head",
    auto_upgrade: bool = True,
    auto_generate: bool = False,
) -> bool:
    """
    执行 Alembic 数据库迁移
    
    Args:
        alembic_ini_path: alembic.ini 文件路径，默认为项目根目录下的 alembic.ini
        upgrade_to: 升级目标版本，默认为 "head"（最新版本）
        auto_upgrade: 是否自动执行升级，如果为 False 则只检查不执行
        auto_generate: 是否自动生成迁移脚本（仅开发环境使用！）
        
    Returns:
        bool: 迁移是否成功
        
    Raises:
        Exception: 迁移失败时抛出异常
    """
    # 确定 alembic.ini 路径
    if alembic_ini_path is None:
        # __file__ is in app/utils/alembic_migration.py
        # parent = utils, parent.parent = app, parent.parent.parent = project root
        project_root = Path(__file__).resolve().parent.parent.parent
        alembic_ini_path = str(project_root / "alembic.ini")
    
    logger.info(f"🔧 检查数据库迁移状态...")
    logger.info(f"   配置文件: {alembic_ini_path}")
    
    # 创建 Alembic 配置并显式设置 script_location
    alembic_cfg = Config(alembic_ini_path)
    
    # 确保 script_location 被正确设置
    # 尝试获取 script_location，如果不存在则设置默认值
    try:
        script_location = alembic_cfg.get_main_option("script_location")
        if not script_location:
            # 如果 script_location 为空，设置默认值
            project_root = Path(__file__).resolve().parent.parent.parent
            script_location = str(project_root / "alembic")
            alembic_cfg.set_main_option("script_location", script_location)
    except Exception:
        # 如果获取失败，设置默认值
        project_root = Path(__file__).resolve().parent.parent.parent
        script_location = str(project_root / "alembic")
        alembic_cfg.set_main_option("script_location", script_location)
    

    
    # 获取同步引擎用于迁移检查
    sync_url = settings.mysql_browser_info_url
    if sync_url.startswith("mysql+aiomysql://"):
        sync_url = sync_url.replace("mysql+aiomysql://", "mysql+pymysql://")
    
    # ✅ 在创建引擎之前，先确保数据库存在
    _ensure_database_exists(sync_url)
    
    from sqlalchemy import create_engine
    engine = create_engine(sync_url)
    
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        current_rev = context.get_current_revision()
        
        script = ScriptDirectory.from_config(alembic_cfg)
        head_rev = script.get_current_head()
        
        if current_rev == head_rev:
            logger.info(f"✅ 数据库已是最新版本: {current_rev}")
            
            # 如果启用了自动生成，即使版本一致也检查是否有未跟踪的变更
            if auto_generate:
                logger.info(f"🔍 检查模型与数据库的结构差异...")
                has_changes = _check_and_autogenerate_if_needed(alembic_cfg, engine)
                if has_changes:
                    # 重新获取最新的 head
                    script = ScriptDirectory.from_config(alembic_cfg)
                    head_rev = script.get_current_head()
                    logger.info(f"📝 已生成新的迁移脚本，最新版本: {head_rev}")
                    # ⚠️ 重要：生成了新迁移后，需要继续执行下面的 upgrade 逻辑
                else:
                    # 没有变化，直接返回
                    return True
            else:
                return True
        else:
            logger.info(f"📝 检测到待应用的迁移:")
            logger.info(f"   当前版本: {current_rev or 'None (初始状态)'}")
            logger.info(f"   最新版本: {head_rev}")
    
    # 执行迁移
    if auto_upgrade:
        logger.info(f"🚀 正在应用数据库迁移 (升级到 {upgrade_to})...")
        command.upgrade(alembic_cfg, upgrade_to)
        logger.info(f"✅ 数据库迁移成功完成!")
        return True
    else:
        logger.info(f"ℹ️  跳过自动迁移（auto_upgrade=False）")
        return True


def check_alembic_status(alembic_ini_path: str | None = None) -> dict:
    """
    检查 Alembic 迁移状态
    
    Args:
        alembic_ini_path: alembic.ini 文件路径
        
    Returns:
        dict: 包含迁移状态信息
            - current_revision: 当前版本
            - head_revision: 最新版本
            - needs_upgrade: 是否需要升级
            
    Raises:
        Exception: 检查失败时抛出异常
    """
    if alembic_ini_path is None:
        # __file__ is in app/utils/alembic_migration.py
        # parent = utils, parent.parent = app, parent.parent.parent = project root
        project_root = Path(__file__).resolve().parent.parent.parent
        alembic_ini_path = str(project_root / "alembic.ini")
    
    # 创建 Alembic 配置并显式设置 script_location
    alembic_cfg = Config(alembic_ini_path)
    
    # 确保 script_location 被正确设置
    # 尝试获取 script_location，如果不存在则设置默认值
    try:
        script_location = alembic_cfg.get_main_option("script_location")
        if not script_location:
            # 如果 script_location 为空，设置默认值
            project_root = Path(__file__).resolve().parent.parent.parent
            script_location = str(project_root / "alembic")
            alembic_cfg.set_main_option("script_location", script_location)
    except Exception:
        # 如果获取失败，设置默认值
        project_root = Path(__file__).resolve().parent.parent.parent
        script_location = str(project_root / "alembic")
        alembic_cfg.set_main_option("script_location", script_location)
    

    
    # 获取同步 URL
    sync_url = settings.mysql_browser_info_url
    if sync_url.startswith("mysql+aiomysql://"):
        sync_url = sync_url.replace("mysql+aiomysql://", "mysql+pymysql://")
    
    from sqlalchemy import create_engine
    engine = create_engine(sync_url)
    
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        current_rev = context.get_current_revision()
        
        script = ScriptDirectory.from_config(alembic_cfg)
        head_rev = script.get_current_head()
        
        return {
            "current_revision": current_rev,
            "head_revision": head_rev,
            "needs_upgrade": current_rev != head_rev,
        }


def _check_and_autogenerate_if_needed(alembic_cfg: Config, engine) -> bool:
    """
    检查模型与数据库的差异，如果有差异则自动生成迁移脚本
    
    Args:
        alembic_cfg: Alembic 配置对象
        engine: SQLAlchemy 引擎
        
    Returns:
        bool: 是否生成了新的迁移脚本
    """
    
    # 获取所有模型的 metadata
    target_metadata = CustomActionModel.metadata
    
    # 比较差异
    script_dir = ScriptDirectory.from_config(alembic_cfg)
    with engine.connect() as conn:
        migration_context = MigrationContext.configure(conn)
        diff = compare_metadata(
            migration_context,
            target_metadata
        )
    
    if not diff:
        logger.info(f"✅ 模型与数据库结构完全一致，无需生成迁移")
        return False
    
    # 有差异，生成迁移脚本
    logger.warning(f"⚠️  检测到 {len(diff)} 处结构差异:")
    for change in diff:
        logger.warning(f"   - {change}")
    
    logger.info(f"🚀 正在自动生成迁移脚本...")
    
    # 使用 alembic revision --autogenerate
    command.revision(
        alembic_cfg,
        message="Auto-generated migration (development mode)",
        autogenerate=True
    )
    
    logger.info(f"✅ 迁移脚本已生成，请检查 alembic/versions/ 目录")
    return True

