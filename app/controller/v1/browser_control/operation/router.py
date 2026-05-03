from fastapi import Depends
from app.models.response import StandardResponse, success_response
from app.models.router.router_prefix import BrowserControlRouterPath
from app.services.RPA_browser.live_service import LiveService
from app.utils.depends.mid_depends import get_auth_info_from_header, AuthInfo
from app.utils.depends.security_depends import verify_browser_ownership
from app.models.common.depends import BrowserReqInfo, BrowserReqAuthInfo
from ..base import new_operation_router

router = new_operation_router()

