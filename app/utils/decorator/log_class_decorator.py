from loguru import logger

from app.config import CONF


def decorator(cls):
    """
    日志类装饰器

    Args:
        cls: 要装饰的类

    Returns:
        装饰后的类
    """
    logger.add(
        f"{CONF.Path.logs}/{cls.__name__}.log",
        rotation="10 MB",
        retention="30 days",
        level="ERROR",
        enqueue=True,
        encoding="utf-8"
    )
    cls.logger = logger
    return cls