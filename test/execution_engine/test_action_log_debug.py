"""用户级 debug 模式（日志调试开关）单测（离线）

对应 ``docs/rpa-workflow-调度外壳计划书.md`` §9.8：debug 是**用户级**开关
（工作流 / 复合动作上的 ``log_debug``，或执行参数里的 ``log.debug``），
不是服务端 env。任一级带 ``debug=True`` 时，``resolve_log_option`` 归一化为
全量采集：入参 / 结果 / 变量全记、成功也记、payload 不截断。

覆盖：
- ``_normalize_debug`` 归一化（幂等 / 透传）；
- ``resolve_log_option`` 优先级：参数 > 父透传 > 工作流 debug > 工作流开关
  > 动作级 > 项目兜底；
- 动作级 ``log_debug`` 经 ``_resolve_db_log_option`` 映射（DB 打桩）。
"""

from types import SimpleNamespace

import pytest

from app.config import settings
from app.models.execution.action_params import ActionLogOption
from app.services.execution.action_logger import (
    _DB_CACHE,
    _ERROR_MESSAGE_MAX_LENGTH,
    _build_result_payload,
    _normalize_debug,
    _resolve_db_log_option,
    _truncate_error_message,
    resolve_log_option,
)


def _full_option(**overrides) -> ActionLogOption:
    """构造一个 debug=True 的选项（字段值随意，归一化会强制覆盖）。"""
    base = dict(
        enabled=False,
        debug=True,
        record_params=False,
        record_result=False,
        record_variables=False,
        only_on_error=True,
        max_payload_length=4000,
        retention_days=7,
    )
    base.update(overrides)
    return ActionLogOption(**base)


class TestNormalizeDebug:
    def test_forces_full_recording(self):
        option = _normalize_debug(_full_option())

        assert option is not None
        assert option.enabled is True
        assert option.record_params is True
        assert option.record_result is True
        assert option.record_variables is True
        assert option.only_on_error is False
        # 0 = 不截断，保证 traceback 等完整排障信息可查
        assert option.max_payload_length == 0
        # 保留期不单独管理，沿用原配置
        assert option.retention_days == 7

    def test_idempotent(self):
        once = _normalize_debug(_full_option())
        twice = _normalize_debug(once)

        assert once == twice

    def test_passthrough_when_debug_off(self):
        plain = ActionLogOption(enabled=True, record_variables=False)

        assert _normalize_debug(plain) is plain
        assert _normalize_debug(None) is None


class TestResolveWithUserDebug:
    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        _DB_CACHE.clear()
        yield
        _DB_CACHE.clear()

    async def test_workflow_debug_overrides_switches(self):
        """工作流 debug 优先于工作流开关（含显式关）与动作级 / 项目级。"""
        option = await resolve_log_option(
            1, "click", workflow_log_enabled=False, workflow_log_debug=True
        )

        assert option is not None
        assert option.enabled is True
        assert option.record_variables is True
        assert option.only_on_error is False
        assert option.max_payload_length == 0

    async def test_workflow_debug_none_falls_through(self):
        """未开启 debug 时行为不变：回落项目兜底。"""
        option = await resolve_log_option(1, "click")

        assert option is not None
        assert option.enabled == settings.action_log_default_enabled
        assert option.debug is False

    async def test_param_level_debug_normalized(self):
        """参数级 log 选项带 debug=True：归一化为全量采集。"""
        option = await resolve_log_option(
            1,
            "click",
            replaced_params={
                "log": {
                    "enabled": False,
                    "debug": True,
                    "record_params": False,
                    "record_variables": False,
                    "only_on_error": True,
                }
            },
        )

        assert option is not None
        assert option.enabled is True
        assert option.record_params is True
        assert option.record_variables is True
        assert option.only_on_error is False

    async def test_param_level_explicit_off_still_wins(self):
        """参数级显式关闭（不带 debug）优先于工作流 debug：更具体的配置获胜。"""
        option = await resolve_log_option(
            1,
            "click",
            replaced_params={"log": {"enabled": False}},
            workflow_log_debug=True,
        )

        assert option is not None
        assert option.enabled is False

    async def test_ctx_inherited_debug_normalized(self):
        """父操作透传的 debug 配置被子步骤继承并归一化。"""
        parent_cfg = _full_option()

        option = await resolve_log_option(
            1, "click", ctx_log_config=parent_cfg
        )

        assert option is not None
        assert option.enabled is True
        assert option.record_variables is True
        assert option.max_payload_length == 0

    async def test_workflow_enabled_still_works_without_debug(self):
        """工作流开关（非 debug）行为不变：显式开 → 记录，字段级用兜底配置。"""
        option = await resolve_log_option(
            1, "click", workflow_log_enabled=True
        )

        assert option is not None
        assert option.enabled is True
        assert option.record_variables is False
        assert option.max_payload_length == settings.action_log_max_payload_length


