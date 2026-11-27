"""Cookie 导入编排服务

只负责「会话编排」：判断浏览器是否在跑、是否允许顺带启动、工作流互斥、刷新活跃时间；
cookie 的实际写入与字段归一化在 ``BrowserDataService.import_cookies``。

设计边界（与导出对称）：
- 导入是**一次性**的：写完即放弃入参，服务端不缓存、不落库，
  后续 cookie 刷新与落盘全部由浏览器自己负责；
- 不做任何「启动时自动注入」的链路 —— 站点会随请求轮换 cookie，
  快照回灌只会用旧值覆盖浏览器刷新后的新值；
- 未运行的浏览器默认**不启动**（启动占队列与时长配额，属于显式副作用），
  需要时由调用方显式置 ``launch_if_not_running``。
"""

from collections.abc import Sequence

from loguru import logger

from playwright._impl._errors import TargetClosedError

from app.models.common.exceptions.base_exception import InvalidParamException
from app.models.runtime.browser_operation import (
    CookieData,
    CookieImportStatus,
    CookieWriteData,
    ImportCookiesBatchItemResult,
)
from app.services.RPA_browser.session import live_session_guard
from app.services.RPA_browser.session.browser_data_service import BrowserDataService
from app.services.RPA_browser.session.live_service import live_service


class CookieImportService:
    """把 cookie 写入指定浏览器（含「未运行时可选启动」的编排）。"""

    @classmethod
    async def import_for_browser(
        cls,
        mid: int,
        browser_id: str,
        cookies: Sequence[CookieData],
        *,
        launch_if_not_running: bool = False,
        reload_pages: bool = True,
        is_vip: bool = False,
    ) -> ImportCookiesBatchItemResult:
        """向单个浏览器写入 cookie，任何失败都以 ``status`` 返回而不抛出。

        Args:
            mid: 浏览器归属者 mid（调用方必须已完成归属校验）
            browser_id: 目标浏览器 id
            cookies: 待写入的 cookie
            launch_if_not_running: 未运行时是否顺带启动
            reload_pages: 写入后是否刷新页面
            is_vip: VIP 身份，决定启动队列

        Returns:
            ImportCookiesBatchItemResult: 单项结果（success / not_running /
                workflow_running / invalid / launch_failed / injection_failed）
        """
        try:
            target_id = int(browser_id)
        except (TypeError, ValueError):
            return cls._failure(
                browser_id, "invalid", message="browser_id 必须是数字"
            )

        # 僵尸会话（浏览器被手动关闭 / 崩溃）在这里探测并清理，统一按「未启动」处理，
        # 而不是让 add_cookies 抛 TargetClosedError 变成「写入失败」
        entry = await live_session_guard.get_live_entry(mid, target_id)
        launched = False

        if entry is None:
            if not launch_if_not_running:
                return cls._failure(
                    browser_id,
                    "not_running",
                    message=(
                        "浏览器未启动或已关闭（会话已清理）；cookie 只能写入运行中的 context。"
                        "如确需本次启动，请置 launch_if_not_running=true，"
                        "或先调用启动接口再导入"
                    ),
                )
            attempt = await live_session_guard.try_launch(
                mid, target_id, is_vip=is_vip
            )
            if attempt.entry is None:
                return cls._failure(
                    browser_id,
                    "launch_failed",
                    message=f"启动浏览器失败: {attempt.error}",
                )
            entry = attempt.entry
            launched = True

        if entry.is_workflow_running:
            return cls._failure(
                browser_id,
                "workflow_running",
                launched=launched,
                message="工作流正在执行，注入 cookie 会打乱其登录态，已拒绝",
            )

        # 真实操作入口：刷新活跃时间，与其它控制接口口径一致
        await live_service.touch(mid, target_id, source="import_cookies")

        data: CookieWriteData | None = None
        for attempt_index in range(2):
            try:
                data = await BrowserDataService.import_cookies(
                    entry.browser_session.browser_context,
                    cookies,
                    reload_pages=reload_pages,
                )
                break
            except InvalidParamException as e:
                return cls._failure(
                    browser_id, "invalid", launched=launched, message=e.msg or str(e)
                )
            except TargetClosedError:
                # 写入瞬间被关闭（手动关闭 / 崩溃 / 闲置回收）：清掉僵尸会话，
                # 否则会一直显示「运行中」，每次导入都撞同一个错
                await live_session_guard.discard_session(mid, target_id)
                if launched or not launch_if_not_running or attempt_index > 0:
                    return cls._failure(
                        browser_id,
                        "not_running",
                        launched=launched,
                        message="浏览器已关闭（会话已清理）；请重新启动后再导入",
                    )
                # 允许启动：就地恢复一次（重建会话后重试写入）
                retry = await live_session_guard.try_launch(
                    mid, target_id, is_vip=is_vip
                )
                if retry.entry is None:
                    return cls._failure(
                        browser_id,
                        "launch_failed",
                        message=f"浏览器已关闭，自动恢复失败: {retry.error}",
                    )
                entry = retry.entry
                launched = True
                await live_service.touch(mid, target_id, source="import_cookies")
            except Exception as e:
                logger.error(f"[cookie 导入] 写入失败: browser_id={browser_id}, {e}")
                return cls._failure(
                    browser_id,
                    "injection_failed",
                    launched=launched,
                    message=f"写入 cookie 失败: {e}",
                )

        if data is None:
            return cls._failure(
                browser_id,
                "injection_failed",
                launched=launched,
                message="写入 cookie 失败",
            )

        session_hint = (
            f"；其中 {data.session_cookie_count} 条为会话 cookie，浏览器重启后会失效"
            if data.session_cookie_count > 0
            else ""
        )

        return ImportCookiesBatchItemResult(
            browser_id=browser_id,
            status="success",
            launched=launched,
            imported_count=data.imported_count,
            reloaded_pages=data.reloaded_pages,
            session_cookie_count=data.session_cookie_count,
            message=(
                f"已写入 {data.imported_count} 条 cookie"
                f"{'（本次启动浏览器）' if launched else ''}，"
                f"已提交后台刷新 {data.reloaded_pages} 个页面{session_hint}；"
                f"写入已生效，落盘由浏览器负责"
            ),
        )

    @staticmethod
    def _failure(
        browser_id: str,
        status: CookieImportStatus,
        *,
        launched: bool = False,
        message: str = "",
    ) -> ImportCookiesBatchItemResult:
        return ImportCookiesBatchItemResult(
            browser_id=browser_id,
            status=status,
            launched=launched,
            message=message,
        )


__all__ = ["CookieImportService"]
