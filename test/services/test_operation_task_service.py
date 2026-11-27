"""操作任务表的状态流转测试（纯内存，不启动浏览器）。"""

import time

from app.services.RPA_browser.session.operation_task_service import (
    TASK_TTL_SECONDS,
    OperationTaskService,
)


def _reset() -> None:
    OperationTaskService._tasks.clear()
    OperationTaskService._browser_locks.clear()


def test_create_and_snapshot() -> None:
    _reset()
    task = OperationTaskService.create(1, "import_cookies", 3)

    assert task.status == "pending"
    snapshot = OperationTaskService.snapshot(task)
    assert snapshot.total == 3
    assert snapshot.progress == 0


def test_progress_and_finish() -> None:
    _reset()
    task = OperationTaskService.create(1, "import_cookies", 2)
    OperationTaskService.start(task.task_id, "正在写入")
    OperationTaskService.update_progress(task.task_id, 1)
    OperationTaskService.finish(task.task_id, message="done")

    stored = OperationTaskService.get(1, task.task_id)
    assert stored is not None
    snapshot = OperationTaskService.snapshot(stored)
    assert snapshot.status == "success"
    assert snapshot.progress == 2
    assert snapshot.message == "done"


def test_get_rejects_other_mid() -> None:
    _reset()
    task = OperationTaskService.create(1, "clear_login_state", 1)

    assert OperationTaskService.get(2, task.task_id) is None


def test_expired_task_is_dropped() -> None:
    _reset()
    task = OperationTaskService.create(1, "clear_login_state", 1)
    OperationTaskService.finish(task.task_id)

    # 手工把完成时间推到 TTL 之外
    task.updated_at = int(time.time()) - TASK_TTL_SECONDS - 1
    assert OperationTaskService.get(1, task.task_id) is None


def test_fail_sets_error() -> None:
    _reset()
    task = OperationTaskService.create(1, "import_cookies", 1)
    OperationTaskService.fail(task.task_id, "boom")

    stored = OperationTaskService.get(1, task.task_id)
    assert stored is not None
    snapshot = OperationTaskService.snapshot(stored)
    assert snapshot.status == "failed"
    assert snapshot.error == "boom"
