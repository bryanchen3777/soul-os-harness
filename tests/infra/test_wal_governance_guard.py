# -*- coding: utf-8 -*-
"""FEAT-SQLITE-WAL-GOVERNANCE 守衛測試 — WAL 治理的結構與行為防護。

背景（本票 B1 段的實測結論，詳見 logs/FEAT-SQLITE-WAL-GOVERNANCE 報告）:
  SQLite 的 `-wal` 檔大小是**歷史高水位** —— PASSIVE checkpoint 只把頁面回寫
  進 DB 檔、**不縮短 `-wal` 檔**（實測 6,147,072 → 6,147,072 bytes, delta +0）。
  而本專案的 GraphStore 連線與 process 同壽命 ⇒「最後一條連線關閉時刪檔」
  永不發生 ⇒ 46 個 agent DB 的 `-wal` 永遠停在 4~6 MB 的殘留空間。
  ⇒ 治理手段必須是 `wal_checkpoint(TRUNCATE)`，且必須是**非阻塞**的。

本檔的 8 條守衛對應 9 條變異（**M1～M9**；其中 M2 與 M9 對應同一條 T2 接線守衛），
**每條變異必須轉紅**。

  T1 行為：checkpoint_wal() 真的把 `-wal` 檔縮到 0 bytes（TRUNCATE 語意）
  T2 接線：掛在**既有**排程器 30s wake；0 新定時器 / 0 新 task / 0 新 sleep
  T3 fail-silent：全例外攔截；且**不得**用 `@_locked`（會 rollback pending writes）
  T4 有界：每輪處理數量有上限（limit 參數 + 常數上限）
  T5 純度：0 LLM / 0 網路 / 0 新 schema / 0 新持久化 / 0 新檔案
  T6 非阻塞：busy_timeout 暫時調低並還原（拿不到鎖就快速放棄）
  T7 只掃既有實例：絕不因掃描而實例化新 store；單一失敗不中斷其他
  T8 回收真的發生：對「有未 checkpoint 內容」的 WAL 截斷後檔案歸零

邊界（工單 §2/§5）:
  * 0 subprocess / 0 os.spawn* / 0 os.system / 0 os.popen
  * 0 生產資料：全部寫在 tmp_path
  * 0 啟動伺服器 / 0 綁生產埠 / 0 對生產 DB 做 checkpoint
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.memory.sage.graph_store import GraphStore  # noqa: E402
from src.memory.sage.models import Fact  # noqa: E402

GRAPH_STORE_PY = REPO_ROOT / "src" / "memory" / "sage" / "graph_store.py"
SCHEDULER_PY = REPO_ROOT / "src" / "soul" / "scheduler.py"


# ── helpers ──────────────────────────────────────────────────────────

def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _tree(path: Path) -> ast.Module:
    return ast.parse(_src(path))


def _find_method(tree: ast.Module, cls_name: str, meth_name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name == meth_name:
                        return item
    raise AssertionError(f"class {cls_name} has no method {meth_name}")


def _method_source(path: Path, cls_name: str, meth_name: str) -> str:
    tree = _tree(path)
    fn = _find_method(tree, cls_name, meth_name)
    lines = _src(path).splitlines()
    return "\n".join(lines[fn.lineno - 1: fn.end_lineno])


def _decorator_names(fn: ast.FunctionDef) -> list[str]:
    names = []
    for dec in fn.decorator_list:
        if isinstance(dec, ast.Name):
            names.append(dec.id)
        elif isinstance(dec, ast.Attribute):
            names.append(dec.attr)
        elif isinstance(dec, ast.Call):
            names.append(getattr(dec.func, "id", getattr(dec.func, "attr", "?")))
    return names


def _string_literal(node: ast.AST) -> str | None:
    """取回字串常量的內容（含 `"..." % x` 這種 BinOp 形式）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod)
            and isinstance(node.left, ast.Constant)
            and isinstance(node.left.value, str)):
        return node.left.value
    return None


def _has_broad_except(fn: ast.FunctionDef) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                return True
            if isinstance(node.type, ast.Name) and node.type.id in ("Exception", "BaseException"):
                return True
    return False


def _fact(fid: str) -> Fact:
    return Fact(
        fact_id=fid,
        subject="s",
        predicate="p",
        object="o",
        timestamp=1000.0,
        weight=1.0,
        source="test",
        session_id="s",
    )


def _wal_path(db: Path) -> Path:
    return db.parent / (db.name + "-wal")


def _wal_size(db: Path) -> int:
    p = _wal_path(db)
    return p.stat().st_size if p.exists() else 0


@pytest.fixture()
def isolated_live_stores():
    """把 GraphStore._live_stores 換成空集合（測試結束後還原）。"""
    original = GraphStore._live_stores
    import weakref
    GraphStore._live_stores = weakref.WeakSet()
    yield GraphStore._live_stores
    GraphStore._live_stores = original


