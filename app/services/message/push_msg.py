"""推送消息入口（RabbitMQ 投递）

活跃路径：本服务的推送统一**发布到 RabbitMQ**，由 message-service 完成实际
渠道投递（集中管理渠道与限流）；per-user 的 ``PushChannelConfig`` 随消息的
``config`` 字段序列化下发。

历史说明：原文件还包含 ``PushMessageService``（26 个渠道的直推实现）与
``WeCom``（企业微信应用消息）——随推送架构演进为 MQ 统一投递后已无任何
调用方（全库 grep 验证），本次重构按死代码移除；如需恢复请从 git 历史取回。
"""

from bili_common.core.push_settings import build_server_label

from app.config import settings
from app.services.message.message_pub import publish_message
from app.utils.http import httpx_client


class PushSubject(str):
    """推送消息主题标识。

    标题统一采用 ``[主题]`` 前缀，便于在告警/通知中区分消息性质：

    =======  ======  ======  =====================
    主题       标识    描述     示例（title）
    -------  ------  ------  ---------------------
    info      [i]    信息     [i]收到一条信息
    success   [s]    成功     [s]任务执行成功
    warning   [w]    警告     [w]服务器cpu告警
    failure   [f]    失败     [f]网站签到失败
    =======  ======  ======  =====================
    """

    INFO = "[i]"
    SUCCESS = "[s]"
    WARNING = "[w]"
    FAILURE = "[f]"


def server_label() -> str:
    """返回本服务标识前缀，例如 ``[rpa-browser@10.0.0.5]``。

    实现统一在 ``bili_common.core.push_settings.build_server_label``（与
    be-bilibili-crawler 共用一份），本函数只做本服务的零参包装，历史调用点保持不变。
    ``SERVER_ADDRESS`` 缺省时自动取本机 hostname。
    """
    return build_server_label(settings)


async def send(
    title: str,
    content: str,
    conf=None,
    subject: str = PushSubject.INFO,
    mid: int | None = None,
    **kwargs,
):
    """
    发送推送消息的全局函数接口（信息类）。

    行为已从「在本服务内直接调用各推送渠道」改为「发布到 RabbitMQ，
    由 message-service 统一完成实际推送」，以便集中管理渠道与限流。
    per-user 的 PushChannelConfig 会一并序列化到消息的 config 字段中；
    也可直接传入 dict（如共享的 MESSAGE_CONFIG）。
    标题会自动加上消息主题标识（默认 ``[i]`` 信息）与本服务标识前缀（服务名@地址），
    顺序为「主题 + 服务标识 + 原标题」。

    报错类推送请使用 :func:`send_error`，其标题只写主题与服务标识、不暴露具体错误，
    具体错误统一放进内容。

    :param subject: 消息主题标识，取自 :class:`PushSubject`，如 ``[i]``/``[s]``/``[w]``/``[f]``。
    :param mid: 接收人 mid；message-service 投递侧据此判定该用户的
        「外部推送总开关（push_enabled / 免打扰时段）」。本服务不做判断，缺省 None
        表示不做用户级拦截。
    """
    # conf 为 pydantic / SQLModel 模型时统一序列化为 dict，再投递给 message-service
    config = conf.model_dump() if conf is not None else None
    label = server_label()
    final_title = f"{subject}{label} {title}" if title else f"{subject}{label}"
    await publish_message(
        final_title, content, push_type=None, config=config, mid=mid
    )


async def send_error(
    content: str,
    *,
    subject: str = "运行异常",
    conf=None,
    topic: str = PushSubject.FAILURE,
    mid: int | None = None,
    **kwargs,
):
    """
    统一的「报错」推送入口（通用函数）。

    标题只含「失败主题[f] + 服务@地址 + 笼统的 subject（如 运行异常）」，不写入任何具体错误信息；
    具体错误内容（异常信息、堆栈、上下文）全部放入 content。

    :param topic: 消息主题标识，默认 ``[f]``（失败），可传 ``[w]`` 等其它主题。
    :param mid: 接收人 mid；message-service 投递侧据此判定该用户的
        「外部推送总开关（push_enabled / 免打扰时段）」，缺省 None 表示不拦截。
    """
    # conf 为 pydantic / SQLModel 模型时统一序列化为 dict，再投递给 message-service
    config = conf.model_dump() if conf is not None else None
    final_title = f"{topic}{server_label()} {subject}"
    await publish_message(
        final_title, content, push_type=None, config=config, mid=mid
    )


async def one() -> str:
    """
    获取一条一言。
    :return:
    """
    url = "https://v1.hitokoto.cn/"

    res = await httpx_client.get(
        url=url,
    )
    res = res.json()
    return res.get("hitokoto", "") + "    ----" + res.get("from", "")
