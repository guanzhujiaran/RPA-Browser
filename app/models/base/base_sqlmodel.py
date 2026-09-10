from bili_common.models.db import BaseTimestamp
from bili_common.models.pagination import BasePaginationReq, BasePaginationResp


class BaseSQLModel(BaseTimestamp):
    """RPA 表模型公共基类：统一 created_at/updated_at（继承 bili_common.BaseTimestamp）。

    `created_at` 带 index，`updated_at` 由 SQLAlchemy 自动刷新（onupdate）。
    """


# 分页请求/响应模型已统一抽入 bili_common.models.pagination（page/per_page 约定），
# 此处仅做再导出，保持 RPA 各服务原有导入路径不变。
__all__ = ["BaseSQLModel", "BasePaginationReq", "BasePaginationResp"]