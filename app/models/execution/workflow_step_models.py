"""工作流步骤模型（steps JSON 的强类型定义）

从 ``action_params`` 拆出。包含：

- ``BaseWorkflowStep`` 与 21 个内置操作步骤子类，经判别联合
  ``WorkflowStep`` 按 ``action_type`` 字段自动匹配；
- ``workflow_step_adapter``：从 dict 反序列化步骤；
- ``create_workflow_step``：工厂函数（未知 action_id 降级为 BaseWorkflowStep）；
- 复合动作（雪花 int ID）识别工具 ``is_composite_action_id`` /
  ``normalize_composite_action_id``。
"""

from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import TypeAdapter, ValidationError, field_validator, model_validator
from sqlmodel import SQLModel

from app.models.execution.action_param_models import (
    AllActionParams,
    BiliReserveParams,
    BUILTIN_ACTION_PARAMS_MAP,
    ClickParams,
    CompositeParams,
    FetchExternalDataParams,
    GetTextParams,
    GetWindowParams,
    HoverParams,
    IfElseParams,
    InputParams,
    LLMParams,
    LoopParams,
    MarkResourceResultParams,
    NavigateParams,
    NewPageParams,
    NotifyParams,
    OpenPageParams,
    ClosePageParams,
    ParallelParams,
    ParallelBranch,
    PickLineParams,
    PrintParams,
    RandomPickParams,
    ScreenshotParams,
    ScrollParams,
    ShuffleListParams,
    SortListParams,
    WaitParams,
)
from app.models.execution.builtin_action_types import BuiltinActionType, OnErrorEnum
from app.models.execution.condition_models import ConditionRule

P = TypeVar("P")


def _fill_action_type_impl(data: Any) -> Any:
    """递归处理 children 中的 action_type 填充。"""
    if isinstance(data, dict):
        if not data.get("action_type") and data.get("action_id"):
            action_id = data["action_id"]
            # 复合动作（雪花 int / 纯数字串）的 action_type 是 "composite"；
            # 直接拿 action_id 填进去（int）会让整条判别联合校验失败
            data["action_type"] = (
                "composite" if is_composite_action_id(action_id) else action_id
            )
        if "children" in data and data["children"]:
            data["children"] = [
                _fill_action_type_impl(item) if isinstance(item, dict) else item
                for item in data["children"]
            ]
    return data


class BaseWorkflowStep(SQLModel, Generic[P]):
    """工作流步骤基类 - 共享字段 + execution_engine 运行时字段"""

    action_id: str
    params: P | None = None
    retry: int = 0
    on_error: OnErrorEnum = OnErrorEnum.STOP
    on_error_branch: list[WorkflowStep] | None = None  # 失败时执行的子步骤（回退/清理）
    input_vars: dict[str, Any] | None = None
    output_vars: list[str] | None = None
    timeout: int | None = None
    children: list[WorkflowStep] | None = None
    # execution_engine 运行时字段
    condition: ConditionRule | None = None
    loop_count: int | None = None
    loop_while: str | None = None
    loop_until: str | None = None
    output_var: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _fill_action_type(cls, data: Any) -> Any:
        """若 action_type 缺失，从 action_id 自动填充，确保 discriminated union 能匹配。"""
        return _fill_action_type_impl(data)

    @field_validator("action_id", mode="before")
    @classmethod
    def _coerce_action_id(cls, v: Any) -> Any:
        """复合动作引用统一归一化为字符串。

        雪花 ``action_id`` 在 Python 侧是 int（CRUD 落库时就是 int，JSON 往返后
        也可能是 int），而步骤模型是 ``action_id: str`` 的判别联合：不在这里
        归一化时，``{"action_id": 1118..., "action_type": "composite"}``
        会让整条 union 校验失败，表现为「引用了自定义动作的工作流跑不起来」。
        内置动作名本身就是字符串，不受影响。
        """
        if isinstance(v, bool) or v is None:
            return v
        if isinstance(v, int):
            return str(v)
        return v


