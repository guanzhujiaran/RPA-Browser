"""
Random wait plugin - Adds intelligent delays with progressive probability for human-like behavior
"""
import random
import asyncio
from typing import Tuple

from app.services.site_rpa_operation.base.base_plugin import BasePlugin, PluginMethodType


class RandomWaitPlugin(BasePlugin):
    """智能随机等待插件 - 基于操作计数和渐进概率的智能等待策略"""

    def __init__(self, conf, **kwargs):
        """
        初始化智能随机等待插件
        
        Args:
            conf: 配置对象，包含所有等待策略配置
        """
        super().__init__(**kwargs)
        self.conf = conf

        # 操作计数器
        self.operation_count = 0
        self.total_wait_time = 0

        # 当前概率（会逐渐上升）
        self.current_long_wait_prob = self.conf.base_long_wait_prob
        self.current_mid_wait_prob = self.conf.base_mid_wait_prob

        # 上次触发类型（用于重置概率）
        self.last_trigger_type = None
        self.last_trigger_count = 0
        
        self.logger.info(f"[RANDOM WAIT PLUGIN] ⏱️ 随机等待插件初始化")
        self.logger.debug(f"[RANDOM WAIT PLUGIN] ⚙️ 配置参数 - 最小等待: {conf.min_wait}s, 中等等待: {conf.mid_wait}s, 最大等待: {conf.max_wait}s")
        self.logger.debug(f"[RANDOM WAIT PLUGIN] 🎲 概率配置 - 长等待基础概率: {conf.base_long_wait_prob:.2%}, 中等待基础概率: {conf.base_mid_wait_prob:.2%}")

        # 添加操作到操作链（只保留操作后等待）
        self.add_operation(PluginMethodType.AFTER_EXEC, self._intelligent_wait_after, "智能操作后等待")

    def _get_wait_time_range(self, wait_type: str) -> Tuple[float, float]:
        """根据等待类型获取时间范围"""
        if wait_type == "long":
            # 长等待：从中间到最大时间随机
            return (self.conf.mid_wait, self.conf.max_wait)
        elif wait_type == "mid":
            # 中等待：从最小到中间时间随机
            return (self.conf.min_wait, self.conf.mid_wait)
        else:
            # 短等待：最小时间附近
            return (self.conf.min_wait, self.conf.min_wait * 1.5)

    def _should_trigger_wait(self) -> str:
        """判断是否应该触发等待以及等待类型"""
        self.operation_count += 1

        # 强制等待检查
        if self.operation_count % self.conf.long_wait_interval == 0:
            return "long"
        if self.operation_count % self.conf.mid_wait_interval == 0:
            return "mid"

        # 概率等待检查
        rand_val = random.random()

        # 检查长等待
        if rand_val < self.current_long_wait_prob:
            return "long"

        # 检查中等待
        if rand_val < self.current_long_wait_prob + self.current_mid_wait_prob:
            return "mid"

        return "short"

    def _update_probabilities(self, triggered_type: str):
        """更新概率，如果触发等待则重置，否则增加概率"""
        if triggered_type in ["long", "mid"]:
            # 触发等待，重置概率
            self.current_long_wait_prob = self.conf.base_long_wait_prob
            self.current_mid_wait_prob = self.conf.base_mid_wait_prob
            self.last_trigger_type = triggered_type
            self.last_trigger_count = self.operation_count

            self.logger.debug(f"[RANDOM WAIT] 触发{triggered_type}等待，概率已重置")
        else:
            # 未触发等待，增加概率
            self.current_long_wait_prob = min(
                self.current_long_wait_prob + self.conf.prob_increase_factor, 0.3
            )
            self.current_mid_wait_prob = min(
                self.current_mid_wait_prob + self.conf.prob_increase_factor, 0.4
            )

            self.logger.debug(
                f"[RANDOM WAIT] 概率更新: 长等待={self.current_long_wait_prob:.2f}, "
                f"中等待={self.current_mid_wait_prob:.2f}"
            )

    async def _intelligent_wait_after(self):
        """智能操作后等待"""
        self.operation_count += 1
        wait_type = self._should_trigger_wait()
        min_wait, max_wait = self._get_wait_time_range(wait_type)

        wait_time = random.uniform(min_wait, max_wait)
        self.total_wait_time += wait_time

        self.logger.info(
            f"[RANDOM WAIT PLUGIN] ⏳ 操作#{self.operation_count}后{wait_type}等待 {wait_time:.2f}秒 "
            f"(范围: {min_wait:.1f}-{max_wait:.1f}s)"
        )
        self.logger.debug(f"[RANDOM WAIT PLUGIN] 📊 累计等待时间: {self.total_wait_time:.2f}秒, 操作计数: {self.operation_count}")

        await asyncio.sleep(wait_time)
        self._update_probabilities(wait_type)

__all__ = ["RandomWaitPlugin"]