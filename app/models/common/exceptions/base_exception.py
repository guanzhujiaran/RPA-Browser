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


class BrowserLaunchQueueTimeoutException(BaseException):
    """启动排队超时（内存长时间不足以放行）"""

    code = ResponseCode.BROWSER_LAUNCH_QUEUE_TIMEOUT
    msg = ResponseMsg.exception_browser_launch_queue_timeout

    def __init__(self, wait_seconds: int):
        self.msg = self.msg.format(seconds=wait_seconds)


class BrowserLaunchQueueCancelledException(BaseException):
    """启动排队被取消（用户主动关闭 / 取消排队）"""

    code = ResponseCode.BROWSER_LAUNCH_QUEUE_CANCELLED
    msg = ResponseMsg.exception_browser_launch_queue_cancelled


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
            page_index=page_index
        )


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


class BrowserWorkflowRunningException(BaseException):
    """工作流执行期间拒绝调试类调用（执行期互斥，见计划书 §5.17）。

    判定不依赖 ``pin_count``：``ExecutionEngine.execute_action``（单步调试自身）与
    ``execute_steps``（工作流）都会 pin，无法据此区分「工作流在执行」与「用户自己在调试」，
    因此依赖 ``BrowserSessionEntry.workflow_run_id``。
    """

    code = ResponseCode.BROWSER_WORKFLOW_RUNNING
    msg = ResponseMsg.exception_browser_workflow_running


class ActionNotFoundException(BaseException):
    """引用的自定义操作不存在异常

    业务缺失（非 HTTP 404 语义）：走业务码 ACTION_NOT_FOUND（HTTP 200 承载），
    便于前端提示「引用的操作已被删除」。
    """

    code = ResponseCode.ACTION_NOT_FOUND
    msg = "引用的操作不存在: {action_id}"

    def __init__(self, action_id: str):
        self.msg = self.msg.format(action_id=action_id)


# ============ 时长 / 会员权益（见计划书 docs/浏览器使用时长与会员权益计划书.md） ============


class DurationInsufficientException(BaseException):
    """时长余额不足，定时工作流拒绝启动"""

    code = ResponseCode.DURATION_INSUFFICIENT
    msg = ResponseMsg.exception_duration_insufficient


class RedeemCodeInvalidException(BaseException):
    """兑换码无效 / 已停用 / 已过期"""

    code = ResponseCode.REDEEM_CODE_INVALID
    msg = ResponseMsg.exception_redeem_code_invalid


class RedeemCodeExhaustedException(BaseException):
    """兑换码可用次数已用尽"""

    code = ResponseCode.REDEEM_CODE_EXHAUSTED
    msg = ResponseMsg.exception_redeem_code_exhausted


class RedeemCodeAlreadyUsedException(BaseException):
    """该用户已兑换过此码"""

    code = ResponseCode.REDEEM_CODE_ALREADY_USED
    msg = ResponseMsg.exception_redeem_code_already_used


class SignInAlreadyTodayException(BaseException):
    """今日已签到"""

    code = ResponseCode.SIGN_IN_ALREADY_TODAY
    msg = ResponseMsg.exception_sign_in_already_today


class PaymentVerifyFailedException(BaseException):
    """支付状态核验失败（Casdoor 查询失败/未配置）"""

    code = ResponseCode.PAYMENT_VERIFY_FAILED
    msg = ResponseMsg.exception_payment_verify_failed


class InvalidLedgerChangeTypeException(BaseException):
    """时长流水过滤参数非法（change_types 含未定义的变动类型）"""

    code = ResponseCode.INVALID_PARAM
    msg = "非法的时长变动类型: {change_type}"

    def __init__(self, change_type: str):
        self.msg = self.msg.format(change_type=change_type)


class InvalidSignInMonthException(BaseException):
    """签到日历查询月份非法（需 YYYY-MM）"""

    code = ResponseCode.INVALID_PARAM
    msg = "非法的查询月份: {month}（需 YYYY-MM）"

    def __init__(self, month: str | None):
        self.msg = self.msg.format(month=month)


class SignMakeupNotAllowedException(BaseException):
    """补签目标日期非法：仅支持补签本月已漏签的过去日期"""

    code = ResponseCode.SIGN_MAKEUP_NOT_ALLOWED
    msg = ResponseMsg.exception_sign_makeup_not_allowed


class SignMakeupAlreadySignedException(BaseException):
    """补签目标日期已签到"""

    code = ResponseCode.SIGN_MAKEUP_ALREADY_SIGNED
    msg = ResponseMsg.exception_sign_makeup_already_signed


class SignMakeupCardNotEnoughException(BaseException):
    """补登卡不足"""

    code = ResponseCode.SIGN_MAKEUP_CARD_NOT_ENOUGH
    msg = ResponseMsg.exception_sign_makeup_card_not_enough


class SignRewardTierLockedException(BaseException):
    """奖励档位未解锁（累计签到天数不足）"""

    code = ResponseCode.SIGN_REWARD_TIER_LOCKED
    msg = ResponseMsg.exception_sign_reward_tier_locked

    def __init__(self, required_days: int):
        self.msg = self.msg.format(days=required_days)


class SignRewardTierExchangedException(BaseException):
    """该奖励档位已兑换过"""

    code = ResponseCode.SIGN_REWARD_TIER_EXCHANGED
    msg = ResponseMsg.exception_sign_reward_tier_exchanged


class SignRewardTierNotFoundException(BaseException):
    """奖励档位不存在（枚举与配置不匹配，属服务端错误）"""

    code = ResponseCode.INVALID_PARAM
    msg = "奖励档位未配置: {tier}"

    def __init__(self, tier: str):
        self.msg = self.msg.format(tier=tier)


class InvalidSignInDateException(BaseException):
    """补签日期格式非法（需 YYYY-MM-DD）"""

    code = ResponseCode.INVALID_PARAM
    msg = "非法的补签日期: {date}（需 YYYY-MM-DD）"

    def __init__(self, date: str | None):
        self.msg = self.msg.format(date=date)


class InvalidSignRewardTierException(BaseException):
    """奖励档位标识非法（需 7d / 14d / 28d）"""

    code = ResponseCode.INVALID_PARAM
    msg = "非法的奖励档位: {tier}（需 7d / 14d / 28d）"

    def __init__(self, tier: str | None):
        self.msg = self.msg.format(tier=tier)


class InvalidSignRewardConfigException(BaseException):
    """签到奖励配置非法（数值非数字 / 档位为空或未按天数升序等）"""

    code = ResponseCode.INVALID_PARAM
    msg = "非法的签到奖励配置: {detail}"

    def __init__(self, detail: str):
        self.msg = self.msg.format(detail=detail)


class InvalidMembershipParamException(BaseException):
    """会员权益模块通用参数非法（兑换码生成参数、日期区间、分页参数等）

    统一复用 ``ResponseCode.INVALID_PARAM``，业务码集中在
    ``bili_common.models.response_code`` 管理。
    """

    code = ResponseCode.INVALID_PARAM
    msg = "{detail}"

    def __init__(self, detail: str):
        self.msg = self.msg.format(detail=detail)


class DateRangeTooLargeException(BaseException):
    """使用统计查询区间过大（保护 DB，限制最大跨度）"""

    code = ResponseCode.INVALID_PARAM
    msg = "查询区间过大：最多 {max_days} 天（当前 {days} 天）"

    def __init__(self, days: int, max_days: int):
        self.msg = self.msg.format(days=days, max_days=max_days)
