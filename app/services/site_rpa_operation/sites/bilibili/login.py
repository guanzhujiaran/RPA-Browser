from app.services.site_rpa_operation.base.base_RPA import BaseRPA
from app.services.geetest.captcha_break import acb

class BiliLoginRPA(BaseRPA):
    async def get_bili_user(self):
        """
        未登录直接抛出错误
        """
        page = await self.session.get_current_page()
        return await page.evaluate("""
            ()=> __BiliUser__
        """)


    async def do_login(self, username: str, password: str):
        page = await self.session.get_current_page()
        await page.goto("https://live.bilibili.com/")
        await page.click(".header-login-entry")
        await page.fill(".bili-mini-account input", username)
        await page.fill('.bili-mini-password input', password)
        await page.click(".universal-btn.login-btn")
        geetest_item_ele = page.locator(".geetest_item_wrap:last-of-type")

        geetest_url = await geetest_item_ele.evaluate(r"""el=>{
                                        const style = window.getComputedStyle(el);
                                        const bg = style.backgroundImage;
                                        const match = bg.match(/url\(["']?(.*?)["']?\)/);
                                        return match ? match[1] : null;
                                        }""")
        result = await acb.predict_chinese_click_from_url(url=geetest_url)
        bound = await geetest_item_ele.bounding_box()
        for x, y in result:
            click_position = {"x": x / 384 * bound.get('width'), "y": y / 384 * bound.get('height')}
            await page.locator(".geetest_item_wrap").click(position=click_position)
        await page.locator(".geetest_commit_tip").click()
        user_info = await self.get_bili_user()
        if not user_info["isLogin"]:
            raise ValueError("登录失败")
