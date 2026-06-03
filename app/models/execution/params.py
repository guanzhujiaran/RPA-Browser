"""
执行模块参数验证模型

所有操作参数模型继承自 BaseActionParams，实现类型复用和参数验证统一。
"""
from typing import Any, Dict, List
from sqlmodel import SQLModel, Field

from app.models.execution.enums import (
    WaitUntilEnum,
    MouseButtonEnum,
    ElementStateEnum,
    ScreenshotTypeEnum,
    KeyboardModifierEnum,
)


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
    loopBranch: list[WorkflowStep] | None = Field(
        default=None, description="循环分支步骤")


class IfElseParams(BaseActionParams):
    """条件分支控制流操作参数"""
    condition: str = Field(max_length=5000, description="条件表达式")
    TrueBranch: list[WorkflowStep] | None = Field(
        default=None, description="真分支步骤")
    FalseBranch: list[WorkflowStep] | None = Field(
        default=None, description="假分支步骤")


class CompositeParams(BaseActionParams):
    """复合操作参数"""
    steps: list[WorkflowStep] = Field(
        default_factory=list, description="复合操作步骤")


AllActionParams = ClickParams | InputParams | NavigateParams | NewPageParams | ScrollParams | WaitParams | HoverParams | ScreenshotParams | LLMParams | LoopParams | IfElseParams | CompositeParams


class WorkflowStep(SQLModel):
    """工作流步骤 - 与 BaseAction 创建参数对齐"""
    action_id: str
    action_type: str | None = None
    params: AllActionParams | None = None
    retry: int = 0
    input_vars: Dict[str, Any] | None = None
    output_vars: List[str] | None = None
    timeout: int | None = None
    children: List["WorkflowStep"] | None = None

# ============ 执行请求参数模型 ============


class ExecutionRequest(SQLModel):
    """执行请求基础参数"""
    mid: int = Field(description="用户ID")
    browser_id: int = Field(description="浏览器ID")
    variables: dict[str, Any] = Field(default_factory=dict, description="变量池")
    page_index: int | None = Field(default=None, description="页面索引")


class ActionExecutionRequest(ExecutionRequest):
    """操作执行请求参数"""
    action_id: str = Field(description="操作ID")
    params: dict[str, Any] = Field(default_factory=dict, description="操作参数")
    input_data: dict[str, Any] = Field(
        default_factory=dict, description="输入数据")
    output: list[str] = Field(default_factory=list, description="输出字段列表")


class StepExecutionRequest(ExecutionRequest):
    """步骤执行请求参数"""
    action_id: str = Field(description="操作ID")
    params: dict[str, Any] = Field(default_factory=dict, description="操作参数")
    step_index: int = Field(default=0, description="步骤索引")


class WorkflowExecutionRequest(ExecutionRequest):
    """工作流执行请求参数"""
    action_id: str = Field(description="操作ID")
    input_data: dict[str, Any] = Field(
        default_factory=dict, description="输入数据")
    output: list[str] = Field(default_factory=list, description="输出字段列表")
