"""动作落库载荷（与导出 JSON 字段对齐）。

只挑「创建 / 更新」认的字段，动作 JSON 里的 DB 冗余字段
（mid / id / created_at / version / likes_count / action_id ...）由 pydantic 自动忽略。
"""

from __future__ import annotations

from pydantic import BaseModel, Field, JsonValue, ValidationError


class ActionSavePayloadError(ValueError):
    """载荷不合法（缺 name / steps 等），可直接回给调用方。"""


class ActionSavePayload(BaseModel):
    """动作落库载荷。"""

    name: str = Field(min_length=1, description="动作显示名称")
    description: str = Field(default="", description="动作描述")
    icon_series: int = Field(default=0, description="图标系列")
    icon_id: int = Field(default=0, description="图标编号")
    parameters_schema: list[JsonValue] = Field(
        default_factory=list, description="参数定义"
    )
    steps: list[JsonValue] = Field(description="步骤列表")
    tags: list[str] = Field(default_factory=list, description="标签")
    input_vars: list[JsonValue] = Field(default_factory=list, description="输入变量定义")
    output_vars: list[str] = Field(default_factory=list, description="输出变量名")
    timeout: int = Field(default=30000, description="超时（毫秒）")
    retry_on_error: bool = Field(default=False, description="失败是否重试")
    retry_times: int = Field(default=0, description="重试次数")
    retry_delay: float = Field(default=1.0, description="重试间隔（秒）")
    log_enabled: bool | None = Field(default=None, description="日志采集开关（三态）")
    log_debug: bool | None = Field(
        default=None,
        description="调试模式（三态）：True=强制全量采集（入参/结果/变量全记、成功也记、不截断）",
    )
    log_record_params: bool = Field(default=True, description="记录入参")
    log_record_result: bool = Field(default=True, description="记录结果")
    log_record_variables: bool = Field(default=False, description="记录变量池")
    log_only_on_error: bool = Field(default=False, description="仅失败时记录")
    log_max_payload_length: int = Field(default=4000, description="大字段截断长度")
    log_retention_days: int = Field(default=30, description="日志保留天数")

    @classmethod
    def from_content(cls, content: object) -> ActionSavePayload:
        """从动作 JSON 内容构造载荷；结构不合法时抛 ``ActionSavePayloadError``。"""
        if not isinstance(content, dict):
            raise ActionSavePayloadError("content 必须是动作 JSON 对象（含 name / steps）")
        try:
            payload = cls.model_validate(content)
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
                for error in exc.errors()
            )
            raise ActionSavePayloadError(f"动作定义不合法: {details}") from exc
        if not payload.steps:
            raise ActionSavePayloadError("steps 为空：拒绝落库（动作至少需要一个步骤）")
        return payload

    def step_action_ids(self) -> list[str]:
        """顶层步骤的 action_id 序列（用于回读比对）。"""
        return [
            str(step.get("action_id", ""))
            for step in self.steps
            if isinstance(step, dict)
        ]
