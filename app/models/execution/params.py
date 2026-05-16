"""
执行模块参数验证模型
"""
from typing import Any
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


class ClickParams(SQLModel):
    """点击操作参数 - 对应 locator.click()"""
    selector: str | None = Field(default=None, max_length=500, description="用于定位元素的选择器")
    button: MouseButtonEnum = Field(default=MouseButtonEnum.LEFT, description="要按下的鼠标按钮 (left/right/middle)，默认为 left")
    click_count: int = Field(default=1, ge=1, le=3, description="点击次数，默认为 1。参见 UIEvent.detail")
    delay: float = Field(default=0, ge=0, le=10000, description="mousedown 和 mouseup 之间等待的时间（毫秒），默认为 0")
    force: bool = Field(default=False, description="是否绕过可操作性检查，默认为 false")
    modifiers: list[KeyboardModifierEnum] | None = Field(default=None, description="要按下的修饰键 (Alt/Control/Meta/Shift)")
    position: Position | None = Field(default=None, description="相对于元素 padding box 左上角的坐标位置")
    timeout: float = Field(default=30000, ge=0, le=300000, description="最大等待时间（毫秒），默认为 30000。传入 0 禁用超时")
    trial: bool = Field(default=False, description="仅执行可操作性检查而不执行实际操作，默认为 false")


class InputParams(SQLModel):
    """输入操作参数 - 对应 locator.fill()"""
    selector: str = Field(max_length=500, description="用于定位输入框元素的选择器")
    value: str = Field(max_length=10000, description="要输入的文本内容")
    force: bool = Field(default=False, description="是否绕过可操作性检查，默认为 false")
    timeout: float = Field(default=30000, ge=0, le=300000, description="最大等待时间（毫秒），默认为 30000。传入 0 禁用超时")


class NavigateParams(SQLModel):
    """导航操作参数"""
    url: str = Field(max_length=2048, description="要导航到的 URL 地址")
    wait_until: WaitUntilEnum = Field(default=WaitUntilEnum.LOAD, description="导航成功前的等待条件")
    timeout: int = Field(default=30000, ge=1000, le=300000, description="导航操作的超时时间（毫秒）")


class NewPageParams(SQLModel):
    """新建页面操作参数"""
    url: str | None = Field(default=None, max_length=2048, description="新页面的初始 URL")
    wait_until: WaitUntilEnum = Field(default=WaitUntilEnum.LOAD, description="导航等待条件（仅在提供 url 时生效）")
    timeout: int = Field(default=30000, ge=1000, le=300000, description="导航超时时间（仅在提供 url 时生效）")


class ScrollParams(SQLModel):
    """滚动操作参数 - 对应 locator.scroll_into_view_if_needed()"""
    selector: str | None = Field(default=None, max_length=500, description="用于定位滚动元素的选择器")
    timeout: float = Field(default=30000, ge=0, le=300000, description="最大等待时间（毫秒），默认为 30000。传入 0 禁用超时")


class WaitParams(SQLModel):
    """等待操作参数 - 对应 locator.wait_for()"""
    selector: str | None = Field(default=None, max_length=500, description="用于定位等待元素的选择器")
    state: ElementStateEnum = Field(default=ElementStateEnum.VISIBLE, description="等待的元素状态 (visible/hidden/attached/detached)")
    timeout: float = Field(default=30000, ge=0, le=300000, description="最大等待时间（毫秒），默认为 30000。传入 0 禁用超时")


class ScreenshotParams(SQLModel):
    """截图操作参数 - 对应 locator.screenshot() 或 page.screenshot()"""
    selector: str | None = Field(default=None, max_length=500, description="用于定位截图元素的选择器（为空时使用 page.screenshot）")
    type: ScreenshotTypeEnum = Field(default=ScreenshotTypeEnum.PNG, description="截图格式 (png/jpeg)")
    quality: int = Field(default=80, ge=1, le=100, description="JPEG 图片质量（1-100），仅在 type=jpeg 时有效")
    full_page: bool = Field(default=False, description="是否截取整个可滚动页面（仅 page.screenshot 支持）")
    omit_background: bool = Field(default=False, description="是否隐藏默认白色背景并截取透明背景（仅 png 格式支持）")
    timeout: float = Field(default=30000, ge=0, le=300000, description="最大等待时间（毫秒），默认为 30000。传入 0 禁用超时")


class LLMParams(SQLModel):
    """LLM 对话操作参数"""
    server_url: str = Field(max_length=2048, description="API 服务器地址")
    api_key: str = Field(max_length=500, description="API 密钥")
    model: str = Field(max_length=200, description="模型名称")
    messages: list[dict[str, str]] = Field(default_factory=list, description="消息列表")
    prompt: str = Field(default="", max_length=100000, description="单轮对话 prompt")
    system_prompt: str = Field(default="", max_length=10000, description="系统提示词")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="温度参数 0-2")
    max_tokens: int = Field(default=2048, ge=1, le=100000, description="最大生成的 token 数")
    timeout: int = Field(default=120000, ge=1000, le=600000, description="请求超时时间(毫秒)")


class LoopParams(SQLModel):
    """循环控制流操作参数"""
    items: list[Any] | None = Field(default=None, description="要遍历的列表")
    count: int = Field(default=1, ge=1, le=10000, description="固定循环次数")


class IfElseParams(SQLModel):
    """条件分支控制流操作参数"""
    condition: str = Field(max_length=5000, description="条件表达式")
