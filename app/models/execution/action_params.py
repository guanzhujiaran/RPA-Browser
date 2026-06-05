from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Dict, Literal, Type
from pydantic import Field, TypeAdapter, model_validator
from sqlmodel import SQLModel

from app.models.execution.enums import (
    WaitUntilEnum,
    MouseButtonEnum,
    ElementStateEnum,
    ScreenshotTypeEnum,
    KeyboardModifierEnum,
)


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
    def params_model(self) -> Type['AllActionParams']:
        return BUILTIN_ACTION_PARAMS_MAP.get(self.value)


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


class InputParams(SelectorActionParams):
    """输入操作参数 - 对应 locator.fill()"""
    selector: str = Field(max_length=500, description="用于定位输入框元素的选择器")
    value: str = Field(max_length=10000, description="要输入的文本内容")
    force: bool = Field(default=False, description="是否绕过可操作性检查，默认为 false")


class NavigateParams(BaseActionParams):
    """导航操作参数"""
    url: str = Field(max_length=2048, description="要导航到的 URL 地址")
    wait_until: WaitUntilEnum = Field(
        default=WaitUntilEnum.LOAD, description="导航成功前的等待条件")
    timeout: int = Field(default=30000, ge=1000, le=300000,
                         description="导航操作的超时时间（毫秒）")


class NewPageParams(BaseActionParams):
    """新建页面操作参数"""
    url: str | None = Field(
        default=None, max_length=2048, description="新页面的初始 URL")
    wait_until: WaitUntilEnum = Field(
        default=WaitUntilEnum.LOAD, description="导航等待条件（仅在提供 url 时生效）")
    timeout: int = Field(default=30000, ge=1000, le=300000,
                         description="导航超时时间（仅在提供 url 时生效）")


class ScrollParams(SelectorActionParams):
    """滚动操作参数 - 对应 locator.scroll_into_view_if_needed()"""
    pass


class WaitParams(SelectorActionParams):
    """等待操作参数 - 对应 locator.wait_for()"""
    state: ElementStateEnum = Field(
        default=ElementStateEnum.VISIBLE, description="等待的元素状态 (visible/hidden/attached/detached)")


