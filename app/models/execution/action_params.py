from __future__ import annotations
from langchain_core.messages import AIMessage

import contextlib
from enum import StrEnum
from typing import Any, Dict, Generic, Literal, Type, TypeVar
from pydantic import Field, TypeAdapter, ValidationError, field_validator, model_validator
from sqlmodel import SQLModel

from app.models.execution.enums import (
    WaitUntilEnum,
    MouseButtonEnum,
    ElementStateEnum,
    ScreenshotTypeEnum,
    KeyboardModifierEnum,
)
from app.models.execution.condition_models import ConditionRule


class BuiltinActionDesc(StrEnum):
    """内置操作描述"""
    CLICK = "点击元素"
    INPUT = "输入文本"
    WAIT = "等待元素出现"
    SCROLL = "滚动到元素"
    NAVIGATE = "导航到 URL"
    SCREENSHOT = "截图"
    LLM = "使用 LLM 生成文本"
    HOVER = "悬停在元素上"
    NEW_PAGE = "打开新页面"
    GET_TEXT = "获取元素文本"

    IF_ELSE = "根据条件执行 true/false 分支"
    LOOP = "循环执行操作"
    COMPOSITE = "执行复合操作"


class BuiltinActionName(StrEnum):
    """内置操作名称"""
    CLICK = "点击"
    INPUT = "输入"
    WAIT = "等待"
    SCROLL = "滚动"
    NAVIGATE = "导航"
    SCREENSHOT = "截图"
    LOOP = "循环"
    COMPOSITE = "复合操作"
    LLM = "LLM"
    IF_ELSE = "条件判断"
    HOVER = "悬停"
    NEW_PAGE = "新页面"
    GET_TEXT = "获取文本"


class BuiltinActionType(StrEnum):
    """内置操作类型"""
    CLICK = "click"
    INPUT = "input"
    WAIT = "wait"
    SCROLL = "scroll"
    NAVIGATE = "navigate"
    SCREENSHOT = "screenshot"
    LLM = "llm"
    HOVER = "hover"
    NEW_PAGE = "new_page"
    GET_TEXT = "get_text"

    LOOP = "loop"
    COMPOSITE = "composite"  # 复合操作 需要拆分开开单独执行
    IF_ELSE = "if_else"

    @property
    def nameDisplay(self) -> str:
        return BuiltinActionName[self.name]

    @property
    def descDisplay(self) -> str:
        return BuiltinActionDesc[self.name]

    @property
    def params_model(self) -> Type[AllActionParams]:
        return BUILTIN_ACTION_PARAMS_MAP.get(self.value, CompositeParams)

    @property
    def result_model(self) -> Type[AllActionResult]:
        """返回该操作类型对应的结果模型"""
        return BUILTIN_ACTION_RESULT_MAP.get(self.value, CompositeResult)

    @property
    def metadata(self) -> ActionMetadata:
        """返回该操作类型的 ActionMetadata"""
        params_model = self.params_model
        json_schema: dict[str, Any] = {}
        parameters: list[ActionParameter] = []
        if hasattr(params_model, 'model_json_schema'):
            with contextlib.suppress(Exception):
                json_schema = params_model.model_json_schema()
                properties = json_schema.get('properties', {})
                for prop_name, prop_schema in properties.items():
                    parameters.append(ActionParameter(
                        name=prop_name,
                        json_schema=prop_schema,
                    ))
        return ActionMetadata(
            id=self,
            name=self.nameDisplay,
            type=self,
            description=self.descDisplay,
            parameters=parameters,
            json_schema=json_schema,
        )


class ActionParameter(SQLModel):
    """操作参数定义（内部使用）"""
    name: str = Field(description="参数名称")
    json_schema: Dict = Field(description="完整的 JSON Schema")


