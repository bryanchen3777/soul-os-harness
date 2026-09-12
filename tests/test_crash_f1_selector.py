"""
test_crash_f1_selector.py
CRASH-F1 驗收 (Test A-E): WindowsSelectorEventLoopPolicy 可切換開關.

驗收範圍:
  A. 預設值驗證 — 無環境變數 + config 為 proactor → 解析結果 proactor, 實際 loop ProactorEventLoop.
  B. 環境變數覆蓋 — SOUL_OS_EVENT_LOOP=selector → selector + SelectorEventLoop; 切回 proactor 平滑.
  C. 模組隔離 — import src.memory.* 與 clients.voice_companion.* 不得觸發全域 loop policy 竄改.
  D. Subprocess 掃描 — AST 掃描生產路徑 (src/ 非 tests 引用 + scripts/run_server.py),
     0 個 asyncio create_subprocess_* / loop.subprocess_exec 生產調用; 若發現標註是否生產路徑.
  E. 無效值安全回退 — SOUL_OS_EVENT_LOOP=bogus → 回退 proactor, 不拋例外.

本測試不啟動 uvicorn、不 bind port：只操作 policy 解析/套用 + 建立即關閉的 probe loop。
"""
import ast
import asyncio
import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.run_server as run_server  # noqa: E402  (CRASH-F1 開關實作處)

IS_WIN32 = sys.platform == "win32"


# ── fixtures ──────────────────────────────────────────────

@pytest.fixture()
def clean_event_loop_env(monkeypatch):
    """Test A/B/E 用: 清掉 SOUL_OS_EVENT_LOOP + 測試後還原 policy (避免污染其他測試)。"""
    monkeypatch.delenv("SOUL_OS_EVENT_LOOP", raising=False)
    yield
    # restore: 回到 process 預設 policy (win32 = WindowsProactorEventLoopPolicy)
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())


def _actual_loop():
    """由目前 policy 建立一個 probe loop (測試專用, 用後關閉)。"""
    loop = asyncio.new_event_loop()
    try:
        return loop
    finally:
        loop.close()


# ── Test A: 預設值驗證 ────────────────────────────────────

def test_a_default_proactor_without_env(clean_event_loop_env):
    # 無環境變數; config server.event_loop = proactor → 解析結果 proactor
    cfg = {"server": {"event_loop": "proactor"}}
    assert run_server._resolve_event_loop(cfg) == "proactor"

    # config 完全沒有 server 區段 (或空 dict / None) → 回退到模組層級預設 proactor
    assert run_server._resolve_event_loop({}) == "proactor"
    assert run_server._resolve_event_loop(None) == "proactor"
    assert run_server._DEFAULT_EVENT_LOOP == "proactor"  # 翻牌點常數必須維持現狀

    run_server._apply_event_loop_policy("proactor")
    loop = _actual_loop()
    assert run_server._active_loop_class_name() == "ProactorEventLoop"
    if IS_WIN32:
        assert isinstance(loop, asyncio.ProactorEventLoop)
    else:
        # 非 win32 預設 policy 就是 SelectorEventLoop (apply 不動作)
        assert isinstance(loop, asyncio.SelectorEventLoop)


# ── Test B: 環境變數覆蓋 + 平滑切回 ───────────────────────

def test_b_env_override_selector_and_back(clean_event_loop_env):
    # env=selector 覆蓋 config 的 proactor
    os.environ["SOUL_OS_EVENT_LOOP"] = "selector"
    pref = run_server._resolve_event_loop({"server": {"event_loop": "proactor"}})
    assert pref == "selector"

    run_server._apply_event_loop_policy(pref)
    loop = _actual_loop()
    assert run_server._active_loop_class_name() == "WindowsSelectorEventLoop"
    assert isinstance(loop, asyncio.SelectorEventLoop)
    if IS_WIN32:
        assert not isinstance(loop, asyncio.ProactorEventLoop)

    # 大小寫不敏感
    os.environ["SOUL_OS_EVENT_LOOP"] = "SELECTOR"
    assert run_server._resolve_event_loop(None) == "selector"

    # 平滑切回 proactor (env 覆蓋 config 的 selector)
    os.environ["SOUL_OS_EVENT_LOOP"] = "proactor"
    pref2 = run_server._resolve_event_loop({"server": {"event_loop": "selector"}})
    assert pref2 == "proactor"
    run_server._apply_event_loop_policy(pref2)
    loop2 = _actual_loop()
    assert run_server._active_loop_class_name() == "ProactorEventLoop"
    if IS_WIN32:
        assert isinstance(loop2, asyncio.ProactorEventLoop)


# ── Test C: 模組隔離 (import 不竄改全域 loop policy) ──────

