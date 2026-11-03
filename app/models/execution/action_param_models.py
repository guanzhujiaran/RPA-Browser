"""操作参数模型（动作入参的强类型定义）

从 ``action_params`` 拆出。包含：

- 参数基类：``BaseActionParams`` / ``SelectorActionParams`` / ``MouseActionParams``
  （类级 ClassVar 声明变量契约，见 ``variable_specs``）；
- 各内置操作的参数模型（``ClickParams`` ~ ``CompositeParams``）与联合类型
  ``AllActionParams``；
- ``BUILTIN_ACTION_PARAMS_MAP``：动作类型 → 参数模型的注册表。
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any, ClassVar, Literal, Type

from pydantic import Field, field_validator, model_validator
from sqlmodel import SQLModel

from bili_common.models.notify import NotifyLevelEnum

from app.models.database.workflow.resource_record import ResourceProcessStatus
from app.models.execution.condition_models import ConditionRule
from app.models.execution.enums import (
    ElementStateEnum,
    HttpBodyTypeEnum,
    HttpMethodEnum,
    KeyboardModifierEnum,
    MouseButtonEnum,
    ScreenshotTypeEnum,
    WaitMatchEnum,
    WaitUntilEnum,
)
from app.models.execution.rpc_method_params import (
    GetAllLotteryRpcParams,
    GetChargeLotteryRpcParams,
    GetOfficialLotteryRpcParams,
    GetOthersLotDynListRpcParams,
    GetReserveLotteryRpcParams,
    GetTopicLotteryRpcParams,
)
from app.models.execution.system_services import RpcMethodName
from app.models.execution.variable_specs import VariableSpec

# 安全字段路径：点分隔标识符 + 数字索引方括号，禁止函数调用/运算符
_SAFE_DOTTED_PATH_RE = re.compile(
    r"[a-zA-Z_$][\w$]*((\.[a-zA-Z_$][\w$]*)|(\[\d+\]))*"
)


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
    debug: bool = Field(
        default=False,
        description=(
            "调试模式（用户级开关，见调度外壳计划书 §9.8）：强制全量采集——"
            "入参/结果/变量全记、成功也记、payload 不截断；"
            "由 action_logger._normalize_debug 归一化，保留期与普通日志一致"
        ),
    )
    record_params: bool = Field(default=True, description="是否记录变量替换后的入参")
    record_result: bool = Field(default=True, description="是否记录执行返回结果")
    record_variables: bool = Field(default=False, description="是否记录变量池快照")
    record_page_snapshot: bool = Field(
        default=False,
        description=(
            "是否记录报错时的页面关键元素快照（可交互元素的 tag/selector/文本摘要，"
            "站点无关）。仅在执行失败时采集，配合 only_on_error 排查「选择器未命中、"
            "验证码/弹窗状态」等页面态问题；体积受 max_payload_length 截断。"
        ),
    )
    only_on_error: bool = Field(default=False, description="仅在执行失败时记录")
    max_payload_length: int = Field(
        default=4000,
        description="params/result/variables 序列化后最大字符数，超出则截断；0 表示不限制",
    )
    retention_days: int = Field(default=30, description="日志保留天数，0 表示永久保留")


class BaseActionParams(SQLModel):
    """操作参数基类 - 所有操作参数模型继承此类

    变量契约（类级 ClassVar 声明，不进 model_json_schema、不参与 params 序列化）：
    - input_variables：动作运行时消费的 {{变量}} 清单（缺失会触发 TemplateResolveError）
    - output_variables：动作执行产出的变量槽位清单（对应 Result 模型字段）
    子类按需覆盖；未声明时由 compute_action_variable_contract 做 {{...}} 模板扫描兜底。
    """

    timeout: float = Field(
        default=30000,
        ge=0,
        le=300000,
        description="最大等待时间（毫秒），默认为 30000。传入 0 禁用超时",
    )
    log: ActionLogOption | None = Field(
        default=None,
        description="操作日志采集选项；为空时按 action 自有配置或服务端兜底决定",
    )

    #: 页面别名：指定该步骤在哪个页面执行（open_page 创建）；空 = 默认页 main
    page: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "页面别名：指定该步骤在哪个页面执行（由 open_page 创建）；"
            "为空时使用默认页面 main"
        ),
    )

    input_variables: ClassVar[list[VariableSpec]] = []
    output_variables: ClassVar[list[VariableSpec]] = []


class SelectorActionParams(BaseActionParams):
    """带元素选择器的操作参数基类"""

    selector: str | None = Field(
        default=None, max_length=500, description="用于定位元素的选择器"
    )


class MouseActionParams(SelectorActionParams):
    """鼠标操作参数基类"""

    position: Position | None = Field(
        default=None, description="相对于元素 padding box 左上角的坐标位置"
    )
    modifiers: list[KeyboardModifierEnum] | None = Field(
        default=None, description="要按下的修饰键 (Alt/Control/Meta/Shift)"
    )
    force: bool = Field(default=False, description="是否绕过可操作性检查，默认为 false")

    @field_validator("position", mode="before")
    @classmethod
    def normalize_position(cls, v: Any) -> Any:
        """将 {"x": null, "y": null} 形式的空坐标转为 None，
        避免 Pydantic 构造 Position(x=None, y=None) 时因 float 不可为 None 而验证失败。"""
        if isinstance(v, dict) and v.get("x") is None and v.get("y") is None:
            return None
        return v


class ClickParams(MouseActionParams):
    """点击操作参数 - 对应 locator.click()"""

    button: MouseButtonEnum = Field(
        default=MouseButtonEnum.LEFT,
        description="要按下的鼠标按钮 (left/right/middle)，默认为 left",
    )
    click_count: int = Field(
        default=1, ge=1, le=3, description="点击次数，默认为 1。参见 UIEvent.detail"
    )
    delay: float = Field(
        default=0,
        ge=0,
        le=10000,
        description="mousedown 和 mouseup 之间等待的时间（毫秒），默认为 0",
    )
    trial: bool = Field(
        default=False, description="仅执行可操作性检查而不执行实际操作，默认为 false"
    )


class InputParams(BaseActionParams):
    """输入操作参数 - 对应 locator.fill()"""

    selector: str = Field(max_length=500, description="用于定位输入框元素的选择器")
    value: str = Field(max_length=10000, description="要输入的文本内容")
    force: bool = Field(default=False, description="是否绕过可操作性检查，默认为 false")


class NavigateParams(BaseActionParams):
    """导航操作参数"""

    url: str = Field(max_length=2048, description="要导航到的 URL 地址")
    wait_until: WaitUntilEnum = Field(
        default=WaitUntilEnum.LOAD, description="导航成功前的等待条件"
    )
    timeout: float = Field(
        default=30000, ge=1000, le=300000, description="导航操作的超时时间（毫秒）"
    )


class NewPageParams(BaseActionParams):
    """新建页面操作参数"""

    url: str | None = Field(
        default=None, max_length=2048, description="新页面的初始 URL"
    )
    wait_until: WaitUntilEnum = Field(
        default=WaitUntilEnum.LOAD, description="导航等待条件（仅在提供 url 时生效）"
    )
    timeout: float = Field(
        default=30000,
        ge=1000,
        le=300000,
        description="导航超时时间（仅在提供 url 时生效）",
    )


class ScrollParams(SelectorActionParams):
    """滚动操作参数

    三种用法（见 ``docs/内置滚动动作参数扩展计划书.md``）：

    - **滚轮模式**：提供 ``x`` / ``y``（任一非 None）→ 分 ``steps`` 次调用
      ``page.mouse.wheel``，与站点无关，会正常触发懒加载 / IntersectionObserver；
    - **元素模式**：提供 ``selector`` → ``locator.scroll_into_view_if_needed()``；
    - **兜底模式**：都不提供 → ``window.scrollTo(0, 0)``（滚回顶部，历史行为）。

    滚轮模式优先于 selector。
    """

    x: int | None = Field(
        default=None,
        ge=-100000,
        le=100000,
        description="水平滚动像素（正数向右）；提供 x/y 时走滚轮相对滚动，忽略 selector",
    )
    y: int | None = Field(
        default=None,
        ge=-100000,
        le=100000,
        description="垂直滚动像素（正数向下）；提供 x/y 时走滚轮相对滚动，忽略 selector",
    )
    steps: int = Field(
        default=1,
        ge=1,
        le=20,
        description="分几步滚完（1=一次到位）；多步+间隔更接近真人，逐步触发懒加载",
    )
    interval_ms: int = Field(
        default=200,
        ge=0,
        le=5000,
        description="滚轮模式下每步之间的间隔（毫秒）",
    )


class WaitParams(SelectorActionParams):
    """等待操作参数

    语义是「选择器命中一批元素，其中是否已有满足 `state` 的」：
    - `match` 缺省时，`visible`/`attached` 按 `any`（任一出现即满足），
      `hidden`/`detached` 按 `all`（全部消失才算消失）；
    - 选择器命中多个元素（如一条评论一个自定义元素）不会因 Playwright 严格模式报错，
      而是按上述范围等待。
    """

    state: ElementStateEnum = Field(
        default=ElementStateEnum.VISIBLE,
        description="等待的元素状态 (visible/hidden/attached/detached)",
    )
    match: WaitMatchEnum | None = Field(
        default=None,
        description=(
            "多元素匹配范围：any=任一满足即返回，all=全部满足才返回；"
            "缺省时 visible/attached 按 any，hidden/detached 按 all"
        ),
    )


class HoverParams(MouseActionParams):
    """悬停操作参数 - 对应 locator.hover()"""

    pass


class GetTextParams(SelectorActionParams):
    """获取元素文本参数 - 对应 locator.all_text_contents()

    两种读取方式：

    - **平铺**（默认，item_selector 为空）：把 selector 命中的每个元素的文本收集成列表；
    - **按条目**（提供 item_selector）：先按 item_selector 切出「条目」，再取每个条目内
      selector 命中的第一个文本，一个条目产生一条结果。条目级还能用 exclude_selector
      剔除整条（例如条目内部出现某个标记节点时跳过该条）。

    结果同时给出拼接文本（text）和原始列表（items），列表可直接交给 random_pick 随机取一条。
    """

    separator: str = Field(
        default="\n",
        max_length=50,
        description="多条文本拼成 text 时的分隔符，默认为换行符",
    )
    item_selector: str = Field(
        default="",
        max_length=300,
        description=(
            "条目选择器（可选）。提供时按条目分组读取：每个条目内只取 selector 的"
            "第一个匹配文本，一个条目一条结果。留空则直接收集 selector 的所有匹配"
        ),
    )
    exclude_selector: str = Field(
        default="",
        max_length=300,
        description=(
            "条目排除选择器（可选，需配合 item_selector）。条目内部命中该选择器时"
            "整条跳过（如置顶标记节点）；支持逗号并列多个选择器。留空表示不按节点排除"
        ),
    )
    exclude_text_selector: str = Field(
        default="",
        max_length=300,
        description=(
            "条目排除文本的选择器（可选，需配合 item_selector）。取条目内该选择器的文本，"
            "与 exclude_text 完全一致（忽略首尾空白）时整条跳过。"
            "例如按用户名排除「UP 主本人的评论」"
        ),
    )
    exclude_text: str = Field(
        default="",
        max_length=500,
        description=(
            "条目排除文本（支持 {{变量}} 模板）。留空时该规则整体不启用，"
            "避免变量解析失败导致所有条目被误排除"
        ),
    )
    fail_on_empty: bool = Field(
        default=False,
        description=(
            "通用开关：选择器未命中或取到的文本为空时，是否把本次动作判为失败。"
            "默认 False（保持「取不到即空串」的既有语义，便于探测类用法）；"
            "需要「取不到必须显式报错」时置 True，失败会写入运行日志并计入失败数。"
        ),
    )

    output_variables: ClassVar[list[VariableSpec]] = [
        VariableSpec(name="text", description="多条文本按 separator 拼接后的内容"),
        VariableSpec(name="items", type="array", description="逐条文本列表（可交给 random_pick）"),
    ]


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
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("property_path 不能为空")
        # 允许：标识符 . 标识符 [数字] 的任意组合，禁止函数调用 () 及其他运算符
        if not _SAFE_DOTTED_PATH_RE.fullmatch(v):
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
        default=ScreenshotTypeEnum.PNG, description="截图格式 (png/jpeg)"
    )
    quality: int = Field(
        default=80,
        ge=1,
        le=100,
        description="JPEG 图片质量（1-100），仅在 type=jpeg 时有效",
    )
    full_page: bool = Field(
        default=False, description="是否截取整个可滚动页面（仅 page.screenshot 支持）"
    )
    omit_background: bool = Field(
        default=False,
        description="是否隐藏默认白色背景并截取透明背景（仅 png 格式支持）",
    )

    output_variables: ClassVar[list[VariableSpec]] = [
        VariableSpec(name="base64", description="Base64 编码的图片数据"),
        VariableSpec(name="size", type="number", description="图片大小(字节)"),
    ]


class LLMModelConfig(SQLModel):
    """LLM 备用模型配置（主模型失败时按顺序回退，各自独立额度池）"""

    model: str = Field(max_length=200, description="模型名称")
    server_url: str = Field(
        max_length=2048,
        description="API 服务器地址",
        # 供前端做通用 URL 校验（前端按 format 判断，不依赖具体字段名）
        json_schema_extra={"format": "uri"},
    )
    api_key: str = Field(max_length=500, description="API 密钥")


def _parse_model_configs(text: str) -> list[dict]:
    """把 JSON 数组文本解析成模型配置列表（兼容模板替换后的 Python repr）"""
    raw = text.strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        try:
            parsed = ast.literal_eval(raw)
        except (ValueError, SyntaxError) as exc:
            raise ValueError(f"不是合法的 JSON / 列表文本: {exc}") from exc
    if not isinstance(parsed, list):
        raise ValueError("必须是数组")
    return parsed


class LLMParams(BaseActionParams):
    """LLM 对话操作参数"""

    server_url: str = Field(max_length=2048, description="API 服务器地址")
    api_key: str = Field(max_length=500, description="API 密钥")
    model: str = Field(max_length=200, description="模型名称")
    prompt: str = Field(default="", max_length=100000, description="单轮对话 prompt")
    system_prompt: str = Field(default="", max_length=10000, description="系统提示词")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="温度参数 0-2")
    max_tokens: int = Field(
        default=2048, ge=1, le=100000, description="最大生成的 token 数"
    )
    timeout: float = Field(
        default=120000, ge=1000, le=600000, description="请求超时时间(毫秒)"
    )
    # 结构化输出
    response_schema: dict | None = Field(
        default=None,
        description="JSON Schema 定义，提供后将启用 LangChain structured output，返回符合 schema 的结构化数据",
    )
    # 备用模型（LangChain with_fallbacks）：主模型抛异常时按顺序回退
    fallbacks: list[LLMModelConfig] = Field(
        default_factory=list,
        description=(
            "备用模型列表（主模型失败时按顺序回退，见 docs/LLM模型回退计划书.md）；"
            "留空=只用主模型，行为与旧版一致"
        ),
    )

    #: 工作流变量注入的备用模型池（JSON 数组文本，通常写成 "{{llm_fallbacks}}"）
    #: 为什么不复用 `fallbacks`：模板字符串会先经过类型校验（详情接口、序列化路径），
    #: 列表字段拿到 "{{var}}" 会校验失败，前端也拿不到数组去渲染。
    fallbacks_json: str | None = Field(
        default=None,
        max_length=20000,
        description=(
            "备用模型池（JSON 数组文本，如 \"[{\\\"model\\\":...}]\"）；"
            "通常填 {{llm_fallbacks}} 由工作流变量注入，与 fallbacks 二选一（fallbacks 优先）"
        ),
    )

    @field_validator("fallbacks", mode="before")
    @classmethod
    def _coerce_fallbacks(cls, value: Any):
        """兼容数组 / JSON 文本 / 模板替换后的 Python repr

        注意：动作 JSON 里若把 `fallbacks` 直接写成 `"{{llm_fallbacks}}"`，在校验阶段
        拿到的只是未解析的模板串——这里返回空列表（不报错），实际池子交给
        `fallbacks_json` 在运行时解析，避免详情接口/步骤反序列化直接失败。
        """
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            if not text or "{{" in text:
                return []
            try:
                return _parse_model_configs(text)
            except ValueError:
                return []
        return value

    output_variables: ClassVar[list[VariableSpec]] = [
        VariableSpec(name="content", description="回复文本内容"),
        VariableSpec(name="structured_data", type="object", description="结构化输出数据（response_schema 提供时有效）"),
    ]


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
        default=None,
        max_length=4096,
        description="HTTP/HTTPS 请求地址（method_name 为空时必填，仅 HTTP 模式生效）",
    )
    method: HttpMethodEnum = Field(
        default=HttpMethodEnum.GET, description="HTTP 方法（仅 HTTP 模式生效）"
    )
    headers: dict[str, str] | None = Field(
        default=None, description="HTTP 附加请求头（仅 HTTP 模式生效）"
    )
    params: dict[str, str] | None = Field(
        default=None, description="HTTP URL 查询参数（仅 HTTP 模式生效）"
    )
    body_type: HttpBodyTypeEnum = Field(
        default=HttpBodyTypeEnum.NONE,
        description="HTTP 请求体类型: none=无, json=JSON, form=表单, raw=原始文本（仅 HTTP 模式生效）",
    )
    body_json: Any | None = Field(
        default=None,
        description="HTTP JSON 请求体（body_type=json 时生效，仅 HTTP 模式生效）",
    )
    body_form: dict[str, str] | None = Field(
        default=None,
        description="HTTP 表单请求体（body_type=form 时生效，仅 HTTP 模式生效）",
    )
    body_raw: str | None = Field(
        default=None,
        max_length=1000000,
        description="HTTP 原始文本请求体（body_type=raw 时生效，仅 HTTP 模式生效）",
    )
    raw_content_type: str | None = Field(
        default=None,
        max_length=200,
        description="HTTP 原始文本请求体的 Content-Type（body_type=raw 时生效，仅 HTTP 模式生效）",
    )
    follow_redirects: bool = Field(
        default=True, description="是否自动跟随重定向（仅 HTTP 模式生效）"
    )
    proxy: str | None = Field(
        default=None,
        max_length=500,
        description="HTTP/HTTPS/SOCKS 代理地址（仅 HTTP 模式生效）",
    )
    timeout: float = Field(
        default=30000, ge=1000, le=300000, description="请求超时时间（毫秒）"
    )
    # RPC 模式专用：每个 method_name 对应独立的强类型 SQLModel 参数字段，与 HTTP params 完全分离
    # 前端根据当前选中的 method_name 仅展示对应字段的表单，各方法参数相互独立
    get_reserve_lottery_params: GetReserveLotteryRpcParams | None = Field(
        default=None,
        description="get_reserve_lottery 方法请求参数（仅 RPC 模式且选择该方法时生效）",
    )
    get_official_lottery_params: GetOfficialLotteryRpcParams | None = Field(
        default=None,
        description="get_official_lottery 方法请求参数（仅 RPC 模式且选择该方法时生效）",
    )
    get_charge_lottery_params: GetChargeLotteryRpcParams | None = Field(
        default=None,
        description="get_charge_lottery 方法请求参数（仅 RPC 模式且选择该方法时生效）",
    )
    get_topic_lottery_params: GetTopicLotteryRpcParams | None = Field(
        default=None,
        description="get_topic_lottery 方法请求参数（仅 RPC 模式且选择该方法时生效）",
    )
    get_all_lottery_params: GetAllLotteryRpcParams | None = Field(
        default=None,
        description="get_all_lottery 方法请求参数（仅 RPC 模式且选择该方法时生效）",
    )
    get_others_lot_dyn_list_params: GetOthersLotDynListRpcParams | None = Field(
        default=None,
        description="get_others_lot_dyn_list 方法请求参数（仅 RPC 模式且选择该方法时生效）",
    )

    # ===== 取数去重（通用主资源处理记录，HTTP/RPC 两种模式均生效）=====
    dedupe: bool = Field(
        default=False,
        description=(
            "是否对返回列表做去重：开启后按 id_field 提取每项主 id，"
            "剔除当前工作流下已处理成功的资源，仅在工作流执行中可用"
        ),
    )
    resource_type: str | None = Field(
        default=None,
        max_length=64,
        description="去重资源类型命名空间（如 lottery/reserve_lottery，可自定义）；dedupe=true 时必填",
    )
    id_field: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "从列表每项中提取主 id 的字段路径，点分隔支持嵌套与数字索引"
            "（如 lottery_id、data.id、items[0].id）；dedupe=true 时必填"
        ),
    )
    list_path: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "当返回数据的列表不在顶层时，定位列表的字段路径"
            "（如 data.list、items[0].records）；留空表示返回 data 本身就是列表"
        ),
    )

    @model_validator(mode="after")
    def check_dedupe_fields(self) -> "FetchExternalDataParams":
        """dedupe 开启时必须提供 resource_type 与 id_field。"""
        if self.dedupe:
            missing = [
                name
                for name, val in (
                    ("resource_type", self.resource_type),
                    ("id_field", self.id_field),
                )
                if not val or not str(val).strip()
            ]
            if missing:
                raise ValueError(
                    "开启 dedupe 时必须提供: " + ", ".join(missing)
                )
        return self

    @field_validator("id_field", "list_path", "resource_type")
    @classmethod
    def validate_dotted_path(cls, v: str | None) -> str | None:
        """安全校验：仅允许点分隔标识符和数字索引方括号，禁止函数调用。"""
        if v is None:
            return v
        v = v.strip()
        if not v:
            return v
        if not _SAFE_DOTTED_PATH_RE.fullmatch(v):
            raise ValueError(
                f"字段路径格式不合法: '{v}'。仅支持点分隔属性路径和数字索引，"
                "如 'data.list'、'items[0].id'，不允许函数调用或变量索引"
            )
        return v

    @model_validator(mode="after")
    def check_mode_exclusive(self) -> "FetchExternalDataParams":
        """确保 method_name 和 url 至少提供一个，且两者互斥

        RpcMethodName.NONE（空值）等价于 None，表示不使用 RPC、走 HTTP 模式。
        """
        is_rpc = self.method_name is not None and self.method_name != RpcMethodName.NONE
        if not is_rpc and not self.url:
            raise ValueError(
                "必须提供 method_name（RPC 模式）或 url（HTTP 模式）中的一个"
            )
        if is_rpc and self.url:
            raise ValueError("method_name 和 url 互斥，不可同时提供")
        return self

    output_variables: ClassVar[list[VariableSpec]] = [
        VariableSpec(name="data", type="object", description="响应数据（JSON 解析结果，无法解析时为 None）"),
        VariableSpec(name="text", description="响应文本（原始字符串）"),
        VariableSpec(name="status_code", type="number", description="HTTP 状态码"),
    ]


class MarkResourceResultParams(BaseActionParams):
    """记录资源处理结果参数 - 配合「获取外部数据 + 去重」做幂等回写

    在工作流循环里处理完单个资源后调用：
        - success=true：记为成功，后续取数去重将默认跳过该资源；
        - success=false：记为失败，下次仍会被取数捞起重试（断点续跑）。
    作用域为 当前用户 + 当前工作流 + resource_type + resource_id（按工作流隔离）。
    """

    resource_type: str = Field(
        max_length=64,
        description="资源类型命名空间（需与取数去重的 resource_type 保持一致）",
    )
    resource_id: str = Field(
        max_length=128,
        description="资源业务主ID（支持 {{var}} 变量替换，如循环项的 {{loop_item.lottery_id}}）",
    )
    success: bool = Field(
        default=True, description="本次处理是否成功（true=成功去重，false=失败可重试）"
    )
    status: ResourceProcessStatus | None = Field(
        default=None,
        description=(
            "显式指定记录状态，优先于 success："
            "success=已处理成功、failed=失败可重试、"
            "skipped=终态跳过（如活动已结束，与 success 一样不再被取数捞起）"
        ),
    )
    fail_reason: str | None = Field(
        default=None,
        max_length=500,
        description="失败原因（success=false 时记录，成功时忽略）",
    )
    extra: dict[str, Any] | None = Field(
        default=None, description="附加快照信息（可选，存入记录的 extra 字段）"
    )


class RandomPickParams(BaseActionParams):
    """随机取值操作参数 - 从列表中等概率随机选取一个元素

    通用工具动作，封装 random.choice 的能力（不访问页面/数据库）。
    列表来源二选一：
        - items_var：指向变量池中某个列表的点路径（如 "comment_pool"、
          "last_output.data.list"），运行时从变量取值；
        - items：直接给常量列表（元素支持 {{var}} 模板替换）。
    优先使用 items_var；两者都为空时动作失败。
    """

    items_var: str = Field(
        default="",
        max_length=200,
        description="列表变量的点路径（如 comment_pool / last_output.data.list）",
    )
    items: list[str] = Field(
        default_factory=list,
        max_length=1000,
        description="常量列表（元素支持 {{var}} 模板）；items_var 为空时使用",
    )
    seed: int | None = Field(
        default=None,
        description="可选随机种子，传入则结果可复现（一般留空）",
    )

    output_variables: ClassVar[list[VariableSpec]] = [
        VariableSpec(name="value", description="随机选中的元素（统一转字符串）"),
        VariableSpec(name="index", type="number", description="选中元素在列表中的下标"),
    ]


class PrintParams(BaseActionParams):
    """打印参数操作参数 - 调试用，打印变量替换后的内容，不执行任何实际操作"""

    message: str = Field(
        default="",
        max_length=10000,
        description="要打印的内容（支持 {{var}} 变量替换），执行时仅打印不执行其他操作",
    )
    dump_variables: bool = Field(
        default=False,
        description=(
            "是否把当前变量池整体打印出来（调试用）：会跳过可调用对象，"
            "长列表/字典只展开前 dump_max_items 项并标注总条数"
        ),
    )
    dump_max_items: int = Field(
        default=5,
        ge=0,
        le=100,
        description="每个列表/字典变量最多展开的条目数；0 表示只打印类型与长度",
    )
    dump_max_length: int = Field(
        default=2000,
        ge=200,
        le=20000,
        description="单个变量序列化后的最大字符数，超出截断",
    )


class NotifyParams(BaseActionParams):
    """发送通知参数（站内系统通知 + 外部推送，见 docs/通知动作与运行告警计划书.md）

    动作只做「发通知」这一通用机制，业务判断（什么时候该发）留给动作 JSON 的 if_else。
    """

    title: str = Field(
        max_length=200, description="通知标题（支持 {{变量}}，带运行唯一标识可避免被幂等吞掉）"
    )
    content: str = Field(
        default="", max_length=4000, description="通知正文（支持 {{变量}}）"
    )
    level: NotifyLevelEnum = Field(
        default=NotifyLevelEnum.NORMAL,
        description="通知级别：1 普通 / 2 重要 / 3 紧急（决定外部推送主题 [i]/[w]/[f]）",
    )
    jump_url: str | None = Field(
        default=None, max_length=1000, description="站内通知的跳转地址（前端路由）"
    )
    channel: Literal["both", "inbox", "push"] = Field(
        default="both", description="投递通道：both=两者都发 / inbox=仅站内 / push=仅外部推送"
    )
    dedupe_key: str | None = Field(
        default=None,
        max_length=200,
        description="去重键（同一 mid + key 在去重窗口内只发一次）；留空用 title",
    )
    once_per_day: bool = Field(
        default=True,
        description="按服务时区自然日去重：同一天内相同 key 只发一次，避免循环里刷屏",
    )


class ShuffleListParams(BaseActionParams):
    """打乱列表操作参数 - 原地随机打乱变量池中的一个列表

    列表来源 items_var 指向变量池中某个列表的点路径（如 lottery_pool、
    last_output.data.items）。打乱后原地写回该变量，并把打乱后的列表设为 last_output。
    """

    items_var: str = Field(
        description="列表变量的点路径（如 lottery_pool / last_output.data.items）",
        max_length=200,
    )
    seed: int | None = Field(
        default=None, description="可选随机种子，传入则结果可复现（一般留空）"
    )


class SortListParams(BaseActionParams):
    """列表排序操作参数 - 按指定字段对变量池中的列表排序

    列表来源 items_var 指向变量池中某个列表的点路径。排序后原地写回该变量，
    并把排序后的列表设为 last_output。
    """

    items_var: str = Field(
        description="列表变量的点路径（如 lottery_pool / last_output.data.items）",
        max_length=200,
    )
    field: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "排序字段（点路径，如 repostCount、extra_info.likeCount）；"
            "留空时按元素本身排序"
        ),
    )
    order: Literal["asc", "desc"] = Field(
        default="desc", description="排序方向: asc 升序 / desc 降序"
    )
    numeric: bool = Field(
        default=True, description="是否按数值排序（字段值无法转数值时按 0 处理）"
    )


class PickLineParams(BaseActionParams):
    """按行随机取值操作参数 - 从一段多行文本中随机取一条

    通用工具动作（不访问页面/数据库）：把 text_var 指向的文本按换行切分、
    过滤空行后随机取一条，写入 variables[output_var]。
    """

    text_var: str = Field(
        default="",
        max_length=200,
        description="多行文本的变量点路径（如 dyn_comments / last_output.text）",
    )
    output_var: str = Field(
        default="picked_line",
        max_length=50,
        description="取到的行写入的变量名（供后续步骤 {{var}} 引用）",
    )
    seed: int | None = Field(
        default=None, description="可选随机种子，传入则结果可复现（一般留空）"
    )


class BiliReserveParams(BaseActionParams):
    """参与 B 站空间预约抽奖参数 - DOM 流程

    导航到预约页面（space 空间页），在预约卡片列表中定位到未参与的预约并点击“预约”按钮，
    通过点击后是否出现“取消预约”按钮判断是否参与成功。
    """

    reserve_url: str = Field(
        max_length=2048, description="预约所在的空间页面地址（如 https://space.bilibili.com/xxx）"
    )
    reserve_sid: str | None = Field(
        default=None,
        max_length=128,
        description="直播预约 sid（可选）；提供且卡片带 sid 属性时用于精确定位",
    )
    card_selector: str = Field(
        default=".subscribe-section-warp",
        max_length=300,
        description="预约卡片列表项选择器（空间页「预约」模块的单个卡片）",
    )
    sid_attr: str = Field(
        default="data-sid",
        max_length=100,
        description="卡片上存放 sid 的属性名（用于按 reserve_sid 精确定位）",
    )
    join_button_selector: str = Field(
        default="button.btn",
        max_length=300,
        description="预约（参与）按钮选择器；相对卡片定位，勿写全路径",
    )
    cancel_button_selector: str = Field(
        default="button.btn:has-text('已预约')",
        max_length=300,
        description="已参与按钮选择器；相对卡片定位，已参与态文案为「已预约」",
    )
    wait_timeout: float = Field(
        default=15000,
        ge=1000,
        le=120000,
        description="等待卡片/按钮出现的超时时间（毫秒）",
    )
    click_retries: int = Field(
        default=3,
        ge=1,
        le=5,
        description=(
            "点击「预约」后未出现「已预约」时的最大尝试次数。"
            "空间页首屏渲染完成后会重新挂载预约模块，紧贴卡片出现的那次点击"
            "会被页面重渲染吞掉（Playwright 报 clicked=true 但业务无效），"
            "因此需要重试一次才真正生效"
        ),
    )
    verify_timeout: float = Field(
        default=4000,
        ge=500,
        le=15000,
        description=(
            "每次点击后轮询「已预约」按钮出现的时长（毫秒）；"
            "超时仍未出现则判定本次点击被吞，进入下一次尝试"
        ),
    )
    retry_interval: float = Field(
        default=1000,
        ge=200,
        le=5000,
        description="两次点击尝试之间的间隔（毫秒）",
    )


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

    循环名称 (name)：
        可选的展示名，用于运行消息 / 进度条里标识「这个循环在干什么」。
        支持 {{变量}} 模板且**每轮按当前作用域解析**（此时 loop_item /
        loop_index 已就绪），如 "处理 {{loop_item.title}}" 在第 3 轮展示为
        「处理 某某的动态」；变量缺失时保留字面量，不影响循环执行。
        留空时回落 action_id（"loop"），与历史行为一致。
    """

    # ── 循环名称（可选，展示用；支持 {{变量}} 模板逐轮解析）──
    name: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "循环显示名称（支持 {{变量}} 模板，如 '处理 {{loop_item.title}}'），"
            "用于运行消息 / 进度中标识本循环在做什么；留空回落 'loop'"
        ),
    )

    # ── 循环来源配置 ──
    loop_source: Literal["fixed_count", "variable", "expression", "json_list"] = Field(
        default="fixed_count",
        description="循环来源: fixed_count=固定次数, variable=从变量获取items, expression=表达式, json_list=直接传入JSON列表",
    )
    count: int = Field(
        default=1,
        ge=1,
        le=10000,
        description="固定循环次数（loop_source=fixed_count 时使用）",
    )
    loop_items_var: str | None = Field(
        default=None,
        max_length=200,
        description="循环项变量引用（loop_source=variable 时使用），如 'previous_output.items'",
    )
    loop_items_expr: str | None = Field(
        default=None,
        max_length=500,
        description="循环项表达式（loop_source=expression 时使用）",
    )
    loop_items_json: list[Any] | None = Field(
        default=None,
        description="直接传入的 JSON 列表（loop_source=json_list 时使用），支持任意嵌套结构",
    )

    # ── 循环变量命名 ──
    loop_item_var: str = Field(
        default="loop_item",
        max_length=50,
        description="当前循环项在作用域中的变量名",
    )
    loop_index_var: str = Field(
        default="loop_index",
        max_length=50,
        description="当前循环索引在作用域中的变量名",
    )

    # ── 参数映射（可选）──
    param_mapping: dict[str, str] | None = Field(
        default=None,
        description="参数映射: { 目标参数名: 源字段路径 }。源路径基于循环项，支持点分隔嵌套访问",
    )

    # ── 每轮固定间隔（成功/失败都生效，类似 finally）──
    iteration_wait_seconds: float = Field(
        default=0,
        ge=0,
        le=600,
        description=(
            "每轮循环结束后的固定等待秒数（成功与失败都等待，类似 finally）；"
            "0 表示不等待。用于控制整批节奏、降低连续操作触发的风控"
        ),
    )

    # ── 循环体 ──
    loopBranch: list["WorkflowStep"] | None = Field(
        default=None, description="循环分支步骤"
    )

    # ── 控制条件 ──
    break_condition: ConditionRule | None = Field(
        default=None,
        description="break 条件规则（结构化条件），每次迭代开始前评估，为真时跳出整个循环",
    )
    continue_condition: ConditionRule | None = Field(
        default=None,
        description="continue 条件规则（结构化条件），每次迭代开始前评估，为真时跳过当前迭代",
    )

    # 向后兼容旧字段
    loop_count: int | None = Field(
        default=None, exclude=True, description="[已废弃] 使用 count + loop_source"
    )
    loop_while: str | None = Field(
        default=None, exclude=True, description="[已废弃] 使用 break_condition"
    )
    items: list[Any] | None = Field(
        default=None, exclude=True, description="[已废弃] 使用 loop_items_var"
    )
    loop_var: str | None = Field(
        default=None, exclude=True, description="[已废弃] 使用 loop_item_var"
    )


