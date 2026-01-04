from sqlalchemy.sql.functions import count
from sqlmodel import select, and_
from sqlmodel.ext.asyncio.session import AsyncSession
from app.models.RPA_browser.browser_info_model import (
    UserBrowserInfo,
    UserBrowserDefaultSetting,
    BrowserFingerprintCreateParams,
    BrowserFingerprintUpsertParams,
    BaseFingerprintBrowserInitParams,
    BrowserFingerprintQueryParams,
    BrowserFingerprintUpdateParams,
    BrowserFingerprintDeleteParams,
    BrowserFingerprintCreateResp,
    BrowserFingerprintQueryResp,
    BrowserFingerprintListParams,
)
from app.models.base.base_sqlmodel import BasePaginationResp
from app.services.broswer_fingerprint.fingerprint_gen import (
    gen_from_browserforge_fingerprint,
)
from app.models.response_code import ResponseCode
from app.services.RPA_browser.plugin_db_service import PluginDBService
from app.models.exceptions.base_exception import BrowserFingerprintNotFoundException
from typing import Union
from pathlib import Path
import asyncio
import shutil


class BrowserDBService:
    @staticmethod
    async def upsert_fingerprint(
        params: BrowserFingerprintUpsertParams, mid: int, session: AsyncSession
    ) -> BrowserFingerprintCreateResp:
        """
        创建或更新浏览器指纹信息 (upsert)

        如果提供了 id 则更新现有记录，否则创建新记录
        """
        if params.id is not None:
            # 更新现有记录
            stmt = select(UserBrowserInfo).where(
                and_(UserBrowserInfo.id == int(params.id), UserBrowserInfo.mid == mid)
            )
            result = await session.exec(stmt)
            browser_info = result.one_or_none()

            if browser_info is None:
                raise BrowserFingerprintNotFoundException()

            # 只更新提供的字段
            update_data = params.model_dump(exclude_unset=True, exclude={"id"})
            for key, value in update_data.items():
                setattr(browser_info, key, value)

            session.add(browser_info)
            await session.commit()
            await session.refresh(browser_info)
        else:
            # 创建新记录
            # 将 UpsertParams 转换为 CreateParams
            create_params = BrowserFingerprintCreateParams(
                fingerprint_int=params.fingerprint_int
            )
            user_default_settings = await BrowserDBService.get_user_default_settings(
                mid=mid,
                session=session,
            )
            # 生成指纹数据
            fingerprint_data: BaseFingerprintBrowserInitParams = (
                await gen_from_browserforge_fingerprint(
                    params=create_params, user_default_settings=user_default_settings
                )
            )

            # 创建浏览器信息对象
            browser_info = UserBrowserInfo(mid=mid, **fingerprint_data.model_dump())

            # 如果有额外的更新参数，应用它们
            update_data = params.model_dump(
                exclude_unset=True, exclude={"id", "fingerprint_int"}
            )
            for key, value in update_data.items():
                setattr(browser_info, key, value)

            # 先将browser_info提交到数据库，确保外键引用存在
            session.add(browser_info)
            await session.commit()
            await session.refresh(browser_info)
            # 获取或创建用户级别的默认插件配置
            await PluginDBService.get_or_create_user_default_plugins(
                browser_info.mid, session
            )

        return BrowserFingerprintCreateResp(mid=mid, id=browser_info.id)

    @staticmethod
    async def create_fingerprint(
        params: BrowserFingerprintCreateParams, mid: int, session: AsyncSession
    ) -> BrowserFingerprintCreateResp:
        """
        创建浏览器指纹信息
        """
        user_default_settings = await BrowserDBService.get_user_default_settings(
            mid=mid,
            session=session,
        )
        # 生成指纹数据
        fingerprint_data: BaseFingerprintBrowserInitParams = (
            await gen_from_browserforge_fingerprint(
                params=params, user_default_settings=user_default_settings
            )
        )

        # 创建浏览器信息对象
        browser_info = UserBrowserInfo(mid=mid, **fingerprint_data.model_dump())

        # 先将browser_info提交到数据库，确保外键引用存在
        session.add(browser_info)
        await session.commit()
        await session.refresh(browser_info)

        # 获取或创建用户级别的默认插件配置
        await PluginDBService.get_or_create_user_default_plugins(
            browser_info.mid, session
        )

        # 返回响应 - 需要将mid和id转换为字符串
        browser_info_dict = browser_info.model_dump()
        browser_info_dict["mid"] = str(browser_info_dict["mid"])
        browser_info_dict["id"] = str(browser_info_dict["id"])
        return BrowserFingerprintCreateResp(**browser_info_dict)

    @staticmethod
    async def read_fingerprint(
        params: BrowserFingerprintQueryParams, mid: int, session: AsyncSession
    ) -> Union[BrowserFingerprintQueryResp, None]:
        """
        读取浏览器指纹信息
        """
        stmt = select(UserBrowserInfo).where(
            and_(
                UserBrowserInfo.mid == mid,
                UserBrowserInfo.id == params.id,
            )
        )
        result = await session.exec(stmt)
        browser_info = result.one_or_none()

        if browser_info is None:
            return None

        # 将mid和id从整数转换为字符串，以匹配BrowserFingerprintQueryResp的期望类型
        browser_info_dict = browser_info.model_dump(by_alias=False)
        browser_info_dict["mid"] = str(browser_info_dict["mid"])
        browser_info_dict["id"] = str(browser_info_dict["id"])
        browser_info_dict["plugins"] = {}
        return BrowserFingerprintQueryResp(**browser_info_dict)

    @staticmethod
    async def update_fingerprint(
        params: BrowserFingerprintUpdateParams, mid: int, session: AsyncSession
    ) -> tuple[ResponseCode, bool, str]:
        stmt = select(UserBrowserInfo).where(
            and_(UserBrowserInfo.id == params.id, UserBrowserInfo.mid == mid)
        )
        result = await session.exec(stmt)
        browser_info_row = result.one_or_none()
        browser_info = (
            browser_info_row[0]
            if isinstance(browser_info_row, tuple)
            else browser_info_row
        )

        if browser_info is None:
            raise BrowserFingerprintNotFoundException()

        update_data = params.model_dump(exclude_unset=True, exclude={"id"})
        for key, value in update_data.items():
            setattr(browser_info, key, value)

        session.add(browser_info)
        await session.commit()
        await session.refresh(browser_info)
        return ResponseCode.SUCCESS, True, "success"

    @staticmethod
    async def delete_fingerprint(
        params: BrowserFingerprintDeleteParams, mid: int, session: AsyncSession
    ) -> tuple[ResponseCode, bool, str]:
        stmt = select(UserBrowserInfo).where(
            and_(
                UserBrowserInfo.id == params.id,
                UserBrowserInfo.mid == mid,
            )
        )
        result = await session.exec(stmt)
        browser_info = result.one_or_none()

        if browser_info is None:
            raise BrowserFingerprintNotFoundException()

        # 异步删除对应的 user_data_dir
        user_data_dir_path = Path(__file__).parent.parent.parent.parent / "user_data_dir" / str(mid) / str(params.id)
        if user_data_dir_path.exists():
            await asyncio.to_thread(shutil.rmtree, user_data_dir_path, ignore_errors=True)

        await session.delete(browser_info)
        await session.commit()
        return ResponseCode.SUCCESS, True, "success"

    @staticmethod
    async def count_fingerprint(mid: int, session: AsyncSession) -> int:
        stmt = select(count(1)).where(
            and_(
                UserBrowserInfo.mid == mid,
            )
        )
        result = await session.exec(stmt)
        res = result.one_or_none()
        return res

    @staticmethod
    async def list_fingerprint(
        params: BrowserFingerprintListParams, mid: int, session: AsyncSession
    ) -> BasePaginationResp[UserBrowserInfo]:
        cnt = await BrowserDBService.count_fingerprint(mid, session)

        if cnt == 0:
            return BasePaginationResp()
        stmt = (
            select(UserBrowserInfo)
            .where(
                UserBrowserInfo.mid == mid,
            )
            .offset((params.page - 1) * params.per_page)
            .limit(params.per_page)
        )
        result = await session.exec(stmt)
        browser_infos = result.all()
        return BasePaginationResp(
            total=cnt,
            items=browser_infos,
            per_page=params.per_page,
            page=params.page,
        )

    @staticmethod
    async def get_user_default_settings(
        mid: int, session: AsyncSession
    ) -> UserBrowserDefaultSetting:
        stmt = select(UserBrowserDefaultSetting).where(
            UserBrowserDefaultSetting.mid == mid,
        )
        result = await session.exec(stmt)
        res = result.one_or_none()
        return res
