"""Alembic 数据库迁移管理路由"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from app.utils.alembic_migration import run_alembic_migrations, check_alembic_status
from app.models.common.response import success_response, error_response
from loguru import logger


router = APIRouter(prefix="/alembic", tags=["数据库迁移"])


class MigrationRequest(BaseModel):
    """迁移请求模型"""
    upgrade_to: str = "head"  # 升级目标版本
    auto_upgrade: bool = True  # 是否自动执行


class MigrationResponse(BaseModel):
    """迁移响应模型"""
    success: bool
    message: str
    current_revision: Optional[str] = None
    head_revision: Optional[str] = None


@router.get("/status", summary="检查数据库迁移状态")
async def get_migration_status():
    """
    检查当前数据库迁移状态
    
    Returns:
        dict: 包含当前版本、最新版本和是否需要升级的信息
    """
    status = check_alembic_status()
    
    return success_response({
        "current_revision": status["current_revision"],
        "head_revision": status["head_revision"],
        "needs_upgrade": status["needs_upgrade"],
        "message": "数据库已是最新版本" if not status["needs_upgrade"] else "有待应用的迁移"
    })


@router.post("/migrate", summary="执行数据库迁移")
async def execute_migration(request: MigrationRequest = None):
    """
    手动执行数据库迁移
    
    Args:
        request: 迁移请求，包含升级目标和是否自动执行
        
    Returns:
        dict: 迁移结果
    """
    if request is None:
        request = MigrationRequest()
    
    logger.info(f"收到手动迁移请求: upgrade_to={request.upgrade_to}")
    
    run_alembic_migrations(
        upgrade_to=request.upgrade_to,
        auto_upgrade=request.auto_upgrade,
    )
    
    # 获取最新状态
    status = check_alembic_status()
    
    return success_response({
        "success": True,
        "message": "数据库迁移成功",
        "current_revision": status.get("current_revision"),
        "head_revision": status.get("head_revision"),
    })


@router.post("/upgrade", summary="升级到最新版本")
async def upgrade_to_head():
    """
    快速升级到最新版本（等同于 migrate with upgrade_to='head'）
    
    Returns:
        dict: 迁移结果
    """
    logger.info("收到升级到最新版本请求")
    
    run_alembic_migrations(
        upgrade_to="head",
        auto_upgrade=True,
    )
    
    status = check_alembic_status()
    
    return success_response({
        "success": True,
        "message": "已升级到最新版本",
        "current_revision": status.get("current_revision"),
        "head_revision": status.get("head_revision"),
    })


@router.post("/downgrade", summary="回滚一个版本")
async def downgrade_one_version():
    """
    回滚一个版本
    
    Returns:
        dict: 迁移结果
    """
    logger.info("收到回滚请求")
    
    from alembic.config import Config
    from alembic import command
    from pathlib import Path
    
    project_root = Path(__file__).resolve().parent.parent.parent
    alembic_ini_path = str(project_root / "alembic.ini")
    alembic_cfg = Config(alembic_ini_path)
    
    # 确保 script_location 被正确设置
    try:
        script_location = alembic_cfg.get_main_option("script_location")
        if not script_location:
            script_location = str(project_root / "alembic")
            alembic_cfg.set_main_option("script_location", script_location)
    except Exception:
        script_location = str(project_root / "alembic")
        alembic_cfg.set_main_option("script_location", script_location)
    
    # 执行降级
    command.downgrade(alembic_cfg, "-1")
    
    logger.info("✅ 数据库回滚成功")
    
    # 获取最新状态
    status = check_alembic_status()
    
    return success_response({
        "success": True,
        "message": "已回滚一个版本",
        "current_revision": status.get("current_revision"),
        "head_revision": status.get("head_revision"),
    })


@router.get("/history", summary="查看迁移历史")
async def get_migration_history(limit: int = 10):
    """
    查看最近的迁移历史
    
    Args:
        limit: 返回的迁移记录数量，默认10条
        
    Returns:
        dict: 迁移历史记录
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from pathlib import Path
    
    project_root = Path(__file__).resolve().parent.parent.parent
    alembic_ini_path = str(project_root / "alembic.ini")
    alembic_cfg = Config(alembic_ini_path)
    
    # 确保 script_location 被正确设置
    try:
        script_location = alembic_cfg.get_main_option("script_location")
        if not script_location:
            script_location = str(project_root / "alembic")
            alembic_cfg.set_main_option("script_location", script_location)
    except Exception:
        script_location = str(project_root / "alembic")
        alembic_cfg.set_main_option("script_location", script_location)
    
    script = ScriptDirectory.from_config(alembic_cfg)
    
    # 获取所有迁移
    revisions = []
    for rev in script.walk_revisions():
        revisions.append({
            "revision": rev.revision,
            "message": rev.doc or "No message",
            "created_at": getattr(rev.module, '__created_at__', None),
        })
        
        if len(revisions) >= limit:
            break
    
    return success_response({
        "total": len(revisions),
        "revisions": revisions,
    })
