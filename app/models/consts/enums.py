from bili_common.models import StrEnumAutoDoc
import sys

class ConfigRunningModeEnum(StrEnumAutoDoc):
    DEV = "dev"
    PROD = "prod"