class ActionMetadata(SQLModel):
    """操作元数据（内部使用）"""
    id: BuiltinActionType = Field(description="操作ID")
    name: str = Field(description="操作名称")
    type: BuiltinActionType = Field(description="操作类型")
    description: str = Field(default="", description="操作描述")
    parameters: list[ActionParameter] = Field(
        default_factory=list, description="参数列表")
    json_schema: Dict | None = Field(
        default=None, description="完整的 JSON Schema")
    timeout: int = Field(default=30000, description="超时时间(毫秒)")
    retry_on_error: bool = Field(default=False, description="错误时重试")
    retry_times: int = Field(default=0, description="重试次数")
    retry_delay: float = Field(default=1.0, description="重试延迟(秒)")
    requires_browser: bool = Field(default=True, description="是否需要浏览器上下文")


class ActionMetadataResponse(SQLModel):
    """操作元数据响应（API 返回）"""
    action_id: str = Field(description="预设操作ID")
    action_type: BuiltinActionType = Field(description="操作类型")
    name: str = Field(default="", description="操作中文名")
    json_schema: Dict = Field(description="完整的 JSON Schema")


# region 操作参数


class Position(SQLModel):
    """坐标位置模型"""
    x: float = Field(description="X 坐标（像素）")
    y: float = Field(description="Y 坐标（像素）")


class BaseActionParams(SQLModel):
    """操作参数基类 - 所有操作参数模型继承此类"""
    timeout: float = Field(default=30000, ge=0, le=300000,
                           description="最大等待时间（毫秒），默认为 30000。传入 0 禁用超时")


class SelectorActionParams(BaseActionParams):
    """带元素选择器的操作参数基类"""
    selector: str | None = Field(
        default=None, max_length=500, description="用于定位元素的选择器")


class MouseActionParams(SelectorActionParams):
    """鼠标操作参数基类"""
    position: Position | None = Field(
        default=None, description="相对于元素 padding box 左上角的坐标位置")
    modifiers: list[KeyboardModifierEnum] | None = Field(
        default=None, description="要按下的修饰键 (Alt/Control/Meta/Shift)")
    force: bool = Field(default=False, description="是否绕过可操作性检查，默认为 false")

    @field_validator("position", mode="before")
    @classmethod
    def normalize_position(cls, v: Any) -> Any:
        """将 {\"x\": null, \"y\": null} 形式的空坐标转为 None，
        避免 Pydantic 构造 Position(x=None, y=None) 时因 float 不可为 None 而验证失败。"""
        if isinstance(v, dict) and v.get("x") is None and v.get("y") is None:
            return None
        return v


class ClickParams(MouseActionParams):
    """点击操作参数 - 对应 locator.click()"""
    button: MouseButtonEnum = Field(
        default=MouseButtonEnum.LEFT, description="要按下的鼠标按钮 (left/right/middle)，默认为 left")
    click_count: int = Field(default=1, ge=1, le=3,
                             description="点击次数，默认为 1。参见 UIEvent.detail")
    delay: float = Field(default=0, ge=0, le=10000,
                         description="mousedown 和 mouseup 之间等待的时间（毫秒），默认为 0")
    trial: bool = Field(
        default=False, description="仅执行可操作性检查而不执行实际操作，默认为 false")


class InputParams(BaseActionParams):
    """输入操作参数 - 对应 locator.fill()"""
    selector: str = Field(
        max_length=500, description="用于定位输入框元素的选择器")
    value: str = Field(max_length=10000, description="要输入的文本内容")
    force: bool = Field(default=False, description="是否绕过可操作性检查，默认为 false")


class NavigateParams(BaseActionParams):
    """导航操作参数"""
    url: str = Field(max_length=2048, description="要导航到的 URL 地址")
    wait_until: WaitUntilEnum = Field(
        default=WaitUntilEnum.LOAD, description="导航成功前的等待条件")
    timeout: float = Field(default=30000, ge=1000, le=300000,
                           description="导航操作的超时时间（毫秒）")


class NewPageParams(BaseActionParams):
    """新建页面操作参数"""
    url: str | None = Field(
        default=None, max_length=2048, description="新页面的初始 URL")
    wait_until: WaitUntilEnum = Field(
        default=WaitUntilEnum.LOAD, description="导航等待条件（仅在提供 url 时生效）")
    timeout: float = Field(default=30000, ge=1000, le=300000,
                           description="导航超时时间（仅在提供 url 时生效）")


