"""RPA 审批流转测试

覆盖：
- 已通过 → 驳回：资源下架（is_public=False）；
- 已驳回 → 通过：资源恢复公开（is_public=True）；
- 提交审批幂等（同一资源重复提交复用待审核单）；
- 已通过资源重复提交被拒绝；
- 不能为他人资源提交审批。
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from bili_common.deps.auth import get_auth_info_from_header
from bili_common.models.admin_status import AdminStatusResponse
from bili_common.models.response_code import ResponseCode

from app.controller.v1.admin.approval_router import router as approval_router
from app.utils.depends.admin_depends import get_admin_status, require_admin
from app.utils.depends.session_manager import DatabaseSessionManager

from test.controller.conftest import OTHER_MID, TEST_MID

PREFIX = "/api/admin/rpa"


@pytest.fixture
async def admin_client(mock_auth):
    """管理员视角的审批客户端（覆写鉴权依赖，不依赖 be-message /me）"""
    app = FastAPI()
    app.include_router(approval_router, prefix=PREFIX)
    app.dependency_overrides[get_auth_info_from_header] = lambda: mock_auth
    app.dependency_overrides[require_admin] = lambda: mock_auth
    app.dependency_overrides[get_admin_status] = lambda: AdminStatusResponse(
        is_root=True, is_admin=True, biz_perms={}, mid=TEST_MID
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


async def _create_action(mid: int, is_public: bool):
    """直接落库创建动作，返回 (模型, action_id)"""
    from app.models.database.workflow.models import BuiltinActionType
    from app.services.execution.crud_service import action_crud_svr

    return await action_crud_svr.create(
        mid=mid,
        name=f"approval_fixture_{mid}",
        action_type=BuiltinActionType.COMPOSITE,
        steps=[{"action_id": "click", "params": {}}],
        is_composite=True,
        is_public=is_public,
    )


async def _create_approval(action_id: int, status: str) -> int:
    from app.models.database.admin.models import ApprovalRequest

    async with DatabaseSessionManager.async_session() as session:
        approval = ApprovalRequest(
            submitter_mid=TEST_MID,
            resource_type="action",
            resource_id=str(action_id),
            action="publish",
            title="审批夹具",
            status=status,
        )
        session.add(approval)
        await session.commit()
        await session.refresh(approval)
        return approval.id


async def _get_action(action_id: int):
    from app.services.execution.crud_service import action_crud_svr

    return await action_crud_svr.get_by_action_id(action_id)


async def _get_approval_status(approval_id: int) -> str:
    from app.models.database.admin.models import ApprovalRequest
    from sqlmodel import select

    async with DatabaseSessionManager.async_session() as session:
        row = (
            await session.exec(
                select(ApprovalRequest).where(ApprovalRequest.id == approval_id)
            )
        ).first()
        return row.status


@pytest.mark.anyio
class TestApprovalReviewTransitions:
    async def test_reject_after_approve_takes_resource_offline(self, admin_client):
        model = await _create_action(TEST_MID, is_public=True)
        approval_id = await _create_approval(model.action_id, "approved")

        resp = await admin_client.post(
            f"{PREFIX}/approval/review",
            json={"approval_id": approval_id, "status": "rejected", "review_note": "撤回"},
        )

        data = resp.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert (await _get_action(model.action_id)).is_public is False
        assert await _get_approval_status(approval_id) == "rejected"

    async def test_approve_after_reject_restores_resource_public(self, admin_client):
        model = await _create_action(TEST_MID, is_public=False)
        approval_id = await _create_approval(model.action_id, "rejected")

        resp = await admin_client.post(
            f"{PREFIX}/approval/review",
            json={"approval_id": approval_id, "status": "approved", "review_note": "恢复"},
        )

        data = resp.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert (await _get_action(model.action_id)).is_public is True
        assert await _get_approval_status(approval_id) == "approved"

    async def test_pending_to_approved_does_not_auto_publish(self, admin_client):
        """首次审核保持原语义：只放开审批，不自动把资源设为公开"""
        model = await _create_action(TEST_MID, is_public=False)
        approval_id = await _create_approval(model.action_id, "pending")

        resp = await admin_client.post(
            f"{PREFIX}/approval/review",
            json={"approval_id": approval_id, "status": "approved"},
        )

        data = resp.json()
        assert data["code"] == ResponseCode.SUCCESS
        assert (await _get_action(model.action_id)).is_public is False


@pytest.mark.anyio
class TestApprovalSubmit:
    async def test_submit_is_idempotent_for_pending(self, admin_client):
        model = await _create_action(TEST_MID, is_public=False)
        body = {
            "resource_type": "action",
            "resource_id": str(model.action_id),
            "action": "publish",
            "title": "发布申请",
        }

        first = (await admin_client.post(f"{PREFIX}/approval/submit", json=body)).json()
        second = (await admin_client.post(f"{PREFIX}/approval/submit", json=body)).json()

        assert first["code"] == ResponseCode.SUCCESS
        assert second["code"] == ResponseCode.SUCCESS
        assert first["data"]["id"] == second["data"]["id"]

    async def test_submit_rejects_duplicate_approved(self, admin_client):
        model = await _create_action(TEST_MID, is_public=False)
        await _create_approval(model.action_id, "approved")

        resp = await admin_client.post(
            f"{PREFIX}/approval/submit",
            json={
                "resource_type": "action",
                "resource_id": str(model.action_id),
                "action": "publish",
            },
        )

        assert resp.json()["code"] == ResponseCode.CONFLICT

    async def test_submit_rejects_other_users_resource(self, admin_client):
        model = await _create_action(OTHER_MID, is_public=False)

        resp = await admin_client.post(
            f"{PREFIX}/approval/submit",
            json={
                "resource_type": "action",
                "resource_id": str(model.action_id),
                "action": "publish",
            },
        )

        assert resp.json()["code"] == ResponseCode.FORBIDDEN
