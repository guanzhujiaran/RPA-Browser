"""数据库表「固定行数上限」裁剪工具。

时间滑动窗口（例如操作日志保留 7 天）只约束「多久之前」，约束不了「一天写进来多少」：
一次重负载运行就能在窗口内写进百万级行，磁盘照样会被打满。这里提供与业务无关的
表级容量上限：超过上限后按时间列**从旧到新**删除，保留最新的 ``max_rows`` 行。

实现要点：
- 用 Core 的 ``__table__`` 操作，避免 ORM 会话加载整行（日志大 JSON 列很贵）；
- 分批删除 + 逐批提交，避免一次删除百万行造成长事务锁表；
- 调用方负责异常收敛与配置开关（0 / 负数 = 不限制），本模块只在真正删除时打日志。
"""

from loguru import logger
from sqlalchemy import Column, and_, delete as sa_delete, func, or_, select
from sqlmodel import SQLModel

from app.utils.depends.session_manager import DatabaseSessionManager


async def trim_table_to_row_cap(
    *,
    model: type[SQLModel],
    order_column: str,
    max_rows: int,
    batch_size: int,
    label: str,
) -> int:
    """把表裁剪到最多 ``max_rows`` 行，返回实际删除行数。

    保留口径：按 ``order_column``（同值时按主键 id）倒序的最新 ``max_rows`` 行；
    其余更旧的行分批删除。

    Args:
        model: 表模型；内部取 ``__table__`` 走 Core 语句。
        order_column: 判定新旧的时间列名（如 ``started_at`` / ``last_processed_at``）。
        max_rows: 行数上限；``<= 0`` 表示不限制，直接返回 0。
        batch_size: 单批删除行数；``<= 0`` 时按 1000 处理。
        label: 日志前缀，用于区分是哪张表。
    """
    if max_rows <= 0:
        return 0

    table = model.__table__
    order_col: Column = table.c[order_column]
    id_col: Column = table.c.id
    batch = max(1, batch_size)
    deleted = 0

    async with DatabaseSessionManager.async_session() as session:
        total = int(await session.scalar(select(func.count()).select_from(table)) or 0)
        if total <= max_rows:
            return 0

        # 第 max_rows 新的那一行是「保留区」的最小边界：只删比它更旧的
        # （同一时间下 id 更小的算更旧），边界这一行本身要保留。
        boundary = (
            await session.execute(
                select(order_col, id_col)
                .order_by(order_col.desc(), id_col.desc())
                .offset(max_rows - 1)
                .limit(1)
            )
        ).first()
        if boundary is None:
            return 0
        boundary_time, boundary_id = boundary
        older_than = or_(
            order_col < boundary_time,
            and_(order_col == boundary_time, id_col < boundary_id),
        )

        while True:
            ids = (
                (
                    await session.execute(
                        select(id_col)
                        .where(older_than)
                        .order_by(id_col)
                        .limit(batch)
                    )
                )
                .scalars()
                .all()
            )
            if not ids:
                break
            result = await session.execute(sa_delete(table).where(id_col.in_(ids)))
            await session.commit()
            deleted += int(result.rowcount or 0)
            if len(ids) < batch:
                break

        if deleted:
            logger.info(
                f"[{label}] 表行数 {total} 超过上限 {max_rows}，"
                f"已按 {order_column} 从旧到新删除 {deleted} 行"
            )
    return deleted


__all__ = ["trim_table_to_row_cap"]
