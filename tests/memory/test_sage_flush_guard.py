"""
tests/memory/test_sage_flush_guard.py — SAGE-FLUSH-1 驗收

目標: 主服務每 15s 定時 flush 所有 live GraphStore 的 pending writes，
把崩潰（原生 access violation 硬殺）時的未 commit 資料損失視窗從
「累積到 batch (20) 或手動 flush」縮短到固定 15 秒。本工單不修崩潰，
只止損。

Frozen contract 邊界 (0 change): SAGE 寫入邏輯為 Owner 授權的窄域解凍——
只新增「定時呼叫既有 flush()」的機制。flush()/close()/_BATCH_SIZE/schema/
讀取路徑全部未動；不實作 C1（每輪 flush）。

驗收 7 項:
  1. 止損生效: 有 pending 的 store → 掃描器 → 另一獨立連線看得到資料
  2. idle 無害: _pending_writes == 0 時不報錯、無副作用
  3. 單一失敗不擴散: monkeypatch 一個 store 的 flush 拋例外 → 其他仍被 flush
  4. 不實例化新 store: 掃描前後註冊表 / __init__ 呼叫數不變
  5. 任務韌性: 0.2s 短週期注入例外 → 任務存活（+ run_server 結構 AST 斷言）
  6. import 0 副作用: 無新 thread / 無 async task / module 級無 create_task
  7. _BATCH_SIZE == 20 未被改動

運行: .\\.venv\\Scripts\\python.exe -m pytest tests/memory/test_sage_flush_guard.py -v
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import sqlite3
import threading
from pathlib import Path

import pytest

from src.memory.sage.graph_store import GraphStore
from src.memory.sage.models import Fact

ROOT = Path(__file__).resolve().parents[2]
RUN_SERVER = ROOT / "scripts" / "run_server.py"


@pytest.fixture
def make_store(tmp_path):
    """建立隔離 GraphStore 實例；teardown 全部 close（close 會 discard 註冊）。"""
    created: list[GraphStore] = []

    def _make(name: str = "store.db") -> GraphStore:
        s = GraphStore(tmp_path / name)
        created.append(s)
        return s

    yield _make
    for s in created:
        s.close()


def _visible_facts(store: GraphStore) -> list[tuple[str, str, str]]:
    """另一條獨立連線（不經 GraphStore）讀取已 commit 的 facts。"""
    ro = sqlite3.connect(str(store.db_path))
    try:
        return [
            tuple(r) for r in ro.execute(
                "SELECT subject, predicate, object FROM facts"
            ).fetchall()
        ]
    finally:
        ro.close()


# ───────────────────────────────────────────────────────────
# 1. 止損生效
# ───────────────────────────────────────────────────────────

def test_flush_all_live_makes_pending_visible(make_store):
    store = make_store("a.db")
    store.add_fact(Fact(subject="alice", predicate="knows", object="bob"))
    assert store._pending_writes == 1, "寫入後應有 1 筆 pending（未達 batch 不 commit）"

    n_live, n_flushed = GraphStore.flush_all_live()

    assert n_live == 1
    assert n_flushed == 1
    assert store._pending_writes == 0
    # 另一條獨立連線必須看得到（這是止損的本質：崩潰時不丟）
    assert ("alice", "knows", "bob") in _visible_facts(store)


# ───────────────────────────────────────────────────────────
# 2. idle 無害
# ───────────────────────────────────────────────────────────

def test_flush_all_live_idle_noop(make_store):
    store = make_store("idle.db")
    assert store._pending_writes == 0

    n_live, n_flushed = GraphStore.flush_all_live()

    assert n_live == 1
    assert n_flushed == 0
    assert store._pending_writes == 0
    # 重複掃描同樣穩定（無副作用）
    n_live2, n_flushed2 = GraphStore.flush_all_live()
    assert n_live2 == 1 and n_flushed2 == 0


# ───────────────────────────────────────────────────────────
# 3. 單一失敗不擴散
# ───────────────────────────────────────────────────────────

def test_flush_failure_isolated(make_store, monkeypatch):
    good = make_store("good.db")
    bad = make_store("bad.db")
    good.add_fact(Fact(subject="u", predicate="p", object="o1"))
    bad.add_fact(Fact(subject="u", predicate="p", object="o2"))

    def boom(self):  # noqa: ANN001 — instance 層遮蔽既有 flush
        raise RuntimeError("simulated flush failure")

    monkeypatch.setattr(bad, "flush", boom)

    # 掃描器不得向外拋
    n_live, n_flushed = GraphStore.flush_all_live()

    assert n_live == 2
    assert n_flushed == 1
    # 失敗的 bad 被跳過，good 仍被 flush 且資料可見
    assert ("u", "p", "o1") in _visible_facts(good)
    assert ("u", "p", "o2") not in _visible_facts(good)
    assert bad._pending_writes == 1  # bad 的 pending 原樣保留
    assert good._pending_writes == 0


# ───────────────────────────────────────────────────────────
# 4. 不實例化新 store
# ───────────────────────────────────────────────────────────

def test_flush_all_live_does_not_instantiate(make_store, monkeypatch):
    store = make_store("reg.db")
    store.add_fact(Fact(subject="s", predicate="p", object="o"))
    init_calls: list[int] = [0]

    orig_init = GraphStore.__init__

    def counting_init(self, *args, **kwargs):
        init_calls[0] += 1
        return orig_init(self, *args, **kwargs)

    monkeypatch.setattr(GraphStore, "__init__", counting_init)

    before = set(GraphStore._live_stores)
    n_live, n_flushed = GraphStore.flush_all_live()
    after = set(GraphStore._live_stores)

    assert init_calls[0] == 0, "掃描不得實例化任何新 store"
    assert before == after
    assert n_live == 1 and n_flushed == 1


# ───────────────────────────────────────────────────────────
# 5. 任務韌性（0.2s 短週期注入，不真的等 15 秒）
# ───────────────────────────────────────────────────────────

def test_flush_loop_survives_errors(make_store, monkeypatch):
    make_store("task.db")
    calls: dict[str, int] = {"n": 0}

    async def flaky_flush_all_live():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated scanner failure")
        return (0, 0)  # 任務韌性只測存活，不需真實 flush

    # classmethod → staticmethod 遮蔽，讓 async 版本可被直接 await
    monkeypatch.setattr(GraphStore, "flush_all_live", staticmethod(flaky_flush_all_live))

    # 與 run_server.py 的 _sage_flush_loop 同構：while True + try/except
    # 包 flush_all_live；例外只記錄不死亡；CancelledError 乾淨退出。
    async def loop():
        while True:
            try:
                await asyncio.sleep(0.2)
                await GraphStore.flush_all_live()
            except asyncio.CancelledError:
                break
            except Exception:
                pass  # 對應 run_server 的 logger.warning

    async def main():
        task = asyncio.create_task(loop())
        await asyncio.sleep(0.9)  # 約 4 輪
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(main())

    # 第一輪拋例外後任務仍存活並繼續跑（至少 3 輪 = 例外 1 次 + 恢復 2+ 次）
    assert calls["n"] >= 3, f"任務應存活多輪，實際 {calls['n']} 輪"


def test_run_server_flush_loop_is_guarded():
    """AST 斷言 run_server.py 的任務結構：while True 內 try/except 包 flush，
    週期 15.0，且任務只由 lifespan（啟動路徑）建立，module 層級 0 啟動。"""
    tree = ast.parse(RUN_SERVER.read_text(encoding="utf-8"))
    funcs = {
        n.name: n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    loop = funcs.get("_sage_flush_loop")
    assert loop is not None, "run_server.py 必須定義 _sage_flush_loop"

    # 週期常數存在
    src = RUN_SERVER.read_text(encoding="utf-8")
    assert "_SAGE_FLUSH_INTERVAL_SECS = 15.0" in src

    # while True 內有 try，handlers 同時含 CancelledError（乾淨退出）與
    # Exception（任務不可死亡）
    while_node = next(n for n in ast.walk(loop) if isinstance(n, ast.While))
    try_nodes = [n for n in ast.walk(while_node) if isinstance(n, ast.Try)]
    assert try_nodes, "_sage_flush_loop 的 while 內必須有 try/except"
    handler_names: set[str] = set()
    for h in try_nodes[0].handlers:
        if h.type is None:
            handler_names.add("bare")
        elif isinstance(h.type, ast.Name):
            handler_names.add(h.type.id)
        elif isinstance(h.type, ast.Attribute):
            handler_names.add(h.type.attr)
    assert "CancelledError" in handler_names
    assert "Exception" in handler_names

    # create_task 只能在函數內（lifespan 啟動路徑），module 層級 0 啟動
    module_level_calls: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                f = sub.func
                if (isinstance(f, ast.Name) and f.id == "create_task") or (
                    isinstance(f, ast.Attribute) and f.attr == "create_task"
                ):
                    module_level_calls.append(ast.unparse(sub))
    assert module_level_calls == [], (
        f"run_server.py module 層級不得啟動任務，發現: {module_level_calls}"
    )


# ───────────────────────────────────────────────────────────
# 6. import 0 副作用
# ───────────────────────────────────────────────────────────

def test_import_no_side_effects():
    before = {t.ident: t.name for t in threading.enumerate()}

    mod = importlib.import_module("src.memory.sage.graph_store")
    importlib.reload(mod)  # 重跑 module 級代碼

    after = {t.ident: t.name for t in threading.enumerate()}
    assert before == after, "import 不得啟動任何新 thread"

    # module 級代碼不得有 create_task / Thread / 週期 sleep
    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    forbidden: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                f = sub.func
                name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
                if name in ("create_task", "Thread", "sleep"):
                    forbidden.add(name)
    assert not forbidden, f"module 級代碼不得啟動任務/執行緒/迴圈: {forbidden}"


def test_run_server_module_level_no_task():
    """工單關鍵約束（VC 也 import src/memory/）：任務只在 run_server 啟動路徑
    顯式啟動，module 被 import 時 0 副作用。"""
    tree = ast.parse(RUN_SERVER.read_text(encoding="utf-8"))
    module_level_calls: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                f = sub.func
                if (isinstance(f, ast.Name) and f.id == "create_task") or (
                    isinstance(f, ast.Attribute) and f.attr == "create_task"
                ):
                    module_level_calls.append(ast.unparse(sub))
    assert module_level_calls == []


# ───────────────────────────────────────────────────────────
# 7. _BATCH_SIZE == 20 未被改動
# ───────────────────────────────────────────────────────────

def test_batch_size_unchanged(make_store):
    import src.memory.sage.graph_store as gs_mod

    store = make_store("bs.db")
    # _BATCH_SIZE 是 module 級常數（寫入點 batch 邏輯的依據）
    assert gs_mod._BATCH_SIZE == 20
    assert store.batch_size == 20  # __init__ 預設仍指向它
    assert type(store.batch_size) is int