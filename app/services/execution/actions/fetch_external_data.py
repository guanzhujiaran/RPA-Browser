"""
获取外部数据 Action - 支持两种模式获取外部数据

模式一（RPC 模式）：通过 RabbitMQ RPC 调用 FastapiApp 内部业务方法
    - params.method_name 为 StrEnum，从预设白名单中选择
    - 每个 method_name 对应独立的强类型 SQLModel 参数字段
    - 参数 JSON 直接作为消息体，服务端由 FastStream 自动 validate

模式二（HTTP 模式）：发送普通 HTTP/HTTPS 请求
    - params.url 指定请求地址
    - 可结合 method/headers/params/body_* 等字段自定义请求

两种模式互斥，由 params 字段自动校验。

结果数据（ActionResult.data）为 FetchExternalDataResult：
- RPC 模式：将其 CommonResponseModel.data 放入 result.data，并附带 code/msg
- HTTP 模式：放入解析后的响应体（JSON 解析结果或文本），并附带 status/headers/url 等元信息
"""

import time
import re
from typing import Dict, List, Any

import httpx
from loguru import logger

from app.services.execution.actions.base import BaseAction, ActionResult
from app.models.execution.action_params import (
    FetchExternalDataParams,
    FetchExternalDataResult,
)
from app.models.execution.enums import HttpBodyTypeEnum
from app.models.execution.system_services import (
    validate_rpc_method,
    routing_key_for,
    RpcMethodName,
)
from app.models.execution.rpc_method_params import (
    RPC_METHOD_PARAMS_FIELD_MAP,
)
from app.models.database.workflow.models import BuiltinActionType
from app.services.mq.rpc_client import rpc_client
from app.services.execution.crud_service.resource_record_crud import (
    ResourceRecordService,
)


_PATH_TOKEN_RE = re.compile(r"[a-zA-Z_$][\w$]*|\[\d+\]")


def _walk_path(obj: Any, path: str | None) -> Any:
    """按点分隔 + 数字索引路径取值（如 data.list、items[0].id）。

    路径非法或中途缺失返回 None。
    """
    if not path:
        return obj
    cur = obj
    for tok in _PATH_TOKEN_RE.findall(path):
        if tok.startswith("["):
            idx = int(tok[1:-1])
            if not isinstance(cur, (list, tuple)) or idx >= len(cur):
                return None
            cur = cur[idx]
        else:
            if not isinstance(cur, dict) or tok not in cur:
                return None
            cur = cur[tok]
    return cur


