"""Workflow 模块 - 自定义（复合）操作请求/响应模型

从 ``app.models/workflow/models.py`` 拆出（原 1141 行单文件）。
历史入口 ``models.py`` 保留为 re-export 门面，既有 import 路径不变。
"""

from __future__ import annotations

from bili_common.models import StrEnumAutoDoc
from app.models.database.workflow.models import BuiltinActionType
from app.models.execution.action_params import VariableSpec
from app.models.execution.condition_models import ConditionRule
from app.models.base.base_sqlmodel import BasePaginationReq
from dataclasses import dataclass
from datetime import datetime
from pydantic import Field
from typing import Any, Dict
from sqlmodel import SQLModel


from app.models.workflow.enums import FilterType, SortBy, SortOrder
from app.models.workflow.mixins import UpstreamSyncFields


class InputVarDefinition(SQLModel):
    """输入变量定义"""

    name: str = Field(description="变量名称")
    type: str = Field(
        default="string", description="变量类型: string/number/boolean/array/object"
    )
    default: Any | None = Field(default=None, description="默认值")
    required: bool = Field(default=False, description="是否必填")
    description: str = Field(default="", description="变量描述")
    items_schema: dict | None = Field(
        default=None,
        description=(
            "数组类型变量的元素结构（JSON Schema，自包含、不含 $ref）："
            "前端据此渲染结构化编辑器；为空时退化为 JSON 文本域"
        ),
    )


class CompositeActionCreateRequest(SQLModel):
    """创建复合操作请求 - 与 CompositeActionModel 数据库模型对齐"""

    name: str = Field(description="操作显示名称（必填）")
    action_type: BuiltinActionType = Field(
        default=BuiltinActionType.COMPOSITE, description="操作类型"
    )
    description: str = Field(default="", description="操作描述")
    icon_series: int = Field(
        default=0, ge=0, le=999999, description="图标系列编号（0 表示默认图标）"
    )
    icon_id: int = Field(
        default=0, ge=0, le=99999999, description="图标在系列内的编号（0 表示默认图标）"
    )
    parameters_schema: list[Dict] = Field(
        default_factory=list, description="参数定义JSON"
    )
    steps: list[Dict] = Field(default_factory=list, description="步骤列表JSON")
    tags: list[str] = Field(default_factory=list, description="标签列表")
    input_vars: list[InputVarDefinition] = Field(
        default_factory=list, description="输入变量定义"
    )
    output_vars: list[str] = Field(default_factory=list, description="输出变量名称列表")
    is_public: bool = Field(default=False, description="是否公开给所有用户")
    timeout: int = Field(default=30000, description="超时时间(毫秒)")
    retry_on_error: bool = Field(default=False, description="错误时重试")
    retry_times: int = Field(default=0, description="重试次数")
    retry_delay: float = Field(default=1.0, description="重试延迟(秒)")
    log_enabled: bool | None = Field(
        default=None, description="是否采集该操作的执行日志（不传=未设置，听从工作流/项目开关）"
    )
    log_debug: bool | None = Field(
        default=None,
        description="调试模式（不传=未设置）：True=强制全量采集（入参/结果/变量全记、成功也记、不截断）",
    )
    log_record_params: bool = Field(
        default=True, description="是否记录变量替换后的入参"
    )
    log_record_result: bool = Field(default=True, description="是否记录执行返回结果")
    log_record_variables: bool = Field(default=False, description="是否记录变量池快照")
    log_only_on_error: bool = Field(default=False, description="仅在执行失败时记录")
    log_max_payload_length: int = Field(default=4000, description="payload 最大字符数")
    log_retention_days: int = Field(default=30, description="日志保留天数，0 表示永久")


class CompositeActionUpdateRequest(SQLModel):
    """更新复合操作请求"""

    action_id: int = Field(description="操作的雪花 action_id")
    name: str | None = Field(default=None, description="新名称")
    description: str | None = Field(default=None, description="新描述")
    icon_series: int | None = Field(
        default=None, ge=0, le=999999, description="图标系列编号（不传表示不修改）"
    )
    icon_id: int | None = Field(
        default=None,
        ge=0,
        le=99999999,
        description="图标在系列内的编号（不传表示不修改）",
    )
    parameters_schema: list[Dict] | None = Field(
        default=None, description="参数定义JSON"
    )
    steps: list[Dict] | None = Field(default=None, description="步骤列表JSON")
    tags: list[str] | None = Field(default=None, description="标签列表")
    input_vars: list[InputVarDefinition] | None = Field(
        default=None, description="输入变量定义"
    )
    output_vars: list[str] | None = Field(default=None, description="输出变量名称列表")
    is_enabled: bool | None = Field(default=None, description="是否启用")
    is_public: bool | None = Field(default=None, description="是否公开")
    timeout: int | None = Field(default=None, description="超时时间(毫秒)")
    retry_on_error: bool | None = Field(default=None, description="错误时重试")
    retry_times: int | None = Field(default=None, description="重试次数")
    retry_delay: float | None = Field(default=None, description="重试延迟(秒)")
    log_enabled: bool | None = Field(
        default=None, description="是否采集该操作的执行日志"
    )
    log_debug: bool | None = Field(
        default=None,
        description="调试模式：True=强制全量采集（入参/结果/变量全记、成功也记、不截断）",
    )
    log_record_params: bool | None = Field(
        default=None, description="是否记录变量替换后的入参"
    )
    log_record_result: bool | None = Field(
        default=None, description="是否记录执行返回结果"
    )
    log_record_variables: bool | None = Field(
        default=None, description="是否记录变量池快照"
    )
    log_only_on_error: bool | None = Field(
        default=None, description="仅在执行失败时记录"
    )
    log_max_payload_length: int | None = Field(
        default=None, description="payload 最大字符数"
    )
    log_retention_days: int | None = Field(
        default=None, description="日志保留天数，0 表示永久"
    )
    auto_sync_upstream: bool | None = Field(
        default=None,
        description="是否开启上游自动同步（不传表示不修改；仅 Fork 资源有意义）",
    )
    browser_ids: list[int] | None = Field(
        default=None,
        description="执行目标浏览器ID列表（不传表示不修改；传空列表=回到单浏览器模式）",
    )
    concurrent_exec: bool | None = Field(
        default=None,
        description="多浏览器时是否并发执行（不传表示不修改）",
    )


