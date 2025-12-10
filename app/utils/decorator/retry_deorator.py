from sqlalchemy.exc import OperationalError
import asyncio

def sql_retry(func):
    """
    Retry decorator for sql operations.
    """
    async def wrapper(*args, **kwargs):
        while 1:
            try:
                return await func(*args, **kwargs)
            except OperationalError as e:
                if session:= kwargs.get('session'):
                    session.rollback()
                await asyncio.sleep(10)
            except Exception as e:
                raise e
    return wrapper