class IfElseParams(BaseActionParams):
    """条件分支控制流操作参数

    条件二选一：
    - `condition`：结构化条件（只能与字面量比较，如 `field == true`）；
    - `expression`：表达式条件（AST 白名单，不做 eval），支持**变量之间**比较，
      如 `self_nickname in latest_comment_user`。
    """

    condition: ConditionRule | None = Field(
        default=None, description="条件规则（结构化条件，不使用 eval）；与 expression 二选一"
    )
    expression: str | None = Field(
        default=None,
        max_length=500,
        description=(
            "表达式条件（AST 白名单，无 eval）：可引用变量名做比较，"
            "如 `self_nickname in latest_comment_user`；与 condition 二选一"
        ),
    )
    TrueBranch: list["WorkflowStep"] | None = Field(
        default=None, description="真分支步骤"
    )
    FalseBranch: list["WorkflowStep"] | None = Field(
        default=None, description="假分支步骤"
    )

    @model_validator(mode="after")
    def _validate_condition_source(self) -> "IfElseParams":
        has_rule = self.condition is not None
        has_expr = bool(self.expression and self.expression.strip())
        if has_rule and has_expr:
            raise ValueError("condition（结构化条件）与 expression（表达式）互斥")
        if not has_rule and not has_expr:
            raise ValueError("必须提供 condition 或 expression 之一")
        return self