class CompositeActionListRequest(BasePaginationReq):
    """获取复合操作列表请求"""

    filter_type: FilterType = Field(default=FilterType.ALL, description="筛选类型")
    sort_by: SortBy = Field(default=SortBy.UPDATED_AT, description="排序字段")
    sort_order: SortOrder = Field(default=SortOrder.DESC, description="排序方向")
    name: str | None = Field(default=None, description="按名称搜索（模糊匹配）")
    tag: str | None = Field(default=None, description="按标签筛选")
    tag_exact: bool = Field(
        default=True, description="标签精确匹配（true）或模糊匹配（false）"
    )


class TagSearchRequest(SQLModel):
    """标签搜索请求"""

    keyword: str | None = Field(default=None, description="搜索关键字")
    filter_type: FilterType = Field(
        default=FilterType.ALL, description="筛选类型（用于统计标签下操作数量）"
    )


class TagWithCount(SQLModel):
    """标签及其关联操作数量"""

    name: str
    count: int


class NameSearchRequest(SQLModel):
    """操作名称搜索请求（用于输入联想）"""

    keyword: str | None = Field(default=None, description="搜索关键字")
    filter_type: FilterType = Field(default=FilterType.ALL, description="筛选类型")


class CompositeActionDetailResponse(UpstreamSyncFields):
    """复合操作详情响应"""

    action_id: int
    name: str
    version: str
    action_type: str
    description: str
    icon_series: int = 0
    icon_id: int = 0
    mid: str = ""
    parameters_schema: list[Dict]
    steps: list[Dict]
    tags: list[str]
    input_vars: list[InputVarDefinition]
    output_vars: list[str]
    input_variables: list[VariableSpec] = Field(
        default_factory=list,
        description="聚合步骤树得到的输入变量契约（消费 − 内部自产）",
    )
    output_variables: list[VariableSpec] = Field(
        default_factory=list,
        description="聚合步骤树得到的输出变量契约（产出槽位）",
    )
    is_enabled: bool
    is_public: bool = False
    timeout: int
    retry_on_error: bool
    retry_times: int
    retry_delay: float
    log_enabled: bool | None = None
    log_debug: bool | None = None
    log_record_params: bool = True
    log_record_result: bool = True
    log_record_variables: bool = False
    log_only_on_error: bool = False
    log_max_payload_length: int = 4000
    log_retention_days: int = 30
    likes_count: int = 0
    reports_count: int = 0
    is_verified: bool = False
    forks_count: int = 0
    forked_from_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CompositeActionListItemResponse(UpstreamSyncFields):
    """复合操作列表项响应"""

    id: int = Field(description="表主键")
    action_id: int = Field(description="雪花 action_id（be-message 互动 rpa_action 的 bizId）")
    name: str
    action_type: str
    description: str
    icon_series: int = 0
    icon_id: int = 0
    steps_count: int
    tags: list[str] = Field(default_factory=list)
    is_enabled: bool
    is_public: bool = False
    log_enabled: bool | None = None
    log_debug: bool | None = None
    likes_count: int = 0
    reports_count: int = 0
    is_verified: bool = False
    forks_count: int = 0
    forked_from_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CompositeActionCreateResponse(SQLModel):
    """创建复合操作响应"""

    id: int
    action_id: int
    name: str


class CompositeActionGetRequest(SQLModel):
    """获取自定义操作详情请求"""

    action_id: int = Field(description="操作的雪花 action_id")


class CompositeActionDeleteRequest(SQLModel):
    """删除自定义操作请求"""

    action_id: int = Field(description="操作的雪花 action_id")


class ActionForkRequest(SQLModel):
    """Fork 自定义操作请求"""

    action_id: int = Field(description="原操作的雪花 action_id")
    new_name: str | None = Field(
        default=None, description="新名称，如果不提供则使用原名称 + ' (Fork)'"
    )
    auto_sync_upstream: bool = Field(
        default=False, description="是否开启上游自动同步（默认关闭）"
    )


class ActionForkResponse(SQLModel):
    """Fork 自定义操作响应"""

    action_id: int
    name: str
    forked_from: int = Field(description="Fork 自哪个操作（雪花 action_id）")


class ActionSyncRequest(SQLModel):
    """同步自定义操作到上游最新版本请求"""

    action_id: int = Field(description="当前 Fork 副本的雪花 action_id")


class ActionSyncResponse(SQLModel):
    """同步自定义操作响应"""

    action_id: int
    updated: list[str] = Field(
        default_factory=list, description="内容已更新的动作名称列表"
    )
    created: list[str] = Field(
        default_factory=list, description="新 Fork 的动作名称列表"
    )
    unavailable: list[int] = Field(
        default_factory=list, description="上游已不可用的动作 ID 列表"
    )
    local_overwritten: bool = Field(
        default=False, description="本次同步是否覆盖了本地修改（动作内容）"
    )

