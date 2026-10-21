"""Fork 上游自动同步服务

定时扫描开启了 ``auto_sync_upstream`` 开关、且上游有新版本的 Fork 资源，
逐个同步并发送站内通知。

设计口径详见 ``docs/fork-上游同步计划书.md`` §4.4 / §4.5：

- 扫描 SQL 只捞「开关开启 + 上游存在且公开 + 上游 updated_at 超过同步快照」的副本；
- 本地修改过（``local_steps_modified``）的副本**绝不自动覆盖**：跳过并通知；
- 每轮有处理上限、单条异常不影响整体，全部 best-effort。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from loguru import logger
from sqlalchemy import func, true
from sqlalchemy.orm import aliased
from sqlmodel import select

from app.models.database.workflow.models import (
    CompositeActionModel,
    UserWorkflow,
)
from app.services.execution.crud_service.action_crud import action_crud_svr
from app.services.execution.crud_service.workflow_crud import workflow_crud_svr
from app.utils.depends.session_manager import DatabaseSessionManager

# 每轮最多处理的资源数（单进程严格串行，防止单轮任务过长）
_SCAN_BATCH_LIMIT = 200

# 「无同步快照」按 epoch 处理：NULL 视为需要同步（存量迁移数据）
_EPOCH = datetime(1970, 1, 1)


@dataclass(slots=True)
class _SyncCandidate:
    """一个待自动同步的候选资源"""

    db_id: int
    mid: int
    snowflake_id: int  # action_id / workflow_id（用于通知定位）


class ForkAutoSyncService:
    """Fork 上游自动同步服务"""

    # ── 内部：候选扫描 ────────────────────────────────────────

    @staticmethod
    async def _find_action_candidates() -> list[_SyncCandidate]:
        """扫描开启了自动同步且上游有新版本的自定义操作 Fork 副本"""
        upstream = aliased(CompositeActionModel)
        stmt = (
            select(
                CompositeActionModel.id,
                CompositeActionModel.mid,
                CompositeActionModel.action_id,
            )
            .join(upstream, upstream.id == CompositeActionModel.forked_from_id)
            .where(
                CompositeActionModel.forked_from_id.is_not(None),
                CompositeActionModel.auto_sync_upstream == true(),
                upstream.is_public == true(),
                upstream.updated_at
                > func.coalesce(CompositeActionModel.last_synced_at, _EPOCH),
            )
            .limit(_SCAN_BATCH_LIMIT)
        )
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(stmt)
            return [
                _SyncCandidate(
                    db_id=row[0], mid=int(row[1]), snowflake_id=row[2]
                )
                for row in result.all()
            ]

    @staticmethod
    async def _find_workflow_candidates() -> list[_SyncCandidate]:
        """扫描开启了自动同步且上游有新版本的工作流 Fork 副本"""
        upstream = aliased(UserWorkflow)
        stmt = (
            select(
                UserWorkflow.id,
                UserWorkflow.mid,
                UserWorkflow.workflow_id,
            )
            .join(upstream, upstream.id == UserWorkflow.forked_from_id)
            .where(
                UserWorkflow.forked_from_id.is_not(None),
                UserWorkflow.auto_sync_upstream == true(),
                upstream.is_public == true(),
                upstream.updated_at
                > func.coalesce(UserWorkflow.last_synced_at, _EPOCH),
            )
            .limit(_SCAN_BATCH_LIMIT)
        )
        async with DatabaseSessionManager.async_session() as session:
            result = await session.exec(stmt)
            return [
                _SyncCandidate(
                    db_id=row[0], mid=int(row[1]), snowflake_id=row[2]
                )
                for row in result.all()
            ]

    # ── 内部：通知 ────────────────────────────────────────────

    @staticmethod
    async def _notify(mid: int, title: str, content: str) -> None:
        """发送站内通知（复用工作流运行通知通道；失败只告警不扩散）"""
        try:
            from app.services.mq.rpc_notify import publish_notify

            await publish_notify(mid=mid, title=title, content=content)
        except Exception as exc:  # noqa: BLE001 - 通知失败不影响同步主流程
            logger.warning(f"Fork 自动同步通知发送失败 mid={mid}: {exc}")

    # ── 内部：单条同步 ────────────────────────────────────────

    @staticmethod
    async def _sync_action(candidate: _SyncCandidate) -> None:
        """同步单个自定义操作 Fork 副本（含依赖树）"""
        model = await action_crud_svr.get_by_id(candidate.db_id)
        if not model:
            return

        # 本地修改保护：宁可不自动，不可丢用户数据
        if model.local_steps_modified:
            await ForkAutoSyncService._notify(
                mid=candidate.mid,
                title=f"上游更新已暂停自动同步（{model.name}）",
                content=(
                    f"你的操作「{model.name}」有本地修改，为避免覆盖，"
                    "已跳过本次自动同步。如需更新请手动同步。"
                ),
            )
            return

        try:
            result = await action_crud_svr.sync_from_upstream(
                action_id=candidate.snowflake_id,
                mid=candidate.mid,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"Fork 操作自动同步失败 action_id={candidate.snowflake_id}: {exc}"
            )
            return

        await ForkAutoSyncService._notify(
            mid=candidate.mid,
            title=f"上游更新已自动同步（{model.name}）{datetime.now():%Y-%m-%d %H:%M}",
            content=(
                f"操作「{model.name}」已自动同步到上游最新版本。"
                f"更新 {len(result['updated'])} 个动作，新增 {len(result['created'])} 个动作。"
            ),
        )

    @staticmethod
    async def _sync_workflow(candidate: _SyncCandidate) -> None:
        """同步单个工作流 Fork 副本（外壳配置 + Action 依赖树）"""
        model = await workflow_crud_svr.get_by_id(candidate.db_id)
        if not model:
            return

        # 本地修改保护：外壳触发配置被用户改过同样跳过
        if model.local_steps_modified:
            await ForkAutoSyncService._notify(
                mid=candidate.mid,
                title=f"上游更新已暂停自动同步（{model.name}）",
                content=(
                    f"你的工作流「{model.name}」有本地修改，为避免覆盖，"
                    "已跳过本次自动同步。如需更新请手动同步。"
                ),
            )
            return

        try:
            result = await workflow_crud_svr.sync_from_upstream(
                id=candidate.db_id,
                mid=candidate.mid,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"Fork 工作流自动同步失败 workflow_id={candidate.snowflake_id}: {exc}"
            )
            return

        if result is None:
            return

        await ForkAutoSyncService._notify(
            mid=candidate.mid,
            title=f"上游更新已自动同步（{model.name}）{datetime.now():%Y-%m-%d %H:%M}",
            content=(
                f"工作流「{model.name}」已自动同步到上游最新版本。"
                f"更新 {len(result['updated'])} 个动作，新增 {len(result['created'])} 个动作。"
            ),
        )

    # ── 入口：定时任务调用 ────────────────────────────────────

    @staticmethod
    async def scan_and_sync_all() -> None:
        """扫描并同步所有开启了自动同步的 Fork 资源（定时任务入口）

        单条资源异常只记录日志，不影响其他资源的同步。
        """
        synced = failed = 0

        for candidate in await ForkAutoSyncService._find_action_candidates():
            try:
                await ForkAutoSyncService._sync_action(candidate)
                synced += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.warning(f"Fork 操作自动同步失败 id={candidate.db_id}: {exc}")

        for candidate in await ForkAutoSyncService._find_workflow_candidates():
            try:
                await ForkAutoSyncService._sync_workflow(candidate)
                synced += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.warning(f"Fork 工作流自动同步失败 id={candidate.db_id}: {exc}")

        if synced or failed:
            logger.info(
                f"Fork 上游自动同步完成: 处理 {synced} 个, 失败 {failed} 个"
            )
