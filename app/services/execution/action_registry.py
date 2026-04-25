"""
操作注册表系统

核心设计:
1. 所有浏览器操作都通过 ActionRegistry 注册
2. 操作可以指定前置条件、后置处理
3. 支持操作链：多个操作可以组合成一个工作流
4. 操作执行结果可以传递给下一个操作
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Type
from enum import StrEnum

from app.models.RPA_browser.custom_execution_models import CustomActionModel
from app.utils.depends.session_manager import DatabaseSessionManager
from sqlmodel import select
import asyncio
import time

from app.services.execution.user_plugin import BaseCustomPlugin


class ActionType(StrEnum):
    """操作类型"""

    NAVIGATION = "navigation"  # 导航操作
    CLICK = "click"  # 点击操作
    INPUT = "input"  # 输入操作
    SCROLL = "scroll"  # 滚动操作
    HOVER = "hover"  # 悬停操作
    WAIT = "wait"  # 等待操作
    SCREENSHOT = "screenshot"  # 截图操作
    EVALUATE = "evaluate"  # JavaScript执行
    SELECT = "select"  # 下拉选择
    KEYBOARD = "keyboard"  # 键盘操作
    MOUSE = "mouse"  # 鼠标操作
    LLM = "llm"  # LLM对话
    CUSTOM = "custom"  # 自定义操作


@dataclass
class ActionParameter:
    """操作参数定义"""

    name: str
    type: Type | str
    required: bool = True
    default: Any = None
    description: str = ""
    validator: Optional[Callable[[Any], bool]] = None


@dataclass
class ActionMetadata:
    """操作元数据"""

    id: str
    name: str
    type: ActionType
    description: str = ""
    parameters: List[ActionParameter] = field(default_factory=list)
    timeout: int = 30000  # 默认超时30秒
    retry_on_error: bool = False
    retry_times: int = 0
    retry_delay: float = 1.0
    requires_browser: bool = True  # 是否需要浏览器上下文


@dataclass
class ActionResult:
    """操作执行结果"""

    success: bool
    data: Any = None
    error: Optional[str] = None
    execution_time: float = 0.0
    action_id: str = ""
    action_name: str = ""


@dataclass
class ActionContext:
    """操作执行上下文"""

    session_id: str
    browser_id: str
    page: Any  # Playwright Page 对象
    browser: Any  # Playwright Browser 对象
    params: Dict[str, Any] = field(default_factory=dict)
    user_data: Dict[str, Any] = field(default_factory=dict)
    plugins: List[BaseCustomPlugin] = field(default_factory=list)


class BaseAction(ABC):
    """
    操作基类

    所有浏览器操作都需要继承此类并实现 execute 方法
    """

    def __init__(self):
        self.metadata = self.get_metadata()

    @abstractmethod
    def get_metadata(self) -> ActionMetadata:
        """返回操作元数据"""
        ...

    @abstractmethod
    async def execute(self, ctx: ActionContext) -> ActionResult:
        """执行操作"""
        ...

    def validate_params(self, params: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        验证参数

        Returns:
            (是否有效, 错误信息)
        """
        for param_def in self.metadata.parameters:
            if param_def.required and param_def.name not in params:
                return False, f"缺少必需参数: {param_def.name}"

            if param_def.name in params:
                value = params[param_def.name]
                if param_def.validator and not param_def.validator(value):
                    return False, f"参数 {param_def.name} 验证失败"

        return True, None