class ClickWorkflowStep(BaseWorkflowStep[ClickParams]):
    """点击步骤"""

    action_type: Literal[BuiltinActionType.CLICK] = BuiltinActionType.CLICK  # type: ignore[assignment]


class InputWorkflowStep(BaseWorkflowStep[InputParams]):
    """输入步骤"""

    action_type: Literal[BuiltinActionType.INPUT] = BuiltinActionType.INPUT  # type: ignore[assignment]


class NavigateWorkflowStep(BaseWorkflowStep[NavigateParams]):
    """导航步骤"""

    action_type: Literal[BuiltinActionType.NAVIGATE] = BuiltinActionType.NAVIGATE  # type: ignore[assignment]


class NewPageWorkflowStep(BaseWorkflowStep[NewPageParams]):
    """新页面步骤"""

    action_type: Literal[BuiltinActionType.NEW_PAGE] = BuiltinActionType.NEW_PAGE  # type: ignore[assignment]


class ScrollWorkflowStep(BaseWorkflowStep[ScrollParams]):
    """滚动步骤"""

    action_type: Literal[BuiltinActionType.SCROLL] = BuiltinActionType.SCROLL  # type: ignore[assignment]


class WaitWorkflowStep(BaseWorkflowStep[WaitParams]):
    """等待步骤"""

    action_type: Literal[BuiltinActionType.WAIT] = BuiltinActionType.WAIT  # type: ignore[assignment]


class HoverWorkflowStep(BaseWorkflowStep[HoverParams]):
    """悬停步骤"""

    action_type: Literal[BuiltinActionType.HOVER] = BuiltinActionType.HOVER  # type: ignore[assignment]


class GetTextWorkflowStep(BaseWorkflowStep[GetTextParams]):
    """获取文本步骤"""

    action_type: Literal[BuiltinActionType.GET_TEXT] = BuiltinActionType.GET_TEXT  # type: ignore[assignment]


class GetWindowWorkflowStep(BaseWorkflowStep[GetWindowParams]):
    """获取窗口属性步骤"""

    action_type: Literal[BuiltinActionType.GET_WINDOW] = BuiltinActionType.GET_WINDOW  # type: ignore[assignment]


class ScreenshotWorkflowStep(BaseWorkflowStep[ScreenshotParams]):
    """截图步骤"""

    action_type: Literal[BuiltinActionType.SCREENSHOT] = BuiltinActionType.SCREENSHOT  # type: ignore[assignment]


class LLMWorkflowStep(BaseWorkflowStep[LLMParams]):
    """LLM 步骤"""

    action_type: Literal[BuiltinActionType.LLM] = BuiltinActionType.LLM  # type: ignore[assignment]


class FetchExternalDataWorkflowStep(BaseWorkflowStep[FetchExternalDataParams]):
    """获取外部数据步骤"""

    action_type: Literal[BuiltinActionType.FETCH_EXTERNAL_DATA] = (
        BuiltinActionType.FETCH_EXTERNAL_DATA
    )  # type: ignore[assignment]


class MarkResourceResultWorkflowStep(BaseWorkflowStep[MarkResourceResultParams]):
    """记录资源处理结果步骤"""

    action_type: Literal[BuiltinActionType.MARK_RESOURCE_RESULT] = (
        BuiltinActionType.MARK_RESOURCE_RESULT
    )  # type: ignore[assignment]


class PrintWorkflowStep(BaseWorkflowStep[PrintParams]):
    """打印参数步骤"""

    action_type: Literal[BuiltinActionType.PRINT] = BuiltinActionType.PRINT  # type: ignore[assignment]


class NotifyWorkflowStep(BaseWorkflowStep[NotifyParams]):
    """发送通知步骤"""

    action_type: Literal[BuiltinActionType.NOTIFY] = BuiltinActionType.NOTIFY  # type: ignore[assignment]


class RandomPickWorkflowStep(BaseWorkflowStep[RandomPickParams]):
    """随机取值步骤"""

    action_type: Literal[BuiltinActionType.RANDOM_PICK] = BuiltinActionType.RANDOM_PICK  # type: ignore[assignment]


