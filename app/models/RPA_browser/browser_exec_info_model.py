import os
from typing import List
from app.config import settings
from pydantic import TypeAdapter
from sqlmodel import SQLModel
from pydantic import computed_field


class BrowserExecInfoModel(SQLModel):
    browser_ua: str
    download_url: str
    full_version: str
    exec_name: str

    @computed_field
    @property
    def exec_path(self) -> str:
        return os.path.join(settings.chromium_executable_dir, self.exec_name)


BrowserExecInfoModels = TypeAdapter(List[BrowserExecInfoModel])