class HoverParams(MouseActionParams):
    """悬停操作参数 - 对应 locator.hover()"""
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
    messages: list[dict[str, str]] = Field(
        default_factory=list, description="消息列表")
    prompt: str = Field(default="", max_length=100000,
                        description="单轮对话 prompt")
    system_prompt: str = Field(
        default="", max_length=10000, description="系统提示词")
    temperature: float = Field(
        default=0.7, ge=0.0, le=2.0, description="温度参数 0-2")
    max_tokens: int = Field(default=2048, ge=1, le=100000,
                            description="最大生成的 token 数")
    timeout: int = Field(default=120000, ge=1000,
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


class IfElseParams(BaseActionParams):
    """条件分支控制流操作参数"""
    condition: str = Field(max_length=5000, description="条件表达式")
    TrueBranch: list['WorkflowStep'] | None = Field(
        default=None, description="真分支步骤")
    FalseBranch: list['WorkflowStep'] | None = Field(
        default=None, description="假分支步骤")


class CompositeParams(BaseActionParams):
    """复合操作参数"""
    steps: list['WorkflowStep'] = Field(
        default_factory=list, description="复合操作步骤")


AllActionParams = ClickParams | InputParams | NavigateParams | NewPageParams | ScrollParams | WaitParams | HoverParams | ScreenshotParams | LLMParams | LoopParams | IfElseParams | CompositeParams

# endregion


class PluginConfig(SQLModel):
    """工作流插件配置 - 单个插件的运行时配置"""
    plugin_id: str = Field(description="插件ID")
    config_params: dict[str, Any] = Field(default_factory=dict, description="插件配置参数")
    hook_type: str = Field(description="钩子类型: before_action/after_action/on_success/on_error/on_timeout")
    priority: int = Field(default=100, description="优先级")

BUILTIN_ACTION_PARAMS_MAP: Dict[str, Type[AllActionParams]] = {
    BuiltinActionType.CLICK: ClickParams,
    BuiltinActionType.INPUT: InputParams,
    BuiltinActionType.SCROLL: ScrollParams,
    BuiltinActionType.WAIT: WaitParams,
    BuiltinActionType.HOVER: HoverParams,
    BuiltinActionType.NAVIGATE: NavigateParams,
    BuiltinActionType.NEW_PAGE: NewPageParams,
    BuiltinActionType.SCREENSHOT: ScreenshotParams,
    BuiltinActionType.LLM: LLMParams,
    BuiltinActionType.LOOP: LoopParams,
    BuiltinActionType.IF_ELSE: IfElseParams,
    BuiltinActionType.COMPOSITE: CompositeParams,
}


class BaseWorkflowStep(SQLModel):
    """工作流步骤基类 - 共享字段 + execution_engine 运行时字段"""
    action_id: str
    params: dict[str, Any] | None = None
    retry: int = 0
    input_vars: dict[str, Any] | None = None
    output_vars: list[str] | None = None
    timeout: int | None = None
    children: list[WorkflowStep] | None = None
    # execution_engine 运行时字段
    condition: str | None = None
    loop_count: int | None = None
    loop_while: str | None = None
    loop_until: str | None = None
    output_var: str | None = None

    @model_validator(mode='before')
    @classmethod
    def _fill_action_type(cls, data: Any) -> Any:
        """若 action_type 缺失，从 action_id 自动填充，确保 discriminated union 能匹配。
        递归处理 children 中的嵌套步骤。"""
        if isinstance(data, dict):
            if not data.get('action_type') and data.get('action_id'):
                data['action_type'] = data['action_id']
            # 递归处理 children
            if 'children' in data and data['children']:
                data['children'] = [
                    cls._fill_action_type(item) if isinstance(item, dict) else item
                    for item in data['children']
                ]
        return data


class ClickWorkflowStep(BaseWorkflowStep):
    """点击步骤"""
    action_type: Literal[BuiltinActionType.CLICK] = BuiltinActionType.CLICK  # type: ignore[assignment]
    params: ClickParams | None = None


class InputWorkflowStep(BaseWorkflowStep):
    """输入步骤"""
    action_type: Literal[BuiltinActionType.INPUT] = BuiltinActionType.INPUT  # type: ignore[assignment]
    params: InputParams | None = None


class NavigateWorkflowStep(BaseWorkflowStep):
    """导航步骤"""
    action_type: Literal[BuiltinActionType.NAVIGATE] = BuiltinActionType.NAVIGATE  # type: ignore[assignment]
    params: NavigateParams | None = None


class NewPageWorkflowStep(BaseWorkflowStep):
    """新页面步骤"""
    action_type: Literal[BuiltinActionType.NEW_PAGE] = BuiltinActionType.NEW_PAGE  # type: ignore[assignment]
    params: NewPageParams | None = None


class ScrollWorkflowStep(BaseWorkflowStep):
    """滚动步骤"""
    action_type: Literal[BuiltinActionType.SCROLL] = BuiltinActionType.SCROLL  # type: ignore[assignment]
    params: ScrollParams | None = None


class WaitWorkflowStep(BaseWorkflowStep):
    """等待步骤"""
    action_type: Literal[BuiltinActionType.WAIT] = BuiltinActionType.WAIT  # type: ignore[assignment]
    params: WaitParams | None = None


class HoverWorkflowStep(BaseWorkflowStep):
    """悬停步骤"""
    action_type: Literal[BuiltinActionType.HOVER] = BuiltinActionType.HOVER  # type: ignore[assignment]
    params: HoverParams | None = None


class ScreenshotWorkflowStep(BaseWorkflowStep):
    """截图步骤"""
    action_type: Literal[BuiltinActionType.SCREENSHOT] = BuiltinActionType.SCREENSHOT  # type: ignore[assignment]
    params: ScreenshotParams | None = None


class LLMWorkflowStep(BaseWorkflowStep):
    """LLM 步骤"""
    action_type: Literal[BuiltinActionType.LLM] = BuiltinActionType.LLM  # type: ignore[assignment]
    params: LLMParams | None = None


class LoopWorkflowStep(BaseWorkflowStep):
    """循环步骤"""
    action_type: Literal[BuiltinActionType.LOOP] = BuiltinActionType.LOOP  # type: ignore[assignment]
    params: LoopParams | None = None


class IfElseWorkflowStep(BaseWorkflowStep):
    """条件分支步骤"""
    action_type: Literal[BuiltinActionType.IF_ELSE] = BuiltinActionType.IF_ELSE  # type: ignore[assignment]
    params: IfElseParams | None = None


class CompositeWorkflowStep(BaseWorkflowStep):
    """复合操作步骤"""
    action_type: Literal[BuiltinActionType.COMPOSITE] = BuiltinActionType.COMPOSITE  # type: ignore[assignment]
    params: CompositeParams | None = None


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
WorkflowStep = Annotated[
    ClickWorkflowStep
    | InputWorkflowStep
    | NavigateWorkflowStep
    | NewPageWorkflowStep
    | ScrollWorkflowStep
    | WaitWorkflowStep
    | HoverWorkflowStep
    | ScreenshotWorkflowStep
    | LLMWorkflowStep
    | LoopWorkflowStep
    | IfElseWorkflowStep
    | CompositeWorkflowStep,
    Field(discriminator='action_type'),
]

# TypeAdapter 用于从 dict 反序列化
workflow_step_adapter: TypeAdapter[WorkflowStep] = TypeAdapter(WorkflowStep)

# 子类映射表，用于工厂函数
_WORKFLOW_STEP_CLASS_MAP: dict[BuiltinActionType, type[BaseWorkflowStep]] = {
    BuiltinActionType.CLICK: ClickWorkflowStep,
    BuiltinActionType.INPUT: InputWorkflowStep,
    BuiltinActionType.NAVIGATE: NavigateWorkflowStep,
    BuiltinActionType.NEW_PAGE: NewPageWorkflowStep,
    BuiltinActionType.SCROLL: ScrollWorkflowStep,
    BuiltinActionType.WAIT: WaitWorkflowStep,
    BuiltinActionType.HOVER: HoverWorkflowStep,
    BuiltinActionType.SCREENSHOT: ScreenshotWorkflowStep,
    BuiltinActionType.LLM: LLMWorkflowStep,
    BuiltinActionType.LOOP: LoopWorkflowStep,
    BuiltinActionType.IF_ELSE: IfElseWorkflowStep,
    BuiltinActionType.COMPOSITE: CompositeWorkflowStep,
}


def create_workflow_step(
    action_id: str,
    params: dict[str, Any] | None = None,
    **kwargs: Any,
) -> BaseWorkflowStep:
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
        return BaseWorkflowStep(**kwargs)

    step_class = _WORKFLOW_STEP_CLASS_MAP[at]
    if params_model_class := BUILTIN_ACTION_PARAMS_MAP.get(at):
        kwargs['params'] = params_model_class.model_validate(params) if params else None
    else:
        kwargs['params'] = params
    return step_class(**kwargs)
