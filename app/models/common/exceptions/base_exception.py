from bili_common.models.response_msg import ResponseMsg
from bili_common.models.response_code import ResponseCode


class BaseException(Exception):
    code: int | None = None
    msg: str | None = None
    # 对外 HTTP 状态码；None = 按 code 推导（见 bili_common.exceptions.http_status_for_code）
    http_status: int | None = None


class BrowserNotifyConfNotFoundException(BaseException):
    # 业务缺失（非 HTTP 404 语义）：走业务码，HTTP 200 承载，前端可读 msg
    code = ResponseCode.BROWSER_NOTIFY_CONF_NOT_FOUND
    msg = ResponseMsg.exception_browser_notify_conf_not_found


class BrowserIdIsNoneExeception(BaseException):
    code = ResponseCode.BAD_REQUEST
    msg = ResponseMsg.exception_browser_id_is_none


class BrowserIdNotBeloneToUserException(BaseException):
    code = ResponseCode.FORBIDDEN
    msg = ResponseMsg.exception_browser_id_not_belone_to_user

    def __init__(self, browser_id: int | str):
        self.msg = self.msg.format(browser_id=browser_id)


# 注意：未登录异常已统一收敛到 bili_common.exceptions.NotLoggedInException
# （业务码 -101 + HTTP 401），本项目不再自定义，避免与公共包产生码值/HTTP 状态分歧。


class InvalidUIDException(BaseException):
    # 「uid 格式非法」是参数错误，不是未认证 —— 用 401 会误触发前端「跳登录」。
    # 与 bili_common.exceptions.InvalidUIDException（INVALID_PARAM）保持同口径。
    code = ResponseCode.BAD_REQUEST
    msg = ResponseMsg.exception_invalid_uid


class InvalidMidFormatException(BaseException):
    code = ResponseCode.BAD_REQUEST
    msg = ResponseMsg.exception_invalid_mid_format


class PluginIdIsNoneException(BaseException):
    code = ResponseCode.BAD_REQUEST
    msg = ResponseMsg.exception_plugin_id_is_none


class PluginIdNotBelongToUserException(BaseException):
    code = ResponseCode.FORBIDDEN
    msg = ResponseMsg.exception_plugin_id_not_belong_to_user

    def __init__(self, plugin_id: int | str):
        self.msg = self.msg.format(plugin_id=plugin_id)


class BrowserNotStartedException(BaseException):
    # 前置状态不满足（不是「路由不存在」）：走业务码，HTTP 200 承载
    code = ResponseCode.BROWSER_NOT_STARTED
    msg = ResponseMsg.exception_browser_not_started


class VideoStreamInitFailedException(BaseException):
    code = ResponseCode.INTERNAL_ERROR
    msg = ResponseMsg.exception_video_stream_init_failed

    def __init__(self, error: str):
        self.msg = self.msg.format(error=error)


class GetBrowserSessionFailedException(BaseException):
    code = ResponseCode.INTERNAL_ERROR
    msg = ResponseMsg.exception_get_browser_session_failed

    def __init__(self, error: str):
        self.msg = self.msg.format(error=error)


class BrowserFingerprintNotFoundException(BaseException):
    # 业务资源不存在（非 HTTP 404 语义）：走业务码，HTTP 200 承载
    code = ResponseCode.BROWSER_ID_NOT_FOUND
    msg = ResponseMsg.exception_browser_fingerprint_not_found


class FingerprintLimitExceededException(BaseException):
    code = ResponseCode.FINGERPRINT_LIMIT_EXCEEDED
    msg = ResponseMsg.exception_fingerprint_limit_exceeded

    def __init__(self, max_fingerprints: int):
        self.msg = self.msg.format(max=max_fingerprints)


class BrowserPageIndexError(BaseException):
    code = ResponseCode.BAD_REQUEST
    msg = ResponseMsg.exception_browser_page_index_error

    def __init__(self, page_index: int):
        self.msg = ResponseMsg.exception_browser_page_index_error.format(
            page_index=page_index)


class GetBrowserInfoFailedException(BaseException):
    code = ResponseCode.INTERNAL_ERROR
    msg = ResponseMsg.exception_get_browser_info_failed

    def __init__(self, error: str):
        self.msg = self.msg.format(error=error)


class WebRTCStreamNotActiveException(BaseException):
    code = ResponseCode.INTERNAL_ERROR
    msg = ResponseMsg.exception_webrtc_stream_not_active


class BilibiliLoginFailedException(BaseException):
    code = ResponseCode.INTERNAL_ERROR
    msg = ResponseMsg.exception_bilibili_login_failed


class NameAlreadyExistsException(BaseException):
    """名称已存在异常（同一用户下）

    业务冲突（非「请求参数错误」）：必须让前端拿到 msg 提示用户改名，
    故使用业务码 NAME_ALREADY_EXISTS（HTTP 200 承载）—— 若沿用 400，
    非 2xx 会使前端 SDK 丢弃响应体，用户只会看到兜底的「操作失败」。
    """
    code = ResponseCode.NAME_ALREADY_EXISTS
    msg = "您已存在名为 '{name}' 的{name_type}，请使用其他名称"

    def __init__(self, name: str, name_type: str = "项目"):
        self.msg = self.msg.format(name=name, name_type=name_type)


class ActionNotAccessibleException(BaseException):
    """无权访问自定义操作异常"""
    code = ResponseCode.FORBIDDEN
    msg = "无权访问操作: {action_id}"

    def __init__(self, action_id: str):
        self.msg = self.msg.format(action_id=action_id)


class ActionNotFoundException(BaseException):
    """引用的自定义操作不存在异常

    业务缺失（非 HTTP 404 语义）：走业务码 ACTION_NOT_FOUND（HTTP 200 承载），
    便于前端提示「引用的操作已被删除」。
    """
    code = ResponseCode.ACTION_NOT_FOUND
    msg = "引用的操作不存在: {action_id}"

    def __init__(self, action_id: str):
        self.msg = self.msg.format(action_id=action_id)
