"""LLM 回退链「全部失败」时的错误采集与汇总测试。

背景：LangChain ``with_fallbacks`` 内部只保留第一个异常
（``langchain_core/runnables/fallbacks.py`` 的 ``raise first_error``），备用模型的失败
原因会被静默吞掉。``_remember_failure`` 在回退链每一环外面记录一次失败，本测试锁定：

- 失败项带「模型@端点#序号」，同名模型的不同 key/中转站可区分；
- 错误按类别归一（429 / 401 / 超时 / 连接失败 …）；
- 密钥脱敏，避免 sk-xxx 进日志与通知；
- 汇总有条数上限，超出按类别计数而不是被长度截断后消失。
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.execution.actions.llm import (
    LLMAction,
    _LLMFailure,
    _classify_llm_error,
    _cooling_state,
    _endpoint_of,
    _failure_details,
    _failure_logs,
    _format_llm_failures,
    _remember_failure,
    _scrub_secrets,
)


class _HttpError(Exception):
    """模拟带 HTTP 状态码的 SDK 异常。"""

    def __init__(self, status_code: int, message: str = "") -> None:
        super().__init__(message or f"HTTP {status_code}")
        self.status_code = status_code


class TestClassifyLlmError:
    @pytest.mark.parametrize(
        ("exc", "expected"),
        [
            (_HttpError(429, "Rate limit reached"), "429 限流"),
            (_HttpError(401, "Incorrect API key"), "401 鉴权失败"),
            (_HttpError(403), "403 无权限"),
            (_HttpError(404, "model not found"), "404 模型或端点不存在"),
            (_HttpError(503), "5xx 服务端错误"),
            (_HttpError(418), "HTTP 418"),
        ],
    )
    def test_status_code_mapping(self, exc: Exception, expected: str) -> None:
        assert _classify_llm_error(exc) == expected

    def test_timeout_detected_by_type(self) -> None:
        assert _classify_llm_error(asyncio.TimeoutError()) == "超时"

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("Connection error.", "连接失败"),
            ("getaddrinfo failed", "连接失败"),
            ("Read timed out", "超时"),
            ("429 Too Many Requests", "429 限流"),
            ("something weird happened", "调用失败"),
        ],
    )
    def test_text_fallback(self, message: str, expected: str) -> None:
        assert _classify_llm_error(RuntimeError(message)) == expected


class TestScrubSecrets:
    def test_masks_common_key_shapes(self) -> None:
        text = (
            'invalid api_key="sk-abcdef12345678" '
            "header=Bearer abcdefgh12345678 "
            "openai_api_key=sk-proj-ZZZZ9999"
        )
        cleaned = _scrub_secrets(text)

        assert "sk-abcdef12345678" not in cleaned
        assert "abcdefgh12345678" not in cleaned
        assert "sk-proj-ZZZZ9999" not in cleaned
        assert cleaned.count("***") >= 3

    def test_keeps_normal_text(self) -> None:
        assert _scrub_secrets("Connection error.") == "Connection error."


class TestEndpoint:
    def test_extracts_host_port_without_scheme_or_query(self) -> None:
        assert _endpoint_of("https://api.deepseek.com/v1?x=1") == "api.deepseek.com"
        assert _endpoint_of("127.0.0.1:1") == "127.0.0.1:1"
        assert _endpoint_of(None) == "-"


class TestFormatFailures:
    def test_lists_everything_when_within_limit(self) -> None:
        failures = [
            _LLMFailure(label="m@a#1", kind="429 限流", message="rate limit"),
            _LLMFailure(label="m@b#2", kind="连接失败", message="Connection error."),
        ]

        text = _format_llm_failures(failures)

        assert "m@a#1 429 限流：rate limit" in text
        assert "m@b#2 连接失败：Connection error." in text

    def test_overflow_is_summarised_by_kind(self) -> None:
        failures = [
            _LLMFailure(label=f"m@a#{i}", kind="401 鉴权失败", message="bad key")
            for i in range(1, 11)
        ]

        text = _format_llm_failures(failures, limit=8)

        # 前 8 条展开，剩余 2 条按类别计数（而不是被整体截断丢失）
        assert "m@a#8" in text
        assert "其余 2 条：401 鉴权失败×2" in text
        assert "m@a#9" not in text

    def test_long_text_is_truncated(self) -> None:
        failures = [
            _LLMFailure(label="m@a#1", kind="5xx 服务端错误", message="x" * 500)
        ]

        text = _format_llm_failures(failures, max_length=100)

        assert len(text) <= 101
        assert text.endswith("…")

    def test_empty_returns_empty(self) -> None:
        assert _format_llm_failures([]) == ""


class TestFailureDetails:
    """结构化明细：写入 `ActionResult.error_details` → 动作日志 result_data。"""

    def test_structured_list_for_machine_reading(self) -> None:
        failures = [
            _LLMFailure(label="m@a#1", kind="401 鉴权失败", message="bad key"),
            _LLMFailure(label="m@b#2", kind="429 限流", message="rate limit"),
        ]

        details = _failure_details(failures)

        assert details is not None
        assert details["model_count"] == 2
        assert details["llm_failures"][0] == {
            "label": "m@a#1",
            "kind": "401 鉴权失败",
            "message": "bad key",
        }
        assert details["llm_failures"][1]["kind"] == "429 限流"

    def test_empty_is_none(self) -> None:
        assert _failure_details([]) is None


class _FailingRunnable:
    """替身 runnable：调用即抛出预设异常（避免真实网络请求）。"""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def ainvoke(self, _messages):
        raise self._exc


class TestRememberFailure:
    async def test_records_label_kind_and_scrubbed_message(self) -> None:
        """失败项带端点与序号，消息脱敏、压成单行。"""
        failures: list[_LLMFailure] = []
        exc = _HttpError(401, "Incorrect API key provided: sk-abcdef123456\nsecond line")
        wrapped = _remember_failure(
            "m1", _FailingRunnable(exc), failures, index=2, endpoint="api.example.com"
        )

        with pytest.raises(_HttpError):
            await wrapped.ainvoke([])

        assert len(failures) == 1
        item = failures[0]
        assert item.label == "m1@api.example.com#2"
        assert item.kind == "401 鉴权失败"
        assert "sk-abcdef" not in item.message
        assert "\n" not in item.message

    async def test_two_entries_with_same_model_are_distinguishable(self) -> None:
        """同名模型、不同端点：靠端点 + 序号区分。"""
        failures: list[_LLMFailure] = []
        exc = RuntimeError("Connection error.")
        first = _remember_failure(
            "deepseek-chat",
            _FailingRunnable(exc),
            failures,
            index=1,
            endpoint="api.a.com",
        )
        second = _remember_failure(
            "deepseek-chat",
            _FailingRunnable(exc),
            failures,
            index=2,
            endpoint="api.b.com",
        )

        for wrapped in (first, second):
            with pytest.raises(RuntimeError):
                await wrapped.ainvoke([])

        labels = [item.label for item in failures]
        assert labels == ["deepseek-chat@api.a.com#1", "deepseek-chat@api.b.com#2"]
        assert all(item.kind == "连接失败" for item in failures)

    async def test_failure_logs_cover_all_items(self) -> None:
        failures = [
            _LLMFailure(label="m@a#1", kind="429 限流", message="rate limit"),
            _LLMFailure(label="m@b#2", kind="超时", message="timeout"),
        ]

        logs = _failure_logs(failures)

        assert len(logs) == 2
        assert "m@a#1（429 限流）" in logs[0]
        assert "m@b#2（超时）" in logs[1]


class TestModelCooldown:
    """模型健康冷却：确定性失败本轮内直接跳过，瞬时错误不跳过

    背景（2026-11-06 run 4e7b37d）：主模型当日额度耗尽 + 备用模型欠费（402），
    75 次调用里 60 次失败、累计 1730s（占整轮 27%）。这些错误在几分钟内不会自愈，
    每条数据都重试一遍纯属浪费。
    """

    def test_402_classified_as_balance(self) -> None:
        assert _classify_llm_error(_HttpError(402, "insufficient balance")) == "402 余额不足"

    def test_quota_429_classified_separately_from_rate_limit(self) -> None:
        """「当天额度用尽」与「瞬时限流」必须区分：前者要冷却，后者只回退。"""
        exc = _HttpError(429, "Rate limit exceeded: free-models-per-day")
        assert _classify_llm_error(exc) == "429 额度耗尽"
        assert _classify_llm_error(_HttpError(429, "Rate limit reached")) == "429 限流"

    async def test_fatal_error_puts_model_into_cooldown(self) -> None:
        failures: list[_LLMFailure] = []
        wrapped = _remember_failure(
            "m1",
            _FailingRunnable(_HttpError(402, "insufficient balance")),
            failures,
            endpoint="api.example.com",
        )
        with pytest.raises(_HttpError):
            await wrapped.ainvoke([])

        assert _cooling_state("m1", "api.example.com") is not None
        active, cooling = LLMAction._split_by_health(
            [("m1", "https://api.example.com/v1", "k"), ("m2", "https://b.com/v1", "k")]
        )
        assert [item[0] for item in active] == ["m2"]
        assert cooling and "402 余额不足" in cooling[0]

    async def test_transient_error_does_not_cool_down(self) -> None:
        """限流 / 超时 / 连接失败走回退链即可，冷却会把可用模型锁死。"""
        for exc in (
            _HttpError(429, "Rate limit reached"),
            asyncio.TimeoutError(),
            RuntimeError("Connection error."),
        ):
            wrapped = _remember_failure(
                "m1", _FailingRunnable(exc), [], endpoint="api.example.com"
            )
            with pytest.raises(BaseException):
                await wrapped.ainvoke([])
            assert _cooling_state("m1", "api.example.com") is None
