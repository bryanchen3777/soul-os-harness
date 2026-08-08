"""
tests/test_m3_1_phase_a.py — M3.1 Phase A Contract Tests

Bry 拍板 2026-08-08 01:57 M3.1 Phase A + 2026-08-08 02:21 A' cleanup:

本檔案驗證 contract (Bry 派工 requires):

  M3.1 Phase A 派工 (5 個 requires + 2 個 additional):
  ──────────────────────────────────────────────────────────────────
  1. WorldEventSource 是 abstract, 不能直接 instantiate
  2. SyntheticWorldEventSource conform WorldEventSource
     - isinstance WorldEventSource
     - source_id == "synthetic"
     - start() / stop() 不出錯
     - stop() idempotent (可重複呼叫)
  3. Registry.register() 拒絕重複 source_id
  4. Registry.start_all() 失敗 isolation (一個 source raise, 其他仍 start)
  5. Registry.stop_all() 失敗 isolation + 重複呼叫不 crash
  6. health_snapshot() 給 started / failed / stopped 狀態
  7. WorldEventInjector 是 @runtime_checkable Protocol

  A' cleanup 派工 (compatibility):
  ──────────────────────────────────────────────────────────────────
  8. src/world/source/base.py orphan/placeholder 必須已移除
  9. src/world/base.py 是唯一 canonical WorldEventSource
  10. src/world/source.py 是最薄 compatibility shim
  11. 三條 import path 指向同一 class:
      - from src.world import SyntheticWorldEventSource
      - from src.world.source import SyntheticWorldEventSource
      - from src.world.source.synthetic import SyntheticWorldEventSource

回歸要求 (Bry 派工): Phase A 不修改既有 M3 behavior。
本檔案不 import middleware / token_manager / run_server。
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys

import pytest

from src.world import (
    WorldEventSource,
    WorldEventInjector,
    WorldEventSourceRegistry,
    SourceStatus,
    SyntheticWorldEventSource,
)
from src.world.perception import WorldEvent


# ───────────────────────────────────────────────────────────
# 1. WorldEventSource 抽象基底不能直接 instantiate
# ───────────────────────────────────────────────────────────

def test_world_event_source_abstract_cannot_instantiate():
    """
    Bry 派工 A1: WorldEventSource 是 abstract base class。
    直接 instantiate 必須 raise TypeError。
    """
    with pytest.raises(TypeError) as exc_info:
        WorldEventSource()  # type: ignore[abstract]

    # 確認錯誤訊息有 abstract 提示
    err_msg = str(exc_info.value).lower()
    assert "abstract" in err_msg, f"error should mention 'abstract', got: {exc_info.value}"


# ───────────────────────────────────────────────────────────
# 2. SyntheticWorldEventSource conform WorldEventSource
# ───────────────────────────────────────────────────────────

def test_synthetic_world_event_source_isinstance():
    """SyntheticWorldEventSource 必須 isinstance WorldEventSource ABC。"""
    s = SyntheticWorldEventSource()
    assert isinstance(s, WorldEventSource)


def test_synthetic_world_event_source_id():
    """source_id 必須 == 'synthetic'。"""
    s = SyntheticWorldEventSource()
    assert s.source_id == "synthetic"


def test_synthetic_world_event_source_start_stop():
    """start() / stop() 是 no-op, 不 raise。"""
    s = SyntheticWorldEventSource()
    asyncio.run(s.start())
    asyncio.run(s.stop())


def test_synthetic_world_event_source_stop_idempotent():
    """stop() contract: 多次呼叫必須安全 (idempotent)。"""
    s = SyntheticWorldEventSource()
    asyncio.run(s.start())
    asyncio.run(s.stop())
    asyncio.run(s.stop())  # 第二次 stop 必須不 raise
    asyncio.run(s.stop())  # 第三次 stop 必須不 raise


def test_synthetic_world_event_source_build_methods_preserved():
    """
    M3 行為 100% 保留 — build_*() factory methods 全部仍可 work。
    (Bry 派工 A4: 不要重寫 event generation logic)
    """
    e1 = SyntheticWorldEventSource.build_rain_started()
    e2 = SyntheticWorldEventSource.build_celebrity_news()
    e3 = SyntheticWorldEventSource.build_calendar_event_30min()
    e4 = SyntheticWorldEventSource.build_temp_fluctuation()
    e5 = SyntheticWorldEventSource.build_user_going_outside()

    # 5 個 event 都有正確的 source / type / novelty_id
    assert e1.source == "weather" and e1.type == "rain_started"
    assert e2.source == "news" and e2.type == "celebrity_news"
    assert e3.source == "calendar" and e3.type == "calendar_event"
    assert e4.source == "weather" and e4.type == "weather_temp_change"
    assert e5.source == "social" and e5.type == "user_going_outside"

    # build_all_five 一次拿 5 個
    all5 = SyntheticWorldEventSource.build_all_five()
    assert len(all5) == 5


# ───────────────────────────────────────────────────────────
# 3. Registry.register() 拒絕重複 source_id
# ───────────────────────────────────────────────────────────

class _StubInjector:
    """測試用 stub, conform WorldEventInjector Protocol。"""

    def __init__(self):
        self.injected: list = []

    async def inject(self, event: WorldEvent) -> None:
        self.injected.append(event)


class _StubSource(WorldEventSource):
    """測試用 stub source。"""

    def __init__(self, source_id: str, fail_on_start: bool = False, fail_on_stop: bool = False):
        self._source_id = source_id
        self._fail_on_start = fail_on_start
        self._fail_on_stop = fail_on_stop
        self.start_called = 0
        self.stop_called = 0

    @property
    def source_id(self) -> str:
        return self._source_id

    async def start(self) -> None:
        self.start_called += 1
        if self._fail_on_start:
            raise RuntimeError(f"start failed for {self._source_id}")

    async def stop(self) -> None:
        self.stop_called += 1
        if self._fail_on_stop:
            raise RuntimeError(f"stop failed for {self._source_id}")


def test_registry_register_accepts_new_source():
    """正常 register 不應該 raise。"""
    registry = WorldEventSourceRegistry(_StubInjector())
    src = _StubSource("weather")
    registry.register(src)
    assert src.source_id in registry.registered_source_ids()
    assert registry.get_status("weather") == SourceStatus.REGISTERED


def test_registry_register_rejects_duplicate_source_id():
    """重複 source_id register 必須 raise ValueError。"""
    registry = WorldEventSourceRegistry(_StubInjector())
    registry.register(_StubSource("weather"))
    with pytest.raises(ValueError) as exc_info:
        registry.register(_StubSource("weather"))
    assert "weather" in str(exc_info.value)


def test_registry_register_does_not_start_source():
    """register 本身不應該 start source。"""
    registry = WorldEventSourceRegistry(_StubInjector())
    src = _StubSource("weather")
    registry.register(src)
    assert src.start_called == 0  # 沒 start 過
    assert registry.get_status("weather") == SourceStatus.REGISTERED  # 仍是 REGISTERED


# ───────────────────────────────────────────────────────────
# 4. Registry.start_all() 失敗 isolation
# ───────────────────────────────────────────────────────────

def test_registry_start_all_starts_all_sources():
    """所有 source 沒 fail, 全部應該 STARTED。"""
    registry = WorldEventSourceRegistry(_StubInjector())
    src_a = _StubSource("weather")
    src_b = _StubSource("news")
    registry.register(src_a)
    registry.register(src_b)
    asyncio.run(registry.start_all())
    assert registry.get_status("weather") == SourceStatus.STARTED
    assert registry.get_status("news") == SourceStatus.STARTED
    assert src_a.start_called == 1
    assert src_b.start_called == 1


def test_registry_start_all_failure_isolation():
    """
    一個 source start 拋 exception, 不應阻擋其他 source start。
    失敗的 source status 應為 FAILED, 成功的 source status 應為 STARTED。
    """
    registry = WorldEventSourceRegistry(_StubInjector())
    src_a = _StubSource("weather")  # OK
    src_b = _StubSource("news", fail_on_start=True)  # fail
    src_c = _StubSource("calendar")  # OK
    registry.register(src_a)
    registry.register(src_b)
    registry.register(src_c)

    # 必須不 raise (即使 src_b 內部 raise)
    asyncio.run(registry.start_all())

    # src_a 跟 src_c 應該 STARTED
    assert registry.get_status("weather") == SourceStatus.STARTED
    assert registry.get_status("calendar") == SourceStatus.STARTED
    assert src_a.start_called == 1
    assert src_c.start_called == 1

    # src_b 應該 FAILED
    assert registry.get_status("news") == SourceStatus.FAILED
    assert src_b.start_called == 1  # 還是嘗試呼叫過


# ───────────────────────────────────────────────────────────
# 5. Registry.stop_all() 失敗 isolation + idempotent
# ───────────────────────────────────────────────────────────

def test_registry_stop_all_stops_all_sources():
    """所有 source 沒 fail, 全部應該 STOPPED。"""
    registry = WorldEventSourceRegistry(_StubInjector())
    src_a = _StubSource("weather")
    src_b = _StubSource("news")
    registry.register(src_a)
    registry.register(src_b)
    asyncio.run(registry.start_all())
    asyncio.run(registry.stop_all())
    assert registry.get_status("weather") == SourceStatus.STOPPED
    assert registry.get_status("news") == SourceStatus.STOPPED
    assert src_a.stop_called == 1
    assert src_b.stop_called == 1


def test_registry_stop_all_failure_isolation():
    """
    一個 source stop 拋 exception, 不應阻擋其他 source stop。
    失敗的 source status 應為 STOP_FAILED, 成功的應為 STOPPED。
    """
    registry = WorldEventSourceRegistry(_StubInjector())
    src_a = _StubSource("weather")
    src_b = _StubSource("news", fail_on_stop=True)  # fail on stop
    src_c = _StubSource("calendar")
    registry.register(src_a)
    registry.register(src_b)
    registry.register(src_c)

    asyncio.run(registry.start_all())
    asyncio.run(registry.stop_all())  # 必須不 raise

    assert registry.get_status("weather") == SourceStatus.STOPPED
    assert registry.get_status("calendar") == SourceStatus.STOPPED
    assert registry.get_status("news") == SourceStatus.STOP_FAILED


def test_registry_stop_all_idempotent():
    """
    重複呼叫 stop_all 必須不 crash (Bry 派工 8/8 01:57 idempotent contract)。
    """
    registry = WorldEventSourceRegistry(_StubInjector())
    registry.register(_StubSource("weather"))
    asyncio.run(registry.start_all())

    asyncio.run(registry.stop_all())
    asyncio.run(registry.stop_all())  # 第二次
    asyncio.run(registry.stop_all())  # 第三次

    # status 應為 STOPPED (不是 STOP_FAILED,因為第二次之後 stub 不再 raise)
    assert registry.get_status("weather") == SourceStatus.STOPPED


# ───────────────────────────────────────────────────────────
# 6. health_snapshot() 給 started / failed / stopped 狀態
# ───────────────────────────────────────────────────────────

def test_registry_health_snapshot_initial_state():
    """剛 register 但還沒 start_all, status 應為 REGISTERED。"""
    registry = WorldEventSourceRegistry(_StubInjector())
    registry.register(_StubSource("weather"))
    snap = registry.health_snapshot()
    assert snap == {"weather": {"status": "registered"}}


def test_registry_health_snapshot_after_start_all():
    """start_all 後, status 應為 started。"""
    registry = WorldEventSourceRegistry(_StubInjector())
    registry.register(_StubSource("weather"))
    registry.register(_StubSource("news"))
    asyncio.run(registry.start_all())
    snap = registry.health_snapshot()
    assert snap == {
        "weather": {"status": "started"},
        "news": {"status": "started"},
    }


def test_registry_health_snapshot_mixed_status():
    """一個 STARTED, 一個 FAILED, health_snapshot 應正確反映。"""
    registry = WorldEventSourceRegistry(_StubInjector())
    registry.register(_StubSource("weather"))
    registry.register(_StubSource("news", fail_on_start=True))
    asyncio.run(registry.start_all())
    snap = registry.health_snapshot()
    assert snap == {
        "weather": {"status": "started"},
        "news": {"status": "failed"},
    }


def test_registry_health_snapshot_after_stop_all():
    """stop_all 後, status 應為 stopped。"""
    registry = WorldEventSourceRegistry(_StubInjector())
    registry.register(_StubSource("weather"))
    registry.register(_StubSource("news"))
    asyncio.run(registry.start_all())
    asyncio.run(registry.stop_all())
    snap = registry.health_snapshot()
    assert snap == {
        "weather": {"status": "stopped"},
        "news": {"status": "stopped"},
    }


# ───────────────────────────────────────────────────────────
# 7. WorldEventInjector 是 @runtime_checkable Protocol
# ───────────────────────────────────────────────────────────

def test_world_event_injector_runtime_checkable_with_stub():
    """
    WorldEventInjector 是 @runtime_checkable Protocol,
    只要有 async def inject(self, event) 就是 conform。
    """
    stub = _StubInjector()
    assert isinstance(stub, WorldEventInjector)


def test_world_event_injector_runtime_checkable_rejects_non_conform():
    """
    沒有 inject method 的物件不應該 conform WorldEventInjector。
    """
    class NotInjector:
        pass

    assert not isinstance(NotInjector(), WorldEventInjector)


def test_stub_injector_actually_injects_event():
    """Stub injector 應該把 inject() 收到的 event 記下來。"""
    stub = _StubInjector()
    e = SyntheticWorldEventSource.build_rain_started()
    asyncio.run(stub.inject(e))
    assert e in stub.injected


# ───────────────────────────────────────────────────────────
# 8. A' cleanup: source/base.py orphan 必須已移除
# ───────────────────────────────────────────────────────────

def test_source_base_py_orphan_removed():
    """
    Bry 派板 A' cleanup (2026-08-08 02:21):
    src/world/source/base.py 必須已移除 (orphaned placeholder)。
    只允許 src/world/base.py 存在 WorldEventSource ABC。
    """
    # 確認 src.world.source.base module 不存在
    with pytest.raises(ImportError):
        importlib.import_module("src.world.source.base")

    # 確認檔案系統上也沒有
    orphan = "src/world/source/base.py"
    assert not os.path.exists(orphan), (
        f"orphan 仍存在: {orphan} — A' cleanup 沒完成"
    )


# ───────────────────────────────────────────────────────────
# 9. A' cleanup: src/world/base.py 是唯一 canonical WorldEventSource
# ───────────────────────────────────────────────────────────

def test_base_py_is_canonical_world_event_source():
    """
    src/world/base.py 是 WorldEventSource 唯一 canonical 位置。
    從 src.world 拿到的 WorldEventSource 必須住在 src.world.base。
    """
    assert WorldEventSource.__module__ == "src.world.base", (
        f"WorldEventSource 應住在 src.world.base, 實際: {WorldEventSource.__module__}"
    )

    # 直接 import src.world.base 也拿得到同一個 class
    import src.world.base as base_mod
    assert WorldEventSource is base_mod.WorldEventSource


# ───────────────────────────────────────────────────────────
# 10. A' cleanup: src/world/source.py 是最薄 compatibility shim
# ───────────────────────────────────────────────────────────

def test_source_py_is_compatibility_shim():
    """
    src/world/source.py 內容是最薄 compatibility shim
    (re-export statement, 不含完整 class 邏輯)。
    """
    shim_path = "src/world/source.py"
    assert os.path.exists(shim_path), f"{shim_path} 必須存在"

    # 確認檔案大小是合理的 shim (不是完整的 source code)
    # 完整 M3 邏輯 ~7-8KB, shim 應該 < 5KB
    size = os.path.getsize(shim_path)
    assert size < 5000, (
        f"src/world/source.py 太大 ({size} bytes), 應該是薄 shim"
    )

    # 確認 source.py 內包含 SyntheticWorldEventSource re-export
    with open(shim_path, encoding="utf-8") as f:
        content = f.read()
    assert "SyntheticWorldEventSource" in content, (
        "shim 必須 re-export SyntheticWorldEventSource"
    )
    assert "SYNTHETIC_TEST_EVENTS" in content, (
        "shim 必須 re-export SYNTHETIC_TEST_EVENTS"
    )


def test_source_py_does_not_contain_class_definition():
    """
    src/world/source.py 是 shim, 不應該定義 SyntheticWorldEventSource class。
    class 邏輯住在 src.world.source.synthetic。
    """
    with open("src/world/source.py", encoding="utf-8") as f:
        content = f.read()

    # shim 不應該有 `class SyntheticWorldEventSource`
    assert "class SyntheticWorldEventSource" not in content, (
        "source.py 是 shim, 不應該定義 SyntheticWorldEventSource class"
    )


# ───────────────────────────────────────────────────────────
# 11. A' cleanup: 三條 import path 指向同一 class
# ───────────────────────────────────────────────────────────

def test_compat_three_import_paths_same_class():
    """
    Bry 派板 A' cleanup (2026-08-08 02:21):
    確認三條 import path 指向同一個 SyntheticWorldEventSource class object。

    Path A: from src.world import SyntheticWorldEventSource
    Path B: from src.world.source import SyntheticWorldEventSource
    Path C: from src.world.source.synthetic import SyntheticWorldEventSource

    NOTE: 不使用 clean_import_state fixture, 因為 fixture 的 teardown
    會清空 sys.modules, 影響後續 test 的 class identity 判定。
    改用「test 內 explicit 重新 import」確保 reference 從同一 sys.modules cache 拿。
    """
    from src.world import SyntheticWorldEventSource as A
    from src.world.source import SyntheticWorldEventSource as B
    from src.world.source.synthetic import SyntheticWorldEventSource as C

    # 三條 path 必須是同一個 class object (identity 測試)
    assert A is B, "Path A (src.world) 跟 Path B (src.world.source) 應是同一 class"
    assert B is C, "Path B (src.world.source) 跟 Path C (src.world.source.synthetic) 應是同一 class"
    assert A is C, "Path A (src.world) 跟 Path C (src.world.source.synthetic) 應是同一 class"

    # 也確認三個 class 都住在同一個 module
    assert A.__module__ == "src.world.source.synthetic"
    assert B.__module__ == "src.world.source.synthetic"
    assert C.__module__ == "src.world.source.synthetic"


def test_compat_src_world_source_resolves_to_package():
    """
    src.world.source 在 source/ subpackage 存在下被 Python 解析成 package。
    (因為 regular package 優先於同名 module)

    這個 test 確認:
    1. src.world.source 有 __path__ (是 package)
    2. 實際加載的是 src/world/source/__init__.py, 不是 src/world/source.py
    """
    import src.world.source as s

    # 是 package (有 __path__ list)
    assert hasattr(s, "__path__"), "src.world.source 應該是 package, 應有 __path__"
    assert isinstance(s.__path__, list), "__path__ 應是 list"

    # __file__ 指向 subpackage 的 __init__.py, 不是 sibling source.py
    assert s.__file__.endswith("source\\__init__.py") or s.__file__.endswith("source/__init__.py"), (
        f"__file__ 應指向 source/__init__.py, 實際: {s.__file__}"
    )


def test_compat_synthetic_class_under_source_subpackage():
    """
    從 src.world.source.synthetic 直接拿 class, 應該拿到 SyntheticWorldEventSource。
    """
    import src.world.source.synthetic as synth_mod
    assert hasattr(synth_mod, "SyntheticWorldEventSource")
    assert hasattr(synth_mod, "SYNTHETIC_TEST_EVENTS")

    # 直接 instantiate 應該是 WorldEventSource instance
    # 從 synth_mod 拿 WorldEventSource (保證跟 instance 的 MRO 一致)
    instance = synth_mod.SyntheticWorldEventSource()
    assert isinstance(instance, synth_mod.WorldEventSource)
    assert instance.source_id == "synthetic"


def test_compat_shim_does_not_shadow_subpackage():
    """
    src/world/source.py shim module 雖然存在, 但因為 source/ subpackage 優先,
    source.py 永遠不會被加載。
    """
    # 確認 src.world.source 是 subpackage, shim module 沒被加載
    import src.world.source as loaded_source
    assert loaded_source.__file__.endswith("__init__.py"), (
        "src.world.source 應解析為 subpackage (__init__.py), "
        "而不是 shim module (source.py)"
    )
