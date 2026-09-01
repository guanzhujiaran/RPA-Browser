from __future__ import annotations

from bili_common.models import StrEnumAutoDoc
import contextlib
from typing import Any, Dict, Generic, Literal, Type, TypeVar
from pydantic import Field, TypeAdapter, ValidationError, field_validator, model_validator
from sqlmodel import SQLModel

from app.models.execution.enums import (
    WaitUntilEnum,
    MouseButtonEnum,
    ElementStateEnum,
    ScreenshotTypeEnum,
    KeyboardModifierEnum,
    HttpMethodEnum,
    HttpBodyTypeEnum,
)
from app.models.execution.condition_models import ConditionRule
from app.models.execution.system_services import RpcMethodName
from app.models.execution.rpc_method_params import (
    GetReserveLotteryRpcParams,
    GetOfficialLotteryRpcParams,
    GetChargeLotteryRpcParams,
    GetTopicLotteryRpcParams,
    GetAllLotteryRpcParams,
    GetOthersLotDynListRpcParams,
    RPC_METHOD_PARAMS_FIELD_MAP,
)


class OnErrorEnum(StrEnumAutoDoc):
    """步骤失败时的处理策略"""
    STOP = "stop"          # 停止执行
    CONTINUE = "continue"  # 忽略错误继续
    RETRY = "retry"        # 重试后停止


class BuiltinActionDesc(StrEnumAutoDoc):
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
    GET_WINDOW = "获取窗口属性"
    FETCH_EXTERNAL_DATA = "获取外部数据"
    PRINT = "打印变量替换后的参数（仅调试用，不执行实际操作）"

    IF_ELSE = "根据条件执行 true/false 分支"
    LOOP = "循环执行"
    COMPOSITE = "执行复合操作"


class BuiltinActionName(StrEnumAutoDoc):
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
    GET_WINDOW = "获取窗口"
    FETCH_EXTERNAL_DATA = "获取外部数据"
    PRINT = "打印参数"


class BuiltinActionType(StrEnumAutoDoc):
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
    GET_WINDOW = "get_window"
    FETCH_EXTERNAL_DATA = "fetch_external_data"
    PRINT = "print"

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


class ActionLogOption(SQLModel):
    """操作日志采集选项（通用参数，可挂在任意操作的 BaseActionParams 上）

    用户在执行参数中携带 log 字段即可声明该次执行的日志采集策略；
    自定义操作（ca_xxx）也可由其 CompositeActionModel 的 log_* 字段映射而来。
    """
    enabled: bool = Field(default=False, description="是否采集该操作的执行日志")
    record_params: bool = Field(default=True, description="是否记录变量替换后的入参")
    record_result: bool = Field(default=True, description="是否记录执行返回结果")
    record_variables: bool = Field(default=False, description="是否记录变量池快照")
    only_on_error: bool = Field(default=False, description="仅在执行失败时记录")
    max_payload_length: int = Field(
        default=4000, description="params/result/variables 序列化后最大字符数，超出则截断；0 表示不限制")
    retention_days: int = Field(
        default=30, description="日志保留天数，0 表示永久保留")


class BaseActionParams(SQLModel):
    """操作参数基类 - 所有操作参数模型继承此类"""
    timeout: float = Field(default=30000, ge=0, le=300000,
                           description="最大等待时间（毫秒），默认为 30000。传入 0 禁用超时")
    log: ActionLogOption | None = Field(
        default=None,
        description="操作日志采集选项；为空时按 action 自有配置或服务端兜底决定")


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
    """获取元素文本参数 - 对应 locator.all_inner_texts()"""
    separator: str = Field(
        default="\n",
        max_length=50,
        description="多个匹配元素文本之间的分隔符，默认为换行符")