class ScrollParams(SelectorActionParams):
    """滚动操作参数 - 对应 locator.scroll_into_view_if_needed()"""
    pass


class WaitParams(SelectorActionParams):
    """
    等待操作参数 - 对应 locator.wait_for()
    如果元素已存在，立即返回True，否则等待超时后返回False
    """
    state: ElementStateEnum = Field(
        default=ElementStateEnum.VISIBLE, description="等待的元素状态 (visible/hidden/attached/detached)")


class HoverParams(MouseActionParams):
    """悬停操作参数 - 对应 locator.hover()"""
    pass


class GetTextParams(SelectorActionParams):
    """获取元素文本参数 - 对应 locator.inner_text()"""
    pass


class ScreenshotParams(SelectorActionParams):
    """截图操作参数 - 对应 locator.screenshot() 或 page.screenshot()"""
    type: ScreenshotTypeEnum = Field(
        default=ScreenshotTypeEnum.PNG, description="截图格式 (png/jpeg)")
    quality: int = Field(default=80, ge=1, le=100,
                         description="JPEG 图片质量（1-100），仅在 type=jpeg 时有效")
    full_page: bool = Field(
        default=False, description="是否截取整个可滚动页面（仅 page.screenshot 支持）")
    omit_background: bool = Field(
        default=False, description="是否隐藏默认白色背景并截取透明背景（仅 png 格式支持）")


class LLMParams(BaseActionParams):
    """LLM 对话操作参数"""
    server_url: str = Field(max_length=2048, description="API 服务器地址")
    api_key: str = Field(max_length=500, description="API 密钥")
    model: str = Field(max_length=200, description="模型名称")
    prompt: str = Field(default="", max_length=100000,
                        description="单轮对话 prompt")
    system_prompt: str = Field(
        default="", max_length=10000, description="系统提示词")
    temperature: float = Field(
        default=0.7, ge=0.0, le=2.0, description="温度参数 0-2")
    max_tokens: int = Field(default=2048, ge=1, le=100000,
                            description="最大生成的 token 数")
    timeout: float = Field(default=120000, ge=1000,
                           le=600000, description="请求超时时间(毫秒)")


class LoopParams(BaseActionParams):
    """循环控制流操作参数"""
    items: list[Any] | None = Field(default=None, description="要遍历的列表")
    count: int = Field(default=1, ge=1, le=10000, description="固定循环次数")
    loop_count: int | None = Field(default=None, description="循环次数")
    loop_while: str | None = Field(default=None, description="循环条件")
    loop_var: str | None = Field(default=None, description="循环变量")
    loopBranch: list['WorkflowStep'] | None = Field(
        default=None, description="循环分支步骤")
    break_condition: str | None = Field(
        default=None, max_length=500, description="break 条件表达式，每步执行后评估，为真时跳出整个循环")
    continue_condition: str | None = Field(
        default=None, max_length=500, description="continue 条件表达式，每步执行后评估，为真时跳过当前迭代剩余步骤")


class IfElseParams(BaseActionParams):
    """条件分支控制流操作参数"""
    condition: ConditionRule = Field(description="条件规则（结构化条件，不使用 eval）")
    TrueBranch: list['WorkflowStep'] | None = Field(
        default=None, description="真分支步骤")
    FalseBranch: list['WorkflowStep'] | None = Field(
        default=None, description="假分支步骤")


class CompositeParams(BaseActionParams):
    """复合操作参数"""
    steps: list['WorkflowStep'] = Field(
        default_factory=list, description="复合操作步骤")


AllActionParams = ClickParams | InputParams | NavigateParams | NewPageParams | ScrollParams | WaitParams | HoverParams | GetTextParams | ScreenshotParams | LLMParams | LoopParams | IfElseParams | CompositeParams