def test_c_module_imports_do_not_mutate_loop_policy():
    """VC 獨立進程與 src.memory 完全不受影響:
    import 期間若有人呼叫 asyncio.set_event_loop_policy → spy 直接讓測試失敗。
    """
    original_setter = asyncio.set_event_loop_policy
    policy_before = asyncio.get_event_loop_policy()
    calls = []

    def _spy(policy):
        calls.append(policy)
        raise AssertionError(f"global event loop policy mutated during import: {policy!r}")

    # 同時攔兩個路徑 (asyncio.set_event_loop_policy 與 asyncio.events 原點)
    asyncio.set_event_loop_policy = _spy
    try:
        # NOTE: 用 importlib 避免函式內 import asyncio.events 把 asyncio 變 local
        importlib.import_module("asyncio.events").set_event_loop_policy = _spy
    except Exception:
        pass

    try:
        # src/memory 生產路徑 (實際被主服務 import 的模組)
        importlib.import_module("src.memory.middleware")
        importlib.import_module("src.memory.sage.graph_store")
        # clients/voice_companion (三個獨立進程, 維持既有機制)
        importlib.import_module("clients.voice_companion.env_config")
        importlib.import_module("clients.voice_companion.session_store")
    finally:
        asyncio.set_event_loop_policy = original_setter
        try:
            importlib.import_module("asyncio.events").set_event_loop_policy = original_setter
        except Exception:
            pass

    assert calls == [], f"import 期間 loop policy 被竄改 {len(calls)} 次: {calls}"
    # 且 policy 物件未被替換
    assert asyncio.get_event_loop_policy() is policy_before


# ── Test D: Subprocess AST 掃描 (生產路徑 0 asyncio subprocess) ──

_ASYNCIO_SUBPROCESS_ATTRS = ("create_subprocess_exec", "create_subprocess_shell", "subprocess_exec")
_BACKUP_DIR_MARKERS = ("_backup",)


def _iter_production_py():
    """src/ 的非 tests 引用 (排除 _backup 快照目錄) + scripts/run_server.py。"""
    for p in sorted((ROOT / "src").rglob("*.py")):
        if any(marker in str(p.relative_to(ROOT)) for marker in _BACKUP_DIR_MARKERS):
            continue
        yield p
    yield ROOT / "scripts" / "run_server.py"


def _production_importers_of(stem: str, exclude: Path):
    """在生產路徑中找誰 textually 引用了該模組 (不含自己)。"""
    refs = []
    for p in _iter_production_py():
        if p == exclude:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if stem in text:
            refs.append(str(p.relative_to(ROOT)))
    return refs


def test_d_zero_asyncio_subprocess_in_production_paths():
    """AST 掃描生產路徑, 斷言 0 個 asyncio create_subprocess_* / loop.subprocess_exec
    生產調用。命中的模組若無任何生產引用 → 標註 test-only, 不算生產路徑。"""
    hits = []  # (relpath, lineno, attr, production_refs)
    for p in _iter_production_py():
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in _ASYNCIO_SUBPROCESS_ATTRS:
                    refs = _production_importers_of(p.stem, exclude=p)
                    hits.append((str(p.relative_to(ROOT)), node.lineno, node.func.attr, refs))

    prod_hits = [h for h in hits if h[3]]
    annotations = "\n".join(
        f"  - {rel}:{lineno} {attr} -> {'PRODUCTION' if refs else 'test-only (無生產引用)'} refs={refs}"
        for rel, lineno, attr, refs in hits
    )
    assert prod_hits == [], (
        f"生產路徑發現 asyncio subprocess 調用 (嚴禁硬切, 需停工回報): {prod_hits}\n"
        f"全部命中註記:\n{annotations}"
    )
    # 已知命中必須是 mcp_stdio_client (tests-only), 確保掃描真的有在跑
    assert any("mcp_stdio_client" in a for a in annotations.splitlines()), (
        f"掃描異常: 預期 mcp_stdio_client.py:245 命中但沒找到。annotations={annotations}"
    )


# ── Test E: 無效值安全回退 ────────────────────────────────

def test_e_invalid_env_value_falls_back_to_proactor(clean_event_loop_env):
    os.environ["SOUL_OS_EVENT_LOOP"] = "bogus"
    # 不拋例外 (啟動必須永遠成功); env 蓋過 config, 但無效值 → 回退 proactor
    pref = run_server._resolve_event_loop({"server": {"event_loop": "selector"}})
    assert pref == "proactor"

    os.environ.pop("SOUL_OS_EVENT_LOOP")
    # config 無效值同樣回退
    pref2 = run_server._resolve_event_loop({"server": {"event_loop": "AsyncIOLoop"}})
    assert pref2 == "proactor"

    # 套用不拋例外, policy 為 proactor
    run_server._apply_event_loop_policy(pref)
    loop = _actual_loop()
    if IS_WIN32:
        assert isinstance(loop, asyncio.ProactorEventLoop)