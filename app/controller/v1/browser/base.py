from fastapi import APIRouter

from app.models.router.all_routes import browser_fingerprint_router, browser_plugin_router, browser_notification_router
from app.utils.controller.router_path import gen_api_router


def new_fingerprint_router(dependencies=None) -> APIRouter:
    return gen_api_router(browser_fingerprint_router, dependencies)


def new_plugin_router(dependencies=None) -> APIRouter:
    return gen_api_router(browser_plugin_router, dependencies)


def new_notify_router(dependencies=None) -> APIRouter:
    return gen_api_router(browser_notification_router, dependencies)
