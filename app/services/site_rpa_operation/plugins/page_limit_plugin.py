"""
页面数量限制插件 - 限制浏览器中最大页面数量
"""
from app.models.RPA_browser.plugin_model import PageLimitPluginModel
from app.services.site_rpa_operation.base.base_plugin import BasePlugin, PluginMethodType


class PageLimitPlugin(BasePlugin):
    """页面数量限制插件 - 限制浏览器中最大页面数量"""

    def __init__(self, conf: PageLimitPluginModel = PageLimitPluginModel(), **kwargs):
        super().__init__(**kwargs)
        self.conf = conf
        self.current_pages = 0
        self.total_closed_pages = 0

        self.logger.info(f"[PAGE LIMIT PLUGIN] 📄 页面限制插件初始化 - 最大页面数: {conf.max_pages}")
        self.logger.debug(f"[PAGE LIMIT PLUGIN] ⚙️ 配置详情 - 插件描述: {conf.description}")

        # 添加操作到操作链
        self.add_operation(PluginMethodType.BEFORE_EXEC, self._check_page_limit, "检查页面数量限制")
        self.add_operation(PluginMethodType.ON_SUCCESS, self._update_page_count, "更新页面计数")
        self.add_operation(PluginMethodType.ON_ERROR, self._handle_page_error, "处理页面错误")

    async def _check_page_limit(self):
        """检查页面数量是否超过限制"""
        # 获取当前页面数量
        if hasattr(self.session, 'pages'):
            self.current_pages = len(self.session.pages)
        else:
            self.current_pages = 0

        self.logger.info(f"[PAGE LIMIT PLUGIN] 📊 当前页面数量: {self.current_pages}/{self.conf.max_pages}")
        self.logger.debug(f"[PAGE LIMIT PLUGIN] 📋 页面使用率: {(self.current_pages/self.conf.max_pages)*100:.1f}%")

        # 如果页面数量超过限制，关闭最旧的页面
        if self.current_pages >= self.conf.max_pages:
            self.logger.warning(f"[PAGE LIMIT PLUGIN] ⚠️ 页面数量达到上限 ({self.conf.max_pages})")
            await self._close_oldest_page()

    async def _close_oldest_page(self):
        """关闭最旧的页面"""
        if hasattr(self.session, 'pages') and len(self.session.pages) > 0:
            # 第一个页面通常是最旧的
            oldest_page = self.session.pages[0]
            page_url = "未知URL"
            
            try:
                page_url = oldest_page.url
            except:
                pass

            # 检查页面是否已经关闭
            if not oldest_page.is_closed():
                self.logger.warning(
                    f"[PAGE LIMIT PLUGIN] 🗑️ 正在关闭最旧页面 - URL: {page_url}"
                )

                try:
                    await oldest_page.close()
                    self.total_closed_pages += 1
                    self.logger.info(f"[PAGE LIMIT PLUGIN] ✅ 最旧页面已关闭 - URL: {page_url}, 总关闭数: {self.total_closed_pages}")
                except Exception as e:
                    self.logger.error(f"[PAGE LIMIT PLUGIN] ❌ 关闭页面失败 - URL: {page_url}, 错误: {e}")

                    # 如果关闭失败，尝试关闭下一个页面
                    if len(self.session.pages) > 1:
                        next_oldest = self.session.pages[1]
                        next_url = "未知URL"
                        try:
                            next_url = next_oldest.url
                        except:
                            pass
                            
                        if not next_oldest.is_closed():
                            try:
                                await next_oldest.close()
                                self.total_closed_pages += 1
                                self.logger.info(f"[PAGE LIMIT PLUGIN] ✅ 备用页面已关闭 - URL: {next_url}, 总关闭数: {self.total_closed_pages}")
                            except Exception as e2:
                                self.logger.error(f"[PAGE LIMIT PLUGIN] ❌ 备用页面关闭也失败 - URL: {next_url}, 错误: {e2}")
            else:
                self.logger.debug(f"[PAGE LIMIT PLUGIN] 📄 最旧页面已经关闭 - URL: {page_url}")

    async def _update_page_count(self):
        """更新页面计数"""
        # 重新计算当前页面数量
        if hasattr(self.session, 'pages'):
            new_count = len(self.session.pages)
            if new_count != self.current_pages:
                old_count = self.current_pages
                self.current_pages = new_count
                self.logger.info(f"[PAGE LIMIT PLUGIN] 📊 页面数量更新: {old_count} → {new_count}/{self.conf.max_pages}")
                self.logger.debug(f"[PAGE LIMIT PLUGIN] 📈 统计信息 - 总关闭页面数: {self.total_closed_pages}, 当前使用率: {(self.current_pages/self.conf.max_pages)*100:.1f}%")

    async def _handle_page_error(self, error):
        """处理页面相关错误"""
        self.logger.error(f"[PAGE LIMIT PLUGIN] ❌ 页面操作出错: {error}")
        self.logger.debug(f"[PAGE LIMIT PLUGIN] 🔍 错误类型: {type(error).__name__}")
        # 更新页面计数
        await self._update_page_count()

    async def get_page_stats(self) -> dict:
        """获取页面统计信息"""
        stats = {
            'max_pages':  self.conf.max_pages,
            'current_pages': self.current_pages,
            'available_slots': max(0,  self.conf.max_pages - self.current_pages)
        }

        # 添加每个页面的详细信息
        if hasattr(self.session, 'pages'):
            stats['pages_info'] = []
            for i, page in enumerate(self.session.pages):
                stats['pages_info'].append({
                    'index': i,
                    'url': page.url if not page.is_closed() else 'CLOSED',
                    'title': page.title() if not page.is_closed() else 'CLOSED',
                    'is_closed': page.is_closed()
                })

        return stats

    async def force_cleanup(self):
        """强制清理超出限制的页面"""
        if hasattr(self.session, 'pages'):
            current_count = len(self.session.pages)
            if current_count >  self.conf.max_pages:
                self.logger.warning(
                    f"[PAGE LIMIT] 强制清理: {current_count} > { self.conf.max_pages}"
                )

                # 关闭超出限制的页面（从最旧的开始）
                pages_to_close = current_count -  self.conf.max_pages
                closed_count = 0

                for i in range(min(pages_to_close, len(self.session.pages))):
                    page = self.session.pages[i]
                    if not page.is_closed():
                        try:
                            await page.close()
                            closed_count += 1
                        except Exception as e:
                            self.logger.error(f"[PAGE LIMIT] 强制关闭页面失败: {e}")

                self.logger.info(f"[PAGE LIMIT] 强制清理完成，关闭了 {closed_count} 个页面")
                await self._update_page_count()
__all__ = ["PageLimitPlugin"]