"""内置动作枚举与展示元数据

从 ``action_params`` 拆出：内置动作的类型 / 名称 / 描述 / 图标编号枚举，
以及失败处理策略 ``OnErrorEnum``。

参数模型（``params_model``）、结果模型（``result_model``）经模块级 import 的
注册表（``action_param_models`` / ``action_result_models``，key 为动作 value
字符串）查找；完整元数据构建见 ``action_metadata.build_action_metadata``。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bili_common.models import IntEnumAutoDoc, StrEnumAutoDoc

from app.models.execution.action_param_models import (
    BUILTIN_ACTION_PARAMS_MAP,
    CompositeParams,
)
from app.models.execution.action_result_models import (
    BUILTIN_ACTION_RESULT_MAP,
    CompositeResult,
)

# 元数据构建器槽位：``metadata`` 属性需要 ActionMetadata 模型，而该模型的
# 字段注解反向依赖本枚举（ActionMetadata.id: BuiltinActionType），直接 import
# 会成环。采用与控制流 CustomActionResolver 一致的晚期绑定——由
# ``action_metadata`` 模块在 import 时经 ``register_metadata_builder`` 注入。
_metadata_builder: Callable[[BuiltinActionType], Any] | None = None


def register_metadata_builder(builder: Callable[[BuiltinActionType], Any]) -> None:
    """注册 BuiltinActionType.metadata 属性的构建器（action_metadata 模块调用）"""
    global _metadata_builder
    _metadata_builder = builder


class OnErrorEnum(StrEnumAutoDoc):
    """步骤失败时的处理策略"""

    STOP = "stop"  # 停止执行
    CONTINUE = "continue"  # 忽略错误继续
    RETRY = "retry"  # 重试后停止


class BuiltinActionDesc(StrEnumAutoDoc):
    """内置操作描述"""

    CLICK = "点击元素"
    INPUT = "输入文本"
    WAIT = "等待元素出现"
    SCROLL = "滚动到元素"
    NAVIGATE = "导航到 URL"
    SCREENSHOT = "截图"
    LLM = "使用 LLM 生成文本"
    HOVER = "悬停在元素上"
    NEW_PAGE = "打开新页面"
    GET_TEXT = "获取元素文本"
    GET_WINDOW = "获取窗口属性"
    FETCH_EXTERNAL_DATA = "获取外部数据"
    MARK_RESOURCE_RESULT = "记录资源处理结果"
    RANDOM_PICK = "随机取值（从列表中随机选取）"
    PRINT = "打印变量替换后的参数（仅调试用，不执行实际操作）"
    SHUFFLE_LIST = "原地随机打乱一个列表变量"
    SORT_LIST = "按指定字段对列表变量排序"
    BILI_RESERVE = "参与 B 站空间预约抽奖"
    PICK_LINE = "按行切分文本后随机取一条"
    NOTIFY = "发送站内 / 外部通知（支持按天去重）"

    OPEN_PAGE = "按别名打开页面（页面满时等待空位，不会关闭其它页面）"
    CLOSE_PAGE = "关闭指定别名的页面并释放容量"

    IF_ELSE = "根据条件执行 true/false 分支"
    LOOP = "循环执行"
    COMPOSITE = "执行复合操作"
    PARALLEL = "多个分支并行执行（每个分支可绑定独立页面）"


class BuiltinActionName(StrEnumAutoDoc):
    """内置操作名称"""

    CLICK = "点击"
    INPUT = "输入"
    WAIT = "等待"
    SCROLL = "滚动"
    NAVIGATE = "导航"
    SCREENSHOT = "截图"
    LOOP = "循环"
    COMPOSITE = "复合操作"
    LLM = "LLM"
    IF_ELSE = "条件判断"
    HOVER = "悬停"
    NEW_PAGE = "新页面"
    GET_TEXT = "获取文本"
    GET_WINDOW = "获取窗口"
    FETCH_EXTERNAL_DATA = "获取外部数据"
    MARK_RESOURCE_RESULT = "记录资源结果"
    RANDOM_PICK = "随机取值"
    PRINT = "打印参数"
    SHUFFLE_LIST = "打乱列表"
    SORT_LIST = "列表排序"
    BILI_RESERVE = "预约抽奖"
    PICK_LINE = "按行取值"
    NOTIFY = "发送通知"
    OPEN_PAGE = "打开页面"
    CLOSE_PAGE = "关闭页面"
    PARALLEL = "并行分支"


class BuiltinActionIconId(IntEnumAutoDoc):
    """内置操作的默认展示图标编号（系列统一见 BUILTIN_ACTION_ICON_SERIES）

    每个内置操作一个独立编号，前端按
    `public/action-icons/{分类}/s_{系列}_{系列名}/i_{编号}_{名称}.{ext}` 映射为实际图标
    （静态资源，编号→文件的映射由前端清单 `npm run icons:manifest` 生成）；
    图库中缺少对应资源时前端回落到内置图标，因此此处只声明编号，不校验资源是否存在。

    ⚠️ 编号**刻意不连续**：`s_33` 内靠前的 `i_1~i_16` 只是阿尔托莉雅 / 阿尔托莉雅〔Alter〕/
    Lily / 尼禄 四个角色的 1/2/3 再临，全用它们会「全是一张脸」。
    这里改为**一个动作一位不同角色、各取该角色的最终形态**（`i_*_<角色>_3.png`，即第三再临）。
    换人只需改下面的数字；⚠️ 素材是批量 dump，**同一显示名可能对应多位不同角色**
    （如 `i_38` / `i_45` 都叫「阿尔托莉雅·潘德拉贡」，实为绿披风王女与白甲骑士），
    换编号前请先打开对应图片确认，不要只看文件名。
    """

    # 最终形态 = `s_33` 内该角色的 `i_*_<角色>_3.png`
    CLICK = 28  # 冲田总司（粉发 + 武士刀）
    INPUT = 65  # 阿尔托莉雅·卡斯特（白甲金冠 + 蓝缎带）
    WAIT = 87  # 摩根（白发黑冠）
    SCROLL = 68  # 雨之魔女梣（白发白裙 + 花饰）
    NAVIGATE = 3  # 阿尔托莉雅·潘德拉贡（白甲 + 王冠）
    SCREENSHOT = 112  # 谜之女主角XX（蓝帽科幻）
    LLM = 101  # 梅塔特隆·贞德（白金翼装甲）
    HOVER = 7  # 阿尔托莉雅·潘德拉贡〔Alter〕（黑裙）
    NEW_PAGE = 11  # 阿尔托莉雅·潘德拉贡〔Lily〕（白裙黑结）
    GET_TEXT = 75  # 格蕾（灰斗篷 + 羽领）
    GET_WINDOW = 14  # 尼禄·克劳狄乌斯（红白礼服 + 狮首）
    FETCH_EXTERNAL_DATA = 121  # 所多玛之兽／德拉科（黑甲红角）
    MARK_RESOURCE_RESULT = 71  # 谜之女主角X
    RANDOM_PICK = 118  # 谜之女主角XX〔Alter〕
    PRINT = 41  # 贞德（银冠 + 蓝绿礼服）
    SHUFFLE_LIST = 32  # 拉克什米·芭伊
    SORT_LIST = 35  # 冲田总司〔Alter〕
    BILI_RESERVE = 50  # 贞德·Alter·Santa·Lily
    PICK_LINE = 53  # 阿尔托莉雅·潘德拉贡〔圣诞Alter〕
    NOTIFY = 90  # 未占用编号，缺资源时前端回落内置图标
    LOOP = 23  # 莫德雷德（红装马尾）
    COMPOSITE = 115  # 谜之偶像X〔Alter〕（霓虹绿 + 眼镜）
    IF_ELSE = 84  # 贞德〔Alter〕（白发黑红）


# 内置操作默认图标的系列编号：**复用现有图库系列** `Saber脸/s_33_FGO状态图`
# （不保留专属号，避免占用/阻塞图库的全局连续编号）；
# 编号由 BuiltinActionIconId 分配（见其 docstring：一动作一角色、取各角色最终形态）。
# ⚠️ 内置动作引用的编号（见 BuiltinActionIconId，当前 22 个）自此被常驻引用，不可再重排。
BUILTIN_ACTION_ICON_SERIES = 33


class BuiltinActionType(StrEnumAutoDoc):
    """内置操作类型"""

    CLICK = "click"
    INPUT = "input"
    WAIT = "wait"
    SCROLL = "scroll"
    NAVIGATE = "navigate"
    SCREENSHOT = "screenshot"
    LLM = "llm"
    HOVER = "hover"
    NEW_PAGE = "new_page"
    GET_TEXT = "get_text"
    GET_WINDOW = "get_window"
    FETCH_EXTERNAL_DATA = "fetch_external_data"
    MARK_RESOURCE_RESULT = "mark_resource_result"
    RANDOM_PICK = "random_pick"
    PRINT = "print"
    SHUFFLE_LIST = "shuffle_list"
    SORT_LIST = "sort_list"
    BILI_RESERVE = "bili_reserve"
    PICK_LINE = "pick_line"
    NOTIFY = "notify"

    OPEN_PAGE = "open_page"
    CLOSE_PAGE = "close_page"

    LOOP = "loop"
    COMPOSITE = "composite"  # 复合操作 需要拆分开开单独执行
    IF_ELSE = "if_else"
    PARALLEL = "parallel"  # 多页面并行分支

    @property
    def nameDisplay(self) -> str:
        return BuiltinActionName[self.name]

    @property
    def descDisplay(self) -> str:
        return BuiltinActionDesc[self.name]

    @property
    def icon(self) -> tuple[int, int]:
        """该内置操作的默认展示图标 (icon_series, icon_id)

        未登记编号时返回 (BUILTIN_ACTION_ICON_SERIES, 0)，由前端回落到内置图标。
        """
        icon_id = BuiltinActionIconId.__members__.get(self.name)
        return BUILTIN_ACTION_ICON_SERIES, int(icon_id) if icon_id is not None else 0

    @property
    def params_model(self):
        """该内置操作对应的参数模型（未登记时回落 CompositeParams）"""
        return BUILTIN_ACTION_PARAMS_MAP.get(self.value, CompositeParams)

    @property
    def result_model(self):
        """返回该操作类型对应的结果模型（未登记时回落 CompositeResult）"""
        return BUILTIN_ACTION_RESULT_MAP.get(self.value, CompositeResult)

    @property
    def metadata(self) -> Any:
        """返回该操作类型的 ActionMetadata

        实现经 ``register_metadata_builder`` 注入的构建器（见
        ``action_metadata.build_action_metadata``）；未注入时说明
        ``action_metadata`` 模块尚未被 import。
        """
        if _metadata_builder is None:
            raise RuntimeError(
                "metadata 构建器未注册：请先 import app.models.execution.action_metadata"
            )
        return _metadata_builder(self)

