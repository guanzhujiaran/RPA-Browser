from fastapi import APIRouter

from app.models.router.all_routes import (
    browser_control_router,
    browser_control_execution_router,
    browser_control_operation_router,
    browser_control_session_router,
    browser_control_system_router,
    browser_control_webrtc_router,
)
from app.utils.controller.router_path import gen_api_router


def new_browser_router(dependencies=None) -> APIRouter:
    return gen_api_router(browser_control_router, dependencies)


def new_execution_router(dependencies=None) -> APIRouter:
    return gen_api_router(browser_control_execution_router, dependencies)


def new_operation_router(dependencies=None) -> APIRouter:
    return gen_api_router(browser_control_operation_router, dependencies)


def new_session_router(dependencies=None) -> APIRouter:
    return gen_api_router(browser_control_session_router, dependencies)


def new_system_router(dependencies=None) -> APIRouter:
    return gen_api_router(browser_control_system_router, dependencies)


def new_webrtc_router(dependencies=None) -> APIRouter:
    return gen_api_router(browser_control_webrtc_router, dependencies)
