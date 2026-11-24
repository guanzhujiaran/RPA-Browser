"""RPA 审批状态机与资源联动

审批单状态机：
    pending  → approved / rejected
    approved → rejected   （撤回：资源下架）
    rejected → approved   （恢复：资源重新公开）

资源联动只发生在「撤回 / 恢复」：
- 已通过 → 驳回：``is_public=False``，资源退出社区广场；
- 已驳回 → 通过：``is_public=True``，资源恢复公开。
首次审核（pending → approved）保持原语义：只放开审批，公开仍由作者自己切换。

所有函数只操作调用方传入的 session，由调用方统一提交，保证
「审批单状态 + 资源可见性」在同一事务内原子生效。
"""

from typing import Any

from sqlmodel import select

from app.models.database.admin.models import ApprovalRequest
from app.models.database.workflow.models import CompositeActionModel, UserWorkflow

#: 审批单状态机（审核按钮显隐与后端校验的唯一真相源）
APPROVAL_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"approved", "rejected"},
    "approved": {"rejected"},
    "rejected": {"approved"},
}

#: 支持资源归属校验 / 公开状态联动的资源类型 → (模型, 业务 ID 字段)
RESOURCE_MODELS: dict[str, tuple[Any, str]] = {
    "action": (CompositeActionModel, "action_id"),
    "workflow": (UserWorkflow, "workflow_id"),
}


def can_transition(current: str, target: str) -> bool:
    """判断审批单是否允许从 current 流转到 target。"""
    return target in APPROVAL_TRANSITIONS.get(current, set())


def _resource_key(resource_id: int | str) -> int | None:
    """审批单里 resource_id 以字符串存储，转回模型主键（雪花 int）。"""
    try:
        return int(str(resource_id))
    except (TypeError, ValueError):
        return None


async def get_owned_resource(
    session, resource_type: str, resource_id: int | str
) -> tuple[Any | None, bool]:
    """按资源类型/业务 ID 取资源。

    Returns:
        (资源, 是否支持归属校验)：plugin 等没有本地表的类型返回 (None, False)，
        其余类型即使查不到也返回 (None, True)，便于调用方区分「不支持」与「不存在」。
    """
    entry = RESOURCE_MODELS.get(resource_type)
    if entry is None:
        return None, False
    model, id_field = entry
    key = _resource_key(resource_id)
    if key is None:
        return None, True
    resource = (
        await session.exec(select(model).where(getattr(model, id_field) == key))
    ).first()
    return resource, True


async def set_resource_public(
    session, resource_type: str, resource_id: int | str, is_public: bool
) -> bool:
    """把资源公开状态写成指定值；资源不存在返回 False。

    只改内存对象，由调用方 commit（与审批单状态同一事务）。
    """
    resource, _ = await get_owned_resource(session, resource_type, resource_id)
    if resource is None:
        return False
    if bool(resource.is_public) != is_public:
        resource.is_public = is_public
        session.add(resource)
    return True


async def sync_visibility_on_transition(
    session,
    approval: ApprovalRequest,
    old_status: str,
    new_status: str,
) -> bool:
    """按「撤回 / 恢复」语义联动资源公开状态。

    - approved → rejected：下架（退出社区广场）；
    - rejected → approved：恢复公开；
    - 其它流转（含首次 pending → approved）：不动资源。

    Returns:
        是否找到并处理了资源（plugin 等无本地表的类型返回 False）。
    """
    if old_status == "approved" and new_status == "rejected":
        return await set_resource_public(
            session, approval.resource_type, approval.resource_id, False
        )
    if old_status == "rejected" and new_status == "approved":
        return await set_resource_public(
            session, approval.resource_type, approval.resource_id, True
        )
    return False