class CompositeParams(BaseActionParams):
    """复合操作参数"""

    steps: list["WorkflowStep"] = Field(
        default_factory=list, description="复合操作步骤"
    )


# ─────────────── 多页面 / 并行（见 docs/多页面并行执行计划书.md） ───────────────


class OpenPageParams(BaseActionParams):
    """按别名打开新页面。

    页面数达到 ``browser_max_pages_per_context`` 时**等待空位**（不会关闭任何
    已有页面），等待超时返回可读业务错误。
    """

    name: str = Field(
        max_length=64,
        description="页面别名（后续步骤用 params.page 引用），同一运行内唯一",
    )
    url: str | None = Field(
        default=None, max_length=2048, description="新页面初始 URL（可选）"
    )
    wait_until: WaitUntilEnum = Field(
        default=WaitUntilEnum.LOAD, description="导航等待条件（仅在提供 url 时生效）"
    )
    nav_timeout: float = Field(
        default=30000, ge=1000, le=300000, description="导航超时（毫秒）"
    )
    acquire_timeout: float = Field(
        default=60,
        ge=0,
        le=3600,
        description="页面已满时等待空位的最长时间（秒）",
    )

    output_variables: ClassVar[list[VariableSpec]] = [
        VariableSpec(name="page_alias", description="新页面别名"),
    ]


