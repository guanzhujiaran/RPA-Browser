import os
from dataclasses import dataclass
from typing import List
from pydantic import computed_field
from app.models.RPA_browser.browser_exec_info_model import (
    BrowserExecInfoModels,
    BrowserExecInfoModel,
)
import aiofiles
import asyncio

current_dir = os.path.dirname(os.path.realpath(__file__))


async def get_browse_exec_infos() -> List[BrowserExecInfoModel]:
    async with aiofiles.open(
        os.path.join(current_dir, "browser_exec_info.json"), mode="r"
    ) as f:
        res = await f.read()
    return BrowserExecInfoModels.validate_json(res)


class BrowserExecInfoHelper:
    browse_exec_infos: List[BrowserExecInfoModel] = []

    async def refresh(self):
        self.browse_exec_infos = await get_browse_exec_infos()

    async def get_exec_info(self, ua: str | None) -> BrowserExecInfoModel:
        if not self.browse_exec_infos:
            await self.refresh()
        for info in self.browse_exec_infos:
            if info.browser_ua == ua:
                return info
        else:
            return self.browse_exec_infos[0]

    async def get_exec_info_ua_list(self) -> List[str]:
        if not self.browse_exec_infos:
            await self.refresh()
        return self.ua_list

    @computed_field
    @property
    def ua_list(self):
        return [info.browser_ua for info in self.browse_exec_infos]


browser_exec_info_helper = BrowserExecInfoHelper()

__all__ = ["get_browse_exec_infos", "browser_exec_info_helper"]

if __name__ == "__main__":
    print(asyncio.run(get_browse_exec_infos()))
