import sys

from enum import StrEnum, IntEnum

class ConfigRunningModeEnum(StrEnum):
    DEV = "dev"
    PROD = "prod"
