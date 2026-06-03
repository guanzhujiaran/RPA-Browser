"""Debug test to see 422 error details"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch


def test_debug_422():
    """Print response body for 422 error"""
    app = FastAPI()
    from app.controller.v1.browser_control.execution.workflow_router import router
    app.include_router(router)

    mock_auth = MagicMock()
    mock_auth.mid = 12345678

    mock_workflow_crud = MagicMock()

    with patch(
        "app.controller.v1.browser_control.execution.workflow_router.get_auth_info_from_header",
        return_value=mock_auth
    ):
        with patch(
            "app.controller.v1.browser_control.execution.workflow_router.workflow_crud",
            mock_workflow_crud
        ):
            with TestClient(app) as client:
                request_data = {
                    "name": "测试工作流",
                    "description": "这是一个测试工作流",
                    "trigger_type": "manual",
                    "trigger_config": {},
                    "is_public": False,
                }
                response = client.post("/api/v1/rpa/browser/control/workflows/create", json=request_data)
                print(f"\nStatus: {response.status_code}")
                print(f"Body: {response.json()}")