# endregion

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
    """获取元素文本结果"""
    text: str = Field(default="", description="元素的文本内容")


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
    content: str = Field(default="", description="回复内容")
    role: str = Field(default="assistant", description="角色")
    model: str = Field(default="", description="模型名称")
    usage: Dict = Field(default_factory=dict, description="token 使用量")
    raw_response: AIMessage = Field(description="原始响应")


class LoopResult(SQLModel):
    """循环操作结果"""
    iterations: int = Field(default=0, description="迭代次数")
    total_results: int = Field(default=0, description="总结果数")
    details: list[Dict] = Field(default_factory=list, description="详细结果")
    results: list[Dict] = Field(default_factory=list, description="结果列表")
    message: str | None = Field(default=None, description="提示消息")
    was_broken: bool = Field(default=False, description="是否由 break_condition 触发中断")
    was_continued: bool = Field(default=False, description="是否由 continue_condition 触发跳过")


class IfElseResult(SQLModel):
    """条件分支操作结果"""
    branch_taken: str = Field(default="", description="执行的分支")
    results: list[Dict] = Field(default_factory=list, description="分支结果")
    branch: str = Field(default="", description="分支名称")
    executed: bool = Field(default=False, description="是否已执行")
    message: str | None = Field(default=None, description="提示消息")


class CompositeResult(SQLModel):
    """复合操作结果"""
    total_steps: int = Field(default=0, description="总步骤数")
    success_count: int = Field(default=0, description="成功步骤数")
    results: list[Dict] = Field(default_factory=list, description="各步骤结果")


AllActionResult = ClickResult | InputResult | ScrollResult | WaitResult | HoverResult | GetTextResult | NavigateResult | NewPageResult | ScreenshotResult | LLMResult | LoopResult | IfElseResult | CompositeResult

BUILTIN_ACTION_RESULT_MAP: Dict[str, Type[AllActionResult]] = {
    BuiltinActionType.CLICK: ClickResult,
    BuiltinActionType.INPUT: InputResult,
    BuiltinActionType.SCROLL: ScrollResult,
    BuiltinActionType.WAIT: WaitResult,
    BuiltinActionType.HOVER: HoverResult,
    BuiltinActionType.GET_TEXT: GetTextResult,
    BuiltinActionType.NAVIGATE: NavigateResult,
    BuiltinActionType.NEW_PAGE: NewPageResult,
    BuiltinActionType.SCREENSHOT: ScreenshotResult,
    BuiltinActionType.LLM: LLMResult,
    BuiltinActionType.LOOP: LoopResult,
    BuiltinActionType.IF_ELSE: IfElseResult,
    BuiltinActionType.COMPOSITE: CompositeResult,
}

# endregion


class PluginConfig(SQLModel):
    """工作流插件配置 - 单个插件的运行时配置"""
    plugin_id: str = Field(description="插件ID")
    config_params: Dict = Field(
        default_factory=dict, description="插件配置参数")
    hook_type: str = Field(
        description="钩子类型: before_action/after_action/on_success/on_error/on_timeout")
    priority: int = Field(default=100, description="优先级")


BUILTIN_ACTION_PARAMS_MAP: Dict[str, Type[AllActionParams]] = {
    BuiltinActionType.CLICK: ClickParams,
    BuiltinActionType.INPUT: InputParams,
    BuiltinActionType.SCROLL: ScrollParams,
    BuiltinActionType.WAIT: WaitParams,
    BuiltinActionType.HOVER: HoverParams,
    BuiltinActionType.GET_TEXT: GetTextParams,
    BuiltinActionType.NAVIGATE: NavigateParams,
    BuiltinActionType.NEW_PAGE: NewPageParams,
    BuiltinActionType.SCREENSHOT: ScreenshotParams,
    BuiltinActionType.LLM: LLMParams,
    BuiltinActionType.LOOP: LoopParams,
    BuiltinActionType.IF_ELSE: IfElseParams,
    BuiltinActionType.COMPOSITE: CompositeParams,
}


P = TypeVar('P')


