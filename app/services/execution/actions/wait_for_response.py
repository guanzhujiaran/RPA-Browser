"""响应级等待 Action - WaitForResponse

与 ``WaitAction`` 不同的是：本动作**超时即失败**。等不到匹配的响应说明请求没发出 /
没回来，继续往下走只会读到过期状态，因此必须交给 ``on_error`` / ``retry`` 接管。

站点无关：这里只做「按 URL 匹配 + 可选 HTTP 方法过滤」的通用机制，
不含任何域名、站点选择器或业务状态码分支。
"""

from __future__ import annotations

import re
import time

from loguru import logger
from playwright._impl._errors import TimeoutError as PlaywrightTimeoutError

from app.models.database.workflow.models import BuiltinActionType
from app.models.execution.action_params import WaitForResponseParams, WaitForResponseResult
from app.models.execution.enums import UrlPatternTypeEnum
from app.services.execution.actions.base import (
    ActionResult,
    BaseAction,
    register_action_class,
)

#: 响应体输出上限：再长对下游（if_else / llm / print）没有意义，还会撑大日志与变量池
_MAX_BODY_CHARS = 20000


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """把 Playwright 风格的 glob 转成正则（用于响应 URL 匹配）。

    与 Playwright 的 glob 口径一致：
    - ``*`` 匹配任意字符但**不跨** ``/``（避免 ``https://a.com/*`` 误命中整条深层路径）；
    - ``**`` 匹配任意字符（含 ``/``）；
    - ``?`` 匹配单个非 ``/`` 字符；
    - 其余字符按字面量转义。
    """
    out: list[str] = []
    i = 0
    length = len(pattern)
    while i < length:
        char = pattern[i]
        if char == "*":
            if i + 1 < length and pattern[i + 1] == "*":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
            i += 1
            continue
        if char == "?":
            out.append("[^/]")
            i += 1
            continue
        out.append(re.escape(char))
        i += 1
    return re.compile("".join(out))


def _build_url_matcher(pattern: str, pattern_type: UrlPatternTypeEnum) -> re.Pattern[str]:
    """按 ``pattern_type`` 构建「整条 URL 匹配」的正则对象。"""
    if pattern_type is UrlPatternTypeEnum.REGEX:
        return re.compile(pattern)
    return _glob_to_regex(pattern)


class WaitForResponseAction(BaseAction[WaitForResponseParams]):
    """等待匹配的 HTTP 响应（命中即返回，超时即失败）"""

    action_id: BuiltinActionType = BuiltinActionType.WAIT_FOR_RESPONSE
    action_type: BuiltinActionType = BuiltinActionType.WAIT_FOR_RESPONSE
    params: WaitForResponseParams

    async def _execute(self) -> ActionResult[WaitForResponseResult]:
        start_time = time.time()

        try:
            _, _, params = self.validate_params_with_model(self.params)
        except ValueError as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        try:
            matcher = _build_url_matcher(params.url_pattern, params.pattern_type)
        except re.error as exc:
            return ActionResult(
                success=False,
                error=f"url_pattern 不是合法的正则表达式: {exc}",
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        expected_method = str(params.method).upper() if params.method is not None else None

        def _matches(response) -> bool:
            """响应是否命中：URL 整条匹配 + 可选方法过滤（先命中先返回）。"""
            if not matcher.fullmatch(response.url):
                return False
            if expected_method is None:
                return True
            return str(response.request.method).upper() == expected_method

        trigger_selector = (params.trigger_selector or "").strip()
        # 区分「点击触发失败」与「响应等不到」：两者都是 PlaywrightTimeoutError，
        # 但排障方向完全不同（元素不可点 vs 请求没回来），必须给出不同错误信息。
        trigger_done = False

        try:
            # 关键点：**先注册监听，再触发**。Playwright 只捕获「注册之后到达」的响应，
            # 若把点击放在上一个步骤里，click 自身耗时（实测 2~3s）足以吞掉响应，
            # 本步骤再注册就只会等满 timeout。
            async with self.page.expect_response(
                _matches, timeout=params.timeout
            ) as response_info:
                if trigger_selector:
                    await self.page.locator(trigger_selector).click(
                        timeout=params.trigger_timeout
                    )
                    trigger_done = True
            response = await response_info.value
        except PlaywrightTimeoutError as exc:
            if trigger_selector and not trigger_done:
                logger.warning(
                    f"[WaitForResponseAction] 触发点击超时: selector={trigger_selector}"
                )
                return ActionResult(
                    success=False,
                    error=(
                        f"触发点击失败（{params.trigger_timeout}ms 内元素不可点击）: "
                        f"selector={trigger_selector}"
                    ),
                    execution_time=time.time() - start_time,
                    action_id=self.metadata.id,
                    action_name=self.metadata.name,
                    logs=[f"{exc}"],
                )
            logger.warning(
                f"[WaitForResponseAction] 等待响应超时: pattern={params.url_pattern} "
                f"pattern_type={params.pattern_type} method={expected_method} "
                f"trigger={trigger_selector or '<无>'}"
            )
            return ActionResult(
                success=False,
                error=(
                    f"等待响应超时（{params.timeout}ms 内没有匹配的响应）: "
                    f"pattern={params.url_pattern} method={expected_method or 'ANY'} "
                    f"trigger={trigger_selector or '<无>'}"
                ),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
                logs=[f"{exc}"],
            )

        response_text = ""
        if params.include_body:
            response_text = await self._read_body_text(response)

        status = int(response.status)
        logger.info(
            f"[WaitForResponseAction] 命中响应: status={status} url={response.url} "
            f"body_chars={len(response_text)}"
        )
        return ActionResult(
            success=True,
            data=WaitForResponseResult(
                response_status=status,
                response_ok=bool(response.ok),
                response_url=response.url,
                response_text=response_text,
            ),
            execution_time=time.time() - start_time,
            action_id=self.metadata.id,
            action_name=self.metadata.name,
        )

    @staticmethod
    async def _read_body_text(response) -> str:
        """读取响应体文本并截断。

        响应体可能已被重定向 / 连接复用等情况丢弃（``text()`` 抛错），
        此时按空串返回：状态码与 URL 已经足够判定，不能因为读不到正文就判整个动作失败。
        """
        try:
            text = await response.text()
        except Exception as exc:  # noqa: BLE001 - 正文不可读属于可预期情况
            logger.warning(f"[WaitForResponseAction] 响应体读取失败，按空串处理: {exc}")
            return ""
        return text[:_MAX_BODY_CHARS]


# 自注册进内置动作注册表（base 的依赖倒置槽位，供控制流策略层按 action_id 查找）
register_action_class(WaitForResponseAction)
