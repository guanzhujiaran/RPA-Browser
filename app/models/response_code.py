from enum import IntEnum


class ResponseCode(IntEnum):
    """
    统一响应码枚举类
    
    错误码规范：
    - 0: 成功
    - 4xx: HTTP 标准错误码
    - 5xx: HTTP 服务器错误码
    - 1xxx: 通用业务错误码
    - 2xxx: 浏览器控制和 WebRTC 相关错误码
    - 3xxx: 会话管理相关错误码
    - 4xxx: 插件相关错误码
    - 5xxx: 指纹相关错误码
    """
    # ==================== 成功状态 ====================
    SUCCESS = 0  # 请求成功

    # ==================== HTTP 标准错误码 (4xx) ====================
    BAD_REQUEST = 400  # 错误的请求：请求参数格式错误或缺少必要参数
    UNAUTHORIZED = 401  # 未授权：用户未登录或认证失败
    FORBIDDEN = 403  # 禁止访问：用户没有权限访问该资源
    NOT_FOUND = 404  # 资源未找到：请求的资源不存在
    METHOD_NOT_ALLOWED = 405  # 方法不允许：使用了不支持的 HTTP 方法
    REQUEST_TIMEOUT = 408  # 请求超时：客户端请求超时
    CONFLICT = 409  # 冲突：请求与当前资源状态冲突
    GONE = 410  # 资源已删除：请求的资源已被永久删除
    TOO_MANY_REQUESTS = 429  # 请求过多：超过速率限制

    # ==================== HTTP 服务器错误码 (5xx) ====================
    INTERNAL_ERROR = 500  # 服务器内部错误：未预期的服务器错误
    NOT_IMPLEMENTED = 501  # 未实现：请求的功能尚未实现
    BAD_GATEWAY = 502  # 错误的网关：网关收到无效响应
    SERVICE_UNAVAILABLE = 503  # 服务不可用：服务暂时不可用（如数据库连接丢失）
    GATEWAY_TIMEOUT = 504  # 网关超时：网关请求超时

    # ==================== 通用业务错误码 (1xxx) ====================
    BUSINESS_ERROR = 1000  # 通用业务错误：未分类的业务逻辑错误
    VALIDATION_ERROR = 1001  # 验证错误：数据验证失败
    DATABASE_ERROR = 1002  # 数据库错误：数据库操作失败
    NETWORK_ERROR = 1003  # 网络错误：网络连接失败
    
    # 用户认证相关
    MID_NOT_FOUND = 1004  # 用户 MID 未找到：请求头中缺少或无效的 B站 MID
    USER_NOT_AUTHENTICATED = 1005  # 用户未认证：用户未登录或会话过期
    INVALID_USER_ID = 1006  # 无效的用户 ID：用户 ID 格式错误或不存在
    INVALID_MID_FORMAT = 1007  # 无效的 MID 格式：x-bili-mid 请求头格式错误
    
    # 资源未找到
    BROWSER_ID_NOT_FOUND = 1008  # 浏览器 ID 未找到：指定的浏览器实例不存在
    RESOURCE_NOT_FOUND = 1009  # 通用资源未找到：请求的通用资源不存在
    
    # 权限相关
    PERMISSION_DENIED = 1010  # 权限拒绝：用户没有执行该操作的权限
    BROWSER_NOT_BELONG_TO_USER = 1011  # 浏览器不属于用户：尝试访问不属于当前用户的浏览器
    PLUGIN_NOT_BELONG_TO_USER = 1012  # 插件不属于用户：尝试访问不属于当前用户的插件

    # ==================== 浏览器控制和 WebRTC 错误码 (2xxx) ====================
    # WebRTC 连接相关
    WEBRTC_OFFER_FAILED = 2001  # WebRTC Offer 创建失败：无法创建 WebRTC SDP offer
    WEBRTC_ANSWER_FAILED = 2002  # WebRTC Answer 设置失败：无法设置 WebRTC SDP answer
    WEBRTC_ICE_CANDIDATE_FAILED = 2003  # ICE Candidate 添加失败：ICE candidate 格式无效或添加失败
    WEBRTC_CLOSE_FAILED = 2004  # WebRTC 关闭失败：无法关闭 WebRTC 连接
    WEBRTC_CONNECTION_FAILED = 2005  # WebRTC 连接失败：WebRTC 连接建立失败
    WEBRTC_STATUS_FAILED = 2006  # WebRTC 状态查询失败：无法获取 WebRTC 连接状态
    WEBRTC_STREAM_NOT_ACTIVE = 2007  # WebRTC 流未激活：尝试操作未激活的 WebRTC 流
    
    # 截图和页面控制
    SCREENSHOT_FAILED = 2008  # 截图失败：页面截图操作失败
    PAGE_CLOSED = 2009  # 页面已关闭：尝试操作已关闭的页面
    PAGE_NAVIGATION_FAILED = 2010  # 页面导航失败：页面跳转或加载失败
    
    # 视频流相关
    VIDEO_STREAM_INIT_FAILED = 2011  # 视频流初始化失败：浏览器视频流初始化失败
    BROWSER_NOT_STARTED = 2012  # 浏览器未启动：尝试操作未启动的浏览器实例
    
    # 浏览器会话
    GET_BROWSER_SESSION_FAILED = 2013  # 获取浏览器会话失败：无法获取浏览器会话信息
    GET_BROWSER_INFO_FAILED = 2014  # 获取浏览器信息失败：无法获取浏览器详细信息
    BILILOGIN_FAILED = 2015  # B站登录失败：B站账号登录操作失败

    # ==================== 会话管理错误码 (3xxx) ====================
    SESSION_NOT_FOUND = 3001  # 会话未找到：浏览器会话不存在
    SESSION_EXPIRED = 3002  # 会话已过期：浏览器会话已过期
    HEARTBEAT_FAILED = 3003  # 心跳失败：心跳检测失败，会话可能已断开
    SESSION_CREATE_FAILED = 3004  # 会话创建失败：无法创建新的浏览器会话
    
    # 通知配置
    BROWSER_NOTIFY_CONFIG_NOT_FOUND = 3005  # 浏览器通知配置未找到：浏览器通知配置不存在

    # ==================== 插件相关错误码 (4xxx) ====================
    PLUGIN_ID_REQUIRED = 4001  # 插件 ID 必填：请求中缺少插件 ID
    PLUGIN_NOT_FOUND = 4002  # 插件未找到：指定的插件不存在
    PLUGIN_LOAD_FAILED = 4003  # 插件加载失败：插件加载过程中出错
    PLUGIN_EXECUTION_FAILED = 4004  # 插件执行失败：插件执行过程中出错

    # ==================== 指纹相关错误码 (5xxx) ====================
    FINGERPRINT_LIMIT_EXCEEDED = 5001  # 指纹数量超限：已达到最大指纹数量限制
    FINGERPRINT_NOT_FOUND = 5002  # 指纹未找到：指定的浏览器指纹不存在
    FINGERPRINT_CREATE_FAILED = 5003  # 指纹创建失败：无法创建新的浏览器指纹
    FINGERPRINT_UPDATE_FAILED = 5004  # 指纹更新失败：无法更新浏览器指纹
    FINGERPRINT_DELETE_FAILED = 5005  # 指纹删除失败：无法删除浏览器指纹


__all__ = ["ResponseCode"]
