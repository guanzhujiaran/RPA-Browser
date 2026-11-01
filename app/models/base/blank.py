"""请求模型公共基类：把前端传来的空字符串按「未提供」处理。

前端表单「没填」通常传 `""`（或全是空格）而不是省略字段/传 `null`，直接落库会带来一串问题：

- ENUM 列写入 `''` → MySQL 1265 Data truncated（500）；
- 空串会覆盖掉生成好的默认值（如指纹字段）；
- `if value` 与 `value is not None` 两种判空口径不一致，容易出现「看起来没配置却生效/生效却不落库」。

因此凡是接收前端表单的请求模型，统一继承本基类即可：所有空白字符串在进入字段校验前先归一成 `None`。
"""

from __future__ import annotations

from typing import Any

from pydantic import model_validator
from sqlmodel import SQLModel


class BlankStringToNoneModel(SQLModel):
    """把入参里的空白字符串统一归一成 `None`。"""

    @model_validator(mode="before")
    @classmethod
    def blank_strings_to_none(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {
                key: None if isinstance(value, str) and not value.strip() else value
                for key, value in data.items()
            }
        return data


__all__ = ["BlankStringToNoneModel"]
