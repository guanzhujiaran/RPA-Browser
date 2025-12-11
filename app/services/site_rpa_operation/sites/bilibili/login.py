from app.services.site_rpa_operation.base.base_RPA import BaseRPA
from app.services.geetest.captcha_break import acb


class BiliLoginRPA(BaseRPA):
    async def get_bili_user(self):
        """
        未登录直接抛出错误

        """
        await page.goto("https://www.bilibili.com/")
        page = await self.session.get_current_page()
        return await page.evaluate(
            """
            ()=> __BiliUser__
        """
        )

    async def do_login_by_pwd(self, username: str, password: str):
        page = await self.session.get_current_page()
        await page.goto("https://live.bilibili.com/")
        await page.click(".header-login-entry")
        await page.fill(".bili-mini-account input", "xxx")
        await page.fill(".bili-mini-password input", "xxx")
        await page.click(".universal-btn.login-btn")

        geetest_url = await page.locator(
            ".geetest_panel.geetest_wind .geetest_item_wrap:last-of-type"
        ).evaluate(
            r"""el=>{
                                const style = window.getComputedStyle(el);
                                const bg = style.backgroundImage;
                                const match = bg.match(/url\(["']?(.*?)["']?\)/);
                                return match ? match[1] : null;
                                }"""
        )
        geetest_result_position = await acb.predict_chinese_click_from_url(
            url=geetest_url
        )

        bound = await page.locator(".geetest_item_wrap:last-of-type").bounding_box()
        for x, y in geetest_result_position:
            print(x, y)
            print(bound)
            click_position = {
                "x": x / 360 * bound.get("width"),
                "y": y / 360 * bound.get("height"),
            }
            print(click_position)
            await asyncio.sleep(1)
            await page.locator(".geetest_table_box").click(position=click_position)
        await page.locator(".geetest_commit_tip").click()
        res = await page.evaluate("()=>{return window.__LIVE_USER_LOGIN_STATUS__ }")
        if res and res.get("isLogin"):
            return
        raise Exception("登录失败")
