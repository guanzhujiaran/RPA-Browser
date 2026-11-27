"""浏览器操作任务模型（异步提交 + 前端轮询）。

为什么需要任务：导入 / 清空要读写浏览器 context，慢站点 / 多页面时能跑几十秒，
超过 nginx 默认 60s 读超时 → 前端收到 504，但操作其实已生效。
接口改为「提交任务立即返回 task_id → 前端轮询状态」，长耗时不再占用 HTTP 请求。

任务只落在进程内存（RPA 单进程单 worker）：重启会丢失任务，但操作本身要么已完成、
要么被中断；前端轮询会拿到「任务不存在或已过期」，不会误以为成功。
"""

from typing import Literal

from sqlmodel import Field, SQLModel

from app.models.runtime.browser_operation import (
    ClearLoginStateBatchResponse,
    ImportCookiesBatchResponse,
)

OperationTaskType = Literal["import_cookies", "clear_login_state"]
OperationTaskStatus = Literal["pending", "running", "success", "failed"]


class OperationTaskCreatedResponse(SQLModel):
    """任务提交响应：前端拿 task_id 轮询"""

    task_id: str = Field(..., description="任务 id")
    task_type: OperationTaskType = Field(..., description="任务类型")
    status: OperationTaskStatus = Field("pending", description="初始状态")
    total: int = Field(0, description="总项数（按浏览器计）")


class OperationTaskStatusRequest(SQLModel):
    """任务状态查询请求"""

    task_id: str = Field(..., min_length=1, description="提交任务时返回的 task_id")


class OperationTaskStatusResponse(SQLModel):
    """任务状态（前端按 1~2s 轮询，success / failed 后可停止）"""

    task_id: str = Field(..., description="任务 id")
    task_type: OperationTaskType = Field(..., description="任务类型")
    status: OperationTaskStatus = Field(..., description="任务状态")
    progress: int = Field(0, description="已完成项数")
    total: int = Field(0, description="总项数")
    message: str = Field("", description="进度说明")
    error: str = Field("", description="失败原因（status=failed 时）")
    import_result: ImportCookiesBatchResponse | None = Field(
        None, description="导入任务的逐项结果（status=success 时）"
    )
    clear_result: ClearLoginStateBatchResponse | None = Field(
        None, description="清空任务的逐项结果（status=success 时）"
    )
    created_at: int = Field(0, description="创建时间（秒级时间戳）")
    updated_at: int = Field(0, description="最后更新时间（秒级时间戳）")


__all__ = [
    "OperationTaskType",
    "OperationTaskStatus",
    "OperationTaskCreatedResponse",
    "OperationTaskStatusRequest",
    "OperationTaskStatusResponse",
]