def _fill_action_type_impl(data: Any) -> Any:
    """递归处理 children 中的 action_type 填充。"""
    if isinstance(data, dict):
        if not data.get('action_type') and data.get('action_id'):
            data['action_type'] = data['action_id']
        if 'children' in data and data['children']:
            data['children'] = [
                _fill_action_type_impl(item) if isinstance(
                    item, dict) else item
                for item in data['children']
            ]
    return data


class BaseWorkflowStep(SQLModel, Generic[P]):
    """工作流步骤基类 - 共享字段 + execution_engine 运行时字段"""
    action_id: str
    params: P | None = None
    retry: int = 0
    input_vars: Dict | None = None
    output_vars: list[str] | None = None
    timeout: int | None = None
    children: list[WorkflowStep] | None = None
    # execution_engine 运行时字段
    condition: ConditionRule | None = None
    loop_count: int | None = None
    loop_while: str | None = None
    loop_until: str | None = None
    output_var: str | None = None

    @model_validator(mode='before')
    @classmethod
    def _fill_action_type(cls, data: Any) -> Any:
        """若 action_type 缺失，从 action_id 自动填充，确保 discriminated union 能匹配。"""
        return _fill_action_type_impl(data)


class ClickWorkflowStep(BaseWorkflowStep[ClickParams]):
    """点击步骤"""
    action_type: Literal[BuiltinActionType.CLICK] = BuiltinActionType.CLICK  # type: ignore[assignment]


class InputWorkflowStep(BaseWorkflowStep[InputParams]):
    """输入步骤"""
    action_type: Literal[BuiltinActionType.INPUT] = BuiltinActionType.INPUT  # type: ignore[assignment]


class NavigateWorkflowStep(BaseWorkflowStep[NavigateParams]):
    """导航步骤"""
    action_type: Literal[BuiltinActionType.NAVIGATE] = BuiltinActionType.NAVIGATE  # type: ignore[assignment]


class NewPageWorkflowStep(BaseWorkflowStep[NewPageParams]):
    """新页面步骤"""
    action_type: Literal[BuiltinActionType.NEW_PAGE] = BuiltinActionType.NEW_PAGE  # type: ignore[assignment]


class ScrollWorkflowStep(BaseWorkflowStep[ScrollParams]):
    """滚动步骤"""
    action_type: Literal[BuiltinActionType.SCROLL] = BuiltinActionType.SCROLL  # type: ignore[assignment]


class WaitWorkflowStep(BaseWorkflowStep[WaitParams]):
    """等待步骤"""
    action_type: Literal[BuiltinActionType.WAIT] = BuiltinActionType.WAIT  # type: ignore[assignment]


class HoverWorkflowStep(BaseWorkflowStep[HoverParams]):
    """悬停步骤"""
    action_type: Literal[BuiltinActionType.HOVER] = BuiltinActionType.HOVER  # type: ignore[assignment]


class GetTextWorkflowStep(BaseWorkflowStep[GetTextParams]):
    """获取文本步骤"""
    action_type: Literal[BuiltinActionType.GET_TEXT] = BuiltinActionType.GET_TEXT  # type: ignore[assignment]


class ScreenshotWorkflowStep(BaseWorkflowStep[ScreenshotParams]):
    """截图步骤"""
    action_type: Literal[BuiltinActionType.SCREENSHOT] = BuiltinActionType.SCREENSHOT  # type: ignore[assignment]


class LLMWorkflowStep(BaseWorkflowStep[LLMParams]):
    """LLM 步骤"""
    action_type: Literal[BuiltinActionType.LLM] = BuiltinActionType.LLM  # type: ignore[assignment]


class LoopWorkflowStep(BaseWorkflowStep[LoopParams]):
    """循环步骤"""
    action_type: Literal[BuiltinActionType.LOOP] = BuiltinActionType.LOOP  # type: ignore[assignment]


class IfElseWorkflowStep(BaseWorkflowStep[IfElseParams]):
    """条件分支步骤"""
    action_type: Literal[BuiltinActionType.IF_ELSE] = BuiltinActionType.IF_ELSE  # type: ignore[assignment]


