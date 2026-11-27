"""指纹 upsert 更新分支的字段映射测试（不连数据库）。

回归：编辑任意字段时，`fingerprint_int` 曾被直接 ``setattr`` 到 ``UserBrowserInfo``，
抛 ``"UserBrowserInfo" object has no field "fingerprint_int"``，导致整个编辑接口 500。
种子入参叫 ``fingerprint_int``，落库字段是 ``fingerprint``，必须单独映射。
"""

from types import SimpleNamespace

from app.models.runtime.api import BrowserFingerprintUpsertParams
from app.services.RPA_browser.fingerprint import browser_fingerprint_service as service


class _FakeResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def one_or_none(self):
        return self._value

    def first(self):
        return self._value


class _FakeSession:
    """按调用顺序返回预设结果：查目标浏览器 → 种子唯一性校验。"""

    def __init__(self, results: list[object]) -> None:
        self._results = list(results)
        self.committed = False

    async def exec(self, _stmt):
        return _FakeResult(self._results.pop(0))

    def add(self, _obj) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, _obj) -> None:
        return None


async def test_upsert_update_maps_seed_and_keeps_other_fields(monkeypatch) -> None:
    async def _noop_apply_versions(*args, **kwargs) -> bool:
        return False

    monkeypatch.setattr(service, "apply_versions", _noop_apply_versions)

    browser_info = SimpleNamespace(
        browser_id=1001,
        mid=1,
        fingerprint=111,
        proxy_server=None,
        fingerprint_real_version="150.0.0.0",
        fingerprint_brand_version="150.0.0.0",
    )
    session = _FakeSession([browser_info, None])

    params = BrowserFingerprintUpsertParams(
        browser_id="1001",
        fingerprint_int=222,
        proxy_server="none",
    )
    await service.BrowserFingerprintService.upsert_fingerprint(params, 1, session)

    # 种子映射到 fingerprint，而不是 setattr 一个不存在的字段
    assert browser_info.fingerprint == 222
    assert browser_info.proxy_server == "none"
    assert session.committed is True


async def test_upsert_update_pool_overrides_old_proxy(monkeypatch) -> None:
    async def _noop_apply_versions(*args, **kwargs) -> bool:
        return False

    monkeypatch.setattr(service, "apply_versions", _noop_apply_versions)

    browser_info = SimpleNamespace(
        browser_id=1001,
        mid=1,
        fingerprint=111,
        proxy_server="1.2.3.4:8080",
        fingerprint_real_version="150.0.0.0",
        fingerprint_brand_version="150.0.0.0",
    )
    # 未传种子：exclude_unset 会排除，不查种子唯一性
    session = _FakeSession([browser_info])

    params = BrowserFingerprintUpsertParams(
        browser_id="1001", proxy_server="pool"
    )
    await service.BrowserFingerprintService.upsert_fingerprint(params, 1, session)

    assert browser_info.fingerprint == 111
    # 池模式用显式 "pool"：空串会被入参层归一成 None 并跳过，旧代理清不掉
    assert browser_info.proxy_server == "pool"