class ShuffleListWorkflowStep(BaseWorkflowStep[ShuffleListParams]):
    """打乱列表步骤"""

    action_type: Literal[BuiltinActionType.SHUFFLE_LIST] = BuiltinActionType.SHUFFLE_LIST  # type: ignore[assignment]


class SortListWorkflowStep(BaseWorkflowStep[SortListParams]):
    """列表排序步骤"""

    action_type: Literal[BuiltinActionType.SORT_LIST] = BuiltinActionType.SORT_LIST  # type: ignore[assignment]


class BiliReserveWorkflowStep(BaseWorkflowStep[BiliReserveParams]):
    """预约抽奖步骤"""

    action_type: Literal[BuiltinActionType.BILI_RESERVE] = BuiltinActionType.BILI_RESERVE  # type: ignore[assignment]


class PickLineWorkflowStep(BaseWorkflowStep[PickLineParams]):
    """按行随机取值步骤"""

    action_type: Literal[BuiltinActionType.PICK_LINE] = BuiltinActionType.PICK_LINE  # type: ignore[assignment]


class LoopWorkflowStep(BaseWorkflowStep[LoopParams]):
    """循环步骤"""

    action_type: Literal[BuiltinActionType.LOOP] = BuiltinActionType.LOOP  # type: ignore[assignment]


class IfElseWorkflowStep(BaseWorkflowStep[IfElseParams]):
    """条件分支步骤"""

    action_type: Literal[BuiltinActionType.IF_ELSE] = BuiltinActionType.IF_ELSE  # type: ignore[assignment]


class CompositeWorkflowStep(BaseWorkflowStep[CompositeParams]):
    """复合操作步骤"""

    action_type: Literal[BuiltinActionType.COMPOSITE] = BuiltinActionType.COMPOSITE  # type: ignore[assignment]


class OpenPageWorkflowStep(BaseWorkflowStep[OpenPageParams]):
    """按别名打开页面步骤"""

    action_type: Literal[BuiltinActionType.OPEN_PAGE] = BuiltinActionType.OPEN_PAGE  # type: ignore[assignment]


class ClosePageWorkflowStep(BaseWorkflowStep[ClosePageParams]):
    """关闭指定别名页面步骤"""

    action_type: Literal[BuiltinActionType.CLOSE_PAGE] = BuiltinActionType.CLOSE_PAGE  # type: ignore[assignment]


class ParallelWorkflowStep(BaseWorkflowStep[ParallelParams]):
    """并行分支步骤"""

    action_type: Literal[BuiltinActionType.PARALLEL] = BuiltinActionType.PARALLEL  # type: ignore[assignment]


def _ensure_action_type(v: Any) -> Any:
    """若 dict 缺少 action_type，从 action_id 自动填充，递归处理 children。"""
    if isinstance(v, dict):
        if "action_type" not in v and "action_id" in v:
            # 自定义操作（雪花 int / 纯数字串）的 action_type 为 "composite"
            if is_composite_action_id(v["action_id"]):
                v["action_type"] = "composite"
            else:
                v["action_type"] = v["action_id"]
        if "children" in v and v["children"]:
            v["children"] = [
                _ensure_action_type(c) if isinstance(c, dict) else c
                for c in v["children"]
            ]
        # 并行分支里的 steps 也要按 action_id 推导 action_type
        params = v.get("params")
        if isinstance(params, dict):
            for branch in params.get("branches") or []:
                if isinstance(branch, dict) and branch.get("steps"):
                    branch["steps"] = [
                        _ensure_action_type(c) if isinstance(c, dict) else c
                        for c in branch["steps"]
                    ]
    return v


# ==================== 复合动作 ID 判断（雪花 int 化）====================
# 历史上复合动作以 ``ca_xxx`` 字符串标识，内置动作以英文名字符串标识
# （如 ``click``）。资源 ID 雪花 int 化后，**复合动作 ID 一律为 int**，
# steps JSON 反序列化后可能是 int，也可能是纯数字字符串（如经过前端/JSON
# 往返）。下列辅助统一识别，替代散落在各处的 ``startswith("ca_")`` 判断。


