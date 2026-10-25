"""工作流运行状态机（状态迁移规则的唯一来源）

背景（见 ``docs/工作流运行状态机计划书.md``）：一次运行的状态原先由 6 处代码各自写入
（创建、排队转运行、正常收口、排队取消、僵尸收口、兜底强制收口、计费熔断），
每条路径都要自己判断「当前状态能不能改成目标状态」，漏判就会写出
「已取消又变回 running」「成功被覆盖成失败」这类脏状态。

本模块只做**纯规则**，不碰数据库：

- :data:`ALLOWED_TRANSITIONS` 是唯一的迁移表；
- :func:`ensure_transition` / :func:`ensure_initial_status` 校验非法迁移；
- 真正的落库仍由 ``WorkflowRunCrudService`` 统一完成（单一写入口）。

约定：
    - 终态（success / failed / interrupted / cancelled）不可迁出；
    - 同状态「迁移」视为幂等空操作（重复收口不应报错）；
    - 状态以 :class:`WorkflowRunStatusEnum` 为准，字符串入参先归一化。
"""

from __future__ import annotations

from bili_common.models.response_code import ResponseCode

from app.models.common.exceptions.base_exception import (
    BaseException as AppBaseException,
)
from app.models.database.workflow.models import WorkflowRunStatusEnum

#: 终态：写入后不再变化（重复收口按幂等处理）
TERMINAL_STATUSES: frozenset[WorkflowRunStatusEnum] = frozenset(
    {
        WorkflowRunStatusEnum.SUCCESS,
        WorkflowRunStatusEnum.FAILED,
        WorkflowRunStatusEnum.INTERRUPTED,
        WorkflowRunStatusEnum.CANCELLED,
    }
)

#: 允许的初始状态（创建运行记录时）
INITIAL_STATUSES: frozenset[WorkflowRunStatusEnum] = frozenset(
    {
        WorkflowRunStatusEnum.QUEUED,
        WorkflowRunStatusEnum.RUNNING,
    }
)

#: 唯一迁移表：current → 允许的 target 集合
ALLOWED_TRANSITIONS: dict[
    WorkflowRunStatusEnum, frozenset[WorkflowRunStatusEnum]
] = {
    # 排队：拿到执行权转 running；排队期间可被取消 / 超时判失败 / 进程重启判中断
    WorkflowRunStatusEnum.QUEUED: frozenset(
        {
            WorkflowRunStatusEnum.RUNNING,
            WorkflowRunStatusEnum.CANCELLED,
            WorkflowRunStatusEnum.FAILED,
            WorkflowRunStatusEnum.INTERRUPTED,
        }
    ),
    # 运行中：四种终态都可能到达
    WorkflowRunStatusEnum.RUNNING: frozenset(
        {
            WorkflowRunStatusEnum.SUCCESS,
            WorkflowRunStatusEnum.FAILED,
            WorkflowRunStatusEnum.INTERRUPTED,
            WorkflowRunStatusEnum.CANCELLED,
        }
    ),
    # 终态：不可迁出
    WorkflowRunStatusEnum.SUCCESS: frozenset(),
    WorkflowRunStatusEnum.FAILED: frozenset(),
    WorkflowRunStatusEnum.INTERRUPTED: frozenset(),
    WorkflowRunStatusEnum.CANCELLED: frozenset(),
}


class WorkflowRunStateError(AppBaseException):
    """非法的运行状态迁移（内部错误：说明有代码绕过了状态机）"""

    code = ResponseCode.BUSINESS_ERROR

    def __init__(
        self,
        current: WorkflowRunStatusEnum,
        target: WorkflowRunStatusEnum,
        run_id: str = "",
    ) -> None:
        self.current = current
        self.target = target
        self.run_id = run_id
        self.msg = (
            f"非法的运行状态迁移: run={run_id or '-'}, "
            f"{current.value} → {target.value}"
        )
        super().__init__(self.msg)


def normalize_status(status: WorkflowRunStatusEnum | str) -> WorkflowRunStatusEnum:
    """把字符串 / 枚举统一成 :class:`WorkflowRunStatusEnum`"""
    if isinstance(status, WorkflowRunStatusEnum):
        return status
    return WorkflowRunStatusEnum(str(status))


def is_terminal(status: WorkflowRunStatusEnum | str) -> bool:
    """是否为终态（写入后不再变化）"""
    return normalize_status(status) in TERMINAL_STATUSES


def allowed_targets(
    current: WorkflowRunStatusEnum | str,
) -> frozenset[WorkflowRunStatusEnum]:
    """当前状态允许迁移到的目标状态集合"""
    return ALLOWED_TRANSITIONS[normalize_status(current)]


def can_transition(
    current: WorkflowRunStatusEnum | str,
    target: WorkflowRunStatusEnum | str,
) -> bool:
    """是否允许迁移（同状态视为幂等，返回 True）"""
    current_status = normalize_status(current)
    target_status = normalize_status(target)
    if current_status == target_status:
        return True
    return target_status in ALLOWED_TRANSITIONS[current_status]


def ensure_transition(
    current: WorkflowRunStatusEnum | str,
    target: WorkflowRunStatusEnum | str,
    *,
    run_id: str = "",
) -> WorkflowRunStatusEnum:
    """校验状态迁移合法性，返回归一化后的目标状态。

    Raises:
        WorkflowRunStateError: 迁移非法（终态迁出 / 跳过了必经状态）。
    """
    current_status = normalize_status(current)
    target_status = normalize_status(target)
    if not can_transition(current_status, target_status):
        raise WorkflowRunStateError(current_status, target_status, run_id)
    return target_status


def ensure_initial_status(
    status: WorkflowRunStatusEnum | str,
    *,
    run_id: str = "",
) -> WorkflowRunStatusEnum:
    """校验创建运行记录时使用的初始状态（queued / running）。

    Raises:
        WorkflowRunStateError: 用终态创建运行记录。
    """
    normalized = normalize_status(status)
    if normalized not in INITIAL_STATUSES:
        raise WorkflowRunStateError(
            WorkflowRunStatusEnum.QUEUED, normalized, run_id
        )
    return normalized


__all__ = [
    "ALLOWED_TRANSITIONS",
    "INITIAL_STATUSES",
    "TERMINAL_STATUSES",
    "WorkflowRunStateError",
    "allowed_targets",
    "can_transition",
    "ensure_initial_status",
    "ensure_transition",
    "is_terminal",
    "normalize_status",
]
