"""
执行模块枚举定义
"""
from enum import StrEnum


class WaitUntilEnum(StrEnum):
    """导航等待条件枚举"""
    LOAD = "load"
    DOMCONTENTLOADED = "domcontentloaded"
    NETWORKIDLE = "networkidle"
    COMMIT = "commit"


class MouseButtonEnum(StrEnum):
    """鼠标按钮枚举"""
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


class ElementStateEnum(StrEnum):
    """元素状态枚举"""
    VISIBLE = "visible"
    HIDDEN = "hidden"
    ATTACHED = "attached"
    DETACHED = "detached"


class ScreenshotTypeEnum(StrEnum):
    """截图格式枚举"""
    PNG = "png"
    JPEG = "jpeg"


class KeyboardModifierEnum(StrEnum):
    """键盘修饰键枚举 - 对应 Playwright modifiers 参数"""
    ALT = "Alt"
    CONTROL = "Control"
    META = "Meta"
    SHIFT = "Shift"