class FetchExternalDataAction(BaseAction[FetchExternalDataParams]):
    """获取外部数据操作 - 支持两种模式

    - RPC 模式（method_name 提供）：通过 RabbitMQ RPC 调用 FastapiApp 内部业务方法
    - HTTP 模式（url 提供）：发送普通 HTTP/HTTPS 请求

    注意：data 字段为结构化的 FetchExternalDataResult（含 status_code / data / text /
    headers / url / elapsed / is_json），与 result_model 对齐，便于强类型消费与预览。
    """

    action_id: BuiltinActionType = BuiltinActionType.FETCH_EXTERNAL_DATA
    action_type: BuiltinActionType = BuiltinActionType.FETCH_EXTERNAL_DATA
    params: FetchExternalDataParams

    @classmethod
    def new_action(
        cls,
        *,
        mid: int,
        page,
        variables: Dict,
        params: FetchExternalDataParams | None = None,
        timeout: int = 30000,
        input_vars: Dict | None = None,
        output_vars: List[str] | None = None,
        action_name: str | None = None,
    ):
        safe_params = cls._convert_params(params or {})
        kwargs = {
            "action_id": cls.action_id,
            "action_type": cls.action_id,
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

    async def _execute(self) -> ActionResult[FetchExternalDataResult]:
        start_time = time.time()

        valid, error_msg, validated_params = self.validate_params_with_model(
            self.params
        )
        if not valid or not validated_params:
            return ActionResult(
                success=False,
                error=error_msg,
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        # 根据参数分支：method_name 走 RPC 模式，url 走 HTTP 模式
        # RpcMethodName.NONE（空值）等价于 None，走 HTTP 模式
        if (
            validated_params.method_name is not None
            and validated_params.method_name != RpcMethodName.NONE
        ):
            result = await self._execute_via_rpc(
                method_name=validated_params.method_name,
                validated_params=validated_params,
                timeout_sec=validated_params.timeout / 1000.0,
                start_time=start_time,
            )
        else:
            result = await self._execute_via_http(
                url=validated_params.url,  # type: ignore[arg-type]  # mode 校验保证 HTTP 模式下 url 必填
                method=validated_params.method,
                params=validated_params.params,
                headers=validated_params.headers,
                body_type=validated_params.body_type,
                body_json=validated_params.body_json,
                body_form=validated_params.body_form,
                body_raw=validated_params.body_raw,
                raw_content_type=validated_params.raw_content_type,
                follow_redirects=validated_params.follow_redirects,
                proxy=validated_params.proxy,
                timeout_sec=validated_params.timeout / 1000.0,
                start_time=start_time,
            )

        if validated_params.dedupe and result.success:
            result = await self._apply_dedupe(validated_params, result, start_time)
        return result

    async def _apply_dedupe(
        self,
        p: FetchExternalDataParams,
        result: ActionResult[FetchExternalDataResult],
        start_time: float,
    ) -> ActionResult[FetchExternalDataResult]:
        """对成功取数结果按工作流主资源记录做去重（原地过滤列表）。

        - workflow_id / browser_id 取自执行上下文 exec_meta；非工作流执行不允许去重；
        - 列表由 list_path 定位（留空则 data 本身即列表）；
        - 每项主 id 由 id_field 提取，调 ResourceRecordService 剔除已成功项。
        """
        workflow_id = str(self.exec_meta.get("workflow_id") or "")
        if not workflow_id:
            return ActionResult(
                success=False,
                error="去重仅在工作流执行中可用（缺少 workflow_id 执行上下文）",
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )
        browser_id = str(self.exec_meta.get("browser_id") or "")

        payload = result.data
        if payload is None:
            return result
        raw = getattr(payload, "data", None)
        items = _walk_path(raw, p.list_path)
        if not isinstance(items, list):
            return ActionResult(
                success=False,
                error=(
                    f"去重失败：list_path={p.list_path or '(顶层)'} 未定位到列表，"
                    "请检查返回结构或 list_path 配置"
                ),
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        # 提取每项主 id（None/重复由 service 侧规范化）；保留 id -> 项 映射
        id_of: list[Any] = []
        for item in items:
            id_of.append(_walk_path(item, p.id_field))

        pending = await ResourceRecordService.filter_pending(
            self.mid, workflow_id, browser_id, str(p.resource_type), id_of
        )
        pending_set = set(pending)

        filtered: list[Any] = []
        for item, raw_id in zip(items, id_of):
            if raw_id is None:
                # 取不到 id 的项无法去重，保守保留，避免误丢数据
                filtered.append(item)
                continue
            if str(raw_id) in pending_set:
                filtered.append(item)

        removed = len(items) - len(filtered)
        logger.info(
            f"[FetchExternalDataAction] dedupe type={p.resource_type} "
            f"workflow={workflow_id} browser={browser_id} "
            f"total={len(items)} removed={removed} pending={len(filtered)}"
        )

        # 将过滤后的列表写回 data.data（保持原容器结构不变，仅替换列表）
        new_raw = self._replace_at_path(raw, p.list_path, filtered)
        if hasattr(payload, "model_copy"):
            new_payload = payload.model_copy(update={"data": new_raw})
        else:
            payload.data = new_raw  # type: ignore[attr-defined]
            new_payload = payload
        result.data = new_payload
        result.logs.append(
            f"去重完成：{len(items)} 项，剔除已成功 {removed} 项，待处理 {len(filtered)} 项"
        )
        return result

    @staticmethod
    def _replace_at_path(root: Any, path: str | None, value: Any) -> Any:
        """把 _walk_path 定位到的列表替换为 value；path 为空直接返回 value。

        沿路径复制途经的 dict / list，避免原地改动原始响应对象。
        """
        if not path:
            return value
        tokens = _PATH_TOKEN_RE.findall(path)

        def _set(obj: Any, idx: int) -> Any:
            tok = tokens[idx]
            last = idx == len(tokens) - 1
            if tok.startswith("["):
                i = int(tok[1:-1])
                seq = list(obj) if isinstance(obj, (list, tuple)) else []
                if last:
                    if i < len(seq):
                        seq[i] = value
                else:
                    if i < len(seq):
                        seq[i] = _set(seq[i], idx + 1)
                return seq
            key = tok
            d = dict(obj) if isinstance(obj, dict) else {}
            if last:
                d[key] = value
            elif key in d:
                d[key] = _set(d[key], idx + 1)
            return d

        return _set(root, 0)

    async def _execute_via_rpc(
        self,
        *,
        method_name: str,
        validated_params: "FetchExternalDataParams",
        timeout_sec: float,
        start_time: float,
    ) -> ActionResult[FetchExternalDataResult]:
        """通过 RabbitMQ RPC（FastStream）调用 FastapiApp 内部业务方法获取外部数据

        - method_name 字段标识要调用的 RPC 方法（如 get_reserve_lottery）
        - 每个方法对应一个独立 routing_key（= 队列名），客户端直接定位
        - 强类型参数模型直接序列化为 JSON dict 作为消息体
        - FastStream 服务端自动将消息体 validate 为对应 Pydantic 模型
        - handler 返回 CommonResponseModel，客户端解析为 dict

        参数解析：根据 method_name 从 validated_params 中读取对应的强类型参数字段
        （如 get_reserve_lottery_params），序列化为 dict 后直接作为 RPC 消息体发送。
        各方法的参数字段相互独立，与 HTTP 模式的 params 字段完全分离。
        """
        # 校验方法名是否属于允许的 RPC 业务方法（StrEnum 已限制取值，二次校验保险）
        ok, reason = validate_rpc_method(method_name)
        if not ok:
            return ActionResult(
                success=False,
                error=reason,
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        routing_key = routing_key_for(method_name)

        # 根据 method_name 读取对应的强类型参数字段
        # 字段名与 method_name 一一对应（如 get_reserve_lottery → get_reserve_lottery_params）
        field_name = RPC_METHOD_PARAMS_FIELD_MAP.get(method_name)
        if field_name is None:
            return ActionResult(
                success=False,
                error=f"RPC 方法 '{method_name}' 未配置对应的参数字段映射",
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        method_params_model = getattr(validated_params, field_name)
        # 将强类型参数模型序列化为 dict，作为 RPC 消息体直接发送
        # 使用 mode="json" 确保 StrEnum 等类型被转为原始值（如 'pubTime' 而非 enum 对象）
        if method_params_model is not None:
            payload: dict[str, Any] = method_params_model.model_dump(
                exclude_none=True, mode="json"
            )
            logger.info(f"[FetchExternalDataAction] RPC {method_name} 参数: {payload}")
        else:
            # RPA 端不注入任何默认值，由 FastapiApp 端 handler 的 Pydantic 模型决定默认值
            payload = {}
            logger.info(
                f"[FetchExternalDataAction] RPC {method_name} 未配置参数，发送空body"
            )

        logger.info(
            f"[FetchExternalDataAction] RPC {method_name} routing_key={routing_key}"
        )

        try:
            # RPC 模式超时必须 > 服务端 handler 超时（60s）+ buffer，
            # 否则客户端先超时取消 reply_to 消费者，服务端 _publish 时会 CancelledError
            rpc_timeout = max(timeout_sec, 70.0)
            rpc_resp = await rpc_client.call(routing_key, payload, timeout=rpc_timeout)
        except Exception as e:
            logger.warning(
                f"[FetchExternalDataAction] RPC 调用失败: {method_name} - {e}"
            )
            return ActionResult(
                success=False,
                error=f"RPC 调用失败: {e}",
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        elapsed = time.time() - start_time

        # rpc_resp 是 CommonResponseModel 序列化后的 dict: {"code": 0, "msg": "success", "data": ...}
        code = rpc_resp.get("code", -1)
        msg = rpc_resp.get("msg", "")
        if code != 0:
            error_msg = msg or f"RPC 服务端返回错误码: {code}"
            logger.warning(
                f"[FetchExternalDataAction] RPC 服务端错误: {method_name} - {error_msg}"
            )
            return ActionResult(
                success=False,
                error=error_msg,
                execution_time=elapsed,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        raw_data: Any = rpc_resp.get("data")
        logger.info(
            f"[FetchExternalDataAction] RPC 响应 {method_name} "
            f"code={code} elapsed={elapsed:.3f}s"
        )

        return ActionResult(
            success=True,
            data=FetchExternalDataResult(
                status_code=code,
                data=raw_data,
                text=str(raw_data),
                headers={},
                url="",
                elapsed=elapsed,
                is_json=True,
            ),
            execution_time=elapsed,
            action_id=self.metadata.id,
            action_name=self.metadata.name,
        )

    async def _execute_via_http(
        self,
        *,
        url: str,
        method: str,
        params: dict[str, str] | None,
        headers: dict[str, str] | None,
        body_type: HttpBodyTypeEnum,
        body_json: Any | None,
        body_form: dict[str, str] | None,
        body_raw: str | None,
        raw_content_type: str | None,
        follow_redirects: bool,
        proxy: str | None,
        timeout_sec: float,
        start_time: float,
    ) -> ActionResult[FetchExternalDataResult]:
        """发送普通 HTTP/HTTPS 请求获取外部数据

        使用 httpx.AsyncClient 发送请求，根据 body_type 自动构造请求体。
        """
        # 构造请求参数
        request_kwargs: dict[str, Any] = {
            "params": params or None,
            "headers": headers or None,
            "timeout": timeout_sec,
            "follow_redirects": follow_redirects,
        }
        if proxy:
            request_kwargs["proxy"] = proxy

        # 根据 body_type 构造请求体
        if body_type == HttpBodyTypeEnum.JSON:
            request_kwargs["json"] = body_json
        elif body_type == HttpBodyTypeEnum.FORM:
            request_kwargs["data"] = body_form or {}
        elif body_type == HttpBodyTypeEnum.RAW:
            request_kwargs["content"] = body_raw or ""
            if raw_content_type:
                request_headers = request_kwargs.get("headers") or {}
                request_headers["Content-Type"] = raw_content_type
                request_kwargs["headers"] = request_headers

        logger.info(
            f"[FetchExternalDataAction] HTTP {method} {url} "
            f"body_type={body_type.value} follow_redirects={follow_redirects} "
            f"proxy={proxy or '-'}"
        )

        try:
            async with httpx.AsyncClient() as client:
                response = await client.request(method, url, **request_kwargs)
        except httpx.RequestError as e:
            logger.warning(
                f"[FetchExternalDataAction] HTTP 请求失败: {method} {url} - {e}"
            )
            return ActionResult(
                success=False,
                error=f"HTTP 请求失败: {e}",
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )
        except Exception as e:
            logger.warning(
                f"[FetchExternalDataAction] HTTP 请求异常: {method} {url} - {e}"
            )
            return ActionResult(
                success=False,
                error=f"HTTP 请求异常: {e}",
                execution_time=time.time() - start_time,
                action_id=self.metadata.id,
                action_name=self.metadata.name,
            )

        elapsed = time.time() - start_time

        # 解析响应体：JSON 优先，失败则返回原始文本
        try:
            raw_data: Any = response.json()
            is_json = True
        except Exception:
            raw_data = response.text
            is_json = False

        logger.info(
            f"[FetchExternalDataAction] HTTP 响应 {method} {url} "
            f"status={response.status_code} is_json={is_json} "
            f"elapsed={elapsed:.3f}s"
        )

        return ActionResult(
            success=True,
            data=FetchExternalDataResult(
                status_code=response.status_code,
                data=raw_data,
                text=response.text,
                headers=dict(response.headers),
                url=str(response.url),
                elapsed=elapsed,
                is_json=is_json,
            ),
            execution_time=elapsed,
            action_id=self.metadata.id,
            action_name=self.metadata.name,
        )
