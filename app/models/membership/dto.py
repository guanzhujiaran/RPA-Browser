"""会员权益模块对外 DTO（纯 pydantic，不落库、不作为 ORM 模型）

用途：
    - 替代服务层 / 客户端之间的裸 dict 契约（项目禁止 Dict[str, Any] 类灵活类型）；
    - 对外整数仍按约定在 **路由层** 转 str，此处保留真实类型便于内部计算。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class StoreProductItem(BaseModel):
    """可售支付商品（由 Casdoor 商品 + 本项目命名约定解析得出）"""

    product_name: str = Field(default="", description="Casdoor 商品名（购买页 URL 用）")
    display_name: str = Field(default="", description="展示名")
    price: float = Field(default=0.0, description="价格（Casdoor 原值，快照）")
    grant_type: str = Field(default="", description="权益类型: duration / month_card")
    duration_seconds: int = Field(default=0, description="入账时长（秒）")
    card_days: int = Field(default=0, description="月卡天数")
    buy_url: str = Field(default="", description="收银台购买 URL（空=未启用 Casdoor）")


class PaymentGrantResult(BaseModel):
    """单笔支付入账结果"""

    product: str = Field(default="", description="商品名")
    summary: str = Field(default="", description="入账摘要")


__all__ = ["StoreProductItem", "PaymentGrantResult"]