def is_composite_action_id(action_id: Any) -> bool:
    """判断 step 的 action_id 是否引用「用户复合动作」（而非内置动作）。

    - int / bool 除外的 int：复合动作（雪花 ID）；
    - 纯数字字符串：复合动作（JSON 往返后的雪花 ID）；
    - 其余非空字符串：内置动作名（click/input/...）；
    - ``ca_`` 前缀：兼容历史数据的复合动作。
    """
    if isinstance(action_id, bool):
        return False
    if isinstance(action_id, int):
        return True
    if isinstance(action_id, str):
        if action_id.startswith("ca_"):
            return True
        s = action_id.strip()
        if s.isdigit():
            return True
    return False


def normalize_composite_action_id(action_id: Any) -> int:
    """把复合动作 ID 归一化为 int（雪花 ID）。

    调用方应先用 :func:`is_composite_action_id` 确认其为复合动作。
    """
    if isinstance(action_id, bool):
        raise ValueError(f"非法的复合动作 ID: {action_id!r}")
    if isinstance(action_id, int):
        return action_id
    if isinstance(action_id, str):
        s = action_id.strip()
        if s.startswith("ca_"):
            raise ValueError(f"历史 ca_ 字符串 ID 已废弃，无法转为雪花 int: {action_id}")
        return int(s)
    raise ValueError(f"非法的复合动作 ID: {action_id!r}")


# discriminated union: 根据 action_type 字段自动匹配子类
WorkflowStep = (
    ClickWorkflowStep
    | InputWorkflowStep
    | NavigateWorkflowStep
    | NewPageWorkflowStep
    | ScrollWorkflowStep
    | WaitWorkflowStep
    | HoverWorkflowStep
    | GetTextWorkflowStep
    | GetWindowWorkflowStep
    | ScreenshotWorkflowStep
    | LLMWorkflowStep
    | FetchExternalDataWorkflowStep
    | MarkResourceResultWorkflowStep
    | RandomPickWorkflowStep
    | ShuffleListWorkflowStep
    | SortListWorkflowStep
    | PickLineWorkflowStep
    | BiliReserveWorkflowStep
    | PrintWorkflowStep
    | NotifyWorkflowStep
    | LoopWorkflowStep
    | IfElseWorkflowStep
    | CompositeWorkflowStep
    | OpenPageWorkflowStep
    | ClosePageWorkflowStep
    | ParallelWorkflowStep
)

# TypeAdapter 用于从 dict 反序列化
workflow_step_adapter: TypeAdapter[WorkflowStep] = TypeAdapter(WorkflowStep)

# 子类映射表，用于工厂函数
_WORKFLOW_STEP_CLASS_MAP: dict[BuiltinActionType, type[BaseWorkflowStep[Any]]] = {
    BuiltinActionType.CLICK: ClickWorkflowStep,
    BuiltinActionType.INPUT: InputWorkflowStep,
    BuiltinActionType.NAVIGATE: NavigateWorkflowStep,
    BuiltinActionType.NEW_PAGE: NewPageWorkflowStep,
    BuiltinActionType.SCROLL: ScrollWorkflowStep,
    BuiltinActionType.WAIT: WaitWorkflowStep,
    BuiltinActionType.HOVER: HoverWorkflowStep,
    BuiltinActionType.GET_TEXT: GetTextWorkflowStep,
    BuiltinActionType.GET_WINDOW: GetWindowWorkflowStep,
    BuiltinActionType.SCREENSHOT: ScreenshotWorkflowStep,
    BuiltinActionType.LLM: LLMWorkflowStep,
    BuiltinActionType.FETCH_EXTERNAL_DATA: FetchExternalDataWorkflowStep,
    BuiltinActionType.MARK_RESOURCE_RESULT: MarkResourceResultWorkflowStep,
    BuiltinActionType.RANDOM_PICK: RandomPickWorkflowStep,
    BuiltinActionType.SHUFFLE_LIST: ShuffleListWorkflowStep,
    BuiltinActionType.SORT_LIST: SortListWorkflowStep,
    BuiltinActionType.PICK_LINE: PickLineWorkflowStep,
    BuiltinActionType.BILI_RESERVE: BiliReserveWorkflowStep,
    BuiltinActionType.PRINT: PrintWorkflowStep,
    BuiltinActionType.NOTIFY: NotifyWorkflowStep,
    BuiltinActionType.LOOP: LoopWorkflowStep,
    BuiltinActionType.IF_ELSE: IfElseWorkflowStep,
    BuiltinActionType.COMPOSITE: CompositeWorkflowStep,
    BuiltinActionType.OPEN_PAGE: OpenPageWorkflowStep,
    BuiltinActionType.CLOSE_PAGE: ClosePageWorkflowStep,
    BuiltinActionType.PARALLEL: ParallelWorkflowStep,
}


