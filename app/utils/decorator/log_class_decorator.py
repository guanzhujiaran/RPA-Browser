from loguru import logger

from app.config import CONF

# 已注册文件 sink 的类名，避免模块重复导入时对同一路径重复 logger.add
# （loguru 的 sink 是全局的，重复 add 会让同一条记录被写多次）
_REGISTERED: set[str] = set()


def decorator(cls):
    """
    日志类装饰器

    为每个被装饰的类绑定一个独立的文件 sink，并只接收该类自身产生的日志。

    loguru 的 sink 是全局的：若直接 ``logger.add`` 而不加过滤，任何一个类记录的
    ERROR 都会写进所有被装饰类的 .log 文件（内容完全相同）。这里用 ``bind`` 给
    日志打上类名标记，再让 sink 按标记过滤，实现「各类日志各归各类」。

    Args:
        cls: 要装饰的类

    Returns:
        装饰后的类
    """
    class_name = cls.__name__

    if class_name not in _REGISTERED:
        logger.add(
            f"{CONF.Path.logs}/{class_name}.log",
            rotation="10 MB",
            retention="30 days",
            level="ERROR",
            enqueue=True,
            encoding="utf-8",
            filter=lambda record, _name=class_name: record["extra"].get("_log_class")
            == _name,
        )
        _REGISTERED.add(class_name)

    cls.logger = logger.bind(_log_class=class_name)
    return cls