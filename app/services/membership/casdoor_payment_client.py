"""Casdoor 支付对账客户端（计划书 §3.3）

RPA 后端以应用 client 凭证（Basic auth）调用 Casdoor 管理端 API，
查询用户支付单真实状态 —— 入账只认这里的查询结果，不信任任何前端参数。

约定：
    - 外部 HTTP 响应在本文件边界处即被解析为 pydantic 模型（``CasdoorPaymentPayload`` /
      ``CasdoorProductPayload``），服务层不再接触裸 dict；
    - URL 路径段（owner / name）一律 quote，防路径注入打到 Casdoor 其他内部端点。

注意：API 端点以部署的 Casdoor 版本为准（v1 按 v2.x `/api/get-payments` 实现）。
"""

from __future__ import annotations

import base64
from urllib.parse import quote

import httpx
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import settings


class CasdoorPaymentPayload(BaseModel):
    """Casdoor 支付单（对外 API 只暴露本项目需要的字段）"""

    model_config = ConfigDict(extra="ignore")

    owner: str = Field(default="", description="支付单归属组织")
    name: str = Field(default="", description="支付单唯一名")
    user: str = Field(default="", description="支付用户（Casdoor 用户名）")
    state: str = Field(default="", description="支付状态: Created / Paid / ...")
    product_name: str = Field(
        default="", description="关联商品名（旧版 Casdoor 单商品字段 productName）"
    )
    # 新版 Casdoor 购物车支付单：商品挂在 products 数组（owner/name），
    # 单个 element 形如 "rpa-monthcard-31d" 或 "Bili/rpa-monthcard-31d"。
    products: list[str] = Field(
        default_factory=list, description="关联商品名列表（新版多商品字段 products）"
    )
    price: float = Field(default=0.0, description="支付金额")

    @model_validator(mode="before")
    @classmethod
    def _normalize_payload(cls, data: object) -> object:
        """兼容 Casdoor 不同版本的 camelCase / snake_case 字段名"""
        if not isinstance(data, dict):
            return data
        raw = dict(data)
        for camel, snake in (
            ("productName", "product_name"),
            ("displayName", "display_name"),
        ):
            if camel in raw:
                raw.setdefault(snake, raw[camel])
        return raw

    @field_validator("products", mode="before")
    @classmethod
    def _products_to_list(cls, value: object) -> list[str]:
        """归一化 products：去空、去掉可能的 "owner/" 前缀（只保留商品 name）"""
        if not value:
            return []
        if isinstance(value, str):
            candidates = [value]
        elif isinstance(value, (list, tuple)):
            candidates = [str(v) for v in value]
        else:
            return []
        names: list[str] = []
        for item in candidates:
            name = item.strip()
            if not name:
                continue
            # Casdoor 元素可能是 "owner/productName"，入账只按商品名约定解析
            name = name.split("/", 1)[1] if "/" in name else name
            names.append(name)
        return names

    @field_validator("price", mode="before")
    @classmethod
    def _price_to_float(cls, value: object) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @field_validator("state", mode="before")
    @classmethod
    def _state_to_str(cls, value: object) -> str:
        return str(value or "").strip()

    @property
    def product_names(self) -> list[str]:
        """支付单关联的全部商品名（去重，保序）。

        优先用新版多商品 ``products``；为空时回退旧版单商品 ``productName``。
        """
        names = list(self.products)
        if self.product_name and self.product_name not in names:
            names.append(self.product_name)
        seen: set[str] = set()
        result: list[str] = []
        for name in names:
            if name not in seen:
                seen.add(name)
                result.append(name)
        return result

    @property
    def payment_name(self) -> str:
        """支付单唯一标识（owner/name）"""
        return f"{self.owner}/{self.name}" if self.owner and self.name else self.name

    @property
    def is_paid(self) -> bool:
        """是否已支付（大小写容错）。

        Casdoor 支付渠道（支付宝 / 微信等）在付款成功后支付单即为 Paid；
        RPA 只对 Paid 单核销，Created（未付款）等状态一律不处理。
        """
        return self.state.strip().lower() == "paid"


