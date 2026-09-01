"""
执行模块枚举定义
"""
from bili_common.models import StrEnumAutoDoc


class WaitUntilEnum(StrEnumAutoDoc):
    """导航等待条件枚举"""
    LOAD = "load"
    DOMCONTENTLOADED = "domcontentloaded"
    NETWORKIDLE = "networkidle"
    COMMIT = "commit"


class MouseButtonEnum(StrEnumAutoDoc):
    """鼠标按钮枚举"""
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


class ElementStateEnum(StrEnumAutoDoc):
    """元素状态枚举"""
    VISIBLE = "visible"
    HIDDEN = "hidden"
    ATTACHED = "attached"
    DETACHED = "detached"


class ScreenshotTypeEnum(StrEnumAutoDoc):
    """截图格式枚举"""
    PNG = "png"
    JPEG = "jpeg"


class KeyboardModifierEnum(StrEnumAutoDoc):
    """键盘修饰键枚举 - 对应 Playwright modifiers 参数"""
    ALT = "Alt"
    CONTROL = "Control"
    META = "Meta"
    SHIFT = "Shift"


class HttpMethodEnum(StrEnumAutoDoc):
    """HTTP 请求方法枚举"""
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    PATCH = "PATCH"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"


class HttpBodyTypeEnum(StrEnumAutoDoc):
    """HTTP 请求体类型枚举"""
    NONE = "none"
    JSON = "json"
    FORM = "form"
    RAW = "raw"
