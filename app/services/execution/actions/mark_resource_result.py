"""记录资源处理结果 Action

配合「获取外部数据（dedupe 去重）」形成工作流内的幂等闭环：
循环体处理完单个资源后调用本动作，把成功/失败结果写入
WorkflowResourceRecord（按 当前用户 + 工作流 + resource_type + resource_id 隔离）。

- success=true：记为成功，后续取数去重默认跳过；
- success=false：记为失败，下次仍可被取数捞起重试（断点续跑）。

仅在工作流执行中可用（依赖 exec_meta.workflow_id）。
"""

import time
from typing import Dict, List

from loguru import logger

from app.models.execution.action_params import (
    BuiltinActionType,
    MarkResourceResultParams,
    MarkResourceResultResult,
)
from app.services.execution.actions.base import BaseAction, ActionResult
from app.services.execution.crud_service.resource_record_crud import (
    ResourceRecordService,
)
from app.models.database.workflow.resource_record import WorkflowResourceRecord
from app.utils.depends.session_manager import DatabaseSessionManager
from sqlmodel import select


class MarkResourceResultAction(BaseAction[MarkResourceResultParams]):
    """记录资源处理结果（去重回写）"""

    action_id: BuiltinActionType = BuiltinActionType.MARK_RESOURCE_RESULT
    action_type: BuiltinActionType = BuiltinActionType.MARK_RESOURCE_RESULT
    params: MarkResourceResultParams

    @classmethod
    def new_action(
        cls,
        *,
        mid: int,
        page,
        variables: Dict,
        params: MarkResourceResultParams | None = None,
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

    async def _execute(self) -> ActionResult[MarkResourceResultResult]:
        start_time = time.time()
        valid, error_msg, p = self.validate_params_with_model(self.params)
        if not valid or not p:
            return ActionResult(
                success=False,
                error=error_msg,
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        workflow_id = str(self.exec_meta.get("workflow_id") or "")
        if not workflow_id:
            return ActionResult(
                success=False,
                error="记录资源结果仅在工作流执行中可用（缺少 workflow_id 执行上下文）",
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )
        browser_id = str(self.exec_meta.get("browser_id") or "")

        resource_id = str(p.resource_id).strip()
        if not resource_id:
            return ActionResult(
                success=False,
                error="resource_id 不能为空",
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        run_id = self.exec_meta.get("run_id")
        try:
            await ResourceRecordService.upsert_result(
                self.mid,
                workflow_id,
                browser_id,
                p.resource_type,
                resource_id,
                bool(p.success),
                run_id=str(run_id) if run_id else None,
                fail_reason=p.fail_reason,
                extra=p.extra,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[MarkResourceResultAction] 写入失败: {e}")
            return ActionResult(
                success=False,
                error=f"记录资源结果失败: {e}",
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        # 回读累计处理次数（upsert 已累加）
        process_count = await self._get_process_count(
            workflow_id, browser_id, p.resource_type, resource_id
        )
        logger.info(
            f"[MarkResourceResultAction] workflow={workflow_id} "
            f"browser={browser_id} type={p.resource_type} "
            f"id={resource_id} success={p.success} count={process_count}"
        )
        return ActionResult(
            success=True,
            data=MarkResourceResultResult(
                recorded=True,
                resource_type=p.resource_type,
                resource_id=resource_id,
                success=bool(p.success),
                process_count=process_count,
            ),
            execution_time=time.time() - start_time,
            action_id=self.metadata.id,
            action_name=self.metadata.name,
        )

    async def _get_process_count(
        self,
        workflow_id: str,
        browser_id: str,
        resource_type: str,
        resource_id: str,
    ) -> int:
        """回读该资源写入后的累计处理次数；查询失败返回 0（不影响主流程）。"""
        try:
            async with DatabaseSessionManager.async_session() as session:
                rec = (
                    await session.exec(  # type: ignore[attr-defined]
                        select(WorkflowResourceRecord.process_count).where(
                            WorkflowResourceRecord.mid == str(self.mid),
                            WorkflowResourceRecord.workflow_id == workflow_id,
                            WorkflowResourceRecord.browser_id == browser_id,
                            WorkflowResourceRecord.resource_type == resource_type,
                            WorkflowResourceRecord.resource_id == resource_id,
                        )
                    )
                ).first()
                return int(rec or 0)
        except Exception:  # noqa: BLE001
            return 0