class GetWindowParams(BaseActionParams):
    """获取 window 属性参数 - 通过 page.evaluate 安全获取 window 对象属性"""
    property_path: str | None = Field(
        default=None,
        max_length=200,
        description="window 对象的属性路径，如 'location.href'、'document.title'、'innerWidth'。仅支持点分隔的标识符，不允许函数调用或运算符。与 object_name 二选一",
    )
    object_name: str | None = Field(
        default=None,
        max_length=200,
        description="window 对象名称，如 'location'、'navigator'。获取该对象后遍历所有字段，返回所有非 null 非 undefined 的值。与 property_path 二选一",
    )

    @field_validator("property_path")
    @classmethod
    def validate_property_path(cls, v: str | None) -> str | None:
        """安全校验：仅允许点分隔标识符和数字索引方括号，禁止函数调用"""
        import re
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("property_path 不能为空")
        # 允许：标识符 . 标识符 [数字] 的任意组合，禁止函数调用 () 及其他运算符
        if not re.fullmatch(r'[a-zA-Z_$][\w$]*((\.[a-zA-Z_$][\w$]*)|(\[\d+\]))*', v):
            raise ValueError(
                f"property_path 格式不合法: '{v}'。仅支持点分隔的属性路径和数字索引，如 'location.href'、'modules[0].name'，不允许函数调用、运算符或变量索引"
            )
        return v

    @model_validator(mode="after")
    def check_one_of(self) -> "GetWindowParams":
        """确保 property_path 和 object_name 至少提供一个"""
        if self.property_path is None and self.object_name is None:
            raise ValueError("property_path 和 object_name 至少需要提供一个")
        return self


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
    # 结构化输出
    response_schema: dict | None = Field(
        default=None,
        description="JSON Schema 定义，提供后将启用 LangChain structured output，返回符合 schema 的结构化数据",
    )


class FetchExternalDataParams(BaseActionParams):
    """获取外部数据操作参数 - RPC 模式（method_name）和 HTTP 模式（url）二选一，互斥

    HTTP 模式与 RPC 模式参数完全分离：
    - HTTP 模式：使用 url/method/headers/params/body_* 等 HTTP 专属字段
    - RPC 模式：每个 method_name 对应一个独立的强类型 SQLModel 参数字段
      （如 get_reserve_lottery_params），与 HTTP 的 params 字段互不影响，前端分开展示
    """
    method_name: RpcMethodName | None = Field(
        default=None,
        title="RPC 方法",
        description="RPC 方法名（StrEnum，从预设白名单中选择）。提供时走 RPC 模式。设为空值或 None 则走 HTTP 模式",
    )
    url: str | None = Field(
        default=None, max_length=4096,
        description="HTTP/HTTPS 请求地址（method_name 为空时必填，仅 HTTP 模式生效）",
    )
    method: HttpMethodEnum = Field(
        default=HttpMethodEnum.GET,
        description="HTTP 方法（仅 HTTP 模式生效）")
    headers: Dict[str, str] | None = Field(
        default=None, description="HTTP 附加请求头（仅 HTTP 模式生效）")
    params: Dict[str, str] | None = Field(
        default=None, description="HTTP URL 查询参数（仅 HTTP 模式生效）")
    body_type: HttpBodyTypeEnum = Field(
        default=HttpBodyTypeEnum.NONE,
        description="HTTP 请求体类型: none=无, json=JSON, form=表单, raw=原始文本（仅 HTTP 模式生效）")
    body_json: Any | None = Field(
        default=None, description="HTTP JSON 请求体（body_type=json 时生效，仅 HTTP 模式生效）")
    body_form: Dict[str, str] | None = Field(
        default=None, description="HTTP 表单请求体（body_type=form 时生效，仅 HTTP 模式生效）")
    body_raw: str | None = Field(
        default=None, max_length=1000000, description="HTTP 原始文本请求体（body_type=raw 时生效，仅 HTTP 模式生效）")
    raw_content_type: str | None = Field(
        default=None, max_length=200,
        description="HTTP 原始文本请求体的 Content-Type（body_type=raw 时生效，仅 HTTP 模式生效）")
    follow_redirects: bool = Field(
        default=True, description="是否自动跟随重定向（仅 HTTP 模式生效）")
    proxy: str | None = Field(
        default=None, max_length=500,
        description="HTTP/HTTPS/SOCKS 代理地址（仅 HTTP 模式生效）")
    timeout: float = Field(default=30000, ge=1000, le=300000,
                           description="请求超时时间（毫秒）")
    # RPC 模式专用：每个 method_name 对应独立的强类型 SQLModel 参数字段，与 HTTP params 完全分离
    # 前端根据当前选中的 method_name 仅展示对应字段的表单，各方法参数相互独立
    get_reserve_lottery_params: GetReserveLotteryRpcParams | None = Field(
        default=None, description="get_reserve_lottery 方法请求参数（仅 RPC 模式且选择该方法时生效）")
    get_official_lottery_params: GetOfficialLotteryRpcParams | None = Field(
        default=None, description="get_official_lottery 方法请求参数（仅 RPC 模式且选择该方法时生效）")
    get_charge_lottery_params: GetChargeLotteryRpcParams | None = Field(
        default=None, description="get_charge_lottery 方法请求参数（仅 RPC 模式且选择该方法时生效）")
    get_topic_lottery_params: GetTopicLotteryRpcParams | None = Field(
        default=None, description="get_topic_lottery 方法请求参数（仅 RPC 模式且选择该方法时生效）")
    get_all_lottery_params: GetAllLotteryRpcParams | None = Field(
        default=None, description="get_all_lottery 方法请求参数（仅 RPC 模式且选择该方法时生效）")
    get_others_lot_dyn_list_params: GetOthersLotDynListRpcParams | None = Field(
        default=None, description="get_others_lot_dyn_list 方法请求参数（仅 RPC 模式且选择该方法时生效）")

    @model_validator(mode="after")
    def check_mode_exclusive(self) -> "FetchExternalDataParams":
        """确保 method_name 和 url 至少提供一个，且两者互斥

        RpcMethodName.NONE（空值）等价于 None，表示不使用 RPC、走 HTTP 模式。
        """
        is_rpc = self.method_name is not None and self.method_name != RpcMethodName.NONE
        if not is_rpc and not self.url:
            raise ValueError("必须提供 method_name（RPC 模式）或 url（HTTP 模式）中的一个")
        if is_rpc and self.url:
            raise ValueError("method_name 和 url 互斥，不可同时提供")
        return self


