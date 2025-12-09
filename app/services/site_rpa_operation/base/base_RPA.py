from abc import ABC, abstractmethod
from dataclasses import dataclass
import loguru
from app.services.RPA_browser.browser_session_pool.session_pool_model import PluginedSessionInfo
from app.utils.decorator import log_class_decorator


@dataclass
@log_class_decorator.decorator
class BaseRPA(ABC):
    session: PluginedSessionInfo
    logger: "loguru.Logger" = None

    @abstractmethod
    async def execute_rpa(self, *args, **kwargs):
        """
        执行操作
        """
        ...
