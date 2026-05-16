from fastapi import APIRouter

from app.models.router.all_routes import (
    browser_control_router,
    browser_control_action_router,
    browser_control_workflow_router,
    browser_control_plugin_router,
    browser_control_operation_router,
    browser_control_session_router,
    browser_control_webrtc_router,
)
from app.utils.controller.router_path import gen_api_router


def new_browser_router(dependencies=None) -> APIRouter:
    return gen_api_router(browser_control_router, dependencies)


def new_action_router(dependencies=None) -> APIRouter:
    """自定义操作管理路由"""
    return gen_api_router(browser_control_action_router, dependencies)


def new_workflow_router(dependencies=None) -> APIRouter:
    """工作流管理路由"""
    return gen_api_router(browser_control_workflow_router, dependencies)


def new_plugin_router(dependencies=None) -> APIRouter:
    """插件挂载管理路由"""
    return gen_api_router(browser_control_plugin_router, dependencies)


def new_operation_router(dependencies=None) -> APIRouter:
    return gen_api_router(browser_control_operation_router, dependencies)


def new_session_router(dependencies=None) -> APIRouter:
    return gen_api_router(browser_control_session_router, dependencies)

def new_webrtc_router(dependencies=None) -> APIRouter:
    return gen_api_router(browser_control_webrtc_router, dependencies)