class ClickAction(BaseAction):
    """点击操作"""

    def get_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            id="click",
            name="点击",
            type=ActionType.CLICK,
            description="点击页面元素",
            parameters=[
                ActionParameter(
                    name="selector",
                    type=str,
                    required=True,
                    description="CSS选择器或xpath",
                ),
                ActionParameter(
                    name="button",
                    type=str,
                    required=False,
                    default="left",
                    description="鼠标按钮: left, right, middle",
                ),
                ActionParameter(
                    name="click_count",
                    type=int,
                    required=False,
                    default=1,
                    description="点击次数",
                ),
                ActionParameter(
                    name="delay",
                    type=int,
                    required=False,
                    default=0,
                    description="点击前延迟(毫秒)",
                ),
                ActionParameter(
                    name="position",
                    type=dict,
                    required=False,
                    default=None,
                    description="点击位置: {x: 0-1, y: 0-1} 相对坐标",
                ),
            ],
        )

    async def execute(self, ctx: ActionContext) -> ActionResult:
        start_time = time.time()

        selector = ctx.params.get("selector")
        button = ctx.params.get("button", "left")
        click_count = ctx.params.get("click_count", 1)
        delay = ctx.params.get("delay", 0)
        position = ctx.params.get("position")

        try:
            if delay > 0:
                await asyncio.sleep(delay / 1000)

            if position:
                # 使用相对坐标
                viewport = ctx.page.viewport_size or await ctx.page.evaluate(
                    """() => ({
                        width: window.innerWidth,
                        height: window.innerHeight
                    })"""
                )
                abs_x = int(position["x"] * viewport["width"])
                abs_y = int(position["y"] * viewport["height"])

                if click_count == 2:
                    await ctx.page.dblclick(x=abs_x, y=abs_y, button=button)
                else:
                    await ctx.page.click(x=abs_x, y=abs_y, button=button)
            else:
                locator = ctx.page.locator(selector)
                if click_count == 2:
                    await locator.dblclick(button=button)
                else:
                    await locator.click(button=button)

            return ActionResult(
                success=True,
                data={"selector": selector, "button": button},
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        except Exception as e:
            return ActionResult(
                success=False,
                error=str(e),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )


class InputAction(BaseAction):
    """输入操作"""

    def get_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            id="input",
            name="输入",
            type=ActionType.INPUT,
            description="向输入框输入文本",
            parameters=[
                ActionParameter(
                    name="selector", type=str, required=True, description="输入框选择器"
                ),
                ActionParameter(
                    name="value", type=str, required=True, description="输入的值"
                ),
                ActionParameter(
                    name="delay",
                    type=int,
                    required=False,
                    default=0,
                    description="逐字输入延迟(毫秒)",
                ),
                ActionParameter(
                    name="clear_first",
                    type=bool,
                    required=False,
                    default=True,
                    description="输入前清空",
                ),
            ],
        )

    async def execute(self, ctx: ActionContext) -> ActionResult:
        start_time = time.time()

        selector = ctx.params.get("selector")
        value = ctx.params.get("value")
        delay = ctx.params.get("delay", 0)
        clear_first = ctx.params.get("clear_first", True)

        try:
            locator = ctx.page.locator(selector)

            if clear_first:
                await locator.clear()

            if delay > 0:
                # 模拟打字效果
                for char in value:
                    await locator.type(char)
                    await asyncio.sleep(delay / 1000)
            else:
                await locator.fill(value)

            return ActionResult(
                success=True,
                data={"selector": selector, "value_length": len(value)},
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        except Exception as e:
            return ActionResult(
                success=False,
                error=str(e),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )


class NavigateAction(BaseAction):
    """导航操作"""

    def get_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            id="navigate",
            name="导航",
            type=ActionType.NAVIGATION,
            description="导航到指定URL",
            parameters=[
                ActionParameter(
                    name="url", type=str, required=True, description="目标URL"
                ),
                ActionParameter(
                    name="wait_until",
                    type=str,
                    required=False,
                    default="load",
                    description="等待条件: load, domcontentloaded, networkidle",
                ),
                ActionParameter(
                    name="timeout",
                    type=int,
                    required=False,
                    default=30000,
                    description="超时时间(毫秒)",
                ),
            ],
        )

    async def execute(self, ctx: ActionContext) -> ActionResult:
        start_time = time.time()

        url = ctx.params.get("url")
        wait_until = ctx.params.get("wait_until", "load")
        timeout = ctx.params.get("timeout", 30000)

        try:
            response = await ctx.page.goto(url, wait_until=wait_until, timeout=timeout)

            return ActionResult(
                success=True,
                data={
                    "url": url,
                    "status": response.status if response else None,
                },
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        except Exception as e:
            return ActionResult(
                success=False,
                error=str(e),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )


class EvaluateAction(BaseAction):
    """JavaScript执行操作"""

    def get_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            id="evaluate",
            name="执行JS",
            type=ActionType.EVALUATE,
            description="在页面中执行JavaScript代码",
            parameters=[
                ActionParameter(
                    name="code", type=str, required=True, description="JavaScript代码"
                ),
                ActionParameter(
                    name="args",
                    type=list,
                    required=False,
                    default=[],
                    description="传递给脚本的参数",
                ),
                ActionParameter(
                    name="timeout",
                    type=int,
                    required=False,
                    default=30000,
                    description="超时时间(毫秒)",
                ),
            ],
        )

    async def execute(self, ctx: ActionContext) -> ActionResult:
        start_time = time.time()

        code = ctx.params.get("code")
        args = ctx.params.get("args", [])
        timeout = ctx.params.get("timeout", 30000)

        try:
            result = await asyncio.wait_for(
                ctx.page.evaluate(code, args), timeout=timeout / 1000
            )

            return ActionResult(
                success=True,
                data=result,
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        except asyncio.TimeoutError:
            return ActionResult(
                success=False,
                error="执行超时",
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )
        except Exception as e:
            return ActionResult(
                success=False,
                error=str(e),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )


class ScrollAction(BaseAction):
    """滚动操作"""

    def get_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            id="scroll",
            name="滚动",
            type=ActionType.SCROLL,
            description="滚动页面或元素",
            parameters=[
                ActionParameter(
                    name="selector",
                    type=str,
                    required=False,
                    default=None,
                    description="要滚动的元素选择器，None表示滚动页面",
                ),
                ActionParameter(
                    name="x",
                    type=int,
                    required=False,
                    default=0,
                    description="水平滚动距离",
                ),
                ActionParameter(
                    name="y",
                    type=int,
                    required=False,
                    default=0,
                    description="垂直滚动距离",
                ),
                ActionParameter(
                    name="behavior",
                    type=str,
                    required=False,
                    default="auto",
                    description="滚动行为: auto, smooth",
                ),
            ],
        )

    async def execute(self, ctx: ActionContext) -> ActionResult:
        start_time = time.time()

        selector = ctx.params.get("selector")
        x = ctx.params.get("x", 0)
        y = ctx.params.get("y", 0)
        behavior = ctx.params.get("behavior", "auto")

        try:
            if selector:
                await ctx.page.locator(selector).scroll_by(x=x, y=y, behavior=behavior)
            else:
                if behavior == "smooth":
                    await ctx.page.evaluate(
                        f"window.scrollBy({{top: {y}, left: {x}, behavior: 'smooth'}})"
                    )
                else:
                    await ctx.page.evaluate(f"window.scrollBy({{top: {y}, left: {x}}})")

            return ActionResult(
                success=True,
                data={"selector": selector, "x": x, "y": y},
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        except Exception as e:
            return ActionResult(
                success=False,
                error=str(e),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )


class WaitAction(BaseAction):
    """等待操作"""

    def get_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            id="wait",
            name="等待",
            type=ActionType.WAIT,
            description="等待指定时间或条件",
            parameters=[
                ActionParameter(
                    name="selector",
                    type=str,
                    required=False,
                    default=None,
                    description="等待元素出现的选择器",
                ),
                ActionParameter(
                    name="timeout",
                    type=int,
                    required=False,
                    default=30000,
                    description="超时时间(毫秒)",
                ),
                ActionParameter(
                    name="state",
                    type=str,
                    required=False,
                    default="visible",
                    description="元素状态: visible, hidden, attached, detached",
                ),
                ActionParameter(
                    name="duration",
                    type=int,
                    required=False,
                    default=None,
                    description="固定等待时间(毫秒)",
                ),
            ],
        )

    async def execute(self, ctx: ActionContext) -> ActionResult:
        start_time = time.time()

        selector = ctx.params.get("selector")
        timeout = ctx.params.get("timeout", 30000)
        state = ctx.params.get("state", "visible")
        duration = ctx.params.get("duration")

        try:
            if duration:
                await asyncio.sleep(duration / 1000)
            elif selector:
                await ctx.page.wait_for_selector(selector, state=state, timeout=timeout)
            else:
                await asyncio.sleep(timeout / 1000)

            return ActionResult(
                success=True,
                data={"selector": selector, "state": state},
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        except Exception as e:
            return ActionResult(
                success=False,
                error=str(e),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )


class ScreenshotAction(BaseAction):
    """截图操作"""

    def get_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            id="screenshot",
            name="截图",
            type=ActionType.SCREENSHOT,
            description="对页面或元素截图",
            parameters=[
                ActionParameter(
                    name="selector",
                    type=str,
                    required=False,
                    default=None,
                    description="元素选择器，None表示全页截图",
                ),
                ActionParameter(
                    name="full_page",
                    type=bool,
                    required=False,
                    default=False,
                    description="是否全页截图",
                ),
                ActionParameter(
                    name="type",
                    type=str,
                    required=False,
                    default="png",
                    description="图片类型: png, jpeg",
                ),
                ActionParameter(
                    name="quality",
                    type=int,
                    required=False,
                    default=80,
                    description="图片质量(jpeg有效)",
                ),
            ],
        )

    async def execute(self, ctx: ActionContext) -> ActionResult:
        start_time = time.time()

        selector = ctx.params.get("selector")
        full_page = ctx.params.get("full_page", False)
        img_type = ctx.params.get("type", "png")
        quality = ctx.params.get("quality", 80)

        try:
            if selector:
                element = ctx.page.locator(selector)
                image_bytes = await element.screenshot(type=img_type, quality=quality)
            else:
                image_bytes = await ctx.page.screenshot(
                    full_page=full_page, type=img_type, quality=quality
                )

            import base64

            image_base64 = base64.b64encode(image_bytes).decode()

            return ActionResult(
                success=True,
                data={
                    "format": img_type,
                    "size": len(image_bytes),
                    "base64": image_base64[:100] + "...",  # 截断显示
                },
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        except Exception as e:
            return ActionResult(
                success=False,
                error=str(e),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )


class LLMAction(BaseAction):
    """
    LLM 对话操作

    支持调用自定义 LLM API (OpenAI 兼容格式)
    - 支持自定义 server_url, api_key, model
    - 支持多轮对话上下文
    - 返回值可传递给下一个 step
    """

    def get_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            id="llm",
            name="LLM对话",
            type=ActionType.LLM,
            description="调用 LLM 进行对话，支持 OpenAI 兼容 API",
            parameters=[
                ActionParameter(
                    name="server_url",
                    type=str,
                    required=True,
                    description="API 服务器地址，如 https://api.openai.com/v1",
                ),
                ActionParameter(
                    name="api_key", type=str, required=True, description="API 密钥"
                ),
                ActionParameter(
                    name="model",
                    type=str,
                    required=True,
                    description="模型名称，如 gpt-4o-mini, gpt-3.5-turbo",
                ),
                ActionParameter(
                    name="messages",
                    type=list,
                    required=False,
                    default=[],
                    description="消息列表 [{role: 'user'|'assistant'|'system', content: '...'}, ...]",
                ),
                ActionParameter(
                    name="prompt",
                    type=str,
                    required=False,
                    default="",
                    description="单轮对话 prompt，将自动添加到 messages",
                ),
                ActionParameter(
                    name="system_prompt",
                    type=str,
                    required=False,
                    default="",
                    description="系统提示词，会作为首条 system 消息发送",
                ),
                ActionParameter(
                    name="temperature",
                    type=float,
                    required=False,
                    default=0.7,
                    description="温度参数 0-2，控制随机性",
                ),
                ActionParameter(
                    name="max_tokens",
                    type=int,
                    required=False,
                    default=2048,
                    description="最大生成的 token 数",
                ),
                ActionParameter(
                    name="timeout",
                    type=int,
                    required=False,
                    default=120000,
                    description="请求超时时间(毫秒)",
                ),
            ],
        )

    async def execute(self, ctx: ActionContext) -> ActionResult:
        start_time = time.time()

        server_url = ctx.params.get("server_url")
        api_key = ctx.params.get("api_key")
        model = ctx.params.get("model")
        messages = ctx.params.get("messages", [])
        prompt = ctx.params.get("prompt", "")
        system_prompt = ctx.params.get("system_prompt", "")
        temperature = ctx.params.get("temperature", 0.7)
        max_tokens = ctx.params.get("max_tokens", 2048)
        timeout = ctx.params.get("timeout", 120000)

        try:
            import httpx

            # 构建消息列表
            final_messages = []

            # 添加系统提示
            if system_prompt:
                final_messages.append({"role": "system", "content": system_prompt})

            # 添加历史消息
            for msg in messages:
                if isinstance(msg, dict) and "role" in msg and "content" in msg:
                    final_messages.append(msg)

            # 添加当前 prompt
            if prompt:
                final_messages.append({"role": "user", "content": prompt})

            if not final_messages:
                return ActionResult(
                    success=False,
                    error="messages 或 prompt 不能同时为空",
                    execution_time=time.time() - start_time,
                    action_id=self.metadata.id,
                    action_name=self.metadata.name,
                )

            # 构建请求
            endpoint = f"{server_url.rstrip('/')}/chat/completions"

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

            payload = {
                "model": model,
                "messages": final_messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }

            async with httpx.AsyncClient(timeout=timeout / 1000) as client:
                response = await client.post(endpoint, json=payload, headers=headers)

                if response.status_code != 200:
                    error_detail = response.text
                    return ActionResult(
                        success=False,
                        error=f"API 请求失败 ({response.status_code}): {error_detail}",
                        execution_time=time.time() - start_time,
                        action_id=self.metadata.id,
                        action_name=self.metadata.name,
                    )

                result_data = response.json()

            # 解析响应
            if "choices" not in result_data or not result_data["choices"]:
                return ActionResult(
                    success=False,
                    error="API 响应格式异常，未找到 choices",
                    execution_time=time.time() - start_time,
                    action_id=self.metadata.id,
                    action_name=self.metadata.name,
                )

            choice = result_data["choices"][0]
            message = choice.get("message", {})
            content = message.get("content", "")
            role = message.get("role", "assistant")

            # 构建返回数据，兼容 next step 传递
            response_data = {
                "content": content,
                "role": role,
                "model": model,
                "usage": result_data.get("usage", {}),
                "raw_response": result_data,
                # 为了兼容下一步使用，添加常用字段
                "text": content,
                "answer": content,
                "result": content,
            }

            return ActionResult(
                success=True,
                data=response_data,
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        except httpx.TimeoutException:
            return ActionResult(
                success=False,
                error=f"请求超时 ({timeout}ms)",
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )
        except httpx.RequestError as e:
            return ActionResult(
                success=False,
                error=f"请求失败: {str(e)}",
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )
        except Exception as e:
            return ActionResult(
                success=False,
                error=str(e),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )


class CompositeAction(BaseAction):
    """
    组合操作

    将多个基础操作组合成一个自定义操作
    支持参数模板替换: {{param_name}}
    """

    def __init__(
        self, action_id: str, name: str, description: str, steps: List[Dict[str, Any]]
    ):
        self._action_id = action_id
        self._name = name
        self._description = description
        self._steps = steps
        self._registry = None  # 将在 execute 时设置

    def set_registry(self, registry: "ActionRegistry"):
        """设置操作注册表引用"""
        self._registry = registry

    def get_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            id=self._action_id,
            name=self._name,
            type=ActionType.CUSTOM,
            description=self._description,
            parameters=[],  # 组合操作的参数由子步骤提供
            requires_browser=True,
        )

    def _replace_params(self, text: str, params: Dict[str, Any]) -> Any:
        """替换模板参数"""
        if isinstance(text, str):
            result = text
            for key, value in params.items():
                result = result.replace(f"{{{{{key}}}}}", str(value))
            return result
        elif isinstance(text, dict):
            return {k: self._replace_params(v, params) for k, v in text.items()}
        elif isinstance(text, list):
            return [self._replace_params(item, params) for item in text]
        return text

    async def execute(self, ctx: ActionContext) -> ActionResult:
        start_time = time.time()

        if not self._registry:
            return ActionResult(
                success=False,
                error="操作注册表未初始化",
                execution_time=time.time() - start_time,
                action_id=self._action_id,
                action_name=self._name,
            )

        results = []
        for i, step in enumerate(self._steps):
            # 替换参数中的模板
            step_params = self._replace_params(step.get("params", {}), ctx.params)

            # 创建子操作上下文
            sub_ctx = ActionContext(
                session_id=ctx.session_id,
                browser_id=ctx.browser_id,
                page=ctx.page,
                browser=ctx.browser,
                params=step_params,
                user_data=ctx.user_data,
                plugins=ctx.plugins,
            )

            # 执行子操作
            sub_action = self._registry.create_action(step["action_id"])
            if not sub_action:
                return ActionResult(
                    success=False,
                    error=f"未找到子操作: {step['action_id']}",
                    execution_time=time.time() - start_time,
                    action_id=self._action_id,
                    action_name=self._name,
                )

            result = await sub_action.execute(sub_ctx)
            results.append(result)

            # 子操作失败则停止
            if not result.success:
                return ActionResult(
                    success=False,
                    error=f"步骤 {i+1} 失败: {result.error}",
                    data={"results": [r.__dict__ for r in results]},
                    execution_time=time.time() - start_time,
                    action_id=self._action_id,
                    action_name=self._name,
                )

        return ActionResult(
            success=True,
            data={
                "composite": True,
                "steps_count": len(self._steps),
                "results": [r.__dict__ for r in results],
            },
            execution_time=time.time() - start_time,
            action_id=self._action_id,
            action_name=self._name,
        )


