"""工作流运行进度模型（SSE 推送载荷）

一次运行的完整进度快照：循环轮次（主轴）、步骤进度、累计耗时与恢复信息。
所有时间字段按项目约定输出**秒级 int32 时间戳**，前端自行格式化。

详见 docs/rpa-workflow-调度外壳计划书.md §10。
"""

from datetime import datetime
from typing import TYPE_CHECKING

from bili_common.core.time_utils import OptionalUnixTime, UnixTime
from sqlmodel import Field, SQLModel

from app.models.database.workflow.models import WorkflowRunStatusEnum

if TYPE_CHECKING:
    from app.models.database.workflow.models import WorkflowRunRecord

#: SSE 事件名（前端据此过滤事件）
WORKFLOW_RUN_SSE_EVENT_NAME = "workflow_run_progress"
#: 运行消息（步骤级明细）SSE 事件名
WORKFLOW_RUN_LOG_SSE_EVENT_NAME = "workflow_run_log"


class WorkflowRunProgressData(SQLModel):
    """工作流单次运行的进度快照"""

    run_id: str = Field(description="运行ID")
    workflow_id: int = Field(description="工作流ID（雪花 int）")
    workflow_name: str = Field(default="", description="工作流名称")
    status: str = Field(
        default=WorkflowRunStatusEnum.RUNNING.value,
        description="运行状态: running/success/failed/interrupted/cancelled",
    )
    trigger_source: str = Field(default="manual", description="触发来源: manual/schedule")
    browser_id: int = Field(default=0, description="执行目标浏览器ID")

    started_at: UnixTime = Field(description="开始时间（秒级时间戳）")
    updated_at: UnixTime = Field(description="进度快照时间（秒级时间戳）")
    finished_at: OptionalUnixTime = Field(default=None, description="结束时间（秒级时间戳）")
    duration_ms: float = Field(default=0.0, description="已运行/总耗时（毫秒）")

    total_steps: int = Field(default=0, description="顶层步骤总数")
    completed_steps: int = Field(default=0, description="已完成步骤数")
    current_step_index: int = Field(default=0, description="当前步骤下标（0 基）")
    current_step_name: str = Field(default="", description="当前步骤显示名")
    success_count: int = Field(default=0, description="成功步骤数（恢复时累计旧运行）")
    failed_count: int = Field(default=0, description="失败步骤数")

    loop_action_name: str = Field(default="", description="当前循环步骤显示名")
    loop_index: int = Field(default=0, description="当前循环轮次（1 基，0=未进入循环）")
    loop_total: int = Field(default=0, description="当前循环总轮次；0=未知")
    loop_total_known: bool = Field(
        default=True, description="循环总轮次是否可知（condition 循环为 False）"
    )
    # 第二条进度条：当前这一轮循环体内的动作执行进度
    loop_body_completed: int = Field(
        default=0, description="本轮循环体内已完成动作数"
    )
    loop_body_total: int = Field(
        default=0, description="本轮循环体动作总数（0=非循环/未知）"
    )
    loop_body_step_name: str = Field(
        default="", description="本轮循环体内当前正在执行的动作名"
    )

    resume_of: str | None = Field(default=None, description="恢复来源的运行ID")
    resume_count: int = Field(default=0, description="连续恢复次数")
    error_message: str | None = Field(default=None, description="失败/中断原因")

    @property
    def is_terminal(self) -> bool:
        """是否已到终态（终态帧推送后服务端关闭 SSE）"""
        return self.status != WorkflowRunStatusEnum.RUNNING.value

    @classmethod
    def from_run_record(
        cls, run: "WorkflowRunRecord", workflow_name: str = ""
    ) -> "WorkflowRunProgressData":
        """由运行记录（DB 模型）构造快照（首帧 / 终态帧）"""
        now = datetime.now()
        updated = run.progress_updated_at or run.finished_at or now
        return cls(
            run_id=run.run_id,
            workflow_id=run.workflow_id,
            workflow_name=workflow_name,
            status=str(run.status),
            trigger_source=str(run.trigger_source),
            browser_id=run.browser_id,
            started_at=run.started_at,
            updated_at=updated,
            finished_at=run.finished_at,
            duration_ms=run.duration_ms,
            total_steps=run.total,
            completed_steps=run.completed_steps,
            current_step_index=run.current_step_index,
            current_step_name=run.current_step_name,
            success_count=run.success_count,
            failed_count=run.failed_count,
            loop_action_name=run.loop_action_name,
            loop_index=run.loop_index,
            loop_total=run.loop_total,
            loop_total_known=run.loop_total > 0 or not run.loop_action_name,
            loop_body_completed=run.loop_body_completed,
            loop_body_total=run.loop_body_total,
            loop_body_step_name=run.loop_body_step_name,
            resume_of=run.resume_of,
            resume_count=run.resume_count,
            error_message=run.error_message,
        )


class WorkflowRunLogEvent(SQLModel):
    """运行消息（步骤级明细）

    供前端「运行消息」栏实时展示：每一步的名称、结果、耗时与错误。
    `text` 为后端统一拼好的可读文案，前端可直接展示，也可按字段自行渲染。
    """

    run_id: str = Field(description="运行ID")
    seq: int = Field(description="运行内自增序号（前端去重/断线补齐用）")
    ts: UnixTime = Field(description="产生时间（秒级时间戳）")
    kind: str = Field(
        description=(
            "事件类型: step_started/step_finished/loop_iteration/loop_finished/"
            "body_step_started/body_step_finished/run_finished"
            "（body_step_* 为循环体内动作，只发消息不计步骤）"
        )
    )
    level: str = Field(default="info", description="展示级别: info/success/warn/error")
    text: str = Field(default="", description="可直接展示的一行文案")
    detail: str | None = Field(
        default=None,
        description=(
            "步骤返回内容明细（step_finished 携带动作的返回数据，"
            "如 fetch_external_data 的接口响应；超长按结构裁剪），无内容为 null"
        ),
    )
    action_id: str = Field(default="", description="步骤/循环动作ID")
    action_name: str = Field(default="", description="步骤/循环显示名")
    step_index: int | None = Field(default=None, description="顶层步骤下标（0 基）")
    total_steps: int = Field(default=0, description="顶层步骤总数")
    success: bool | None = Field(default=None, description="结果是否成功（进行中为空）")
    duration_ms: float | None = Field(default=None, description="本步骤耗时（毫秒）")
    error: str | None = Field(default=None, description="失败原因")
    loop_index: int = Field(default=0, description="循环轮次（1 基）")
    loop_total: int = Field(default=0, description="循环总轮次；0=未知")
    depth: int = Field(
        default=0,
        description=(
            "嵌套层级：0=顶层步骤消息；1=最外层循环体内；2=再内一层……"
            "打印是全量的（含嵌套），前端据此逐级缩进/嵌套展示"
        ),
    )


__all__ = [
    "WORKFLOW_RUN_SSE_EVENT_NAME",
    "WORKFLOW_RUN_LOG_SSE_EVENT_NAME",
    "WorkflowRunProgressData",
    "WorkflowRunLogEvent",
]
