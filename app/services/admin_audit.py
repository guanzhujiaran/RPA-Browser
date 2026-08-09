"""管理员操作审计日志服务

提供 log_admin_action，供各 admin 治理接口在成功执行后记录操作痕迹。
审计写入失败仅告警，不影响主业务流程。
"""
from datetime import datetime
from loguru import logger

from app.utils.depends.session_manager import DatabaseSessionManager
from app.models.database.admin.models import AdminAuditLog


async def log_admin_action(
    admin_mid: int,
    action: str,
    target_type: str = "",
    target_id: str | int = "",
    detail: str = "",
) -> None:
    """记录一条管理员操作审计日志

    Args:
        admin_mid: 操作的管理员 mid
        action: 操作类型，如 role:grant / cert:certify / tag:create / approval:review / report:review
        target_type: 目标类型（如 user / tag / action / workflow / report / approval）
        target_id: 目标 ID
        detail: 操作详情（建议传人类可读文本或 JSON 字符串）
    """
    try:
        async with DatabaseSessionManager.async_session() as session:
            session.add(
                AdminAuditLog(
                    admin_mid=admin_mid,
                    action=action,
                    target_type=target_type,
                    target_id=str(target_id),
                    detail=detail,
                    created_at=datetime.now(),
                )
            )
            await session.commit()
    except Exception as e:  # 审计失败不应阻断业务
        logger.warning(f"⚠️ 写入管理员审计日志失败: {e}")
