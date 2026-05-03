from enum import StrEnum
from app.config import settings


class RouterPrefix(StrEnum):
    # === 核心资源层 ===
    BROWSER = "/browser"  # 浏览器指纹/插件/通知配置管理
    BROWSER_SESSION = "/browser/session"  # 浏览器会话管理（动态实例）

    # === 操作控制层 ===
    BROWSER_CONTROL = "/browser/control"  # 浏览器实时控制总入口

    # === 系统管理层 ===
    ADMIN = settings.admin_base_path  # 管理员接口
    SYSTEM = "/system"  # 系统级接口（健康检查等）


class BrowserFingerprintRouterPath(StrEnum):
    """浏览器指纹管理路由路径 - prefix: /browser"""

    gen_rand_fingerprint = "/gen_rand_fingerprint"
    upsert_fingerprint = "/upsert_fingerprint"
    read_fingerprint = "/read_fingerprint"
    delete_fingerprint = "/delete_fingerprint"
    count_fingerprint = "/count_fingerprint"
    list_fingerprint = "/list_fingerprint"
    rename_fingerprint = "/rename_fingerprint"


class BrowserSessionRouterPath(StrEnum):
    """会话管理路由路径 - prefix: /browser/session"""

    heartbeat = "/heartbeat"
    create = "/create"
    status = "/status"
    close = "/close"


class BrowserControlRouterPath(StrEnum):
    # === 浏览器操作信息 ===
    browser_info = "/browser/info"

    # === 操作管理 ===
    actions_registered = "/actions/registered"
    actions_execute = "/actions/execute"
    actions_batch = "/actions/batch"
    actions_preview = "/actions/preview"
    actions_validate = "/actions/validate"
    actions_execute_step = "/actions/execute-step"

    # === 自定义操作 ===
    custom_actions_list = "/custom-actions/list"
    custom_actions_reload = "/custom-actions/reload"
    custom_actions_get = "/custom-actions/get"
    custom_actions_create = "/custom-actions/create"
    custom_actions_update = "/custom-actions/update"
    custom_actions_delete = "/custom-actions/delete"

    # === 工作流管理 ===
    workflows_list = "/workflows/list"
    workflows_get = "/workflows/get"
    workflows_create = "/workflows/create"
    workflows_update = "/workflows/update"
    workflows_delete = "/workflows/delete"
    workflows_duplicate = "/workflows/duplicate"
    workflows_execute = "/workflows/execute"

    # === WebRTC 视频流 ===
    webrtc_offer = "/webrtc/offer"
    webrtc_answer = "/webrtc/answer"
    webrtc_ice_candidate = "/webrtc/ice-candidate"
    webrtc_status = "/webrtc/status"
    webrtc_close = "/webrtc/close"

class UserBrowserDefaultSettingRouterPath(StrEnum):
    """用户浏览器默认设置路由路径 - prefix: /browser"""

    get_settings = "/default-settings/get"
    create_or_update_settings = "/default-settings/create-or-update"
    delete_settings = "/default-settings/delete"
    apply_settings = "/default-settings/apply"
    get_server_user_setting_defaults = "/default-settings/server-defaults/get"

class NotifyRouterPath(StrEnum):
    """通知管理路由路径 - prefix: /browser"""

    upsert_notify_config = "/notify/conf/upsert"
    read_notify_config = "/notify/conf/read"
    delete_notify_config = "/notify/conf/delete"
    test_notify = "/notify/test"
