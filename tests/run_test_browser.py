import sys
import asyncio

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from app.services.site_rpa_operation.base.plugined_page_manager import PluginedSessionInfo
from app.services.RPA_browser.browser_session_pool.playwright_pool import get_default_session_pool
import uuid


async def main():
    uuid_str = uuid.UUID("0fc69198bccb4b00928b9372c99190b7")

    sp = get_default_session_pool()

    bp, bc = await sp.get_session(uuid_str, headless=False)

    pp = PluginedSessionInfo(base_undetected_playwright=bp, session=bc)
    page = await pp.get_current_page()

if __name__ == "__main__":
    asyncio.run(main())
