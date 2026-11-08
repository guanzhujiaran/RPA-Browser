"""分页响应泛型契约：OpenAPI 中 ``items`` 必须展开为具体项类型的 ``$ref``

回归背景：``BasePaginationResp`` 曾是 ``SQLModel + Generic[T]``，SQLModel 0.0.48
不会把 ``T`` 替换进 ``items`` 的注解，FastAPI 生成的 schema 里 ``items``
退化成空对象 ``{}``，hey-api 只能产出 ``Array<unknown>``（运行记录列表、
操作日志、动作列表等所有分页接口都受影响）。改为 ``pydantic.BaseModel`` 后
应展开为指向具体项类型的 ``$ref``。
"""

from bili_common.models.pagination import BasePaginationResp

from app.models.workflow.workflow_responses import WorkflowRunLogItemResponse


def test_items_schema_points_to_concrete_model() -> None:
    schema = BasePaginationResp[WorkflowRunLogItemResponse].model_json_schema()

    items = schema["properties"]["items"]["items"]
    assert "$ref" in items, "items 不能退化成空对象 {}"
    assert items["$ref"].endswith("/WorkflowRunLogItemResponse")
    defs = schema.get("$defs", {})
    assert "WorkflowRunLogItemResponse" in defs
    assert "resource_total" in defs["WorkflowRunLogItemResponse"]["properties"]


def test_items_schema_is_parameterized_per_item_type() -> None:
    """不同泛型实参必须指向各自的 $ref，不能共用同一个空 schema。"""
    schema = BasePaginationResp[int].model_json_schema()

    items = schema["properties"]["items"]["items"]
    assert items == {"type": "integer"}


def test_from_attributes_is_preserved() -> None:
    assert BasePaginationResp.model_config.get("from_attributes") is True


def test_response_behaviour_is_unchanged() -> None:
    """构造、计算字段与序列化口径不变（避免修复 schema 破坏运行时）。"""
    paged = BasePaginationResp[int](page=2, per_page=10, total=25, items=[1, 2, 3])

    dumped = paged.model_dump()
    assert dumped["items"] == [1, 2, 3]
    assert dumped["pages"] == 3
    assert dumped["has_next"] is True
    assert dumped["has_prev"] is True
    assert dumped["next_page"] == 3
    assert dumped["prev_page"] == 1
