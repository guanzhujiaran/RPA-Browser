"""GeoIP 属地 / 运营商 RPC 客户端（RPA-Browser 为客户端）。

调用 be-message-service 的 `message.geoip.rpc.resolve_ip_region`
（契约见 `bili_common.rpc.geoip`），把观看者客户端 IP 解析成
「属地 + 运营商」（形如「浙江 杭州」/ `China Unicom Shanghai network`），
供「谁在看」列表与直播流日志展示。

为什么不在本服务装 geoip2：GeoLite2 mmdb（City + ASN）与更新流程只留在 be-message 侧
（单一来源 + 口径与评论 / 动态一致），本服务用既有的 RPC 客户端即可。

⚠️ 这些都是**展示信息**，因此这里全程静默降级，绝不影响建流：

- 空 IP / 内网 / 回环：不发起调用直接返回空画像（本地开发即命中，省一次往返）；
- RPC 未连接 / 超时 / be-message 不可用 / 返回非 0：返回空画像，只记 debug 日志。

RPC 客户端单例（`rpc_client`）由 main.py 的 lifespan 在启动时 connect()。
"""

import ipaddress

from bili_common.rpc.base import geoip_rpc_routing_key_for
from bili_common.rpc.geoip import GeoIpRpcMethodName, ResolveIpRegionParams
from loguru import logger
from pydantic import BaseModel, Field

from app.services.mq.rpc_client import rpc_client

# 属地 / 运营商解析超时（秒）：一次建流只调一次，且属展示信息，
# 宁可显示「未知属地」也不能拖慢建流，故取一个远小于默认 180s 的值。
_RESOLVE_TIMEOUT_SEC = 2.0


class IpProfile(BaseModel):
    """一次 IP 解析的展示画像（属地 + 运营商）

    任一项解析不出都是**空串**（不是「未知」文案）：文案归前端 i18n / 日志占位，
    后端只负责给值。
    """

    region: str = Field("", description="属地，如「浙江 杭州」；解析不出为空串")
    isp: str = Field(
        "",
        description=(
            "运营商（ASN 组织名，GeoLite2-ASN 原值、**英文**，"
            "如 `China Unicom Shanghai network`）；解析不出为空串"
        ),
    )


def _is_private_or_loopback(ip: str) -> bool:
    """内网 / 回环 / 保留地址没有属地意义（mmdb 也查不到），直接跳过。"""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved


async def resolve_ip_profile(ip: str) -> IpProfile:
    """解析 IP 的属地与运营商。

    Args:
        ip: 客户端 IP（网关注入的 `x-bili-client-ip`）

    Returns:
        `IpProfile`；任何解析不出 / 调用失败的场景都返回空画像
        （`region=""` / `isp=""`），由调用方回退为「未知属地 / 未知运营商」。
    """
    ip = (ip or "").strip()
    if not ip or _is_private_or_loopback(ip):
        return IpProfile()
    if not rpc_client.connected:
        # RPC 未就绪（启动早期 / 测试环境）：不抛异常，直接降级
        logger.debug(f"IP 画像 RPC 未连接，回退为空画像: ip={ip}")
        return IpProfile()

    try:
        resp = await rpc_client.call(
            geoip_rpc_routing_key_for(GeoIpRpcMethodName.RESOLVE_IP_REGION),
            ResolveIpRegionParams(ip=ip).model_dump(),
            timeout=_RESOLVE_TIMEOUT_SEC,
        )
    except Exception as e:  # noqa: BLE001 - 展示信息，失败不抛
        logger.debug(f"IP 画像 RPC 调用失败（回退为空画像）: ip={ip}, error={e}")
        return IpProfile()

    if resp.get("code") != 0:
        logger.debug(f"IP 画像 RPC 返回失败码（回退为空画像）: ip={ip}, resp={resp}")
        return IpProfile()

    data = resp.get("data") or {}
    return IpProfile(
        region=str(data.get("region") or ""),
        isp=str(data.get("isp") or ""),
    )


__all__ = ["IpProfile", "resolve_ip_profile"]