class ActionRegistry:
    """
    操作注册表

    管理所有可用操作的注册和执行。
    - 内置操作 (_builtin_actions) 和系统级自定义操作 (_actions) 全局共享。
    - 用户自定义组合操作 (_user_composite_actions) 按 mid 隔离，仅作缓存，
      执行时通过 create_action_for_user 按需从数据库加载。
    """

    def __init__(self):
        self._actions: Dict[str, Type[BaseAction]] = {}
        self._builtin_actions: Dict[str, Type[BaseAction]] = {
            "click": ClickAction,
            "input": InputAction,
            "navigate": NavigateAction,
            "evaluate": EvaluateAction,
            "scroll": ScrollAction,
            "wait": WaitAction,
            "screenshot": ScreenshotAction,
            "llm": LLMAction,
        }

    def register(self, action_class: Type[BaseAction], action_id: Optional[str] = None):
        """注册系统级自定义操作（全局共享，非用户隔离）"""
        temp_instance = action_class()
        metadata = temp_instance.get_metadata()
        action_id = action_id or metadata.id

        if action_id in self._actions:
            raise ValueError(f"操作 ID {action_id} 已存在")

        self._actions[action_id] = action_class

    def unregister(self, action_id: str):
        """注销系统级自定义操作"""
        if action_id in self._actions:
            del self._actions[action_id]

    def get_action(self, action_id: str) -> Optional[Type[BaseAction]]:
        """获取系统级操作类"""
        return self._actions.get(action_id)

    # ------------------------------------------------------------------
    # 操作实例创建
    # ------------------------------------------------------------------

    def create_action(self, action_id: str) -> Optional[BaseAction]:
        """创建系统级操作实例"""
        if action_id in self._builtin_actions:
            return self._builtin_actions[action_id]()
        if action_id in self._actions:
            return self._actions[action_id]()
        return None

    async def create_action_for_user(
        self, action_id: str, mid: str
    ) -> Optional[BaseAction]:
        """
        为指定用户创建操作实例。

        查找顺序：
        1. 内置操作
        2. 系统级自定义操作
        3. 数据库用户自定义操作

        Args:
            action_id: 操作ID
            mid: 用户 mid

        Returns:
            BaseAction 实例，未找到返回 None
        """
        # 1 & 2: 系统级
        system_action = self.create_action(action_id)
        if system_action:
            return system_action

        # 3: 从数据库直接读取（不缓存）

        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(
                select(CustomActionModel).where(
                    CustomActionModel.action_id == action_id,
                    CustomActionModel.mid == mid,
                    CustomActionModel.is_enabled == True,
                )
            )
            model = result.first()

        if model and (model.is_composite or model.get_steps()):
            composite = CompositeAction(
                action_id=model.action_id,
                name=model.name,
                description=model.description,
                steps=model.get_steps(),
            )
            composite.set_registry(self)
            return composite

        return None

    # ------------------------------------------------------------------
    # 元数据查询
    # ------------------------------------------------------------------

    def get_all_actions(self) -> List[ActionMetadata]:
        """获取所有系统级操作的元数据（内置 + 系统自定义）"""
        result = []
        for action_class in self._builtin_actions.values():
            result.append(action_class().get_metadata())
        for action_class in self._actions.values():
            result.append(action_class().get_metadata())
        return result

    def get_action_metadata(self, action_id: str) -> Optional[ActionMetadata]:
        """获取系统级操作元数据"""
        if action_id in self._builtin_actions:
            return self._builtin_actions[action_id]().get_metadata()
        if action_id in self._actions:
            return self._actions[action_id]().get_metadata()
        return None


# 全局操作注册表（仅含系统级操作，不含任何用户数据）
action_registry = ActionRegistry()
