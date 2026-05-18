"""
导航类 Action - Navigate, NewPage
"""
import contextlib
import asyncio
import socket
import ipaddress
from urllib.parse import urlparse
import time
from loguru import logger

from app.services.execution.actions.base import BaseAction
from app.models.execution.params import NavigateParams, NewPageParams
from app.models.database.workflow.models import ActionType, ActionMetadata, ActionResult, ActionContext
from app.models.core.browser.security import SecurityCheckResult
from app.config import settings


class URLSecurityChecker:
    """URL 安全检查工具类"""
    
    @staticmethod
    def _check_protocol(scheme: str) -> SecurityCheckResult:
        """检查协议是否允许"""
        if scheme in ["http", "https"]:
            return SecurityCheckResult(allowed=True)
        return SecurityCheckResult(
            allowed=False, reason=f"不支持的协议: {scheme}。只允许 http 和 https"
        )
    
    @staticmethod
    def _check_hostname_basic(hostname: str) -> SecurityCheckResult:
        """检查主机名基本规则"""
        localhost_variants = [
            "localhost", "localhost.localdomain",
            "ip6-localhost", "ip6-loopback",
        ]
        if hostname in localhost_variants:
            return SecurityCheckResult(allowed=False, reason="禁止访问 localhost")
        
        if hostname in ["127.0.0.1", "::1", "0.0.0.0"]:
            return SecurityCheckResult(allowed=False, reason=f"禁止访问回环地址: {hostname}")
        
        return SecurityCheckResult(allowed=True)
    
    @staticmethod
    def _check_ip_address(ip_str: str, hostname: str) -> SecurityCheckResult:
        """检查 IP 地址安全性"""
        with contextlib.suppress(ValueError):
            ip = ipaddress.ip_address(ip_str)
            
            if ip.version == 4 and ip in ipaddress.ip_network("127.0.0.0/8"):
                return SecurityCheckResult(allowed=False, reason="禁止访问 127.0.0.0/8 网段")
            
            if ip.version == 6 and ip == ipaddress.ip_address("::1"):
                return SecurityCheckResult(allowed=False, reason="禁止访问 IPv6 回环地址 ::1")
            
            if ip.is_private:
                return SecurityCheckResult(allowed=False, reason=f"禁止访问私有地址: {hostname}")
            
            if ip.is_loopback:
                return SecurityCheckResult(allowed=False, reason=f"禁止访问回环地址: {hostname}")
            
            if ip.is_link_local:
                return SecurityCheckResult(allowed=False, reason=f"禁止访问链路本地地址: {hostname}")
            
            if ip.is_multicast:
                return SecurityCheckResult(allowed=False, reason=f"禁止访问多播地址: {hostname}")
            
            if ip.is_reserved:
                return SecurityCheckResult(allowed=False, reason=f"禁止访问保留地址: {hostname}")
            
            return SecurityCheckResult(allowed=True)
        
        return SecurityCheckResult(allowed=True)
    
    @staticmethod
    async def _check_dns_resolution(hostname: str, max_retries: int = 2) -> SecurityCheckResult:
        """DNS 解析并检查地址安全性"""
        for attempt in range(max_retries):
            try:
                addr_info_v4 = socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM)
                addr_info_v6 = socket.getaddrinfo(hostname, None, socket.AF_INET6, socket.SOCK_STREAM)
                all_addr_info = addr_info_v4 + addr_info_v6
                
                if not all_addr_info:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(0.5)
                        continue
                    return SecurityCheckResult(
                        allowed=True,
                        reason=f"DNS 解析未返回地址，但允许浏览器尝试验证: {hostname}",
                    )
                
                for info in all_addr_info:
                    ip_str = info[4][0]
                    result = URLSecurityChecker._check_ip_address(ip_str, hostname)
                    if not result.allowed:
                        return result
                
                return SecurityCheckResult(allowed=True)
            
            except socket.gaierror:
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5)
                    continue
                return SecurityCheckResult(
                    allowed=True,
                    reason=f"DNS 解析失败（已重试{max_retries}次）: {hostname}。允许浏览器尝试验证",
                )
            except Exception as e:
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5)
                    continue
                return SecurityCheckResult(
                    allowed=True,
                    reason=f"DNS 检查异常（已重试{max_retries}次）: {str(e)}。允许浏览器尝试验证",
                )
        
        return SecurityCheckResult(allowed=True)
    
    @classmethod
    async def check_url_security(cls, url: str) -> SecurityCheckResult:
        """检查 URL 的安全性"""
        try:
            parsed = urlparse(url)
            hostname = parsed.hostname
            scheme = parsed.scheme.lower()
            
            if not hostname:
                return SecurityCheckResult(allowed=False, reason="无法解析 URL 主机名")
            
            # 检查协议
            protocol_result = cls._check_protocol(scheme)
            if not protocol_result.allowed:
                return protocol_result
            
            # 清理主机名
            hostname = hostname.strip().lower()
            
            # 检查主机名基本规则
            hostname_result = cls._check_hostname_basic(hostname)
            if not hostname_result.allowed:
                return hostname_result
            
            # 尝试作为 IP 地址检查
            check_hostname = hostname.strip("[]")
            with contextlib.suppress(ValueError):
                ip = ipaddress.ip_address(check_hostname)
                
                if ip.version == 4 and ip in ipaddress.ip_network("127.0.0.0/8"):
                    return SecurityCheckResult(allowed=False, reason="禁止访问 127.0.0.0/8 网段")
                
                if ip.version == 6 and ip == ipaddress.ip_address("::1"):
                    return SecurityCheckResult(allowed=False, reason="禁止访问 IPv6 回环地址 ::1")
                
                if ip.is_private:
                    return SecurityCheckResult(allowed=False, reason=f"禁止访问私有地址: {hostname}")
                
                if ip.is_loopback:
                    return SecurityCheckResult(allowed=False, reason=f"禁止访问回环地址: {hostname}")
                
                if ip.is_link_local:
                    return SecurityCheckResult(allowed=False, reason=f"禁止访问链路本地地址: {hostname}")
                
                if ip.is_multicast:
                    return SecurityCheckResult(allowed=False, reason=f"禁止访问多播地址: {hostname}")
                
                if ip.is_reserved:
                    return SecurityCheckResult(allowed=False, reason=f"禁止访问保留地址: {hostname}")
                
                return SecurityCheckResult(allowed=True)
            
            # DNS 解析检查
            return await cls._check_dns_resolution(hostname)
        
        except Exception as e:
            return SecurityCheckResult(allowed=False, reason=f"URL 安全检查失败: {str(e)}")


