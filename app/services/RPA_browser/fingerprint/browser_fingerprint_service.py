from sqlalchemy.sql.functions import count
from sqlmodel import select, and_
from sqlmodel.ext.asyncio.session import AsyncSession
from app.models.database.browser.info import (
    UserBrowserInfo,
    UserBrowserDefaultSetting,
    UserBrowserDefaultSettingRequest,
    UserBrowserDefaultSettingResponse,
)
from app.models.core.browser.fingerprint import BaseFingerprintBrowserInitParams
from app.models.runtime.api import (
    BrowserFingerprintCreateParams,
    BrowserFingerprintUpsertParams,
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
from app.utils.snow_flake_gen import generate_browser_id
from bili_common.models.response_code import ResponseCode
from app.config import CONF
from app.models.common.exceptions.base_exception import (
    BrowserFingerprintNotFoundException,
    NameAlreadyExistsException,
)
from typing import Union
from pathlib import Path
import asyncio
import shutil

from loguru import logger


def _rmdir_if_empty(path: Path) -> None:
    """目录为空时删除；非空（同一 mid 下还有其他指纹）或删除失败则静默忽略。"""
    try:
        path.rmdir()
    except OSError:
        pass


class BrowserFingerprintService:
    @staticmethod
    async def upsert_fingerprint(
        params: BrowserFingerprintUpsertParams, mid: int, session: AsyncSession
    ) -> BrowserFingerprintCreateResp:
        """
        创建或更新浏览器指纹信息 (upsert)

        如果提供了 browser_id 则更新现有记录，否则创建新记录
        """
        if params.browser_id is not None:
            # 更新现有记录
            stmt = select(UserBrowserInfo).where(
                and_(
                    UserBrowserInfo.browser_id == int(params.browser_id),
                    UserBrowserInfo.mid == mid,
                )
            )
            result = await session.exec(stmt)
            browser_info = result.one_or_none()

            if browser_info is None:
                raise BrowserFingerprintNotFoundException()

            # 只更新提供的字段（exclude_none：空值=未提供，不覆盖原有值）
            update_data = params.model_dump(
                exclude_unset=True,
                exclude_none=True,
                exclude={"browser_id", "browser_id_str"},
            )
        else:
            # 创建新记录
            # 将 UpsertParams 转换为 CreateParams
            create_params = BrowserFingerprintCreateParams(
                fingerprint_int=params.fingerprint_int
            )
            user_default_settings = await BrowserFingerprintService.get_user_default_settings(
                mid=mid,
                session=session,
            )
            # 生成指纹数据
            fingerprint_data: BaseFingerprintBrowserInitParams = (
                await gen_from_browserforge_fingerprint(
                    params=create_params, user_default_settings=user_default_settings
                )
            )

            # 创建浏览器信息对象（browser_id 由应用层雪花生成器显式生成，非自增）
            browser_info = UserBrowserInfo(
                mid=mid,
                browser_id=await generate_browser_id(),
                **fingerprint_data.model_dump(),
            )

            # 如果有额外的更新参数，应用它们
            # exclude_none：空字符串已在入参层归一为 None，此处再跳过 None，
            # 保证「未填写」的字段不会覆盖指纹生成器/默认设置填好的值
            update_data = params.model_dump(
                exclude_unset=True,
                exclude_none=True,
                exclude={"browser_id", "browser_id_str", "fingerprint_int"},
            )

        # 检查 custom_name 是否重复
        custom_name = update_data.get("custom_name")
        if params.browser_id is None and (custom_name is None or not custom_name.strip()):
            # 创建场景：名称为空时生成默认名称（同一用户下唯一）
            custom_name = f"浏览器_{browser_info.browser_id}"
            update_data["custom_name"] = custom_name
        elif (
            params.browser_id is not None
            and custom_name is not None
            and not custom_name.strip()
        ):
            # 更新场景：传入空名称时不覆盖原有名称
            update_data.pop("custom_name", None)
            custom_name = None

        if custom_name is not None:
            duplicate_stmt = select(UserBrowserInfo).where(
                and_(
                    UserBrowserInfo.mid == mid,
                    UserBrowserInfo.custom_name == custom_name,
                )
            )
            # 如果是更新操作，排除当前浏览器
            if params.browser_id is not None:
                duplicate_stmt = duplicate_stmt.where(
                    UserBrowserInfo.browser_id != int(params.browser_id)
                )
            duplicate_result = await session.exec(duplicate_stmt)
            duplicate_browser = duplicate_result.one_or_none()

            if duplicate_browser is not None:
                raise NameAlreadyExistsException(name=custom_name, name_type="浏览器")

        # 统一处理属性更新
        for key, value in update_data.items():
            setattr(browser_info, key, value)

        # 先将browser_info提交到数据库，确保外键引用存在
        session.add(browser_info)
        await session.commit()
        await session.refresh(browser_info)

        return BrowserFingerprintCreateResp(mid=mid, browser_id=browser_info.browser_id)

    @staticmethod
    async def create_fingerprint(
        params: BrowserFingerprintCreateParams, mid: int, session: AsyncSession
    ) -> BrowserFingerprintCreateResp:
        """
        创建浏览器指纹信息
        """
        user_default_settings = await BrowserFingerprintService.get_user_default_settings(
            mid=mid,
            session=session,
        )
        # 生成指纹数据
        fingerprint_data: BaseFingerprintBrowserInitParams = (
            await gen_from_browserforge_fingerprint(
                params=params, user_default_settings=user_default_settings
            )
        )

        # 创建浏览器信息对象（browser_id 由应用层雪花生成器显式生成，非自增）
        browser_info = UserBrowserInfo(
            mid=mid,
            browser_id=await generate_browser_id(),
            **fingerprint_data.model_dump(),
        )

        # 先将browser_info提交到数据库，确保外键引用存在
        session.add(browser_info)
        await session.commit()
        await session.refresh(browser_info)

        # 返回响应 - 需要将mid和id转换为字符串
        browser_info_dict = browser_info.model_dump()
        browser_info_dict["mid"] = str(browser_info_dict["mid"])
        browser_info_dict["id"] = str(browser_info_dict["id"])
        return BrowserFingerprintCreateResp(**browser_info_dict)

    @staticmethod
    async def read_fingerprint(
        browser_id: int, mid: int, session: AsyncSession
    ) -> Union[BrowserFingerprintQueryResp, None]:
        """
        读取浏览器指纹信息
        """
        stmt = select(UserBrowserInfo).where(
            and_(
                UserBrowserInfo.mid == mid,
                UserBrowserInfo.browser_id == browser_id,
            )
        )
        result = await session.exec(stmt)
        browser_info = result.one_or_none()

        if browser_info is None:
            return None

        browser_info_dict = browser_info.model_dump(by_alias=False)
        return BrowserFingerprintQueryResp(**browser_info_dict)

    @staticmethod
    async def update_fingerprint(
        params: BrowserFingerprintUpdateParams, mid: int, session: AsyncSession
    ) -> tuple[ResponseCode, bool, str]:
        stmt = select(UserBrowserInfo).where(
            and_(
                UserBrowserInfo.browser_id == params.browser_id,
                UserBrowserInfo.mid == mid,
            )
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

        update_data = params.model_dump(
            exclude_unset=True, exclude_none=True, exclude={"id"}
        )
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
                UserBrowserInfo.browser_id == params.browser_id,
                UserBrowserInfo.mid == mid,
            )
        )
        result = await session.exec(stmt)
        browser_info = result.one_or_none()

        if browser_info is None:
            raise BrowserFingerprintNotFoundException()

        # 删除前先关闭该指纹对应的浏览器实例（关闭会话 + 从会话池释放），
        # 否则 profile 仍被占用：既可能删不干净，也可能被运行中的浏览器重建。
        # 注意：live_service 的导入链反向依赖本模块（session_pool_model 引用本服务），
        # 故必须函数内延迟导入，避免循环导入。
        try:
            from app.services.RPA_browser.session.live_service import live_service

            released = await live_service.release_browser_session(
                mid, int(params.browser_id)
            )
            if not released:
                logger.warning(
                    f"关闭浏览器实例失败，仍继续删除指纹: mid={mid}, "
                    f"browser_id={params.browser_id}"
                )
        except Exception as e:
            logger.warning(
                f"关闭浏览器实例异常，仍继续删除指纹: mid={mid}, "
                f"browser_id={params.browser_id}, error={e}"
            )

        # 异步删除对应的 user_data_dir（含 browser_id 目录本身）
        mid_dir_path = Path(CONF.Path.user_data_dir) / str(mid)
        user_data_dir_path = mid_dir_path / str(params.browser_id)
        if user_data_dir_path.exists():
            await asyncio.to_thread(
                shutil.rmtree, user_data_dir_path, ignore_errors=True
            )
            if user_data_dir_path.exists():
                # 例如浏览器实例仍在运行占用 profile 时可能删不干净，留日志便于排查
                logger.warning(f"user_data 目录未完全删除: {user_data_dir_path}")

        # browser_id 目录删除后，{mid} 目录若已空则一并删除，避免残留空目录
        if mid_dir_path.exists():
            await asyncio.to_thread(_rmdir_if_empty, mid_dir_path)

        await session.delete(browser_info)
        await session.commit()
        return ResponseCode.SUCCESS, True, "success"

    @staticmethod
    async def rename_fingerprint(
        params: BrowserFingerprintRenameParams, browser_id: int, session: AsyncSession
    ) -> BrowserFingerprintRenameResp:
        """
        重命名浏览器指纹

        Args:
            params: 包含指纹ID和新名称的参数
            browser_id: 浏览器ID
            session: 数据库会话

        Returns:
            BrowserFingerprintRenameResp: 更新结果
        """
        stmt = select(UserBrowserInfo).where(
            UserBrowserInfo.browser_id == browser_id,
        )
        result = await session.exec(stmt)
        browser_info = result.one_or_none()

        if browser_info is None:
            raise BrowserFingerprintNotFoundException()

        # 如果 custom_name 不为空，检查是否已存在同名
        if params.custom_name is not None:
            duplicate_stmt = select(UserBrowserInfo).where(
                and_(
                    UserBrowserInfo.mid == browser_info.mid,
                    UserBrowserInfo.custom_name == params.custom_name,
                    UserBrowserInfo.browser_id != browser_id,  # 排除当前浏览器
                )
            )
            duplicate_result = await session.exec(duplicate_stmt)
            duplicate_browser = duplicate_result.one_or_none()

            if duplicate_browser is not None:
                raise NameAlreadyExistsException(
                    name=params.custom_name, name_type="浏览器"
                )

        browser_info.custom_name = params.custom_name
        session.add(browser_info)
        await session.commit()
        await session.refresh(browser_info)

        return BrowserFingerprintRenameResp(
            mid=browser_info.mid,
            browser_id=browser_info.browser_id,
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
        return result.one_or_none() or 0

    @staticmethod
    async def list_fingerprint(
        params: BrowserFingerprintListParams, mid: int, session: AsyncSession
    ) -> BasePaginationResp[UserBrowserInfo]:
        cnt = await BrowserFingerprintService.count_fingerprint(mid, session)

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
        stmt = select(UserBrowserInfo.browser_id).where(
            and_(
                UserBrowserInfo.browser_id == browser_info_id,
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
        existing_settings = await BrowserFingerprintService.get_user_default_settings(
            mid, session
        )

        if existing_settings:
            # 更新现有设置
            update_data = request.model_dump(exclude_unset=True)
            settings_to_save = existing_settings
        else:
            # 创建新设置（browser_id 为继承来的主键，同样由应用层雪花生成器显式生成）
            new_settings = UserBrowserDefaultSetting(
                mid=mid,
                browser_id=await generate_browser_id(),
                **request.model_dump(),
            )
            settings_to_save = new_settings
            update_data = {}

        # 统一处理属性更新（仅当更新现有设置时）
        if existing_settings:
            for key, value in update_data.items():
                setattr(existing_settings, key, value)

        # 保存设置到数据库
        session.add(settings_to_save)
        await session.commit()
        await session.refresh(settings_to_save)

        # 转换为响应模型
        response_data = settings_to_save.model_dump()
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
        existing_settings = await BrowserFingerprintService.get_user_default_settings(
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
        default_settings = await BrowserFingerprintService.get_user_default_settings(
            mid, session
        )

        if not default_settings:
            return False

        # 获取浏览器实例
        stmt = select(UserBrowserInfo).where(
            and_(
                UserBrowserInfo.browser_id == browser_id,
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
        if default_settings.default_proxy_server and hasattr(
            browser_info, "proxy_server"
        ):
            setattr(browser_info, "proxy_server", default_settings.default_proxy_server)

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