def create_workflow_step(
    action_id: str,
    params: dict | None = None,
    **kwargs: Any,
) -> BaseWorkflowStep[Any]:
    """工厂函数：根据 action_id 创建对应类型的 WorkflowStep 子类实例。

    若 action_id 对应已知内置类型，则 params dict 会被校验并转换为对应的 Params 模型；
    若为未知类型（自定义 action），则返回 BaseWorkflowStep 实例，params 保持 dict。
    """
    action_type = kwargs.pop("action_type", None) or action_id
    kwargs["action_id"] = action_id
    kwargs["action_type"] = action_type

    try:
        at = BuiltinActionType(action_type)
    except ValueError:
        kwargs["params"] = params
        return BaseWorkflowStep[dict](**kwargs)

    step_class = _WORKFLOW_STEP_CLASS_MAP[at]
    if params_model_class := BUILTIN_ACTION_PARAMS_MAP.get(at):
        try:
            kwargs["params"] = (
                params_model_class.model_validate(params) if params else None
            )
        except ValidationError:
            # 参数校验失败时保留原始 dict，让执行阶段处理错误
            kwargs["params"] = params
    else:
        kwargs["params"] = params
    try:
        return step_class(**kwargs)
    except ValidationError:
        # 子类构造失败（如 params 类型不匹配），降级为 BaseWorkflowStep
        kwargs["params"] = params
        return BaseWorkflowStep[dict](**kwargs)


# 参数模型中通过字符串前向引用 "WorkflowStep" 声明嵌套步骤字段
# （LoopParams.loopBranch / IfElseParams.*Branch / CompositeParams.steps），
# 其定义模块没有 WorkflowStep 符号，需在此显式注入命名空间完成重建。
# ParallelBranch 自身也通过字符串引用 WorkflowStep，必须一并注入命名空间
for _params_model in (
    LoopParams,
    IfElseParams,
    CompositeParams,
    ParallelParams,
    ParallelBranch,
):
    _params_model.model_rebuild(_types_namespace={"WorkflowStep": WorkflowStep})

__all__ = [
    "AllActionParams",
    "BaseWorkflowStep",
    "BiliReserveWorkflowStep",
    "ClickWorkflowStep",
    "CompositeWorkflowStep",
    "FetchExternalDataWorkflowStep",
    "GetTextWorkflowStep",
    "GetWindowWorkflowStep",
    "HoverWorkflowStep",
    "IfElseWorkflowStep",
    "InputWorkflowStep",
    "LLMWorkflowStep",
    "LoopWorkflowStep",
    "MarkResourceResultWorkflowStep",
    "OpenPageWorkflowStep",
    "ClosePageWorkflowStep",
    "ParallelWorkflowStep",
    "NavigateWorkflowStep",
    "NewPageWorkflowStep",
    "PickLineWorkflowStep",
    "PrintWorkflowStep",
    "RandomPickWorkflowStep",
    "ScreenshotWorkflowStep",
    "ScrollWorkflowStep",
    "ShuffleListWorkflowStep",
    "SortListWorkflowStep",
    "WaitWorkflowStep",
    "WorkflowStep",
    "_ensure_action_type",
    "_fill_action_type_impl",
    "_WORKFLOW_STEP_CLASS_MAP",
    "create_workflow_step",
    "is_composite_action_id",
    "normalize_composite_action_id",
    "workflow_step_adapter",
]
