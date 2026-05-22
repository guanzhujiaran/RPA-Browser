from snowflake import SnowflakeGenerator
from app.config import settings
browser_id_gen = SnowflakeGenerator(
    instance=settings.snowflake_id, epoch=1735689600000, seq=2
)
