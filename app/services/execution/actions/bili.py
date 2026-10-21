"""B 站相关 Action

提供在「浏览器上下文内」操作 B 站的能力（复用页面 cookie / 登录态）。

- BiliReserveAction  参与空间预约抽奖：导航到空间页并点击「预约」按钮（纯 DOM 流程）。

设计约束（风控安全）：
- **禁止** page.evaluate 注入 JS、**禁止**在页面内 fetch 直接调用 B 站接口。
  所有 B 站交互必须走真实 DOM（navigate / click / input / wait / get_text），
  模拟真人行为，避免触发风控。
- 因此登录检查、关注、点赞、转发等均不在此处实现，而是由复合动作使用
  通用原子动作（navigate / click / wait / if_else / loop）在 DOM 上拼接完成。

设计要点：
- 这些动作的 ActionResult.success 表示「动作本身是否完成」（DOM 流程是否走通），
  业务是否成功放在结果字段（如 joined），供条件分支判断，
  避免单次失败直接中断整个工作流/循环。
"""

import asyncio
import time
from typing import Dict, List

from loguru import logger

from app.models.execution.action_params import (
    BuiltinActionType,
    BiliReserveParams,
    BiliReserveResult,
)
from app.services.execution.actions.base import ActionResult, BaseAction, register_action_class


