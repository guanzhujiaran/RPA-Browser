"""VIP 身份判定 - 供浏览器启动队列做 VIP / 普通分级

会员信息由网关（pptr）经 ``x-bili-vip-status`` / ``x-bili-vip-type`` 请求头转发，
本服务无需再调 B 站接口查询。
"""

from bili_common.deps.auth import AuthInfo

# B 站大会员状态：0=无，1=有效大会员
VIP_STATUS_ACTIVE = "1"


def is_vip_user(auth_info: AuthInfo) -> bool:
    """当前用户是否有效大会员（决定进入哪条启动队列）"""
    return str(auth_info.vip_status or "0") == VIP_STATUS_ACTIVE


__all__ = ["is_vip_user", "VIP_STATUS_ACTIVE"]