# ── T1 行為：TRUNCATE 真的把 -wal 縮到 0 ────────────────────────────

def test_T1_checkpoint_wal_truncates_wal_file(tmp_path):
    """checkpoint_wal() 必須把 `-wal` 檔縮到 0 bytes（PASSIVE 做不到這件事）。"""
    db = tmp_path / "graph.sqlite"
    store = GraphStore(db_path=db)
    try:
        for i in range(25):
            store.add_fact(_fact("f%02d" % i))
        store.flush()
        before = _wal_size(db)
        assert before > 0, "寫入後 -wal 應有內容（TRUNCATE 才有東西可縮）"

        busy, log, ckpt = store.checkpoint_wal()

        after = _wal_size(db)
        assert busy == 0, f"busy 應為 0（無阻擋者），實得 {busy}"
        assert after == 0, f"-wal 應被截斷為 0 bytes，實得 {after}（before={before}）"
        assert isinstance(log, int) and isinstance(ckpt, int)
    finally:
        store.close()


# ── T2 接線：既有排程器 30s wake / 0 新定時器 ──────────────────────────

def test_T2_checkpoint_is_wired_into_existing_scheduler_wake():
    """必須掛在既有 30s wake（`Scheduler._run_loop`）的 additive 分支。

    0 新定時器 / 0 新 tick / 0 新 task / 0 新 sleep：治理只能作為既有
    `_run_loop`（`await asyncio.sleep(30)`）上的一個 additive 分支存在。
    """
    src = _src(SCHEDULER_PY)
    tree = _tree(SCHEDULER_PY)

    assert "checkpoint_all_live(" in src, \
        "scheduler.py 必須呼叫 GraphStore.checkpoint_all_live()"

    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and getattr(n.func, "attr", "") == "checkpoint_all_live"
    ]
    assert len(calls) == 1, \
        f"scheduler.py 內應恰有 1 個 checkpoint_all_live() 呼叫，實得 {len(calls)}"

    # 呼叫點必須在 Scheduler 類別的某個方法內
    sched_cls = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "SoulScheduler":
            sched_cls = node
            break
    assert sched_cls is not None, "找不到 SoulScheduler 類別"
    meth_names = {
        item.name
        for item in sched_cls.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    enclosing = None
    for item in sched_cls.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(c in ast.walk(item) for c in calls):
                enclosing = item.name
                break
    assert enclosing is not None, "checkpoint_all_live() 必須在某個 Scheduler 方法內"
    assert enclosing in meth_names

    # 該方法必須被既有的 wake 迴圈呼叫（await self.<name>(...)）
    assert re.search(r"await\s+self\.%s\(" % re.escape(enclosing), src), \
        f"{enclosing}() 必須被既有 wake 迴圈 await（0 新定時器）"

    # 不得為 WAL checkpoint 新增 background task
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fname = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if fname in ("create_task", "ensure_future"):
                seg = (ast.get_source_segment(src, node) or "").lower()
                assert "checkpoint" not in seg and "_wal" not in seg, \
                    "不得為 WAL checkpoint 新增 background task"

    # 既有 30s 節奏不得被更動
    assert "await asyncio.sleep(30)" in src, "既有 30s wake 不得被更動"


# ── T3 fail-silent / 不得用 @_locked ────────────────────────────────

def test_T3_checkpoint_is_fail_silent_and_not_lock_decorated():
    """兩個方法都要全例外攔截；checkpoint_wal 不得掛 @_locked。"""
    tree = _tree(GRAPH_STORE_PY)

    ck = _find_method(tree, "GraphStore", "checkpoint_wal")
    assert "_locked" not in _decorator_names(ck), \
        "checkpoint_wal 不得掛 @_locked —— 該裝飾器的鎖錯誤路徑會 rollback " \
        "尚未 commit 的 pending writes（資料遺失）"
    assert _has_broad_except(ck), "checkpoint_wal 必須有寬鬆 except 攔截（fail-silent）"

    all_live = _find_method(tree, "GraphStore", "checkpoint_all_live")
    assert _has_broad_except(all_live), \
        "checkpoint_all_live 必須有寬鬆 except 攔截（單一失敗不中斷其他）"


# ── T4 有界 ─────────────────────────────────────────────────────────

def test_T4_checkpoint_all_live_is_bounded():
    """每輪處理數量必須有上限（limit 參數 + 常數上限）。"""
    tree = _tree(GRAPH_STORE_PY)
    fn = _find_method(tree, "GraphStore", "checkpoint_all_live")

    args = fn.args
    assert args.kwonlyargs or args.args, "checkpoint_all_live 必須宣告參數"
    names = [a.arg for a in (args.args + args.kwonlyargs)]
    assert "limit" in names, "checkpoint_all_live 必須有 limit 參數（有界）"

    src = _src(GRAPH_STORE_PY)
    assert "_WAL_CKPT_LIMIT_PER_ROUND" in src, "limit 預設值必須是具名常數"
    line = [ln for ln in src.splitlines()
            if ln.strip().startswith("_WAL_CKPT_LIMIT_PER_ROUND")]
    assert line, "找不到 _WAL_CKPT_LIMIT_PER_ROUND 定義"
    value = int(line[0].split("=")[1].split("#")[0].strip())
    assert 0 < value <= 32, f"每輪上限必須在 1..32，實得 {value}"

    # 預設值確實綁到該常數
    assert "_WAL_CKPT_LIMIT_PER_ROUND" in ast.unparse(fn)


# ── T5 純度：0 LLM / 0 網路 / 0 新 schema / 0 新持久化 ─────────────

def test_T5_checkpoint_code_is_side_effect_free():
    """新增程式碼不得含 LLM / 網路 / DDL / 檔案寫入 / 外部呼叫。"""
    pieces = [
        _method_source(GRAPH_STORE_PY, "GraphStore", "checkpoint_wal"),
        _method_source(GRAPH_STORE_PY, "GraphStore", "checkpoint_all_live"),
    ]
    forbidden = [
        "requests", "httpx", "urllib", "aiohttp", "socket", "http.client",
        "openai", "anthropic", "deepseek", "minimax", "llm_call",
        "CREATE TABLE", "INSERT INTO", "UPDATE ", "DELETE FROM",
        "DROP TABLE", "ALTER TABLE",
        "open(", "write_text", "write_bytes", "os.remove", "os.unlink",
        "shutil", "subprocess", "os.system", "os.popen", "eval(", "exec(",
        "sleep", "create_task", "ensure_future",
    ]
    for piece in pieces:
        for token in forbidden:
            assert token not in piece, f"治理程式碼不得出現 {token!r}"


# ── T6 非阻塞：busy_timeout 暫時調低並還原 ──────────────────────────

def test_T6_checkpoint_lowers_and_restores_busy_timeout():
    """checkpoint 必須暫時調低 busy_timeout（快速放棄）並在結束前還原。

    以 AST 數「`PRAGMA busy_timeout=` 指派」的**呼叫點**數量：
      取得（`PRAGMA busy_timeout`，無 `=`）不算；設定與還原各算 1 ⇒ 必須 ≥ 2。
    """
    tree = _tree(GRAPH_STORE_PY)
    fn = _find_method(tree, "GraphStore", "checkpoint_wal")
    assignments = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            args = list(node.args) + [kw.value for kw in node.keywords]
            for arg in args:
                text = _string_literal(arg)
                if text and text.startswith("PRAGMA busy_timeout="):
                    assignments.append(text)
    assert len(assignments) >= 2, (
        "busy_timeout 必須「暫時調低 + 還原」兩個指派點"
        f"（實得 {len(assignments)} 個）—— 只調不還會改變既有連線契約"
    )


# ── T7 只掃既有實例 / 單一失敗不中斷 ────────────────────────────────

def test_T7_checkpoint_all_live_isolates_failures(isolated_live_stores, tmp_path):
    """單一 store 失敗不得中斷其他 store；且絕不因掃描而實例化新 store。"""
    good = GraphStore(db_path=tmp_path / "a.sqlite")
    bad = GraphStore(db_path=tmp_path / "b.sqlite")
    n_live_before = len(GraphStore._live_stores)

    def boom():
        raise RuntimeError("boom")

    bad.checkpoint_wal = boom  # type: ignore[method-assign]

    stats = GraphStore.checkpoint_all_live(limit=8)

    assert stats["n_scanned"] == n_live_before, \
        f"應處理全部 {n_live_before} 個 live store，實得 {stats['n_scanned']}"
    assert stats["n_err"] == 1, f"壞掉的 store 應被計為 1 個錯誤，實得 {stats['n_err']}"
    assert stats["n_truncated"] >= 1, "健康的 store 仍應被成功 checkpoint"
    # 掃描不得新增實例
    assert len(GraphStore._live_stores) == n_live_before
    good.close()
    bad.close()


# ── T8 回收真的發生：未 checkpoint 的內容被截斷 ─────────────────────

def test_T8_truncate_reclaims_uncheckpointed_wal(tmp_path):
    """有未 checkpoint 內容時，TRUNCATE 後 -wal 檔必須歸零。"""
    db = tmp_path / "graph.sqlite"
    store = GraphStore(db_path=db)
    try:
        for i in range(60):
            store.add_fact(_fact("g%02d" % i))
        store.flush()
        assert _wal_size(db) > 0
        busy, _log, _ckpt = store.checkpoint_wal()
        assert busy == 0
        assert _wal_size(db) == 0
    finally:
        store.close()