class ClosePageParams(BaseActionParams):
    """关闭指定别名的页面并释放容量（唤醒等待中的 open_page）。"""

    name: str = Field(max_length=64, description="要关闭的页面别名（不能是 main）")


class ParallelBranch(SQLModel):
    """并行分支定义。"""

    name: str = Field(
        max_length=64, description="分支名（日志前缀与变量命名空间），同层唯一"
    )
    page: str | None = Field(
        default=None,
        max_length=64,
        description="该分支默认页面别名；分支内步骤未显式指定 page 时使用",
    )
    steps: list["WorkflowStep"] = Field(
        default_factory=list, description="分支内顺序执行的步骤"
    )
    export: list[str] = Field(
        default_factory=list,
        description="分支结束后要导出到父作用域的变量名（避免并发写冲突）",
    )


class ParallelParams(BaseActionParams):
    """多个分支并行执行（每个分支可绑定独立页面）。"""

    branches: list[ParallelBranch] = Field(
        default_factory=list, description="并行分支列表"
    )
    on_branch_error: Literal["continue", "abort"] = Field(
        default="continue",
        description="单个分支失败时的策略：continue=其它分支继续跑完；abort=取消其余分支",
    )




AllActionParams = (
    ClickParams
    | InputParams
    | NavigateParams
    | NewPageParams
    | ScrollParams
    | WaitParams
    | HoverParams
    | GetTextParams
    | GetWindowParams
    | ScreenshotParams
    | LLMParams
    | FetchExternalDataParams
    | MarkResourceResultParams
    | RandomPickParams
    | PrintParams
    | ShuffleListParams
    | SortListParams
    | PickLineParams
    | BiliReserveParams
    | NotifyParams
    | LoopParams
    | IfElseParams
    | CompositeParams
    | OpenPageParams
    | ClosePageParams
    | ParallelParams
)