class NavigateAction(BaseAction):
    """导航操作"""

    params_model = NavigateParams

    def get_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            id="navigate", name="导航", type=ActionType.NAVIGATION,
            description="导航到指定URL",
            parameters=self.get_parameters_from_model(),
            json_schema=self.get_full_schema(),
        )

    async def execute(self, ctx: ActionContext) -> ActionResult:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(ctx.params)
        if not valid:
            return ActionResult(
                success=False, error=error_msg, execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        url = validated_params.url
        wait_until = validated_params.wait_until
        timeout = validated_params.timeout

        try:
            # 验证 URL 格式
            if not (url.startswith("http://") or url.startswith("https://")):
                if "." in url and not url.startswith("www."):
                    url = "https://" + url
                elif url.startswith("www."):
                    url = "https://" + url
                else:
                    return ActionResult(
                        success=False,
                        error=f"无效的 URL 格式: {url}。只允许 http:// 和 https:// 协议的网站",
                        execution_time=time.time() - start_time,
                        action_id=self.metadata.id, action_name=self.metadata.name,
                    )

            # 安全检查
            security_check = await URLSecurityChecker.check_url_security(url)
            if not security_check.allowed:
                return ActionResult(
                    success=False, error=security_check.reason,
                    execution_time=time.time() - start_time,
                    action_id=self.metadata.id, action_name=self.metadata.name,
                )

            # 执行导航（SQLModel 已验证参数）
            goto_kwargs = {
                "wait_until": str(wait_until),
                "timeout": timeout
            }
            response = await ctx.page.goto(url, **goto_kwargs)

            return ActionResult(
                success=True,
                data={"url": url, "status": response.status if response else None},
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            logger.error(f"[NavigateAction] 导航操作执行异常: {e}\n{error_traceback}")
            return ActionResult(
                success=False, error=str(e), execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
                logs=[f"NavigateAction failed with exception: {str(e)}", f"Traceback:\n{error_traceback}"]
            )


class NewPageAction(BaseAction):
    """新建页面操作"""

    params_model = NewPageParams

    def get_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            id="new_page", name="新建页面", type=ActionType.NAVIGATION,
            description="在浏览器中创建新页面（标签页）",
            parameters=self.get_parameters_from_model(),
            json_schema=self.get_full_schema(),
        )

    async def execute(self, ctx: ActionContext) -> ActionResult:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(ctx.params)
        if not valid:
            return ActionResult(
                success=False, error=error_msg, execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        url = validated_params.url
        wait_until = validated_params.wait_until
        timeout = validated_params.timeout

        try:
            # 获取 browser_context
            if ctx.page:
                browser_context = ctx.page.context
            elif ctx.browser:
                logger.warning("没有可用的 page 对象，使用 browser.new_page() 创建新窗口")
                new_page = await ctx.browser.new_page()
                browser_context = new_page.context
            else:
                return ActionResult(
                    success=False, error="浏览器对象不可用，无法创建新页面",
                    execution_time=time.time() - start_time,
                    action_id=self.metadata.id, action_name=self.metadata.name,
                )
            
            # 检查页面数量限制
            current_pages = len([p for p in browser_context.pages if not p.is_closed()])
            max_pages = settings.browser_max_pages_per_context
            
            if current_pages >= max_pages:
                logger.warning(f"⚠️ 页面数量达到限制 ({current_pages}/{max_pages})，将关闭最旧的页面")
                for page in browser_context.pages:
                    if not page.is_closed():
                        try:
                            await page.close()
                            logger.info(f"🗑️ 已关闭旧页面: {page.url}")
                            break
                        except Exception as e:
                            logger.error(f"关闭旧页面失败: {e}")
                await asyncio.sleep(0.5)
            
            # 创建新页面
            new_page = await browser_context.new_page()
            
            # 🔑 激活新页面（bring to front）
            try:
                await new_page.bring_to_front()
                logger.info(f"✅ 新页面已激活: {new_page.url}")
            except Exception as e:
                logger.warning(f"⚠️ bring_to_front() 失败: {e}，但页面已创建")
            
            # 如果提供了 URL，则导航到该 URL
            if url:
                security_check = await URLSecurityChecker.check_url_security(url)
                if not security_check.allowed:
                    await new_page.close()
                    return ActionResult(
                        success=False, error=security_check.reason,
                        execution_time=time.time() - start_time,
                        action_id=self.metadata.id, action_name=self.metadata.name,
                    )
                
                response = await new_page.goto(url, wait_until=str(wait_until), timeout=timeout)
                page_count = len([p for p in browser_context.pages if not p.is_closed()])
                
                return ActionResult(
                    success=True,
                    data={
                        "page_created": True, "url": url,
                        "status": response.status if response else None,
                        "page_count": page_count,
                    },
                    execution_time=time.time() - start_time,
                    action_id=self.metadata.id, action_name=self.metadata.name,
                )
            else:
                page_count = len([p for p in browser_context.pages if not p.is_closed()])
                return ActionResult(
                    success=True,
                    data={"page_created": True, "url": "about:blank", "page_count": page_count},
                    execution_time=time.time() - start_time,
                    action_id=self.metadata.id, action_name=self.metadata.name,
                )

        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            logger.error(f"[NewPageAction] 新建页面操作执行异常: {e}\n{error_traceback}")
            return ActionResult(
                success=False, error=str(e), execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
                logs=[f"NewPageAction failed with exception: {str(e)}", f"Traceback:\n{error_traceback}"]
            )
