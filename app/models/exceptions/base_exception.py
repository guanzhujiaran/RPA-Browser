from app.models.response_msg import ResponseMsg
from app.models.response_code import ResponseCode


class BaseException(Exception):
    code: int | None = None
    msg: str | None = None


class BrowserNotifyConfNotFoundException(BaseException):
    code = ResponseCode.NOT_FOUND
    msg = ResponseMsg.exception_browser_notify_conf_not_found


class BrowserIdIsNoneExeception(BaseException):
    code = ResponseCode.BAD_REQUEST
    msg = ResponseMsg.exception_browser_id_is_none


class BrowserIdNotBeloneToUserException(BaseException):
    code = ResponseCode.FORBIDDEN
    msg = ResponseMsg.exception_browser_id_not_belone_to_user

    def __init__(self, browser_id: int | str):
        self.msg = self.msg.format(browser_id=browser_id)


class NotLoggedInException(BaseException):
    code = ResponseCode.UNAUTHORIZED
    msg = ResponseMsg.exception_not_logged_in


class InvalidUIDException(BaseException):
    code = ResponseCode.UNAUTHORIZED
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
    code = ResponseCode.NOT_FOUND
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
    code = ResponseCode.NOT_FOUND
    msg = ResponseMsg.exception_browser_fingerprint_not_found


class FingerprintLimitExceededException(BaseException):
    code = ResponseCode.FINGERPRINT_LIMIT_EXCEEDED
    msg = ResponseMsg.exception_fingerprint_limit_exceeded

    def __init__(self, max_fingerprints: int):
        self.msg = self.msg.format(max=max_fingerprints)
