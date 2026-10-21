"""操作结果模型（动作执行产出的强类型定义）

从 ``action_params`` 拆出。包含：

- 各内置操作的结果模型（``ClickResult`` ~ ``CompositeResult``）与联合类型
  ``AllActionResult``；
- ``BUILTIN_ACTION_RESULT_MAP``：动作类型 → 结果模型的注册表。
"""

from __future__ import annotations

from typing import Any, Type

from pydantic import Field
from sqlmodel import SQLModel

from app.models.database.workflow.resource_record import ResourceProcessStatus


# region 操作结果模型


class ClickResult(SQLModel):
    """点击操作结果"""

    clicked: bool = Field(default=True, description="是否点击成功")


class InputResult(SQLModel):
    """输入操作结果"""

    value_length: int = Field(default=0, description="输入文本长度")


class ScrollResult(SQLModel):
    """滚动操作结果"""

    scrolled: bool = Field(default=True, description="是否滚动成功")


class WaitResult(SQLModel):
    """等待操作结果"""

    element_found: bool = Field(default=True, description="是否等待到了目标元素")


class HoverResult(SQLModel):
    """悬停操作结果"""

    hovered: bool = Field(default=True, description="是否悬停成功")


class GetTextResult(SQLModel):
    """获取元素文本结果（text 在前，保持既有 output_vars 映射不变）"""

    text: str = Field(default="", description="多条文本按 separator 拼接后的内容")
    items: list[str] = Field(
        default_factory=list,
        description="逐条文本列表（按条目读取时一个条目一条），可直接给 random_pick 使用",
    )


class GetWindowResult(SQLModel):
    """获取 window 属性结果"""

    value: str = Field(default="", description="属性的字符串值（property_path 模式）")
    values: dict[str, str] = Field(
        default_factory=dict, description="对象所有字段的非空值（object_name 模式）"
    )


class NavigateResult(SQLModel):
    """导航操作结果"""

    status: int | None = Field(default=None, description="HTTP 状态码")


class NewPageResult(SQLModel):
    """新建页面操作结果"""

    page_created: bool = Field(default=False, description="页面是否创建成功")
    page_count: int = Field(default=0, description="当前页面数")
    status: int | None = Field(default=None, description="HTTP 状态码")


class ScreenshotResult(SQLModel):
    """截图操作结果"""

    format: str = Field(default="png", description="截图格式")
    size: int = Field(default=0, description="图片大小(字节)")
    base64: str = Field(default="", description="Base64 编码的图片数据")


class LLMResult(SQLModel):
    """LLM 操作结果"""

    content: str = Field(default="", description="回复文本内容")
    role: str = Field(default="assistant", description="角色")
    model: str = Field(default="", description="实际使用的模型名称")
    usage: dict = Field(default_factory=dict, description="token 使用量")
    # 结构化输出
    is_structured: bool = Field(default=False, description="是否为结构化输出模式")
    structured_data: dict | None = Field(
        default=None, description="结构化输出数据（response_schema 提供时有效）"
    )


class FetchExternalDataResult(SQLModel):
    """获取外部数据操作结果"""

    status_code: int = Field(default=0, description="HTTP 状态码")
    data: Any | None = Field(
        default=None,
        description="响应数据（JSON 解析后的字典/列表，无法解析时为 None）",
    )
    text: str = Field(default="", description="响应文本（原始字符串）")
    headers: dict[str, str] = Field(
        default_factory=dict, description="响应头（键值对）"
    )
    url: str = Field(default="", description="最终请求 URL（可能经过重定向）")
    elapsed: float = Field(default=0.0, description="请求耗时（秒）")
    is_json: bool = Field(default=False, description="响应体是否为 JSON 格式")


class MarkResourceResultResult(SQLModel):
    """记录资源处理结果操作结果"""

    recorded: bool = Field(default=True, description="是否已写入记录")
    resource_type: str = Field(default="", description="资源类型")
    resource_id: str = Field(default="", description="资源主ID")
    success: bool = Field(default=True, description="记录的处理结果")
    status: ResourceProcessStatus = Field(
        default=ResourceProcessStatus.SUCCESS, description="写入的记录状态"
    )
    process_count: int = Field(
        default=0, description="写入后该资源的累计处理次数"
    )