class PrintParams(BaseActionParams):
    """打印参数操作参数 - 调试用，打印变量替换后的内容，不执行任何实际操作"""
    message: str = Field(default="", max_length=10000,
                         description="要打印的内容（支持 {{var}} 变量替换），执行时仅打印不执行其他操作")


class LoopParams(BaseActionParams):
    """循环控制流操作参数

    循环来源 (loop_source)：
        - fixed_count: 固定次数循环，使用 count 字段
        - variable: 从变量获取 items 列表，使用 loop_items_var 字段
        - expression: 通过表达式计算 items，使用 loop_items_expr 字段

    参数映射 (param_mapping)：
        将循环项的字段映射到循环体内步骤的参数。
        例如 { "selector": "loop_item.name", "url": "loop_item.link" }
        表示将当前循环项的 name 字段值注入到子步骤的 selector 参数，
        link 字段值注入到 url 参数。

        映射源路径支持点分隔嵌套访问，如 "loop_item.user.profile.id"。
        也支持引用循环索引 "loop_index"。
    """
    # ── 循环来源配置 ──
    loop_source: Literal["fixed_count", "variable", "expression", "json_list"] = Field(
        default="fixed_count",
        description="循环来源: fixed_count=固定次数, variable=从变量获取items, expression=表达式, json_list=直接传入JSON列表",
    )
    count: int = Field(default=1, ge=1, le=10000, description="固定循环次数（loop_source=fixed_count 时使用）")
    loop_items_var: str | None = Field(
        default=None, max_length=200,
        description="循环项变量引用（loop_source=variable 时使用），如 'previous_output.items'",
    )
    loop_items_expr: str | None = Field(
        default=None, max_length=500,
        description="循环项表达式（loop_source=expression 时使用）",
    )
    loop_items_json: list[Any] | None = Field(
        default=None,
        description="直接传入的 JSON 列表（loop_source=json_list 时使用），支持任意嵌套结构",
    )

    # ── 循环变量命名 ──
    loop_item_var: str = Field(
        default="loop_item", max_length=50,
        description="当前循环项在作用域中的变量名",
    )
    loop_index_var: str = Field(
        default="loop_index", max_length=50,
        description="当前循环索引在作用域中的变量名",
    )

    # ── 参数映射（可选）──
    param_mapping: dict[str, str] | None = Field(
        default=None,
        description="参数映射: { 目标参数名: 源字段路径 }。源路径基于循环项，支持点分隔嵌套访问",
    )

    # ── 循环体 ──
    loopBranch: list['WorkflowStep'] | None = Field(
        default=None, description="循环分支步骤")

    # ── 控制条件 ──
    break_condition: ConditionRule | None = Field(
        default=None,
        description="break 条件规则（结构化条件），每次迭代开始前评估，为真时跳出整个循环")
    continue_condition: ConditionRule | None = Field(
        default=None,
        description="continue 条件规则（结构化条件），每次迭代开始前评估，为真时跳过当前迭代")

    # 向后兼容旧字段
    loop_count: int | None = Field(default=None, exclude=True, description="[已废弃] 使用 count + loop_source")
    loop_while: str | None = Field(default=None, exclude=True, description="[已废弃] 使用 break_condition")
    items: list[Any] | None = Field(default=None, exclude=True, description="[已废弃] 使用 loop_items_var")
    loop_var: str | None = Field(default=None, exclude=True, description="[已废弃] 使用 loop_item_var")


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


