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


class LocatorStrategyEnum(StrEnumAutoDoc):
    """元素定位策略枚举（与站点无关的通用定位机制）

    只描述「用哪种方式找元素」，不含任何具体站点规则；业务差异一律由动作 JSON 表达。
    """

    CSS = "css"
    """CSS 选择器（默认，兼容历史动作 JSON）"""

    TEXT = "text"
    """按可见文本定位（对应 Playwright ``get_by_text``）"""

    ROLE = "role"
    """按 ARIA role 定位（对应 ``get_by_role``，value 写 role 名如 button/link）"""

    TESTID = "testid"
    """按 ``data-testid`` 定位（对应 ``get_by_test_id``）"""

    LABEL = "label"
    """按表单控件的关联 label 文本定位（对应 ``get_by_label``）"""

    PLACEHOLDER = "placeholder"
    """按输入框 placeholder 定位（对应 ``get_by_placeholder``）"""

    XPATH = "xpath"
    """XPath 定位（对应 ``xpath=`` 引擎）"""


class LocatorPickEnum(StrEnumAutoDoc):
    """定位命中多个元素时的取值策略

    Playwright 单元素 API 默认严格模式：多命中直接抛错。真实动态页里同构节点
    常常成组出现（列表容器、隐藏的响应式副本），需要显式声明取哪一个。
    """

    STRICT = "strict"
    """严格模式：要求恰好命中一个，多命中即报错（单元素动作的默认值）"""

    FIRST = "first"
    """取第一个命中元素"""

    LAST = "last"
    """取最后一个命中元素"""

    NTH = "nth"
    """取第 nth 个命中元素（需同时提供 ``nth``，从 0 开始）"""

    ALL = "all"
    """不做收窄：交给动作按整批处理（读取类动作的默认值）"""


class WaitMatchEnum(StrEnumAutoDoc):
    """等待动作的多元素匹配范围

    Playwright 的单元素 API（`locator.wait_for`）默认开启严格模式：选择器命中多个
    元素会直接抛错。等待的语义是「这一批里有没有满足状态的元素」，因此需要显式声明
    范围：命中列表容器（一条数据一个节点）时按 `any` 等待，判断「整块是否消失」
    时按 `all`。
    """

    ANY = "any"
    """任一命中元素满足状态即返回"""

    ALL = "all"
    """全部命中元素满足状态才返回（0 个命中=已消失）"""


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


class UrlPatternTypeEnum(StrEnumAutoDoc):
    """URL / 响应 URL 的匹配方式枚举

    站点无关：只描述「怎么匹配字符串」，不含任何具体域名或站点规则。
    """

    GLOB = "glob"  # 通配符（* / ? / **），Playwright 原生支持
    REGEX = "regex"  # 正则表达式（re.search 语义）


class ElementStateKindEnum(StrEnumAutoDoc):
    """element_state 动作的读取类型枚举

    用枚举分流，避免把「读属性 / 数数量 / 判可见 / 判可用 / 读输入值」拆成多个动作
    或堆成互相排斥的自由参数。
    """

    ATTRIBUTE = "attribute"  # 读任意 HTML 属性（取不到返回空串）
    COUNT = "count"  # 选择器命中的元素数量
    VISIBLE = "visible"  # 首个命中元素是否可见
    ENABLED = "enabled"  # 首个命中元素是否可用（未被 disabled）
    INPUT_VALUE = "input_value"  # 首个命中输入框的当前值


class MissingPolicyEnum(StrEnumAutoDoc):
    """目标元素不存在（定位器所有候选都没命中）时的处理策略

    - 探针类动作（``element_state``）默认 ``ignore``：按空值成功返回；
    - 必须打到元素的动作（``click`` / ``input`` 等）默认 ``fail``：
      交由 retry / on_error 接管，避免「没点到却记成功」。
    """

    IGNORE = "ignore"  # 按空值成功返回（当作探针）
    FAIL = "fail"  # 直接失败（交由 retry / on_error 接管）


class SelectOptionByEnum(StrEnumAutoDoc):
    """select_option 的定位方式枚举（对应 Playwright select_option 的关键字）"""

    VALUE = "value"
    LABEL = "label"
    INDEX = "index"


class NavigateModeEnum(StrEnumAutoDoc):
    """navigate 动作的导航方式枚举

    - ``goto``：跳转到 ``url``（唯一需要 ``url`` 的模式）；
    - ``reload`` / ``back`` / ``forward``：复用当前页面历史，``url`` 被忽略。
    """

    GOTO = "goto"
    RELOAD = "reload"
    BACK = "back"
    FORWARD = "forward"
