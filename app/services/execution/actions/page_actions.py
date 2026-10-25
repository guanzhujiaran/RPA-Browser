"""多页面（按别名）操作：open_page / close_page。

设计见 ``docs/多页面并行执行计划书.md``：

- 页面用别名标识，后续步骤通过 ``params.page`` 引用；
- 页面数达到 ``browser_max_pages_per_context`` 时**等待空位**，不关闭任何已有页面
  （旧 ``new_page`` 会静默关最旧页，可能砸掉正在跑的页面）；
- 别名归属 ``(mid, browser_id)`` + 发起运行，运行结束由 ``close_owned`` 清理。
"""

from __future__ import annotations

import time

from loguru import logger

from app.config import settings
from app.models.execution.action_param_models import ClosePageParams, OpenPageParams
from app.models.execution.builtin_action_types import BuiltinActionType
from app.services.RPA_browser.session.page_registry import (
    PageRegistryError,
    page_registry,
)
from app.services.execution.actions.base import (
    ActionResult,
    BaseAction,
    register_action_class,
)
from app.services.execution.actions.navigation import URLSecurityChecker


def _session_key(action: BaseAction) -> tuple[int, int]:
    """从执行元信息取 ``(mid, browser_id)``。"""
    browser_id = action.exec_meta.get("browser_id") or action.exec_meta.get(
        "session_id"
    )
    return int(action.mid), int(browser_id or 0)


def _owner_run_id(action: BaseAction) -> str:
    """页面归属的运行 ID（工作流里 = run_id；单动作执行为本次 execution_id）。"""
    return str(
        action.exec_meta.get("execution_id")
        or action.exec_meta.get("workflow_id")
        or ""
    )


class OpenPageAction(BaseAction[OpenPageParams]):
    """按别名打开新页面（满则等待空位）。"""

    action_id: BuiltinActionType = BuiltinActionType.OPEN_PAGE
    action_type: BuiltinActionType = BuiltinActionType.OPEN_PAGE
    params: OpenPageParams

    async def _execute(self) -> ActionResult:
        start_time = time.time()
        valid, error_msg, validated = self.validate_params_with_model(self.params)
        if not valid or validated is None:
            return ActionResult(
                success=False,
                error=error_msg,
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        if self.page is None:
            return ActionResult(
                success=False,
                error="没有可用页面，无法创建新页面",
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        key = _session_key(self)
        security_check = (
            URLSecurityChecker.check_url_security(validated.url)
            if validated.url
            else None
        )
        try:
            page = await page_registry.open_page(
                key,
                alias=validated.name,
                opener=self.page,
                owner_run_id=_owner_run_id(self),
                url=validated.url,
                wait_until=str(validated.wait_until),
                nav_timeout=validated.nav_timeout,
                acquire_timeout=validated.acquire_timeout,
                capacity=int(settings.browser_max_pages_per_context),
                security_check=security_check,
            )
        except PageRegistryError as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )
        except Exception as exc:  # noqa: BLE001 - 底层异常收敛为可读错误
            logger.exception(f"[OpenPageAction] 打开页面失败: {exc}")
            return ActionResult(
                success=False,
                error=f"打开页面失败: {exc}",
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        try:
            page_count = len([p for p in page.context.pages if not p.is_closed()])
            current_url = page.url
        except Exception:  # noqa: BLE001 - 页面刚创建即被关闭
            page_count, current_url = 0, ""

        return ActionResult(
            success=True,
            data={
                "page_alias": validated.name,
                "page_count": page_count,
                "url": current_url,
            },
            execution_time=time.time() - start_time,
            action_id=self.metadata.id,
            action_name=self.metadata.name,
        )


class ClosePageAction(BaseAction[ClosePageParams]):
    """关闭指定别名的页面并释放容量。"""

    action_id: BuiltinActionType = BuiltinActionType.CLOSE_PAGE
    action_type: BuiltinActionType = BuiltinActionType.CLOSE_PAGE
    params: ClosePageParams

    async def _execute(self) -> ActionResult:
        start_time = time.time()
        valid, error_msg, validated = self.validate_params_with_model(self.params)
        if not valid or validated is None:
            return ActionResult(
                success=False,
                error=error_msg,
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        key = _session_key(self)
        try:
            closed = await page_registry.close_page(
                key, alias=validated.name, run_id=_owner_run_id(self)
            )
        except PageRegistryError as exc:
            return ActionResult(
                success=False,
                error=str(exc),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        if not closed:
            logger.warning(
                f"[ClosePageAction] 页面别名不存在或已关闭: {validated.name}"
            )
        return ActionResult(
            success=True,
            data={
                "page_alias": validated.name,
                "closed": closed,
                "remaining_pages": len(page_registry.list_aliases(key)),
            },
            execution_time=time.time() - start_time,
            action_id=self.metadata.id,
            action_name=self.metadata.name,
        )


register_action_class(OpenPageAction)
register_action_class(ClosePageAction)