# endregion


# endregion

# 动作类型 → 参数模型注册表。
# key 为 BuiltinActionType 成员的 value 字符串（StrEnum 与其 value 哈希相等，
# 故 ``MAP.get(BuiltinActionType.CLICK)`` 与 ``MAP.get("click")`` 等价）；
# 刻意不用枚举作 key：本模块若 import builtin_action_types，会与枚举上的
# params_model 属性形成循环导入。新增动作时需与此处保持一一对应。
BUILTIN_ACTION_PARAMS_MAP: dict[str, Type[AllActionParams]] = {
    "click": ClickParams,
    "input": InputParams,
    "scroll": ScrollParams,
    "wait": WaitParams,
    "hover": HoverParams,
    "get_text": GetTextParams,
    "get_window": GetWindowParams,
    "navigate": NavigateParams,
    "new_page": NewPageParams,
    "screenshot": ScreenshotParams,
    "llm": LLMParams,
    "fetch_external_data": FetchExternalDataParams,
    "mark_resource_result": MarkResourceResultParams,
    "random_pick": RandomPickParams,
    "print": PrintParams,
    "shuffle_list": ShuffleListParams,
    "sort_list": SortListParams,
    "pick_line": PickLineParams,
    "notify": NotifyParams,
    "bili_reserve": BiliReserveParams,
    "loop": LoopParams,
    "if_else": IfElseParams,
    "composite": CompositeParams,
    "open_page": OpenPageParams,
    "close_page": ClosePageParams,
    "parallel": ParallelParams,
}
