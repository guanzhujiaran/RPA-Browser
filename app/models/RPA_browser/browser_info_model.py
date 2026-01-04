"""
浏览器信息模型 - 主入口文件

此文件作为浏览器相关模型的统一入口点，重新导出分类后的模型。
"""

# 重新导出所有分类后的模型
from .browser_fingerprint_models import *
from .browser_database_models import *
from .browser_api_models import *
from .rpa_operation_models import *
from .live_control_models import *

# 重新导出live_control_models中的重要模型以确保向后兼容
from .live_control_models import (
    BrowserStatus,
    LiveControlCommand,
    VideoStreamParams,
    VideoStreamStatus,
    VideoStreamResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    ManualOperationRequest,
    AutomationResumeRequest,
    BrowserCleanupPolicy,
    BrowserStatusEnum,
    OperationPriority
)

# 重新导出其他相关模型（保持原有导入）
from app.models.RPA_browser.plugin_model import (
    LogPluginModel,
    PageLimitPluginModel,
    RandomWaitPluginModel,
    RetryPluginModel,
)
from app.models.RPA_browser.browser_exec_info_model import BrowserExecInfoModel
from app.utils.consts.browser_exe_info.browser_exec_info_utils import (
    get_browse_exec_infos,
)
