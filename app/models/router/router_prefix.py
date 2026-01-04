from enum import StrEnum
from app.config import settings


class RouterPrefix(StrEnum):
    browser = "/browser"
    browser_live_control = "/browser_live_control"
    admin = settings.admin_base_path


class BrowserRouterPath(StrEnum):
    gen_rand_fingerprint = "/gen_rand_fingerprint"
    upsert_fingerprint = "/upsert_fingerprint"
    read_fingerprint = "/read_fingerprint"
    delete_fingerprint = "/delete_fingerprint"
    count_fingerprint = "/count_fingerprint"
    list_fingerprint = "/list_fingerprint"


class BrowserControlRouterPath(StrEnum):
    # 基础路径
    base = "/browser"
    stream_base = "/stream"

    # 心跳和会话管理
    heartbeat = "/browser/heartbeat"
    session_create = "/browser/session/create"
    session_status = "/browser/session/status"

    # 操作控制
    manual_stop = "/browser/manual/stop"
    operation_status = "/browser/operation/status"
    control = "/browser/control"

    # 插件管理
    plugins_pause = "/browser/plugins/pause"
    plugins_status = "/browser/plugins/status"

    # 浏览器信息
    info = "/browser/info"
    status = "/browser/status"
    navigate = "/browser/navigate"
    evaluate = "/browser/evaluate"

    # 视频流
    stream_status = "/browser/stream/status"
    stream_mjpeg = "/stream/mjpeg"
    screenshot = "/stream/screenshot"

    # WebRTC 视频流
    webrtc_offer = "/webrtc/offer"
    webrtc_answer = "/webrtc/answer"
    webrtc_ice_candidate = "/webrtc/ice-candidate"
    webrtc_ice_candidates_get = "/webrtc/ice-candidates"
    webrtc_status = "/webrtc/status"
    webrtc_close = "/webrtc/close"

    # 操作执行
    click = "/browser/click"
    execute = "/browser/execute"
    safe_execute = "/browser/safe_execute"

    # 资源管理
    cleanup_policy = "/browser/cleanup/policy"
    force_release = "/browser/force/release"

    # 系统管理
    system_statistics = "/system/statistics"
    system_cleanup = "/system/cleanup"
    system_health = "/system/health"

    # 管理员管理
    admin_all_sessions = "/admin/sessions/all"
    admin_all_streams = "/admin/streams/all"
    admin_all_stats = "/admin/stats/all"

    # 安全检查
    security_check = "/security/check"

    # 兼容性路由
    screenshot_legacy = "/screenshot"
    live_create = "/live/create"
    live_view = "/live/view"
    live_stream = "/live/stream"
    live_ws = "/live/ws"
    live_stop = "/live/stop"


class PluginRouterPath(StrEnum):
    """插件管理路由路径"""

    create_plugin = "/plugin"
    update_plugin = "/plugin"
    get_plugins = "/plugins/list"
    get_plugin = "/plugin/get"
    delete_plugin = "/plugin/delete"


class NotifyRouterPath(StrEnum):
    """通知管理路由路径"""

    upsert_notify_config = "/notify/conf/upsert"
    read_notify_config = "/notify/conf/read"
    delete_notify_config = "/notify/conf/delete"
    test_notify = "/notify/test"