class TestActionLevelDebug:
    """动作级 log_debug 经 _resolve_db_log_option 映射（DB 打桩，不连真库）。"""

    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        _DB_CACHE.clear()
        yield
        _DB_CACHE.clear()

    @pytest.fixture
    def stub_action_crud(self, monkeypatch):
        def _install(model):
            async def _get_by_action_id(action_id):
                return model

            monkeypatch.setattr(
                "app.services.execution.action_logger.action_crud_svr",
                SimpleNamespace(get_by_action_id=_get_by_action_id),
            )

        return _install

    async def test_action_debug_mapped(self, stub_action_crud):
        stub_action_crud(
            SimpleNamespace(
                log_enabled=None,
                log_debug=True,
                log_record_params=False,
                log_record_result=True,
                log_record_variables=False,
                log_only_on_error=True,
                log_max_payload_length=4000,
                log_retention_days=30,
            )
        )

        option = await _resolve_db_log_option("ca_debug_test")

        assert option is not None
        assert option.debug is True
        # 归一化在 resolve_log_option 出口完成，这里只验证映射
        normalized = _normalize_debug(option)
        assert normalized.enabled is True
        assert normalized.record_params is True
        assert normalized.record_variables is True
        assert normalized.only_on_error is False
        assert normalized.max_payload_length == 0

    async def test_action_without_debug(self, stub_action_crud):
        stub_action_crud(
            SimpleNamespace(
                log_enabled=True,
                log_debug=None,
                log_record_params=True,
                log_record_result=True,
                log_record_variables=False,
                log_only_on_error=False,
                log_max_payload_length=4000,
                log_retention_days=30,
            )
        )

        option = await _resolve_db_log_option("ca_plain_test")

        assert option is not None
        assert option.debug is False
        assert option.enabled is True


class TestErrorMessageColumnGuard:
    """`error_message` 是 varchar(2000)：超长必须截断，否则整条日志写不进去。"""

    def test_long_error_is_truncated_to_column_width(self):
        truncated = _truncate_error_message("x" * (_ERROR_MESSAGE_MAX_LENGTH + 500))

        assert truncated is not None
        assert len(truncated) == _ERROR_MESSAGE_MAX_LENGTH

    def test_normal_error_kept_as_is(self):
        assert _truncate_error_message("Connection error.") == "Connection error."

    def test_empty_error_becomes_none(self):
        assert _truncate_error_message("") is None
        assert _truncate_error_message(None) is None


class TestResultPayload:
    """`result_data` 要能带结构化失败明细（LLM 逐模型失败列表）。"""

    def test_includes_error_details_when_present(self):
        result = SimpleNamespace(
            data=None,
            error_details={
                "model_count": 2,
                "llm_failures": [
                    {"label": "m@a#1", "kind": "401 鉴权失败", "message": "bad key"}
                ],
            },
        )

        payload = _build_result_payload(result, success=False)

        assert payload["success"] is False
        assert payload["data"] is None
        assert payload["error_details"]["model_count"] == 2

    def test_omits_error_details_when_absent(self):
        payload = _build_result_payload(SimpleNamespace(data="ok"), success=True)

        assert payload == {"success": True, "data": "ok"}
