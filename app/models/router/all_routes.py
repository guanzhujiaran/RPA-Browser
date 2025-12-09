from sqlmodel import SQLModel

from app.models.router.router_prefix import RouterPrefix
from app.models.router.router_tag import VersionTag, RouterTag


class RouterInfo(SQLModel):
    version_tag: VersionTag
    router_tag: RouterTag
    router_prefix: RouterPrefix


browser_router = RouterInfo(
    version_tag=VersionTag.v1,
    router_tag=RouterTag.browser_fingerprint,
    router_prefix=RouterPrefix.browser,
)

browser_control_router = RouterInfo(
    version_tag=VersionTag.v1,
    router_tag=RouterTag.browser_control,
    router_prefix=RouterPrefix.browser_control,
)

plugin_router = RouterInfo(
    version_tag=VersionTag.v1,
    router_tag=RouterTag.plugin_management,
    router_prefix=RouterPrefix.browser,
)

notify_router = RouterInfo(
    version_tag=VersionTag.v1,
    router_tag=RouterTag.notification_management,
    router_prefix=RouterPrefix.browser,
)