"""
截图类 Action - Screenshot
"""
from typing import Dict, Any, List

import base64
import time
from loguru import logger

from app.services.execution.actions.base import BaseAction
from app.models.execution.action_params import ScreenshotParams
from app.models.database.workflow.models import BuiltinActionType, ActionResult


class ScreenshotAction(BaseAction):
    """截图操作"""
    action_id: BuiltinActionType = BuiltinActionType.SCREENSHOT
    params: ScreenshotParams

    @classmethod
    def new_action(cls, *, mid: int, page, variables: Dict[str, Any], params: ScreenshotParams | None = None, timeout: int = 30000, input_vars: Dict[str, Any] | None = None, output_vars: List[str] | None = None, action_name: str | None = None):
        return super().new_action(
            mid=mid, page=page, variables=variables,
            params=params, timeout=timeout,
            input_vars=input_vars, output_vars=output_vars,
            action_name=action_name,
        )

    async def execute(self) -> ActionResult:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(
            self.params)
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
                element = self.page.locator(selector)
                logger.info(f"[ScreenshotAction] 元素截图参数: {screenshot_params}")
                image_bytes = await element.screenshot(**screenshot_params)
            else:
                # Page.screenshot() 支持 full_page 参数
                if full_page:
                    screenshot_params["full_page"] = full_page

                logger.info(f"[ScreenshotAction] 页面截图参数: {screenshot_params}")
                image_bytes = await self.page.screenshot(**screenshot_params)

            image_base64 = base64.b64encode(image_bytes).decode()

            return ActionResult(
                success=True,
                data={"format": img_type, "size": len(
                    image_bytes), "base64": image_base64},
                execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )

        except Exception as e:
            logger.error(f"[ScreenshotAction] 截图操作执行异常: {e}")
            return ActionResult(
                success=False, error=str(e), execution_time=time.time() - start_time,
                action_id=self.metadata.id, action_name=self.metadata.name,
            )
