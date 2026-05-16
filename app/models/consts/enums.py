import sys

# Python 3.10 兼容性：StrEnum 在 3.11+ 中引入
if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    from enum import Enum
    class StrEnum(str, Enum):
        """Python 3.10 兼容的 StrEnum"""
        pass

import sys

# Python 3.10 兼容性：StrEnum 在 3.11+ 中引入
if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    from enum import Enum
    class StrEnum(str, Enum):
        """Python 3.10 兼容的 StrEnum"""
        pass


class ConfigRunningModeEnum(StrEnum):
    DEV = "dev"
    PROD = "prod"