AllActionParams = ClickParams | InputParams | NavigateParams | NewPageParams | ScrollParams | WaitParams | HoverParams | GetTextParams | GetWindowParams | ScreenshotParams | LLMParams | FetchExternalDataParams | PrintParams | LoopParams | IfElseParams | CompositeParams

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


class GetWindowResult(SQLModel):
    """获取 window 属性结果"""
    value: str = Field(default="", description="属性的字符串值（property_path 模式）")
    values: dict[str, str] = Field(default_factory=dict, description="对象所有字段的非空值（object_name 模式）")



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
    usage: Dict = Field(default_factory=dict, description="token 使用量")
    # 结构化输出
    is_structured: bool = Field(
        default=False, description="是否为结构化输出模式")
    structured_data: dict | None = Field(
        default=None, description="结构化输出数据（response_schema 提供时有效）")


class FetchExternalDataResult(SQLModel):
    """获取外部数据操作结果"""
    status_code: int = Field(default=0, description="HTTP 状态码")
    data: Any | None = Field(
        default=None, description="响应数据（JSON 解析后的字典/列表，无法解析时为 None）")
    text: str = Field(default="", description="响应文本（原始字符串）")
    headers: Dict[str, str] = Field(
        default_factory=dict, description="响应头（键值对）")
    url: str = Field(default="", description="最终请求 URL（可能经过重定向）")
    elapsed: float = Field(default=0.0, description="请求耗时（秒）")
    is_json: bool = Field(
        default=False, description="响应体是否为 JSON 格式")


class PrintResult(SQLModel):
    """打印操作结果"""
    message: str = Field(default="", description="打印的内容")


class LoopResult(SQLModel):
    """循环操作结果"""
    iterations: int = Field(default=0, description="迭代次数")
    total_results: int = Field(default=0, description="总结果数")
    details: list[Dict] = Field(default_factory=list, description="详细结果")
    results: list[Dict] = Field(default_factory=list, description="结果列表")
    message: str | None = Field(default=None, description="提示消息")
    was_broken: bool = Field(
        default=False, description="是否由 break_condition 触发中断")
    was_continued: bool = Field(
        default=False, description="是否由 continue_condition 触发跳过")


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