class CompositeWorkflowStep(BaseWorkflowStep[CompositeParams]):
    """复合操作步骤"""
    action_type: Literal[BuiltinActionType.COMPOSITE] = BuiltinActionType.COMPOSITE  # type: ignore[assignment]


def _ensure_action_type(v: Any) -> Any:
    """若 dict 缺少 action_type，从 action_id 自动填充，递归处理 children。"""
    if isinstance(v, dict):
        if 'action_type' not in v and 'action_id' in v:
            # 自定义操作（以 ca_ 开头）的 action_type 为 "composite"
            if v['action_id'].startswith('ca_'):
                v['action_type'] = 'composite'
            else:
                v['action_type'] = v['action_id']
        if 'children' in v and v['children']:
            v['children'] = [
                _ensure_action_type(c) if isinstance(c, dict) else c
                for c in v['children']
            ]
    return v


# discriminated union: 根据 action_type 字段自动匹配子类
WorkflowStep = ClickWorkflowStep\
    | InputWorkflowStep\
    | NavigateWorkflowStep\
    | NewPageWorkflowStep\
    | ScrollWorkflowStep\
    | WaitWorkflowStep\
    | HoverWorkflowStep\
    | GetTextWorkflowStep\
    | ScreenshotWorkflowStep\
    | LLMWorkflowStep\
    | LoopWorkflowStep\
    | IfElseWorkflowStep\
    | CompositeWorkflowStep

# TypeAdapter 用于从 dict 反序列化
workflow_step_adapter: TypeAdapter[WorkflowStep] = TypeAdapter(WorkflowStep)

# 子类映射表，用于工厂函数
_WORKFLOW_STEP_CLASS_MAP: dict[BuiltinActionType, type[BaseWorkflowStep[Any]]] = {
    BuiltinActionType.CLICK: ClickWorkflowStep,
    BuiltinActionType.INPUT: InputWorkflowStep,
    BuiltinActionType.NAVIGATE: NavigateWorkflowStep,
    BuiltinActionType.NEW_PAGE: NewPageWorkflowStep,
    BuiltinActionType.SCROLL: ScrollWorkflowStep,
    BuiltinActionType.WAIT: WaitWorkflowStep,
    BuiltinActionType.HOVER: HoverWorkflowStep,
    BuiltinActionType.GET_TEXT: GetTextWorkflowStep,
    BuiltinActionType.SCREENSHOT: ScreenshotWorkflowStep,
    BuiltinActionType.LLM: LLMWorkflowStep,
    BuiltinActionType.LOOP: LoopWorkflowStep,
    BuiltinActionType.IF_ELSE: IfElseWorkflowStep,
    BuiltinActionType.COMPOSITE: CompositeWorkflowStep,
}


def create_workflow_step(
    action_id: str,
    params: Dict | None = None,
    **kwargs: Any,
) -> BaseWorkflowStep[Any]:
    """工厂函数：根据 action_id 创建对应类型的 WorkflowStep 子类实例。

    若 action_id 对应已知内置类型，则 params dict 会被校验并转换为对应的 Params 模型；
    若为未知类型（自定义 action），则返回 BaseWorkflowStep 实例，params 保持 dict。
    """
    action_type = kwargs.pop('action_type', None) or action_id
    kwargs['action_id'] = action_id
    kwargs['action_type'] = action_type

    try:
        at = BuiltinActionType(action_type)
    except ValueError:
        kwargs['params'] = params
        return BaseWorkflowStep[Dict](**kwargs)

    step_class = _WORKFLOW_STEP_CLASS_MAP[at]
    if params_model_class := BUILTIN_ACTION_PARAMS_MAP.get(at):
        try:
            kwargs['params'] = params_model_class.model_validate(
                params) if params else None
        except ValidationError:
            # 参数校验失败时保留原始 dict，让执行阶段处理错误
            kwargs['params'] = params
    else:
        kwargs['params'] = params
    try:
        return step_class(**kwargs)
    except ValidationError:
        # 子类构造失败（如 params 类型不匹配），降级为 BaseWorkflowStep
        kwargs['params'] = params
        return BaseWorkflowStep[Dict](**kwargs)
