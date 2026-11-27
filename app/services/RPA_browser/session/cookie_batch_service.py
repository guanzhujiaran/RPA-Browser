"""Cookie / 登录态批量编排服务

批量接口的逐项隔离都在这里：归属校验依赖 DB 会话，留在 controller 层；
本服务只负责「按 browser_id 取会话 → touch → 调 ``BrowserDataService`` → 转单项结果」。
任何失败都以 ``status`` 返回而不抛异常，保证单项失败不影响同批其它浏览器。

设计边界与单浏览器接口一致：

- 导出只处理**运行中**的浏览器：cookie 的唯一可读来源是活跃 ``BrowserContext``，
  profile 磁盘里的 ``Default/Cookies`` 是 Chromium 加密的 SQLite，读不出明文；
  单浏览器导出可按调用方要求先启动（``launch_if_not_running``），**批量导出不启动** ——
  批量里串行启动 N 个会把一个 HTTP 请求拖成分钟级，启动应由前端逐项调用会话创建接口；
- 清空同样**不在接口内启动**：未启动项返回 ``not_running``，由前端逐项调用会话创建
  接口恢复后重试清空（与批量导出一致，避免一个请求里串行启动多个浏览器）；
- 服务端不缓存、不落库任何 cookie 值，审计日志只记条数。
"""

from collections.abc import Sequence

from loguru import logger
from playwright._impl._errors import TargetClosedError

from app.models.common.exceptions.base_exception import InvalidParamException
from app.models.runtime.browser_operation import (
    ClearLoginStateBatchItemResult,
    CookieClearStatus,
    CookieExportStatus,
    ExportCookiesBatchItemResult,
    ExportCookiesResponse,
)
from app.models.runtime.live_service import BrowserSessionEntry
from app.services.RPA_browser.session import live_session_guard
from app.services.RPA_browser.session.browser_data_service import BrowserDataService
from app.services.RPA_browser.session.live_service import live_service


