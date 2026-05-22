"""Alembic 数据库迁移工具"""
from pathlib import Path
from alembic.config import Config
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.script import ScriptDirectory
from alembic.runtime.migration import MigrationContext
from alembic import context
from app.models.database.workflow.models import *
from app.models.database.browser.info import *
from app.models.database.notify.models import *
from sqlalchemy import create_engine, pool
from sqlalchemy.pool import QueuePool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
import pymysql
from urllib.parse import urlparse, parse_qs
from app.config import settings
from loguru import logger
import time
import asyncio

# 获取 Alembic 配置（用于 env.py 调用）
_config = None

def get_alembic_config_for_env() -> Config:
    """获取 Alembic 配置对象（供 env.py 使用）"""
    global _config
    if _config is None:
        project_root = Path(__file__).resolve().parent.parent.parent
        alembic_ini_path = str(project_root / "alembic.ini")
        _config = Config(alembic_ini_path)
        
        # 确保 script_location 被正确设置
        try:
            script_location = _config.get_main_option("script_location")
            if not script_location:
                script_location = str(project_root / "alembic")
                _config.set_main_option("script_location", script_location)
        except Exception:
            script_location = str(project_root / "alembic")
            _config.set_main_option("script_location", script_location)
    
    return _config

# 模型 metadata（供 env.py 使用）
target_metadata = CustomAction.metadata

# 缓存已创建的引擎，避免重复创建
_engine_cache = {}

# 缓存上次检查结果，避免频繁检查
_last_check_time = 0
_last_check_result = None
_CHECK_CACHE_DURATION = 60  # 缓存有效期60秒


def _get_sync_engine(db_url: str) -> object:
    """
    获取或创建 SQLAlchemy 同步引擎（带连接池）

    Args:
        db_url: 数据库连接 URL

    Returns:
        SQLAlchemy 引擎对象
    """
    global _engine_cache

    if db_url in _engine_cache:
        return _engine_cache[db_url]

    # 创建带连接池的引擎
    engine = create_engine(
        db_url,
        poolclass=QueuePool,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        pool_recycle=3600,
        echo=False,
        connect_args={
            'connect_timeout': 10,
            'read_timeout': 30,
            'write_timeout': 30,
        } if db_url.startswith('mysql') else {}
    )

    _engine_cache[db_url] = engine
    return engine


