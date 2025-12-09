from sqlalchemy.sql.functions import count
from sqlmodel import select, and_
from sqlmodel.ext.asyncio.session import AsyncSession
from app.models.RPA_browser.browser_info_model import (
    UserBrowserInfo,
    UserBrowserInfoCreateParams,
    BaseFingerprintBrowserInitParams,
    UserBrowserInfoReadParams,
    UserBrowserInfoUpdateParams,
    UserBrowserInfoDeleteParams,
    UserBrowserInfoCreateResp,
    UserBrowserInfoReadResp, UserBrowserInfoCountParams, UserBrowserInfoListParams,
)
from app.models.base.base_sqlmodel import BasePaginationResp
from app.services.broswer_fingerprint.fingerprint_gen import gen_from_browserforge_fingerprint
from app.models.response_code import ResponseCode
from app.services.RPA_browser.plugin_db_service import PluginDBService
from typing import Union


class BrowserDBService:
    @staticmethod
    async def create_fingerprint(
            params: UserBrowserInfoCreateParams,
            session: AsyncSession
    ) -> UserBrowserInfoCreateResp:
        """
        创建浏览器指纹信息
        """
        # 生成指纹数据
        fingerprint_data: BaseFingerprintBrowserInitParams = gen_from_browserforge_fingerprint(params=params)

        # 创建浏览器信息对象
        browser_info = UserBrowserInfo(browser_token=params.browser_token, **fingerprint_data.model_dump())

        # 先将browser_info提交到数据库，确保外键引用存在
        session.add(browser_info)
        await session.commit()
        await session.refresh(browser_info)

        # 获取或创建用户级别的默认插件配置
        await PluginDBService.get_or_create_user_default_plugins(browser_info.browser_token, session)

        return UserBrowserInfoCreateResp(**browser_info.model_dump())

    @staticmethod
    async def read_fingerprint(
            params: UserBrowserInfoReadParams,
            session: AsyncSession
    ) -> Union[UserBrowserInfoReadResp, None]:
        """
        读取浏览器指纹信息
        """
        stmt = select(UserBrowserInfo).where(
            and_(
                UserBrowserInfo.browser_token == params.browser_token,
                UserBrowserInfo.id == params.id,
            ))
        result = await session.exec(stmt)
        browser_info = result.one_or_none()
        return browser_info

    @staticmethod
    async def update_fingerprint(
            params: UserBrowserInfoUpdateParams,
            session: AsyncSession
    ) -> tuple[ResponseCode, bool, str]:
        stmt = select(UserBrowserInfo).where(UserBrowserInfo.id == params.id)
        result = await session.exec(stmt)
        browser_info_row = result.one_or_none()
        browser_info = browser_info_row[0] if isinstance(browser_info_row, tuple) else browser_info_row

        if browser_info is None:
            return ResponseCode.BROWSER_ID_NOT_FOUND, False, "未找到该浏览器指纹信息"

        update_data = params.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(browser_info, key, value)

        session.add(browser_info)
        await session.commit()
        await session.refresh(browser_info)
        return ResponseCode.SUCCESS, True, 'success'

    @staticmethod
    async def delete_fingerprint(
            params: UserBrowserInfoDeleteParams,
            session: AsyncSession
    ) -> tuple[ResponseCode, bool, str]:
        stmt = select(UserBrowserInfo).where(and_(
            UserBrowserInfo.id == params.id,
            UserBrowserInfo.browser_token == params.browser_token,
        ))
        result = await session.exec(stmt)
        browser_info = result.one_or_none()

        if browser_info is None:
            return ResponseCode.BROWSER_TOKEN_NOT_FOUND, False, "未找到该浏览器指纹信息"

        await session.delete(browser_info)
        await session.commit()
        return ResponseCode.SUCCESS, True, 'success'

    @staticmethod
    async def count_fingerprint(params: UserBrowserInfoCountParams, session: AsyncSession) -> int:
        stmt = select(count(1)).where(
            and_(
                UserBrowserInfo.browser_token == params.browser_token,
            ))
        result = await session.exec(stmt)
        res = result.one_or_none()
        return  res

    @staticmethod
    async def list_fingerprint(params: UserBrowserInfoListParams, session: AsyncSession) -> BasePaginationResp[
        UserBrowserInfoReadResp]:
        cnt = await BrowserDBService.count_fingerprint(UserBrowserInfoCountParams(
            **params.model_dump()
        ), session)
        if cnt == 0:
            return BasePaginationResp()
        stmt = select(UserBrowserInfo).where(
            UserBrowserInfo.browser_token == params.browser_token,
        ).offset((params.page - 1) * params.per_page).limit(params.per_page)
        result = await session.exec(stmt)
        res = result.all()
        # 从查询结果中提取UserBrowserInfo对象
        return BasePaginationResp(
            total=cnt,
            items=res
        )