AllActionResult = ClickResult | InputResult | ScrollResult | WaitResult | HoverResult | GetTextResult | GetWindowResult | NavigateResult | NewPageResult | ScreenshotResult | LLMResult | FetchExternalDataResult | PrintResult | LoopResult | IfElseResult | CompositeResult

BUILTIN_ACTION_RESULT_MAP: Dict[str, Type[AllActionResult]] = {
    BuiltinActionType.CLICK: ClickResult,
    BuiltinActionType.INPUT: InputResult,
    BuiltinActionType.SCROLL: ScrollResult,
    BuiltinActionType.WAIT: WaitResult,
    BuiltinActionType.HOVER: HoverResult,
    BuiltinActionType.GET_TEXT: GetTextResult,
    BuiltinActionType.GET_WINDOW: GetWindowResult,
    BuiltinActionType.NAVIGATE: NavigateResult,
    BuiltinActionType.NEW_PAGE: NewPageResult,
    BuiltinActionType.SCREENSHOT: ScreenshotResult,
    BuiltinActionType.LLM: LLMResult,
    BuiltinActionType.FETCH_EXTERNAL_DATA: FetchExternalDataResult,
    BuiltinActionType.PRINT: PrintResult,
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
    BuiltinActionType.GET_WINDOW: GetWindowParams,
    BuiltinActionType.NAVIGATE: NavigateParams,
    BuiltinActionType.NEW_PAGE: NewPageParams,
    BuiltinActionType.SCREENSHOT: ScreenshotParams,
    BuiltinActionType.LLM: LLMParams,
    BuiltinActionType.FETCH_EXTERNAL_DATA: FetchExternalDataParams,
    BuiltinActionType.PRINT: PrintParams,
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
    on_error: OnErrorEnum = OnErrorEnum.STOP
    on_error_branch: list[WorkflowStep] | None = None  # 失败时执行的子步骤（回退/清理）
    input_vars: Dict[str, Any] | None = None
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


class GetWindowWorkflowStep(BaseWorkflowStep[GetWindowParams]):
    """获取窗口属性步骤"""
    action_type: Literal[BuiltinActionType.GET_WINDOW] = BuiltinActionType.GET_WINDOW  # type: ignore[assignment]


class ScreenshotWorkflowStep(BaseWorkflowStep[ScreenshotParams]):
    """截图步骤"""
    action_type: Literal[BuiltinActionType.SCREENSHOT] = BuiltinActionType.SCREENSHOT  # type: ignore[assignment]


class LLMWorkflowStep(BaseWorkflowStep[LLMParams]):
    """LLM 步骤"""
    action_type: Literal[BuiltinActionType.LLM] = BuiltinActionType.LLM  # type: ignore[assignment]


class FetchExternalDataWorkflowStep(BaseWorkflowStep[FetchExternalDataParams]):
    """获取外部数据步骤"""
    action_type: Literal[BuiltinActionType.FETCH_EXTERNAL_DATA] = BuiltinActionType.FETCH_EXTERNAL_DATA  # type: ignore[assignment]


class PrintWorkflowStep(BaseWorkflowStep[PrintParams]):
    """打印参数步骤"""
    action_type: Literal[BuiltinActionType.PRINT] = BuiltinActionType.PRINT  # type: ignore[assignment]


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
    | GetWindowWorkflowStep\
    | ScreenshotWorkflowStep\
    | LLMWorkflowStep\
    | FetchExternalDataWorkflowStep\
    | PrintWorkflowStep\
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
    BuiltinActionType.GET_WINDOW: GetWindowWorkflowStep,
    BuiltinActionType.SCREENSHOT: ScreenshotWorkflowStep,
    BuiltinActionType.LLM: LLMWorkflowStep,
    BuiltinActionType.FETCH_EXTERNAL_DATA: FetchExternalDataWorkflowStep,
    BuiltinActionType.PRINT: PrintWorkflowStep,
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