async def _ensure_database_exists(db_url: str) -> None:
    """
    确保数据库存在，如果不存在则创建（异步版本）

    Args:
        db_url: 数据库连接 URL
    """
    import aiomysql

    # 解析 URL
    parsed = urlparse(db_url)

    # 提取数据库名称
    db_name = parsed.path.lstrip('/')
    if not db_name:
        logger.warning("⚠️  无法从 URL 中提取数据库名称")
        return

    # 提取连接参数
    query_params = parse_qs(parsed.query)

    # ⚠️ 关键：移除 auth_plugin 参数，避免使用 mysql_native_password
    if 'auth_plugin' in query_params:
        logger.info("🔧 检测到 URL 中包含 auth_plugin 参数，已移除以避免认证问题")
        del query_params['auth_plugin']

    # 从 URL 或查询参数中提取用户名和密码
    # 支持两种格式:
    # 1. mysql://user:pass@host/db (标准格式)
    # 2. mysql://host/db?user=xxx&password=yyy (查询参数格式)
    username = parsed.username or query_params.get('user', ['root'])[0]
    password = parsed.password or query_params.get('password', [''])[0]

    # 构建连接到 MySQL 服务器的参数 (不包含数据库名)
    connection_params = {
        'host': parsed.hostname or '127.0.0.1',
        'port': parsed.port or 3306,
        'user': username,
        'password': password,
        # 不使用 auth_plugin，让 aiomysql 自动协商
    }

    logger.info(f"🔍 检查数据库是否存在：{db_name} (用户：{username})")

    # 使用 aiomysql 异步连接
    connection = await aiomysql.connect(**connection_params)
    try:
        async with connection.cursor() as cursor:
            # 检查数据库是否存在
            await cursor.execute(
                "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = %s",
                (db_name,)
            )

            if await cursor.fetchone():
                logger.info(f"✅ 数据库已存在：{db_name}")
            else:
                # 数据库不存在，创建它
                logger.info(f"🚀 数据库不存在，正在创建：{db_name}")
                await cursor.execute(
                    f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
                await connection.commit()
                logger.info(f"✅ 数据库创建成功：{db_name}")
    finally:
        connection.close()


def _ensure_database_exists_sync(db_url: str) -> None:
    """
    确保数据库存在，如果不存在则创建（同步版本，用于同步函数调用）

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

    # ⚠️ 关键：移除 auth_plugin 参数，避免使用 mysql_native_password
    if 'auth_plugin' in query_params:
        logger.info("🔧 检测到 URL 中包含 auth_plugin 参数，已移除以避免认证问题")
        del query_params['auth_plugin']

    # 从 URL 或查询参数中提取用户名和密码
    # 支持两种格式:
    # 1. mysql://user:pass@host/db (标准格式)
    # 2. mysql://host/db?user=xxx&password=yyy (查询参数格式)
    username = parsed.username or query_params.get('user', ['root'])[0]
    password = parsed.password or query_params.get('password', [''])[0]

    # 构建连接到 MySQL 服务器的参数 (不包含数据库名)
    connection_params = {
        'host': parsed.hostname or '127.0.0.1',
        'port': parsed.port or 3306,
        'user': username,
        'password': password,
        # 不使用 auth_plugin，让 PyMySQL 自动协商
    }

    logger.info(f"🔍 检查数据库是否存在：{db_name} (用户：{username})")

    # 直接使用 PyMySQL 连接 (不通过 SQLAlchemy)
    connection = pymysql.connect(**connection_params)
    with connection.cursor() as cursor:
        # 检查数据库是否存在
        cursor.execute(
            "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = %s",
            (db_name,)
        )

        if cursor.fetchone():
            logger.info(f"✅ 数据库已存在：{db_name}")
        else:
            # 数据库不存在，创建它
            logger.info(f"🚀 数据库不存在，正在创建：{db_name}")
            cursor.execute(
                f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            connection.commit()
            logger.info(f"✅ 数据库创建成功：{db_name}")

    connection.close()


def _get_alembic_config(alembic_ini_path: str | None = None) -> Config:
    """
    获取 Alembic 配置对象（复用逻辑）

    Args:
        alembic_ini_path: alembic.ini 文件路径

    Returns:
        Alembic Config 对象
    """
    if alembic_ini_path is None:
        project_root = Path(__file__).resolve().parent.parent.parent
        alembic_ini_path = str(project_root / "alembic.ini")

    alembic_cfg = Config(alembic_ini_path)

    # 确保 script_location 被正确设置
    script_location = alembic_cfg.get_main_option("script_location")
    if not script_location:
        project_root = Path(__file__).resolve().parent.parent.parent
        script_location = str(project_root / "alembic")
        alembic_cfg.set_main_option("script_location", script_location)
    return alembic_cfg


def run_alembic_migrations(
    alembic_ini_path: str | None = None,
    upgrade_to: str = "heads",
    auto_upgrade: bool = True,
    auto_generate: bool = False,
) -> bool:
    """
    执行 Alembic 数据库迁移（优化版）

    Args:
        alembic_ini_path: alembic.ini 文件路径，默认为项目根目录下的 alembic.ini
        upgrade_to: 升级目标版本，默认为 "heads"（最新版本）
        auto_upgrade: 是否自动执行升级，如果为 False 则只检查不执行
        auto_generate: 是否自动生成迁移脚本（仅开发环境使用！）

    Returns:
        bool: 迁移是否成功

    Raises:
        Exception: 迁移失败时抛出异常
    """
    start_time = time.time()

    # 获取 Alembic 配置
    alembic_cfg = _get_alembic_config(alembic_ini_path)
    logger.info(f"🔧 检查数据库迁移状态...")

    # 获取同步引擎（使用缓存）
    sync_url = settings.mysql_browser_info_url
    if sync_url.startswith("mysql+aiomysql://"):
        sync_url = sync_url.replace("mysql+aiomysql://", "mysql+pymysql://")

    # 确保数据库存在（同步版本）
    _ensure_database_exists_sync(sync_url)

    # 使用带连接池的引擎
    engine = _get_sync_engine(sync_url)

    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        current_rev = context.get_current_revision()

        script = ScriptDirectory.from_config(alembic_cfg)
        head_rev = script.get_current_head()

        if current_rev == head_rev:
            logger.info(f"✅ 数据库已是最新版本: {current_rev}")

            # 如果启用了自动生成，检查是否有未跟踪的变更
            if auto_generate:
                logger.info(f"🔍 检查模型与数据库的结构差异...")
                has_changes = _check_and_autogenerate_if_needed(
                    alembic_cfg, engine)
                if has_changes:
                    script = ScriptDirectory.from_config(alembic_cfg)
                    head_rev = script.get_current_head()
                    logger.info(f"📝 已生成新的迁移脚本，最新版本: {head_rev}")
                else:
                    elapsed = time.time() - start_time
                    logger.info(f"✅ 迁移检查完成，耗时: {elapsed:.2f}s")
                    return True
            else:
                elapsed = time.time() - start_time
                logger.info(f"✅ 迁移检查完成，耗时: {elapsed:.2f}s")
                return True
        else:
            logger.info(f"📝 检测到待应用的迁移:")
            logger.info(f"   当前版本: {current_rev or 'None (初始状态)'}")
            logger.info(f"   最新版本: {head_rev}")


    # 执行迁移
    if auto_upgrade:
        logger.info(f"🚀 正在应用数据库迁移 (升级到 {upgrade_to})...")
        command.upgrade(alembic_cfg, upgrade_to)
        elapsed = time.time() - start_time
        logger.info(f"✅ 数据库迁移成功完成! 总耗时: {elapsed:.2f}s")
        return True
        
    else:
        elapsed = time.time() - start_time
        logger.info(f"ℹ️  跳过自动迁移（auto_upgrade=False），耗时: {elapsed:.2f}s")
        return True


def check_alembic_status(alembic_ini_path: str | None = None, use_cache: bool = True) -> dict:
    """
    检查 Alembic 迁移状态（优化版，支持缓存）

    Args:
        alembic_ini_path: alembic.ini 文件路径
        use_cache: 是否使用缓存（默认启用，60秒有效期）

    Returns:
        dict: 包含迁移状态信息
            - current_revision: 当前版本
            - head_revision: 最新版本
            - needs_upgrade: 是否需要升级

    Raises:
        Exception: 检查失败时抛出异常
    """
    global _last_check_time, _last_check_result

    # 如果启用缓存且缓存未过期，直接返回缓存结果
    if use_cache and _last_check_result is not None:
        elapsed = time.time() - _last_check_time
        if elapsed < _CHECK_CACHE_DURATION:
            logger.debug(f"📦 使用缓存的迁移状态（缓存时间: {elapsed:.1f}s）")
            return _last_check_result

    # 获取 Alembic 配置
    alembic_cfg = _get_alembic_config(alembic_ini_path)

    # 获取同步 URL
    sync_url = settings.mysql_browser_info_url
    if sync_url.startswith("mysql+aiomysql://"):
        sync_url = sync_url.replace("mysql+aiomysql://", "mysql+pymysql://")

    # 使用带连接池的引擎
    engine = _get_sync_engine(sync_url)

    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        current_rev = context.get_current_revision()

        script = ScriptDirectory.from_config(alembic_cfg)
        head_rev = script.get_current_head()

        result = {
            "current_revision": current_rev,
            "head_revision": head_rev,
            "needs_upgrade": current_rev != head_rev,
        }

        # 更新缓存
        _last_check_time = time.time()
        _last_check_result = result

        return result


def _check_and_autogenerate_if_needed(alembic_cfg: Config, engine) -> bool:
    """
    检查模型与数据库的差异，如果有差异则自动生成迁移脚本（优化版）

    Args:
        alembic_cfg: Alembic 配置对象
        engine: SQLAlchemy 引擎

    Returns:
        bool: 是否生成了新的迁移脚本
    """
    start_time = time.time()
    
    # 获取所有模型的 metadata（复用已加载的模型）
    target_metadata = CustomAction.metadata
    
    # 比较差异（最耗时的操作）
    logger.debug(f"🔍 开始比较模型与数据库结构差异...")
    compare_start = time.time()
    
    with engine.connect() as conn:
        migration_context = MigrationContext.configure(conn)
        diff = compare_metadata(
            migration_context,
            target_metadata
        )
    
    compare_elapsed = time.time() - compare_start
    logger.debug(f"✅ 差异比较完成，耗时: {compare_elapsed:.2f}s")

    if not diff:
        total_elapsed = time.time() - start_time
        logger.info(f"✅ 模型与数据库结构完全一致，无需生成迁移（总耗时: {total_elapsed:.2f}s）")
        return False

    # 有差异，生成迁移脚本
    logger.warning(f"⚠️  检测到 {len(diff)} 处结构差异:")
    for change in diff:
        logger.warning(f"   - {change}")

    logger.info(f"🚀 正在自动生成迁移脚本...")
    
    # 使用 alembic revision --autogenerate
    revision_start = time.time()
    command.revision(
        alembic_cfg,
        message="Auto-generated migration (development mode)",
        autogenerate=True
    )
    revision_elapsed = time.time() - revision_start
    logger.debug(f"✅ 迁移脚本生成完成，耗时: {revision_elapsed:.2f}s")

    total_elapsed = time.time() - start_time
    logger.info(f"✅ 迁移脚本已生成，请检查 alembic/versions/ 目录（总耗时: {total_elapsed:.2f}s）")
    return True


async def run_alembic_migrations_async(
    alembic_ini_path: str | None = None,
    upgrade_to: str = "heads",
    auto_upgrade: bool = True,
    auto_generate: bool = False,
) -> bool:
    """
    执行 Alembic 数据库迁移（异步版本，用于从已运行的事件循环中调用）

    Args:
        alembic_ini_path: alembic.ini 文件路径，默认为项目根目录下的 alembic.ini
        upgrade_to: 升级目标版本，默认为 "heads"（最新版本）
        auto_upgrade: 是否自动执行升级，如果为 False 则只检查不执行
        auto_generate: 是否自动生成迁移脚本（仅开发环境使用！）

    Returns:
        bool: 迁移是否成功

    Raises:
        Exception: 迁移失败时抛出异常
    """
    start_time = time.time()

    # 获取 Alembic 配置
    alembic_cfg = _get_alembic_config(alembic_ini_path)
    logger.info(f"🔧 检查数据库迁移状态...")

    # 获取同步引擎（使用缓存）
    sync_url = settings.mysql_browser_info_url
    if sync_url.startswith("mysql+aiomysql://"):
        sync_url = sync_url.replace("mysql+aiomysql://", "mysql+pymysql://")

    # 确保数据库存在（异步版本）
    await _ensure_database_exists(sync_url)

    # 使用带连接池的引擎
    engine = _get_sync_engine(sync_url)

    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        current_rev = context.get_current_revision()

        script = ScriptDirectory.from_config(alembic_cfg)
        head_rev = script.get_current_head()

        if current_rev == head_rev:
            logger.info(f"✅ 数据库已是最新版本: {current_rev}")

            # 如果启用了自动生成，检查是否有未跟踪的变更
            if auto_generate:
                logger.info(f"🔍 检查模型与数据库的结构差异...")
                # 使用线程执行同步操作，避免事件循环冲突
                has_changes = await asyncio.to_thread(
                    _check_and_autogenerate_if_needed,
                    alembic_cfg, engine
                )
                if has_changes:
                    script = ScriptDirectory.from_config(alembic_cfg)
                    head_rev = script.get_current_head()
                    logger.info(f"📝 已生成新的迁移脚本，最新版本: {head_rev}")
                    # 生成新迁移后需要继续执行迁移升级
                else:
                    elapsed = time.time() - start_time
                    logger.info(f"✅ 迁移检查完成，耗时: {elapsed:.2f}s")
                    return True
            else:
                elapsed = time.time() - start_time
                logger.info(f"✅ 迁移检查完成，耗时: {elapsed:.2f}s")
                return True
        else:
            logger.info(f"📝 检测到待应用的迁移:")
            logger.info(f"   当前版本: {current_rev or 'None (初始状态)'}")
            logger.info(f"   最新版本: {head_rev}")


    # 执行迁移
    if auto_upgrade:
        logger.info(f"🚀 正在应用数据库迁移 (升级到 {upgrade_to})...")
        # 使用线程执行同步的 command.upgrade，避免事件循环冲突
        await asyncio.to_thread(command.upgrade, alembic_cfg, upgrade_to)
        elapsed = time.time() - start_time
        logger.info(f"✅ 数据库迁移成功完成! 总耗时: {elapsed:.2f}s")
        return True

    else:
        elapsed = time.time() - start_time
        logger.info(f"ℹ️  跳过自动迁移（auto_upgrade=False），耗时: {elapsed:.2f}s")
        return True


# ============ Alembic env.py 相关函数 ============

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = settings.mysql_browser_info_url
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """执行迁移（内部函数）"""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """执行异步迁移（供在线迁移使用）"""
    db_url = settings.mysql_browser_info_url
    # 确保使用异步驱动
    if db_url.startswith("mysql+pymysql://"):
        db_url = db_url.replace("mysql+pymysql://", "mysql+aiomysql://")
    
    config = get_alembic_config_for_env()
    config.set_main_option("sqlalchemy.url", db_url)
    
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    # 使用新的事件循环，避免与已有事件循环冲突
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_async_migrations())
    finally:
        loop.close()


async def run_migrations_online_async() -> None:
    """Run migrations in 'online' mode - async version for calling from existing event loop."""
    await run_async_migrations()
