"""运行级资源处理统计（供运行结论判定 / 结束通知使用）

背景（2026-11-06）：
    工作流运行的成败之前只看「步骤是否执行成功」。但业务类工作流（抽奖、批量互动）
    真正关心的指标是**资源处理结果**——`mark_resource_result` 记 failed 时动作本身
    仍然是成功返回，于是「75 条动态里 13 条处理失败」的运行会显示为成功。
    现在把资源统计抬到运行结论的主指标：有资源统计时，资源失败数决定运行结论
    （步骤级的容错失败只作为告警，不再单独判失败）。

状态口径（见 `ResourceProcessStatus`）：
    - success：处理成功（终态，下次默认跳过）
    - skipped：显式跳过（终态，如 404/活动结束），**不算失败**
    - failed：处理失败（下次仍会被捞起重试），按 `fail_reason` 再分两类：
        * 可重试软失败（soft_failed）：明确「下一轮重试 / 结果未确认」这类过程性失败，
          只留痕与告警，不改变运行结论；
        * 致命失败（fatal_failed）：其余全部失败，仍按阈值决定运行结论。

分类关键词由配置注入（见 `settings.workflow_resource_soft_fail_keywords`），
本模块只提供纯判定函数，不内置任何业务文案。
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field


def is_retryable_failure(fail_reason: str | None, keywords: Sequence[str]) -> bool:
    """判断一条失败原因是否属于「可重试软失败」。

    采用**白名单 + fail-safe**：只有命中调用方注入的关键词才算软失败；
    原因为空、未配置关键词或未命中，一律返回 False（按致命失败处理），
    避免新出现的未知错误被静默放过。
    """
    if not fail_reason or not keywords:
        return False
    return any(keyword and keyword in fail_reason for keyword in keywords)


class RunResourceStats(BaseModel):
    """一次运行涉及的全部资源记录按状态聚合后的结果"""

    total: int = Field(default=0, description="本次运行处理过的资源总数")
    success: int = Field(default=0, description="成功数（终态）")
    failed: int = Field(default=0, description="失败数（下一轮会重试）")
    skipped: int = Field(default=0, description="终态跳过数（如 404，不算失败）")
    soft_failed: int = Field(
        default=0, description="失败中按 fail_reason 判定的可重试软失败数"
    )

    @property
    def fatal_failed(self) -> int:
        """致命失败数（只有它参与运行结论降级）"""
        return max(self.failed - self.soft_failed, 0)

    @property
    def failed_ratio(self) -> float:
        """失败占比（分母为本次运行处理过的资源总数）"""
        if self.total <= 0:
            return 0.0
        return self.failed / self.total

    @property
    def has_data(self) -> bool:
        """是否有资源统计（没有资源记录的工作流要退回步骤级判定）"""
        return self.total > 0

    def describe(self) -> str:
        """一行可读摘要：「资源：成功 62 / 失败 13 / 跳过 2（共 77）」"""
        return (
            f"资源：成功 {self.success} / 失败 {self.failed} / "
            f"跳过 {self.skipped}（共 {self.total}）"
        )


__all__ = ["RunResourceStats", "is_retryable_failure"]