class CasdoorProductPayload(BaseModel):
    """Casdoor 商品"""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(default="", description="商品名（唯一标识）")
    display_name: str = Field(default="", description="展示名")
    price: float = Field(default=0.0, description="价格")

    @model_validator(mode="before")
    @classmethod
    def _normalize_payload(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        raw = dict(data)
        if "displayName" in raw:
            raw.setdefault("display_name", raw["displayName"])
        return raw

    @field_validator("price", mode="before")
    @classmethod
    def _price_to_float(cls, value: object) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0


class CasdoorPaymentClient:
    """Casdoor 支付查询（无状态，静态方法 + 显式配置）"""

    @staticmethod
    def _basic_auth_header() -> str:
        token = base64.b64encode(
            f"{settings.casdoor_client_id}:{settings.casdoor_client_secret}".encode()
        ).decode()
        return f"Basic {token}"

    @staticmethod
    def _endpoint_or_raise() -> str:
        """校验并返回 Casdoor 站点地址（未配置时抛 RuntimeError）"""
        endpoint = settings.casdoor_endpoint.rstrip("/")
        if not endpoint or not settings.casdoor_client_id:
            raise RuntimeError(
                "Casdoor 支付对账未配置（casdoor_endpoint / client_id 为空）"
            )
        return endpoint

    @staticmethod
    def _parse_payment_list(payload: object) -> list[CasdoorPaymentPayload]:
        """把 Casdoor 响应解析为支付单列表（兼容 {status,data} 与裸数组）"""
        from pydantic import TypeAdapter

        if isinstance(payload, dict):
            data = payload.get("data") or []
        elif isinstance(payload, list):
            data = payload
        else:
            data = []
        adapter: TypeAdapter[list[CasdoorPaymentPayload]] = TypeAdapter(
            list[CasdoorPaymentPayload]
        )
        try:
            return adapter.validate_python(data)
        except Exception as exc:  # noqa: BLE001 - 脏数据不能拖垮支付页
            logger.warning(f"[CasdoorPayment] 支付单响应解析失败，忽略异常记录: {exc}")
            return []

    @staticmethod
    async def list_user_payments(casdoor_user: str) -> list[CasdoorPaymentPayload]:
        """查询指定用户的 Casdoor 支付单（全量拉取后按 user 本地过滤，v1 简化）

        用户调用会员相关接口时由服务端主动查询核销：支付渠道付款成功后支付单
        即为 Paid，RPA 据此幂等发放权益，不依赖 Casdoor 跳转回调。

        Returns:
            属于指定用户的支付单列表；未配置 / 请求失败抛 RuntimeError。

        Raises:
            RuntimeError: 未配置 Casdoor 或请求失败。
        """
        endpoint = CasdoorPaymentClient._endpoint_or_raise()
        url = f"{endpoint}/api/get-payments"
        try:
            async with httpx.AsyncClient(
                timeout=settings.casdoor_verify_timeout
            ) as client:
                resp = await client.get(
                    url,
                    headers={
                        "Authorization": CasdoorPaymentClient._basic_auth_header()
                    },
                )
                resp.raise_for_status()
                payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[CasdoorPayment] 查询支付单失败: {exc}")
            raise RuntimeError(f"Casdoor 查询支付单失败: {exc}") from exc

        target = (casdoor_user or "").strip().lower()
        # 与 notify_and_grant 的归属校验保持同一口径（strip + 大小写容错），
        # 否则 Casdoor 返回的用户名大小写/空格差异会让核销漏单
        return [
            p
            for p in CasdoorPaymentClient._parse_payment_list(payload)
            if p.user.strip().lower() == target
        ]

    @staticmethod
    async def get_payment(owner: str, name: str) -> CasdoorPaymentPayload | None:
        """查询单笔支付（GET /api/get-payment），入账前服务端核验用

        路径参数（owner/name）来自用户输入，必须先在调用方做格式校验，
        这里再做一次 quote 防止拼出非预期路径。

        Returns:
            Casdoor payment 对象；未找到返回 None。

        Raises:
            RuntimeError: 未配置 / 请求失败。
        """
        endpoint = CasdoorPaymentClient._endpoint_or_raise()
        url = f"{endpoint}/api/get-payment"
        try:
            async with httpx.AsyncClient(
                timeout=settings.casdoor_verify_timeout
            ) as client:
                resp = await client.get(
                    url,
                    params={"id": name, "owner": owner},
                    headers={
                        "Authorization": CasdoorPaymentClient._basic_auth_header()
                    },
                )
                resp.raise_for_status()
                payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[CasdoorPayment] 查询单笔支付失败: {owner}/{name}, {exc}")
            raise RuntimeError(f"Casdoor 查询单笔支付失败: {exc}") from exc

        data = payload.get("data") if isinstance(payload, dict) else payload
        if isinstance(data, dict) and data:
            return CasdoorPaymentPayload.model_validate(data)
        return None

    @staticmethod
    async def notify_payment(owner: str, name: str) -> None:
        """完成交易（POST /api/notify-payment/{owner}/{name}）

        配置了 Success URL 的商品，Casdoor 不自动完成交易，必须由跳转目标调用本接口。
        调用方必须先校验支付单归属（避免替他人完成交易）。

        Raises:
            RuntimeError: 未配置 / 请求失败。
        """
        endpoint = CasdoorPaymentClient._endpoint_or_raise()
        safe_owner = quote(owner, safe="")
        safe_name = quote(name, safe="")
        url = f"{endpoint}/api/notify-payment/{safe_owner}/{safe_name}"
        try:
            async with httpx.AsyncClient(
                timeout=settings.casdoor_verify_timeout
            ) as client:
                resp = await client.post(
                    url,
                    headers={
                        "Authorization": CasdoorPaymentClient._basic_auth_header()
                    },
                )
                resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[CasdoorPayment] notify-payment 失败: {owner}/{name}, {exc}")
            raise RuntimeError(f"Casdoor notify-payment 失败: {exc}") from exc

    @staticmethod
    async def list_products() -> list[CasdoorProductPayload]:
        """拉取 Casdoor 商品列表（GET /api/get-products，admin）

        Raises:
            RuntimeError: 未配置 / 请求失败。
        """
        endpoint = CasdoorPaymentClient._endpoint_or_raise()
        url = f"{endpoint}/api/get-products"
        try:
            async with httpx.AsyncClient(
                timeout=settings.casdoor_verify_timeout
            ) as client:
                resp = await client.get(
                    url,
                    headers={
                        "Authorization": CasdoorPaymentClient._basic_auth_header()
                    },
                )
                resp.raise_for_status()
                payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[CasdoorPayment] 查询商品列表失败: {exc}")
            raise RuntimeError(f"Casdoor 查询商品列表失败: {exc}") from exc

        from pydantic import TypeAdapter

        if isinstance(payload, dict):
            data = payload.get("data") or []
        elif isinstance(payload, list):
            data = payload
        else:
            data = []
        adapter: TypeAdapter[list[CasdoorProductPayload]] = TypeAdapter(
            list[CasdoorProductPayload]
        )
        try:
            return adapter.validate_python(data)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[CasdoorPayment] 商品列表解析失败: {exc}")
            return []


__all__ = [
    "CasdoorPaymentClient",
    "CasdoorPaymentPayload",
    "CasdoorProductPayload",
]
