from sqlalchemy.sql.functions import count
from sqlmodel import select, and_
from sqlmodel.ext.asyncio.session import AsyncSession
from app.models.RPA_browser.browser_info_model import (
    UserBrowserInfo,
    UserBrowserDefaultSetting,
    UserBrowserDefaultSettingRequest,
    UserBrowserDefaultSettingResponse,
    BrowserFingerprintCreateParams,
    BrowserFingerprintUpsertParams,
    BaseFingerprintBrowserInitParams,
    BrowserFingerprintQueryParams,
    BrowserFingerprintUpdateParams,
    BrowserFingerprintDeleteParams,
    BrowserFingerprintCreateResp,
    BrowserFingerprintQueryResp,
    BrowserFingerprintListParams,
    BrowserFingerprintRenameParams,
    BrowserFingerprintRenameResp,
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
        user_data_dir_path = (
            Path(__file__).parent.parent.parent.parent
            / "user_data_dir"
            / str(mid)
            / str(params.id)
        )
        if user_data_dir_path.exists():
            await asyncio.to_thread(
                shutil.rmtree, user_data_dir_path, ignore_errors=True
            )

        await session.delete(browser_info)
        await session.commit()
        return ResponseCode.SUCCESS, True, "success"

    @staticmethod
    async def rename_fingerprint(
        params: BrowserFingerprintRenameParams, mid: int, session: AsyncSession
    ) -> BrowserFingerprintRenameResp:
        """
        重命名浏览器指纹

        Args:
            params: 包含指纹ID和新名称的参数
            mid: 用户ID
            session: 数据库会话

        Returns:
            BrowserFingerprintRenameResp: 更新结果
        """
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

        browser_info.custom_name = params.custom_name
        session.add(browser_info)
        await session.commit()
        await session.refresh(browser_info)

        return BrowserFingerprintRenameResp(
            mid=mid,
            id=browser_info.id,
            custom_name=browser_info.custom_name,
            is_success=True,
        )

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

    @staticmethod
    async def verify_browser_info_ownership(
        mid: int, browser_info_id: int, session: AsyncSession
    ) -> bool:
        """
        验证浏览器实例是否属于指定用户

        Args:
            mid: 用户ID
            browser_info_id: 浏览器实例ID
            session: 数据库会话

        Returns:
            bool: 如果浏览器实例属于该用户返回True，否则返回False
        """
        stmt = select(UserBrowserInfo.id).where(
            and_(
                UserBrowserInfo.id == browser_info_id,
                UserBrowserInfo.mid == mid,
            )
        )
        result = await session.exec(stmt)
        return result.one_or_none() is not None

    # ============ UserBrowserDefaultSetting 服务方法 ============

    @staticmethod
    async def get_user_default_settings(
        mid: int, session: AsyncSession
    ) -> UserBrowserDefaultSetting | None:
        """
        获取用户的默认设置

        Args:
            mid: 用户ID
            session: 数据库会话

        Returns:
            UserBrowserDefaultSetting: 用户的默认设置，如果不存在则返回None
        """
        stmt = select(UserBrowserDefaultSetting).where(
            UserBrowserDefaultSetting.mid == mid,
        )
        result = await session.exec(stmt)
        return result.one_or_none()

    @staticmethod
    async def create_or_update_user_default_settings(
        mid: int, request: UserBrowserDefaultSettingRequest, session: AsyncSession
    ) -> UserBrowserDefaultSettingResponse:
        """
        创建或更新用户的默认设置（如果存在则更新，不存在则创建）

        Args:
            mid: 用户ID
            request: 默认设置请求
            session: 数据库会话

        Returns:
            UserBrowserDefaultSettingResponse: 创建或更新后的默认设置响应
        """
        # 检查是否已存在用户的默认设置
        existing_settings = await BrowserDBService.get_user_default_settings(
            mid, session
        )

        if existing_settings:
            # 更新现有设置
            update_data = request.model_dump(exclude_unset=True)
            for key, value in update_data.items():
                setattr(existing_settings, key, value)

            session.add(existing_settings)
            await session.commit()
            await session.refresh(existing_settings)

            # 转换为响应模型
            response_data = existing_settings.model_dump()
            return UserBrowserDefaultSettingResponse(**response_data)
        else:
            # 创建新设置
            new_settings = UserBrowserDefaultSetting(mid=mid, **request.model_dump())
            response_data = new_settings.model_dump()

            session.add(new_settings)
            await session.commit()
            await session.refresh(new_settings)

            # 转换为响应模型
            return UserBrowserDefaultSettingResponse(**response_data)

    @staticmethod
    async def delete_user_default_settings(mid: int, session: AsyncSession) -> bool:
        """
        删除用户的默认设置

        Args:
            mid: 用户ID
            session: 数据库会话

        Returns:
            bool: 删除成功返回True，如果设置不存在返回False
        """
        # 检查是否已存在用户的默认设置
        existing_settings = await BrowserDBService.get_user_default_settings(
            mid, session
        )

        if not existing_settings:
            return False

        await session.delete(existing_settings)
        await session.commit()
        return True

    @staticmethod
    async def apply_default_settings_to_browser(
        browser_id: int, mid: int, session: AsyncSession
    ) -> bool:
        """
        将用户的默认设置应用到指定的浏览器实例

        Args:
            browser_id: 浏览器实例ID
            mid: 用户ID
            session: 数据库会话

        Returns:
            bool: 应用成功返回True，否则返回False
        """
        # 获取用户的默认设置
        default_settings = await BrowserDBService.get_user_default_settings(
            mid, session
        )

        if not default_settings:
            return False

        # 获取浏览器实例
        stmt = select(UserBrowserInfo).where(
            and_(
                UserBrowserInfo.id == browser_id,
                UserBrowserInfo.mid == mid,
            )
        )
        result = await session.exec(stmt)
        browser_info = result.one_or_none()

        if not browser_info:
            return False

        # 应用默认设置到浏览器实例
        # 这里可以根据需要选择性地应用某些设置
        # 例如，只更新代理设置或视口设置等

        # 示例：更新代理设置
        if default_settings.default_proxy_server:
            # 这里需要根据实际的浏览器模型结构来更新
            # 假设浏览器模型有 proxy_server 字段
            if hasattr(browser_info, "proxy_server"):
                setattr(
                    browser_info, "proxy_server", default_settings.default_proxy_server
                )

        # 示例：更新视口设置
        if (
            default_settings.default_viewport_width
            and default_settings.default_viewport_height
        ):
            # 假设浏览器模型有 viewport_width 和 viewport_height 字段
            if hasattr(browser_info, "viewport_width"):
                setattr(
                    browser_info,
                    "viewport_width",
                    default_settings.default_viewport_width,
                )
            if hasattr(browser_info, "viewport_height"):
                setattr(
                    browser_info,
                    "viewport_height",
                    default_settings.default_viewport_height,
                )

        session.add(browser_info)
        await session.commit()
        return True