class RandomPickResult(SQLModel):
    """随机取值操作结果"""

    value: str = Field(default="", description="随机选中的元素（统一转字符串）")
    index: int = Field(default=-1, description="选中元素在列表中的下标")
    total: int = Field(default=0, description="列表长度")


class PrintResult(SQLModel):
    """打印操作结果"""

    message: str = Field(default="", description="打印的内容")


class ShuffleListResult(SQLModel):
    """打乱列表操作结果"""

    total: int = Field(default=0, description="打乱后的列表长度")


class SortListResult(SQLModel):
    """列表排序操作结果"""

    total: int = Field(default=0, description="排序后的列表长度")


class PickLineResult(SQLModel):
    """按行随机取值操作结果"""

    value: str = Field(default="", description="随机选中的一行文本")
    total: int = Field(default=0, description="切分并过滤空行后的行数")


class BiliReserveResult(SQLModel):
    """预约抽奖操作结果"""

    value: bool = Field(default=False, description="是否参与成功（含此前已参与）")
    joined: bool = Field(default=False, description="是否参与成功")
    already: bool = Field(default=False, description="此前是否已参与")
    found: bool = Field(default=False, description="是否定位到预约卡片")


class LoopResult(SQLModel):
    """循环操作结果"""
    iterations: int = Field(default=0, description="迭代次数")
    total_results: int = Field(default=0, description="总结果数")
    details: list[dict] = Field(default_factory=list, description="详细结果")
    results: list[dict] = Field(default_factory=list, description="结果列表")
    message: str | None = Field(default=None, description="提示消息")
    was_broken: bool = Field(
        default=False, description="是否由 break_condition 触发中断"
    )
    was_continued: bool = Field(
        default=False, description="是否由 continue_condition 触发跳过"
    )


class IfElseResult(SQLModel):
    """条件分支操作结果"""

    branch_taken: str = Field(default="", description="执行的分支")
    results: list[dict] = Field(default_factory=list, description="分支结果")
    branch: str = Field(default="", description="分支名称")
    executed: bool = Field(default=False, description="是否已执行")
    message: str | None = Field(default=None, description="提示消息")


class CompositeResult(SQLModel):
    """复合操作结果"""

    total_steps: int = Field(default=0, description="总步骤数")
    success_count: int = Field(default=0, description="成功步骤数")
    results: list[dict] = Field(default_factory=list, description="各步骤结果")


AllActionResult = (
    ClickResult
    | InputResult
    | ScrollResult
    | WaitResult
    | HoverResult
    | GetTextResult
    | GetWindowResult
    | NavigateResult
    | NewPageResult
    | ScreenshotResult
    | LLMResult
    | FetchExternalDataResult
    | MarkResourceResultResult
    | RandomPickResult
    | PrintResult
    | ShuffleListResult
    | SortListResult
    | PickLineResult
    | BiliReserveResult
    | LoopResult
    | IfElseResult
    | CompositeResult
)

# endregion

# 动作类型 → 结果模型注册表。key 为 BuiltinActionType 成员的 value 字符串
# （StrEnum 与其 value 哈希相等，枚举查询等价命中）；不用枚举作 key 的原因
# 同 action_param_models：避免与 builtin_action_types 形成循环导入。
BUILTIN_ACTION_RESULT_MAP: dict[str, Type[AllActionResult]] = {
    "click": ClickResult,
    "input": InputResult,
    "scroll": ScrollResult,
    "wait": WaitResult,
    "hover": HoverResult,
    "get_text": GetTextResult,
    "get_window": GetWindowResult,
    "navigate": NavigateResult,
    "new_page": NewPageResult,
    "screenshot": ScreenshotResult,
    "llm": LLMResult,
    "fetch_external_data": FetchExternalDataResult,
    "mark_resource_result": MarkResourceResultResult,
    "random_pick": RandomPickResult,
    "print": PrintResult,
    "shuffle_list": ShuffleListResult,
    "sort_list": SortListResult,
    "pick_line": PickLineResult,
    "bili_reserve": BiliReserveResult,
    "loop": LoopResult,
    "if_else": IfElseResult,
    "composite": CompositeResult,
}