class CookieBatchService:
    """批量导出 / 批量清空的逐项执行编排。"""

    # ============ 批量导出（只读） ============

    @classmethod
    async def export_for_browser(
        cls,
        mid: int,
        browser_id: str,
        *,
        domains: Sequence[str] | None = None,
        launch_if_not_running: bool = False,
        is_vip: bool = False,
        source: str = "export_cookies_batch",
    ) -> ExportCookiesBatchItemResult:
        """导出单个浏览器的 cookie，失败以 ``status`` 返回。

        cookie 只能从活跃 ``BrowserContext`` 读取（profile 里的 SQLite 是加密的），
        因此浏览器未启动时必须先启动：``launch_if_not_running=True`` 时先启动再读，
        否则直接返回 ``not_running``（启动会占启动队列与时长配额，不做隐式副作用）。
        批量导出**不传**这个参数：批量里串行启动多个浏览器会拖成分钟级长请求，
        启动改由前端逐项调用会话创建接口完成。

        只读操作，不做工作流执行期互斥（工作流运行中同样可以读）。
        站点过滤非法属于请求级参数错误，应在批量入口统一校验，不应靠这里逐项失败。

        Args:
            mid: 浏览器归属者 mid（调用方必须已完成归属校验）
            browser_id: 目标浏览器 id
            domains: 站点过滤；``None`` / 空 = 全部
            launch_if_not_running: 未运行时是否顺带启动
            is_vip: VIP 身份，决定启动队列
            source: 刷新活跃时间时记录的来源（单浏览器 / 批量入口区分审计）
        """
        target_id = cls._parse_browser_id(browser_id)
        if target_id is None:
            return cls._export_failure(
                browser_id, "invalid", message="browser_id 必须是数字"
            )

        # entry 还在池里不代表浏览器活着（外部关闭 / 崩溃时 is_closed 可能滞后），
        # _get_live_entry 会做一次真实探测并清掉僵尸会话
        entry = await cls._get_live_entry(mid, target_id)
        launched = False

        if entry is None:
            if not launch_if_not_running:
                return cls._export_failure(
                    browser_id,
                    "not_running",
                    message=(
                        "浏览器未启动（或已关闭）；cookie 只能从运行中的 context 读取。"
                        "如确需本次启动，请置 launch_if_not_running=true，"
                        "或先调用启动接口再导出"
                    ),
                )
            attempt = await cls._try_launch(mid, target_id, is_vip=is_vip)
            if attempt.entry is None:
                return cls._export_failure(
                    browser_id,
                    "launch_failed",
                    message=f"启动浏览器失败: {attempt.error}",
                )
            entry = attempt.entry
            launched = True

        # 真实操作入口：刷新活跃时间，避免读到一半被闲置回收
        await live_service.touch(mid, target_id, source=source)

        data: ExportCookiesResponse | None = None
        for attempt_index in range(2):
            try:
                data = await BrowserDataService.export_cookies(
                    entry.browser_session.browser_context, domains=domains
                )
                break
            except InvalidParamException as e:
                return cls._export_failure(
                    browser_id, "invalid", launched=launched, message=e.msg or str(e)
                )
            except TargetClosedError:
                # 读取瞬间会话被关闭（外部关闭 / 崩溃 / 闲置回收）：
                # 必须清掉僵尸 entry，否则后续每次操作都会撞同一个错
                await cls._discard_session(mid, target_id)
                if launched or not launch_if_not_running or attempt_index > 0:
                    return cls._export_failure(
                        browser_id,
                        "not_running",
                        launched=launched,
                        message="浏览器已关闭，僵尸会话已清理；请重新启动后再导出",
                    )
                # 允许启动：就地恢复一次（重建会话后重试读取）
                retry = await cls._try_launch(mid, target_id, is_vip=is_vip)
                if retry.entry is None:
                    return cls._export_failure(
                        browser_id,
                        "launch_failed",
                        message=f"浏览器已关闭，自动恢复失败: {retry.error}",
                    )
                entry = retry.entry
                launched = True
            except Exception as e:
                logger.error(f"[cookie 导出] 读取失败: browser_id={browser_id}, {e}")
                return cls._export_failure(
                    browser_id,
                    "failed",
                    launched=launched,
                    message=f"读取 cookie 失败: {e}",
                )

        if data is None:
            return cls._export_failure(
                browser_id, "failed", launched=launched, message="读取 cookie 失败"
            )

        return ExportCookiesBatchItemResult(
            browser_id=browser_id,
            status="success",
            launched=launched,
            cookie_count=data.cookie_count,
            matched_domains=data.matched_domains,
            cookies=data.cookies,
            message=(
                f"{data.message}（本次启动浏览器）" if launched else data.message
            ),
        )

    # ============ 批量清空登录态（换号） ============

    @classmethod
    async def clear_for_browser(
        cls,
        mid: int,
        browser_id: str,
        *,
        domains: Sequence[str] | None = None,
        reload_pages: bool = True,
    ) -> ClearLoginStateBatchItemResult:
        """清空单个运行中浏览器的登录态，失败以 ``status`` 返回。

        ``domains`` 为空 = 清空全部（换号）；指定站点时只清匹配的 cookie
        与 origin 匹配页面的 web storage，权限授权仍整体重置。

        工作流执行中**跳过而不是排队**：清登录态会把正在跑的工作流打成
        「莫名其妙的登录失败」，与单浏览器接口的 ``reject_workflow_running`` 同口径。

        Args:
            mid: 浏览器归属者 mid（调用方必须已完成归属校验）
            browser_id: 目标浏览器 id
            domains: 站点过滤；``None`` / 空 = 全部
            reload_pages: 清空后是否刷新已打开页面
        """
        target_id = cls._parse_browser_id(browser_id)
        if target_id is None:
            return cls._clear_failure(
                browser_id, "invalid", message="browser_id 必须是数字"
            )

        entry = await cls._get_live_entry(mid, target_id)
        if entry is None:
            return cls._clear_failure(
                browser_id,
                "not_running",
                message="浏览器未启动（或已关闭）；登录态只能在运行中的会话里清空，请先启动",
            )

        if entry.is_workflow_running:
            return cls._clear_failure(
                browser_id,
                "workflow_running",
                message="工作流正在执行，清空登录态会打断其登录流程，已跳过",
            )

        # 真实操作入口：刷新活跃时间，与其它控制接口口径一致
        await live_service.touch(mid, target_id, source="clear_login_state_batch")

        try:
            data = await BrowserDataService.clear_login_state(
                entry.browser_session,
                domains=domains,
                reload_pages=reload_pages,
            )
        except InvalidParamException as e:
            return cls._clear_failure(
                browser_id, "invalid", message=e.msg or str(e)
            )
        except TargetClosedError:
            await cls._discard_session(mid, target_id)
            return cls._clear_failure(
                browser_id,
                "not_running",
                message="浏览器已关闭，僵尸会话已清理；请重新启动后再清空",
            )
        except Exception as e:
            logger.error(f"[批量清空登录态] 清理失败: browser_id={browser_id}, {e}")
            return cls._clear_failure(
                browser_id, "failed", message=f"清空登录态失败: {e}"
            )

        return ClearLoginStateBatchItemResult(
            browser_id=browser_id,
            status="success",
            cleared_cookie_count=data.cleared_cookie_count,
            matched_domains=data.matched_domains,
            cleared_pages=data.cleared_pages,
            reloaded_pages=data.reloaded_pages,
            affected_domains=data.affected_domains,
            message=data.message,
        )

    # ============ 内部工具 ============

    @staticmethod
    async def _get_live_entry(
        mid: int, browser_id: int
    ) -> BrowserSessionEntry | None:
        """取**活着**的会话（探测 + 清理僵尸会话，见 ``live_session_guard``）。"""
        return await live_session_guard.get_live_entry(mid, browser_id)

    @staticmethod
    async def _discard_session(mid: int, browser_id: int) -> None:
        await live_session_guard.discard_session(mid, browser_id)

    @staticmethod
    async def _try_launch(
        mid: int, browser_id: int, *, is_vip: bool
    ) -> live_session_guard.LaunchAttempt:
        return await live_session_guard.try_launch(mid, browser_id, is_vip=is_vip)

    @staticmethod
    def _parse_browser_id(browser_id: str) -> int | None:
        """对外 ID 是字符串（前端无法安全表示超过 2^53 的整数），内部统一转 int。"""
        try:
            return int(browser_id)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _export_failure(
        browser_id: str,
        status: CookieExportStatus,
        *,
        launched: bool = False,
        message: str,
    ) -> ExportCookiesBatchItemResult:
        return ExportCookiesBatchItemResult(
            browser_id=browser_id,
            status=status,
            launched=launched,
            message=message,
        )

    @staticmethod
    def _clear_failure(
        browser_id: str,
        status: CookieClearStatus,
        *,
        message: str,
    ) -> ClearLoginStateBatchItemResult:
        return ClearLoginStateBatchItemResult(
            browser_id=browser_id, status=status, message=message
        )


__all__ = ["CookieBatchService"]