class BiliReserveAction(BaseAction[BiliReserveParams]):
    """参与 B 站空间预约抽奖（纯 DOM 流程）"""

    action_id: BuiltinActionType = BuiltinActionType.BILI_RESERVE
    action_type: BuiltinActionType = BuiltinActionType.BILI_RESERVE
    params: BiliReserveParams

    @classmethod
    def new_action(
        cls,
        *,
        mid: int,
        page,
        variables: Dict,
        params: BiliReserveParams | None = None,
        timeout: int = 30000,
        input_vars: Dict | None = None,
        output_vars: List[str] | None = None,
        action_name: str | None = None,
    ):
        safe_params = cls._convert_params(params or {})
        kwargs = {
            "action_id": cls.action_id,
            "action_type": cls.action_type,
            "mid": mid,
            "page": page,
            "params": safe_params,
            "timeout": timeout,
            "input_vars": input_vars or {},
            "output_vars": output_vars or [],
            "variables": variables or {},
        }
        if action_name is not None:
            kwargs["_action_name"] = action_name
        return cls(**kwargs)

    async def _execute(self) -> ActionResult[BiliReserveResult]:
        start = time.time()
        valid, error_msg, p = self.validate_params_with_model(self.params)
        if not valid or not p:
            return ActionResult(
                success=False,
                error=error_msg,
                execution_time=time.time() - start,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        try:
            await self.page.goto(p.reserve_url, wait_until="domcontentloaded")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[BiliReserveAction] 打开预约页面失败: {p.reserve_url} - {e}")
            return ActionResult(
                success=False,
                error=f"打开预约页面失败: {p.reserve_url} - {e}",
                execution_time=time.time() - start,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        cards = self.page.locator(p.card_selector)
        try:
            await cards.first.wait_for(timeout=p.wait_timeout)
        except Exception:  # noqa: BLE001
            # 未出现卡片（可能无可预约内容）：视为已完成但未参与。
            # 业务语义不变（found=False 供条件分支判断），但把现场写进过程日志，
            # 便于区分「活动真的结束」与「页面改版 / 未登录 / 风控」。
            # 注意：空间页会渲染一个空的 .subscribe-section-warp 容器（count>0 但不可见），
            # 用 count 才能区分「模块在但无活动卡片（活动已结束）」与「模块没渲染（未登录/改版）」。
            node_count = await cards.count()
            scene = (
                "预约模块存在但没有有效卡片（活动已结束 / 无可预约内容）"
                if node_count > 0
                else "页面未渲染预约模块（活动不存在 / 页面改版 / 未登录 / 风控）"
            )
            logger.info(
                f"[BiliReserveAction] 未找到预约卡片: {p.card_selector} 节点数={node_count} - {scene}"
            )
            return ActionResult(
                success=True,
                data=BiliReserveResult(
                    value=False, joined=False, already=False, found=False
                ),
                logs=[
                    f"未找到预约卡片: 选择器 [{p.card_selector}] 在 {p.wait_timeout}ms "
                    f"内未出现（url={p.reserve_url}；{scene}）"
                ],
                execution_time=time.time() - start,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        target_index = await self._locate_card(cards, p)
        if target_index is None:
            return ActionResult(
                success=True,
                data=BiliReserveResult(
                    value=False, joined=False, already=False, found=False
                ),
                logs=[
                    f"未定位到目标预约卡片: sid={p.reserve_sid} url={p.reserve_url} "
                    f"（页面有卡片但按 sid 未命中，且无「第一个未参与卡片」可点）"
                ],
                execution_time=time.time() - start,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        # 点击「预约」→ 校验是否出现「已预约」→ 未生效则重试。
        # 空间页首屏渲染完成后会重新挂载预约模块：紧贴卡片出现时的那一次点击
        # 会被重渲染吞掉（Playwright 返回 clicked=True，但页面状态不变、无异常），
        # 等页面稳定后再点一次即生效。实测首次点击从未成功、第二次必成功，
        # 因此这里不再依赖单次点击，而是「点击 + 轮询校验 + 重试」。
        last_error: str | None = None
        for attempt in range(1, p.click_retries + 1):
            # 每轮重新定位卡片：模块重挂载后 DOM 节点会换新，但按 sid 命中的那张
            # 卡片始终是同一张；无 sid 属性时退回首轮下标，避免重试误点下一张卡片
            target_index = await self._locate_card(
                cards, p, fallback_index=target_index
            )
            if target_index is None:
                return ActionResult(
                    success=True,
                    data=BiliReserveResult(
                        value=False, joined=False, already=False, found=False
                    ),
                    logs=[
                        f"第 {attempt} 次尝试前未定位到目标预约卡片: sid={p.reserve_sid} "
                        f"url={p.reserve_url}（页面模块发生了重渲染）"
                    ],
                    execution_time=time.time() - start,
                    action_id=self.metadata.id,
                    action_name=self.metadata.name,
                )

            card = cards.nth(target_index)
            # 已经是「已预约」状态 → 此前已参与（也用于确认上一次点击是否已生效）
            if await card.locator(p.cancel_button_selector).count() > 0:
                already = attempt == 1
                logger.info(
                    f"[BiliReserveAction] reserve_url={p.reserve_url} sid={p.reserve_sid} "
                    f"joined=True already={already} attempts={attempt}"
                )
                return ActionResult(
                    success=True,
                    data=BiliReserveResult(
                        value=True, joined=True, already=already, found=True
                    ),
                    execution_time=time.time() - start,
                    action_id=self.metadata.id,
                    action_name=self.metadata.name,
                )

            join_btn = card.locator(p.join_button_selector)
            if await join_btn.count() == 0:
                return ActionResult(
                    success=True,
                    data=BiliReserveResult(
                        value=False, joined=False, already=False, found=True
                    ),
                    logs=[
                        f"预约卡片中未找到预约按钮: 选择器 [{p.join_button_selector}] "
                        f"（sid={p.reserve_sid}；可能页面改版或按钮未加载完成）"
                    ],
                    execution_time=time.time() - start,
                    action_id=self.metadata.id,
                    action_name=self.metadata.name,
                )

            try:
                await join_btn.first.click(timeout=p.wait_timeout)
            except Exception as e:  # noqa: BLE001
                last_error = f"点击预约按钮失败: {e}"
                logger.warning(
                    f"[BiliReserveAction] 第 {attempt}/{p.click_retries} 次点击异常: {e}"
                )
                await asyncio.sleep(p.retry_interval / 1000)
                continue

            if await self._wait_until_joined(card, p):
                logger.info(
                    f"[BiliReserveAction] reserve_url={p.reserve_url} sid={p.reserve_sid} "
                    f"joined=True already=False attempts={attempt}"
                )
                return ActionResult(
                    success=True,
                    data=BiliReserveResult(
                        value=True, joined=True, already=False, found=True
                    ),
                    execution_time=time.time() - start,
                    action_id=self.metadata.id,
                    action_name=self.metadata.name,
                )

            # 点击返回成功但状态没变：典型的首屏重渲染吞点击
            last_error = "点击成功但未出现「已预约」（被页面重渲染吞掉）"
            logger.info(
                f"[BiliReserveAction] 第 {attempt}/{p.click_retries} 次点击未生效: "
                f"sid={p.reserve_sid} url={p.reserve_url}，"
                f"{p.verify_timeout}ms 内未出现「已预约」，准备重试"
            )
            await asyncio.sleep(p.retry_interval / 1000)

        logger.warning(
            f"[BiliReserveAction] 连续 {p.click_retries} 次点击预约均未生效: "
            f"sid={p.reserve_sid} url={p.reserve_url}"
        )
        return ActionResult(
            success=True,
            data=BiliReserveResult(
                value=False, joined=False, already=False, found=True
            ),
            error=(
                f"连续 {p.click_retries} 次点击「预约」后仍未出现「已预约」按钮，预约未生效"
                f"（sid={p.reserve_sid} url={p.reserve_url}；最后一次：{last_error}；"
                "可能被风控拦截或需要验证）"
            ),
            execution_time=time.time() - start,
            action_id=self.metadata.id,
            action_name=self.metadata.name,
        )

    async def _wait_until_joined(self, card, p: BiliReserveParams) -> bool:
        """轮询等待卡片出现「已预约」按钮（毫秒级 verify_timeout）"""
        deadline = time.monotonic() + p.verify_timeout / 1000
        while True:
            if await card.locator(p.cancel_button_selector).count() > 0:
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.4)

    async def _locate_card(
        self,
        cards,
        p: BiliReserveParams,
        fallback_index: int | None = None,
    ) -> int | None:
        """定位目标卡片下标：优先按 reserve_sid + sid_attr 精确匹配；
        其次沿用调用方给的首轮下标（重试时保证仍是同一张卡片）；
        否则返回第一个未参与（无取消按钮）的卡片下标。
        若全部卡片均已参与，则回落到第一张，交由调用方识别「此前已参与」。
        """
        count = await cards.count()
        sid = (p.reserve_sid or "").strip()
        if sid and p.sid_attr:
            for i in range(count):
                attr = await cards.nth(i).get_attribute(p.sid_attr)
                if attr is not None and str(attr).strip() == sid:
                    return i
            # 提供 sid 但页面无该属性时，回落到“首轮下标 / 第一个未参与”策略
        if fallback_index is not None and 0 <= fallback_index < count:
            return fallback_index
        for i in range(count):
            if await cards.nth(i).locator(p.cancel_button_selector).count() == 0:
                return i
        # 无未参与卡片：回落到第一张已参与卡片，使调用方能返回 already=True
        return 0 if count > 0 else None


# 自注册进内置动作注册表（base 的依赖倒置槽位，供控制流策略层按 action_id 查找）

register_action_class(BiliReserveAction)
