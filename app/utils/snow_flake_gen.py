"""浏览器指纹对外主键 `browser_id` 的雪花 ID 生成器。

对外发布 ID 统一收敛到 bili_common 的通用生成器（见规则
`.codebuddy/rules/snowflake-id.mdc`），取代原先第三方 `snowflake-id`
包的毫秒级 63 bits（18~19 位十进制）实现。

位布局（分钟级短 ID，分钟步进）：::

    | 时间戳(分钟, 相对 epoch) | 4 bits worker_id | N bits 序列号 |

总位数恒 39 bits，初始 7~8 位十进制。各实体独立配置 `worker_id` / `epoch`，
使 browser_id 与 uid / moment_id / topic_id 落在不同数值空间，避免跨实体
（例如共用 be-message 的 `bizId` 列）碰撞。

用法（`next()` 是 async，异步上下文中直接 await）::

    from app.utils.snow_flake_gen import generate_browser_id

    browser_id = await generate_browser_id()
"""

from bili_common.core.snowflake import MinuteSnowflakeIdGenerator

from app.config import settings

_browser_id_generator = MinuteSnowflakeIdGenerator(
    worker_id=settings.browser_id_worker_id,
    epoch_sec=settings.browser_id_epoch_sec,
    sequence_bits=settings.browser_id_sequence_bits,
)


async def generate_browser_id() -> int:
    """生成一个新的浏览器指纹 ID（短雪花 ID，分钟步进）。"""
    return await _browser_id_generator.next()


__all__ = ["generate_browser_id"]
