# -*- coding: utf-8 -*-
"""Alembic 数据库迁移与 Schema 一致性校验

参照 FastapiApp Utils/FastAPI/alembic_manager.py 模式，仅保留：
  - run_alembic_upgrade_head(): 执行增量迁移
  - check_schemas(): Schema 一致性校验
"""

import asyncio
import re
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import inspect, create_engine
from sqlmodel import SQLModel

from app.config import settings
from loguru import logger

# 导入所有模型，确保 SQLModel.metadata 中注册了所有表
from app.models.database.workflow.models import *  # noqa: F401, F403
from app.models.database.browser.info import *  # noqa: F401, F403
from app.models.database.notify.models import *  # noqa: F401, F403

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


# ================================================================
# 公开入口
# ================================================================

async def run_alembic_upgrade_head() -> bool:
    """执行 alembic upgrade head（增量迁移），成功返回 True"""
    logger.info("===== 开始执行 alembic upgrade head =====")
    alembic_ini = _project_root / "alembic.ini"
    try:
        cfg = AlembicConfig(str(alembic_ini))
        # env.py 内部会 asyncio.run，需在独立线程中执行以避免与当前事件循环冲突
        await asyncio.to_thread(command.upgrade, cfg, "head")
        logger.info("===== alembic upgrade head 完成 =====")
        return True
    except Exception as e:
        logger.error(f"alembic upgrade head 失败: {e}")
        return False


async def check_schemas() -> bool:
    """检查 Schema 是否与模型一致，不一致返回 False"""
    return await asyncio.to_thread(_check_schema_sync)


# ================================================================
# Schema 一致性校验
# ================================================================

def _normalize_column_type(col_type_str: str) -> str:
    """将 SQLAlchemy 列类型字符串归一化，便于跨驱动比较"""
    s = col_type_str.upper().strip()
    s = re.sub(r"\s+COLLATE\s+.*$", "", s)
    s = re.sub(r"\(.*\)", "", s)
    aliases = {
        "LONGTEXT": "TEXT", "MEDIUMTEXT": "TEXT", "TINYTEXT": "TEXT",
        "INTEGER": "INT",
        "TIMESTAMP": "DATETIME",
        "BOOL": "TINYINT", "BOOLEAN": "TINYINT",
    }
    return aliases.get(s, s)


def _is_type_compatible(model_type: str, db_type: str) -> bool:
    """判断两个归一化后的类型是否兼容"""
    if model_type == db_type:
        return True
    text_types = {"VARCHAR", "CHAR", "TEXT", "LONGTEXT", "MEDIUMTEXT", "TINYTEXT"}
    if model_type in text_types and db_type in text_types:
        return True
    int_types = {"TINYINT", "SMALLINT", "INT", "BIGINT", "INTEGER"}
    if model_type in int_types and db_type in int_types:
        return True
    if {model_type, db_type} <= {"DATETIME", "TIMESTAMP"}:
        return True
    if model_type == "JSON" and db_type in ("TEXT", "LONGTEXT"):
        return True
    # StrEnum/IntEnum 兼容：模型报告为 VARCHAR/INT，但数据库存储为 ENUM
    if (model_type in text_types or model_type in int_types) and db_type == "ENUM":
        return True
    if db_type in (text_types | int_types) and model_type == "ENUM":
        return True
    return False


def _check_schema_sync() -> bool:
    """
    同步执行 Schema 一致性校验

    检查项（关键，阻塞启动）:
        - 模型中声明但数据库不存在的表
        - 模型中存在但数据库缺少的列
        - 列类型不匹配
    非关键（仅警告，不阻塞）:
        - 数据库中存在但模型未声明的列
    """
    logger.info("===== 开始 Schema 一致性校验 =====")

    sync_url = settings.mysql_browser_info_url
    if sync_url.startswith("mysql+aiomysql://"):
        sync_url = sync_url.replace("mysql+aiomysql://", "mysql+pymysql://")

    metadata = SQLModel.metadata

    critical: list[str] = []
    non_critical: list[str] = []

    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            inspector = inspect(conn)
            db_tables = set(inspector.get_table_names())
            model_tables = set(metadata.tables.keys())

            missing_tables = model_tables - db_tables
            if missing_tables:
                critical.append(f"模型中声明但数据库中不存在的表: {sorted(missing_tables)}")

            for table_name in sorted(model_tables & db_tables):
                db_cols = {
                    col["name"]: _normalize_column_type(str(col["type"]))
                    for col in inspector.get_columns(table_name)
                }
                model_cols = {
                    col.name: _normalize_column_type(str(col.type))
                    for col in metadata.tables[table_name].columns
                }
                mcs, dcs = set(model_cols), set(db_cols)
                for col in sorted(mcs - dcs):
                    critical.append(
                        f"{table_name}: 模型中存在但DB缺少列 '{col}' ({model_cols[col]})")
                for col in sorted(dcs - mcs):
                    non_critical.append(
                        f"{table_name}: DB中存在但模型中未声明的列 '{col}' ({db_cols[col]})")
                for col in sorted(mcs & dcs):
                    mt, dt = model_cols[col], db_cols[col]
                    if mt != dt and not _is_type_compatible(mt, dt):
                        critical.append(
                            f"{table_name}.{col}: 类型不匹配 模型={mt}, DB={dt}")
    except Exception as e:
        logger.error(f"Schema 校验异常: {e}")
        return False
    finally:
        engine.dispose()

    if non_critical:
        logger.warning("=" * 60)
        logger.warning("Schema 非关键差异 (不阻塞启动):")
        for w in non_critical:
            logger.warning(f"  {w}")
        logger.warning("=" * 60)

    if critical:
        logger.error("=" * 60)
        logger.error("Schema 不一致，拒绝启动:")
        for w in critical:
            logger.error(f"  {w}")
        logger.error("=" * 60)
        logger.error("请先执行 alembic upgrade head 同步 Schema")
        return False

    logger.info("===== Schema 一致性校验全部通过 =====")
    return True