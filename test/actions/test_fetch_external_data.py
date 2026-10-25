"""
测试获取外部数据 Action

支持两种模式：
- RPC 模式：method_name（StrEnum）指定 RPC 方法，通过 RabbitMQ RPC 调用 FastapiApp
- HTTP 模式：url 指定请求地址，发送普通 HTTP/HTTPS 请求

RPC 协议已简化：
- 请求端直接发送强类型参数模型的 JSON（含 mid 字段）作为消息体
- 服务端返回 CommonResponseModel 序列化后的 dict: {"code": 0, "msg": "success", "data": ...}

结果数据（ActionResult.data）为结构化的 FetchExternalDataResult：
- RPC 模式：服务端 data 放在 .data，code 放在 .status_code
- HTTP 模式：解析后的响应体放在 .data，并附带 status/headers/url 等元信息
- output_vars 按该模型字段**顺序**取值（status_code → data → …），
  抽奖动作 JSON 的 `['xxx_code', 'xxx_data']` 即依赖这一口径
- 失败错误由基类统一出口补「[动作名(动作ID)]」前缀

使用 unittest.mock 模拟 rpc_client.call 与 httpx.AsyncClient。
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from typing import Any

import httpx

from app.services.execution.actions.fetch_external_data import FetchExternalDataAction
from app.models.execution.action_params import FetchExternalDataParams
from app.models.execution.enums import HttpMethodEnum, HttpBodyTypeEnum
from app.models.execution.system_services import RpcMethodName
from app.models.execution.rpc_method_params import (
    GetReserveLotteryRpcParams,
    GetOthersLotDynListRpcParams,
    OthersLotDynSortEnum,
    OthersLotDynSortOrderEnum,
)


# ========== 辅助函数 ==========


def _make_httpx_response(
    *,
    status_code: int = 200,
    json_data: Any | None = None,
    text: str = "",
    headers: dict[str, str] | None = None,
    url: str = "https://api.example.com/data",
) -> httpx.Response:
    """构造一个 httpx.Response 对象用于测试

    必须带上 `request`：动作结果模型会读取 `response.url`（最终 URL），
    裸构造的 Response 没有 request 时访问 `.url` 会抛
    "The request instance has not been set"。
    """
    request = httpx.Request("GET", url)
    if json_data is not None:
        import json as _json

        content = _json.dumps(json_data).encode("utf-8")
        hdrs = {"content-type": "application/json"}
        if headers:
            hdrs.update(headers)
        return httpx.Response(
            status_code=status_code, content=content, headers=hdrs, request=request
        )
    content = text.encode("utf-8") if text else b""
    hdrs = headers or {"content-type": "text/plain"}
    return httpx.Response(
        status_code=status_code, content=content, headers=hdrs, request=request
    )


def _make_rpc_success_response(data: Any) -> dict[str, Any]:
    """构造 RPC 成功响应 dict（CommonResponseModel 序列化后的形式）"""
    return {"code": 0, "msg": "success", "data": data}


def _make_rpc_error_response(msg: str, code: int = 500) -> dict[str, Any]:
    """构造 RPC 错误响应 dict"""
    return {"code": code, "msg": msg, "data": None}


# ========== RPC 模式测试 ==========


class TestFetchExternalDataRpcMode:
    """获取外部数据操作 - RPC 模式测试"""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_rpc_success(self):
        """测试 RPC 调用返回成功响应"""
        rpc_resp = _make_rpc_success_response({"ok": True, "items": [1, 2, 3]})
        action = FetchExternalDataAction.new_action(
            mid=67890,
            page=None,
            variables={"test": True},
            params=FetchExternalDataParams(
                method=HttpMethodEnum.GET,
                method_name=RpcMethodName.GET_RESERVE_LOTTERY,
                get_reserve_lottery_params=GetReserveLotteryRpcParams(
                    page_num=1, page_size=20
                ),
            ),
        )

        with patch(
            "app.services.execution.actions.fetch_external_data.rpc_client.call",
            new=AsyncMock(return_value=rpc_resp),
        ) as mock_call:
            result = await action.execute()

        assert result.success
        # 结果数据是结构化的 FetchExternalDataResult，RPC 响应体在 .data 字段
        payload_result = result.data
        assert payload_result.data == {"ok": True, "items": [1, 2, 3]}
        assert payload_result.is_json is True
        # 验证 RPC 调用参数：第一个是 routing_key，第二个是 payload dict
        mock_call.assert_awaited_once()
        call_args = mock_call.call_args.args
        routing_key = call_args[0]
        payload = call_args[1]
        assert routing_key == "FastapiApp.rpc.get_reserve_lottery"
        # 强类型参数字段被序列化后作为 RPC 消息体传递
        assert payload["page_num"] == 1
        assert payload["page_size"] == 20

    @pytest.mark.asyncio(loop_scope="session")
    async def test_rpc_response_error(self):
        """测试 RPC 服务端返回错误（code != 0）"""
        rpc_resp = _make_rpc_error_response(msg="内部 HTTP 请求超时", code=500)
        action = FetchExternalDataAction.new_action(
            mid=1,
            page=None,
            variables={"test": True},
            params=FetchExternalDataParams(
                method=HttpMethodEnum.GET,
                method_name=RpcMethodName.GET_RESERVE_LOTTERY,
            ),
        )

        with patch(
            "app.services.execution.actions.fetch_external_data.rpc_client.call",
            new=AsyncMock(return_value=rpc_resp),
        ):
            result = await action.execute()

        assert not result.success
        # 失败错误由基类统一出口补「动作名(动作ID)」上下文前缀（_annotate_error_context）
        assert result.error == "[获取外部数据(fetch_external_data)] 内部 HTTP 请求超时"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_rpc_exception(self):
        """测试 RPC 调用抛出异常（超时/未连接）"""
        action = FetchExternalDataAction.new_action(
            mid=1,
            page=None,
            variables={"test": True},
            params=FetchExternalDataParams(
                method=HttpMethodEnum.GET,
                method_name=RpcMethodName.GET_RESERVE_LOTTERY,
            ),
        )

        with patch(
            "app.services.execution.actions.fetch_external_data.rpc_client.call",
            new=AsyncMock(side_effect=TimeoutError("RPC 请求超时（30s）")),
        ):
            result = await action.execute()

        assert not result.success
        assert "RPC 调用失败" in result.error
        assert "RPC 请求超时" in result.error

    @pytest.mark.asyncio(loop_scope="session")
    async def test_rpc_output_vars_merge(self):
        """测试 RPC 模式 output_vars 变量合并 - 按结果模型字段顺序取值

        `output_vars` 按 `FetchExternalDataResult.model_dump()` 的字段**顺序**取值，
        即第 1 个变量 = status_code、第 2 个 = data。抽奖动作 JSON 正是依赖这一口径：
        `output_vars: ['others_code', 'others_data']` → (响应码, 响应数据)。
        """
        rpc_resp = _make_rpc_success_response({"token": "abc123"})
        action = FetchExternalDataAction.new_action(
            mid=1,
            page=None,
            variables={"test": True},
            params=FetchExternalDataParams(
                method=HttpMethodEnum.GET,
                method_name=RpcMethodName.GET_RESERVE_LOTTERY,
            ),
            output_vars=["resp_code", "resp_data"],
        )

        with patch(
            "app.services.execution.actions.fetch_external_data.rpc_client.call",
            new=AsyncMock(return_value=rpc_resp),
        ):
            result = await action.execute()

        assert result.success
        assert "resp_code" in result.variables
        assert result.variables["resp_code"] == 0
        assert result.variables["resp_data"] == {"token": "abc123"}
        # last_output 始终是完整的结果模型
        assert result.variables["last_output"].data == {"token": "abc123"}

    @pytest.mark.asyncio(loop_scope="session")
    async def test_rpc_typed_params_forwarded_per_method(self):
        """测试 RPC 模式按 method_name 读取对应强类型参数字段并转发（与 HTTP params 分离）"""
        rpc_resp = _make_rpc_success_response({"ok": True})
        # 每个 method_name 对应独立的强类型 SQLModel 字段，仅当前方法字段被使用
        action = FetchExternalDataAction.new_action(
            mid=67890,
            page=None,
            variables={"test": True},
            params=FetchExternalDataParams(
                method_name=RpcMethodName.GET_RESERVE_LOTTERY,
                get_reserve_lottery_params=GetReserveLotteryRpcParams(
                    page_num=1,
                    page_size=20,
                ),
                # 其他方法的字段不应影响当前 RPC 调用
                get_others_lot_dyn_list_params=GetOthersLotDynListRpcParams(
                    page_num=2,
                    sort_by=OthersLotDynSortEnum.pub_time,
                ),
                # HTTP 专属字段，RPC 模式下应被忽略
                params={"http_query": "should_be_ignored"},
                headers={"X-HTTP-Header": "should_be_ignored"},
            ),
        )

        with patch(
            "app.services.execution.actions.fetch_external_data.rpc_client.call",
            new=AsyncMock(return_value=rpc_resp),
        ) as mock_call:
            result = await action.execute()

        assert result.success
        mock_call.assert_awaited_once()
        payload = mock_call.call_args.args[1]
        # 应使用 get_reserve_lottery_params 字段的值，None 值被 exclude_none 过滤
        assert payload["page_num"] == 1
        assert payload["page_size"] == 20
        # HTTP 专属字段不应出现在 RPC payload 中
        assert "http_query" not in payload
        assert "headers" not in payload
        assert "body_type" not in payload
        assert "body_json" not in payload
        assert "body_form" not in payload
        assert "body_raw" not in payload
        # 其他方法的字段不应出现（如 sort_by 是 get_others_lot_dyn_list 专属）
        assert "sort_by" not in payload
        # mid 不应出现在 payload 中（已移除）
        assert "mid" not in payload

    @pytest.mark.asyncio(loop_scope="session")
    async def test_rpc_typed_params_none_field_sends_empty_body(self):
        """测试 RPC 模式下对应方法字段为 None 时发送空 body

        契约（2026-10 起）：RPA 端**不注入任何默认值**，未配置参数字段时发送
        空 body `{}`，由 FastapiApp 端 handler 的 Pydantic 模型决定默认值。
        """
        rpc_resp = _make_rpc_success_response({"ok": True})
        # 未配置 get_reserve_lottery_params（字段为 None）
        action = FetchExternalDataAction.new_action(
            mid=1,
            page=None,
            variables={"test": True},
            params=FetchExternalDataParams(
                method_name=RpcMethodName.GET_RESERVE_LOTTERY,
            ),
        )

        with patch(
            "app.services.execution.actions.fetch_external_data.rpc_client.call",
            new=AsyncMock(return_value=rpc_resp),
        ) as mock_call:
            result = await action.execute()

        assert result.success
        payload = mock_call.call_args.args[1]
        # 空 body：默认值由服务端模型决定，RPA 端不代为填充
        assert payload == {}
        # HTTP 专属字段不应出现
        assert "headers" not in payload
        assert "body_json" not in payload
        # mid 不应出现在 payload 中（已移除）
        assert "mid" not in payload

    @pytest.mark.asyncio(loop_scope="session")
    async def test_rpc_typed_params_serializes_enum_and_typed_values(self):
        """测试 RPC 模式强类型参数的 StrEnum 与各类型值被正确序列化（mode=json）"""
        rpc_resp = _make_rpc_success_response({"ok": True})
        action = FetchExternalDataAction.new_action(
            mid=1,
            page=None,
            variables={"test": True},
            params=FetchExternalDataParams(
                method_name=RpcMethodName.GET_OTHERS_LOT_DYN_LIST,
                get_others_lot_dyn_list_params=GetOthersLotDynListRpcParams(
                    page_num=1,
                    page_size=50,
                    is_lot=True,
                    sort_by=OthersLotDynSortEnum.pub_time,
                    sort_order=OthersLotDynSortOrderEnum.desc,
                    pub_time_start=1700000000,
                    pub_time_end=1800000000,
                ),
            ),
        )

        with patch(
            "app.services.execution.actions.fetch_external_data.rpc_client.call",
            new=AsyncMock(return_value=rpc_resp),
        ) as mock_call:
            result = await action.execute()

        assert result.success
        payload = mock_call.call_args.args[1]
        # StrEnum 应被序列化为原始字符串值（如 'pubTime' 而非 enum 对象）；
        # exclude_none=True 只剔除未设置的 None 字段，模型默认值（时间快捷筛选）
        # 会一并下发，由服务端模型校验
        assert payload == {
            "page_num": 1,
            "page_size": 50,
            "is_lot": True,
            "sort_by": "pubTime",
            "sort_order": "desc",
            "pub_time_start": 1700000000,
            "pub_time_end": 1800000000,
            "created_at_preset": "30d",
            "pub_time_preset": "7d",
        }
        assert isinstance(payload["page_num"], int)
        assert isinstance(payload["is_lot"], bool)
        assert payload["sort_by"] == "pubTime"  # StrEnum 原始值，而非 enum 对象
        # mid 不应出现在 payload 中（已移除）
        assert "mid" not in payload


# ========== HTTP 模式测试 ==========


class TestFetchExternalDataHttpMode:
    """获取外部数据操作 - HTTP 模式测试"""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_none_method_name_routes_to_http(self):
        """测试 method_name=NONE + url 应走 HTTP 模式（不调用 RPC）"""
        action = FetchExternalDataAction.new_action(
            mid=1,
            page=None,
            variables={"test": True},
            params=FetchExternalDataParams(
                method=HttpMethodEnum.GET,
                method_name=RpcMethodName.NONE,
                url="https://api.example.com/data",
            ),
        )

        mock_response = _make_httpx_response(
            status_code=200,
            json_data={"ok": True},
        )

        with (
            patch(
                "app.services.execution.actions.fetch_external_data.httpx.AsyncClient",
            ) as mock_client_cls,
            patch(
                "app.services.execution.actions.fetch_external_data.rpc_client.call",
                new=AsyncMock(),
            ) as mock_rpc_call,
        ):
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await action.execute()

        assert result.success
        # 结果数据是 FetchExternalDataResult，解析后的响应体在 .data
        assert result.data.data == {"ok": True}
        assert result.data.is_json is True
        assert result.data.status_code == 200
        # RPC 不应被调用
        mock_rpc_call.assert_not_awaited()
        # HTTP 应被调用
        mock_client.request.assert_awaited_once()

    @pytest.mark.asyncio(loop_scope="session")
    async def test_http_get_json_success(self):
        """测试 HTTP GET 请求返回 JSON 成功响应"""
        action = FetchExternalDataAction.new_action(
            mid=1,
            page=None,
            variables={"test": True},
            params=FetchExternalDataParams(
                method=HttpMethodEnum.GET,
                url="https://api.example.com/data",
                params={"page": "1"},
            ),
        )

        mock_response = _make_httpx_response(
            status_code=200,
            json_data={"ok": True, "items": [1, 2, 3]},
        )

        with patch(
            "app.services.execution.actions.fetch_external_data.httpx.AsyncClient",
        ) as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await action.execute()

        assert result.success
        assert result.data.data == {"ok": True, "items": [1, 2, 3]}
        assert result.data.url == "https://api.example.com/data"
        # 验证 httpx 请求参数
        mock_client.request.assert_awaited_once()
        call_args = mock_client.request.call_args
        assert call_args.args == ("GET", "https://api.example.com/data")
        kwargs = call_args.kwargs
        assert kwargs["params"] == {"page": "1"}
        assert kwargs["follow_redirects"] is True

    @pytest.mark.asyncio(loop_scope="session")
    async def test_http_post_json_body(self):
        """测试 HTTP POST 请求 + JSON body"""
        action = FetchExternalDataAction.new_action(
            mid=1,
            page=None,
            variables={"test": True},
            params=FetchExternalDataParams(
                method=HttpMethodEnum.POST,
                url="https://api.example.com/submit",
                body_type=HttpBodyTypeEnum.JSON,
                body_json={"name": "test", "value": 42},
            ),
        )

        mock_response = _make_httpx_response(
            status_code=201,
            json_data={"id": 100},
        )

        with patch(
            "app.services.execution.actions.fetch_external_data.httpx.AsyncClient",
        ) as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await action.execute()

        assert result.success
        assert result.data.data == {"id": 100}
        assert result.data.status_code == 201
        kwargs = mock_client.request.call_args.kwargs
        assert kwargs["json"] == {"name": "test", "value": 42}

    @pytest.mark.asyncio(loop_scope="session")
    async def test_http_form_body(self):
        """测试 HTTP 请求 + 表单 body"""
        action = FetchExternalDataAction.new_action(
            mid=1,
            page=None,
            variables={"test": True},
            params=FetchExternalDataParams(
                method=HttpMethodEnum.POST,
                url="https://api.example.com/form",
                body_type=HttpBodyTypeEnum.FORM,
                body_form={"field1": "value1", "field2": "value2"},
            ),
        )

        mock_response = _make_httpx_response(
            status_code=200,
            json_data={"received": True},
        )

        with patch(
            "app.services.execution.actions.fetch_external_data.httpx.AsyncClient",
        ) as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await action.execute()

        assert result.success
        kwargs = mock_client.request.call_args.kwargs
        assert kwargs["data"] == {"field1": "value1", "field2": "value2"}

    @pytest.mark.asyncio(loop_scope="session")
    async def test_http_raw_body_with_content_type(self):
        """测试 HTTP 请求 + 原始文本 body + 自定义 Content-Type"""
        action = FetchExternalDataAction.new_action(
            mid=1,
            page=None,
            variables={"test": True},
            params=FetchExternalDataParams(
                method=HttpMethodEnum.POST,
                url="https://api.example.com/xml",
                body_type=HttpBodyTypeEnum.RAW,
                body_raw="<root><item>1</item></root>",
                raw_content_type="application/xml",
            ),
        )

        mock_response = _make_httpx_response(
            status_code=200,
            json_data={"parsed": True},
        )

        with patch(
            "app.services.execution.actions.fetch_external_data.httpx.AsyncClient",
        ) as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await action.execute()

        assert result.success
        kwargs = mock_client.request.call_args.kwargs
        assert kwargs["content"] == "<root><item>1</item></root>"
        assert kwargs["headers"]["Content-Type"] == "application/xml"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_http_non_json_response(self):
        """测试 HTTP 返回非 JSON 响应（返回 text）"""
        action = FetchExternalDataAction.new_action(
            mid=1,
            page=None,
            variables={"test": True},
            params=FetchExternalDataParams(
                method=HttpMethodEnum.GET,
                url="https://example.com/plain",
            ),
        )

        mock_response = _make_httpx_response(
            status_code=200,
            text="Hello, World!",
        )

        with patch(
            "app.services.execution.actions.fetch_external_data.httpx.AsyncClient",
        ) as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await action.execute()

        assert result.success
        assert result.data.data == "Hello, World!"
        assert result.data.is_json is False

    @pytest.mark.asyncio(loop_scope="session")
    async def test_http_request_error(self):
        """测试 HTTP 请求抛出 RequestError（连接失败等）"""
        action = FetchExternalDataAction.new_action(
            mid=1,
            page=None,
            variables={"test": True},
            params=FetchExternalDataParams(
                method=HttpMethodEnum.GET,
                url="https://invalid.example.com",
            ),
        )

        with patch(
            "app.services.execution.actions.fetch_external_data.httpx.AsyncClient",
        ) as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.request = AsyncMock(
                side_effect=httpx.ConnectError("connection refused")
            )
            mock_client_cls.return_value = mock_client

            result = await action.execute()

        assert not result.success
        assert "HTTP 请求失败" in result.error
        assert "connection refused" in result.error

    @pytest.mark.asyncio(loop_scope="session")
    async def test_http_output_vars_merge(self):
        """测试 HTTP 模式 output_vars 变量合并 - 按结果模型字段顺序取值"""
        action = FetchExternalDataAction.new_action(
            mid=1,
            page=None,
            variables={"test": True},
            params=FetchExternalDataParams(
                method=HttpMethodEnum.GET,
                url="https://api.example.com/data",
            ),
            output_vars=["resp_code", "resp_data"],
        )

        mock_response = _make_httpx_response(
            status_code=200,
            json_data={"token": "xyz"},
        )

        with patch(
            "app.services.execution.actions.fetch_external_data.httpx.AsyncClient",
        ) as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await action.execute()

        assert result.success
        assert "resp_code" in result.variables
        assert result.variables["resp_code"] == 200
        assert result.variables["resp_data"] == {"token": "xyz"}
        assert result.variables["last_output"].data == {"token": "xyz"}


# ========== 参数校验测试 ==========


class TestFetchExternalDataParamsValidation:
    """获取外部数据操作参数校验测试"""

    def test_both_method_name_and_url_rejected(self):
        """测试同时提供 method_name 和 url 应被拒绝"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError) as exc_info:
            FetchExternalDataParams(
                method_name=RpcMethodName.GET_RESERVE_LOTTERY,
                url="https://example.com",
            )
        assert "互斥" in str(exc_info.value)

    def test_neither_method_name_nor_url_rejected(self):
        """测试既不提供 method_name 也不提供 url 应被拒绝"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError) as exc_info:
            FetchExternalDataParams()
        assert "必须提供" in str(exc_info.value)

    def test_method_name_only_accepted(self):
        """测试仅提供 method_name 应被接受"""
        params = FetchExternalDataParams(
            method_name=RpcMethodName.GET_RESERVE_LOTTERY,
        )
        assert params.method_name == RpcMethodName.GET_RESERVE_LOTTERY
        assert params.url is None

    def test_url_only_accepted(self):
        """测试仅提供 url 应被接受"""
        params = FetchExternalDataParams(
            url="https://example.com/api",
        )
        assert params.url == "https://example.com/api"
        assert params.method_name is None

    def test_none_method_name_with_url_accepted(self):
        """测试 method_name=NONE + url 应被接受（等价于 HTTP 模式）"""
        params = FetchExternalDataParams(
            method_name=RpcMethodName.NONE,
            url="https://example.com/api",
        )
        assert params.method_name == RpcMethodName.NONE
        assert params.url == "https://example.com/api"

    def test_none_method_name_without_url_rejected(self):
        """测试 method_name=NONE 且无 url 应被拒绝（必须提供 url）"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError) as exc_info:
            FetchExternalDataParams(method_name=RpcMethodName.NONE)
        assert "必须提供" in str(exc_info.value)

    def test_empty_string_method_name_with_url_accepted(self):
        """测试 method_name='' + url 应被接受（空字符串解析为 NONE）"""
        params = FetchExternalDataParams(
            method_name="",
            url="https://example.com/api",
        )
        assert params.method_name == RpcMethodName.NONE
        assert params.url == "https://example.com/api"
