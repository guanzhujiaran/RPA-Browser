"""
截图类 Action - Screenshot
"""
import base64
import time
from loguru import logger

from app.services.execution.actions.base import BaseAction
from app.models.execution.params import ScreenshotParams
from app.models.database.workflow.models import ActionType, ActionMetadata, ActionResult, ActionContext


class ScreenshotAction(BaseAction):
    """截图操作"""

    params_model = ScreenshotParams

    def get_metadata(self) -> ActionMetadata:
        return ActionMetadata(
            id="screenshot", name="截图", type=ActionType.SCREENSHOT,
            description="对页面或元素截图",
            parameters=self.get_parameters_from_model(),
            json_schema=self.get_full_schema(),
        )

    async def execute(self, ctx: ActionContext) -> ActionResult:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(ctx.params)
        if not valid:
            return ActionResult(
                success=False, error=error_msg, execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        selector = validated_params.selector
        img_type = str(validated_params.type)
        quality = validated_params.quality
        full_page = validated_params.full_page
        omit_background = validated_params.omit_background
        timeout = validated_params.timeout

        try:
            # 构建 screenshot 参数字典（符合 Playwright API）
            screenshot_params = {"type": img_type}
            
            # JPEG 格式需要 quality 参数
            if img_type.lower() in ["jpeg", "jpg"]:
                screenshot_params["quality"] = quality
            
            # omit_background 仅 png 格式支持
            if img_type.lower() == "png" and omit_background:
                screenshot_params["omit_background"] = omit_background
            
            if timeout != 30000:
                screenshot_params["timeout"] = timeout
            
            if selector:
                # Locator.screenshot() 不支持 full_page 参数
                element = ctx.page.locator(selector)
                logger.info(f"[ScreenshotAction] 元素截图参数: {screenshot_params}")
                image_bytes = await element.screenshot(**screenshot_params)
            else:
                # Page.screenshot() 支持 full_page 参数
                if full_page:
                    screenshot_params["full_page"] = full_page
                
                logger.info(f"[ScreenshotAction] 页面截图参数: {screenshot_params}")
                image_bytes = await ctx.page.screenshot(**screenshot_params)

            image_base64 = base64.b64encode(image_bytes).decode()

            return ActionResult(
                success=True,
                data={"format": img_type, "size": len(image_bytes), "base64": image_base64},
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        except Exception as e:
            logger.error(f"[ScreenshotAction] 截图操作执行异常: {e}")
            return ActionResult(
                success=False, error=str(e), execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )
