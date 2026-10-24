# -*- coding: utf-8 -*-
"""GOAL-DB-LOCKED-1 守衛測試 — Goal 主循環 DB 鎖死事故的回歸防護。

事故背景（logs/INCIDENT-GOAL-DB-LOCKED.md）:
  2026-10-23 14:31:32 起 600 筆 `[Goal] 主循环扫描异常 (fail-closed):
  OperationalError: database is locked`（14:31→19:32，每 30 秒 2 筆，302/302
  分鐘桶無例外）⇒ Goal 主循環 100% 停擺。

本檔的 5 條守衛各自對應一條變異（M1～M5），每條變異必須轉紅。

  T1 per-agent 故障隔離：一個 agent 撞鎖不得吃掉其他 agent 的掃描
  T2 可診斷性：告警必須帶 agent_id 與 traceback
  T3 flush 失敗必須 rollback（不得留著寫交易 ⇒ 不得永久持有寫鎖）
  T4 鎖錯誤自癒：重建連線後重試一次
  T5 等待預算顯式化：busy_timeout 不倚賴直譯器預設值

邊界（工單 §4）:
  * 0 subprocess / 0 os.spawn* / 0 os.system / 0 os.popen
  * 0 生產資料：全部寫在 tmp_path
  * 0 啟動伺服器 / 0 綁生產埠
"""
from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.memory.sage.graph_store import GraphStore  # noqa: E402
from src.memory.sage.models import Fact  # noqa: E402


def _fact(fid: str = "f1") -> Fact:
    return Fact(
        fact_id=fid,
        subject="a",
        predicate="p",
        object="b",
        timestamp=1000.0,
        weight=1.0,
        source="test",
        session_id="s",
    )


# ── T1: per-agent 故障隔離 ───────────────────────────────────────────

class _StubProvider:
    """GoalMotiveProvider / GoalSeedProvider 的最小替身。"""

    def __init__(self, agent_id: str, boom: set[str]) -> None:
        self.agent_id = agent_id
        self._boom = boom

    def _maybe_raise(self) -> None:
        if self.agent_id in self._boom:
            raise sqlite3.OperationalError("database is locked")

    # motive_provider 介面
    def apply_interrupt_signals(self, now=None):
        self._maybe_raise()
        return []

    def scheduled_wakeup_scan(self, now=None):
        self._maybe_raise()
        return []

    # seed_provider 介面
    async def scan_seeds(self, now=None):
        self._maybe_raise()
        return []

    def _load_state(self):
        return None

    def _save_state(self, state):
        return None


@pytest.mark.asyncio
async def test_T1_goal_scan_isolates_a_locked_agent(monkeypatch):
    """一個 agent 撞 "database is locked" 時，其餘 agent 的掃描必須繼續。

    事故前的實作：try/except 包在整個 for 迴圈之外 ⇒ 第一個撞鎖的 agent
    就讓 **全部 46 個** agent 的掃描中止（單點故障放大成 100% 停擺）。
    """
    import src.goals.motive_provider as mp
    import src.goals.seed_provider as sp
    import src.social.relation_settlement as rs
    from src.soul.scheduler import SoulScheduler

    boom = {"agent_b"}
    visited: list[str] = []

    def _for_agent(agent_id):
        visited.append(agent_id)
        return _StubProvider(agent_id, boom)

    monkeypatch.setattr(mp.GoalMotiveProvider, "for_agent", staticmethod(_for_agent))
    monkeypatch.setattr(sp.GoalSeedProvider, "for_agent", staticmethod(_for_agent))
    monkeypatch.setattr(rs, "settle_relations", lambda *a, **kw: {})

    class _StubScheduler:
        _all_agents = ["agent_a", "agent_b", "agent_c"]

    stub = _StubScheduler()
    await SoulScheduler._goal_scan_all(stub)

    # agent_b 撞鎖後，agent_c 仍必須被掃描到
    assert "agent_c" in visited, (
        f"agent_b 撞鎖後 agent_c 未被掃描 ⇒ 故障隔離失效；visited={visited}"
    )
    first_visits = [v for i, v in enumerate(visited) if i == 0 or visited[i - 1] != v]
    assert first_visits == ["agent_a", "agent_b", "agent_c"], (
        f"掃描順序或隔離行為不符；first_visits={first_visits}"
    )


# ── T2: 可診斷性 ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_T2_goal_scan_warning_carries_agent_and_traceback(monkeypatch, caplog):
    """告警必須帶 agent_id 與 traceback。

    事故期間的日誌既無 agent_id 也無 traceback ⇒ 600 筆告警無法定位到任何
    agent、任何行號（logs/INCIDENT-GOAL-DB-LOCKED.md §3）。
    """
    import src.goals.motive_provider as mp
    import src.goals.seed_provider as sp
    import src.social.relation_settlement as rs
    from src.soul.scheduler import SoulScheduler

    boom = {"agent_b"}

    def _for_agent(agent_id):
        return _StubProvider(agent_id, boom)

    monkeypatch.setattr(mp.GoalMotiveProvider, "for_agent", staticmethod(_for_agent))
    monkeypatch.setattr(sp.GoalSeedProvider, "for_agent", staticmethod(_for_agent))
    monkeypatch.setattr(rs, "settle_relations", lambda *a, **kw: {})

    class _StubScheduler:
        _all_agents = ["agent_a", "agent_b"]

    with caplog.at_level(logging.WARNING, logger="soul_os.soul.scheduler"):
        await SoulScheduler._goal_scan_all(_StubScheduler())

    goal_warnings = [
        r for r in caplog.records
        if "[Goal]" in r.getMessage() and "主循环扫描异常" in r.getMessage()
    ]
    assert goal_warnings, "沒有任何 [Goal] 告警被記錄下來"
    rec = goal_warnings[-1]
    assert "agent=agent_b" in rec.getMessage(), (
        f"告警缺少 agent_id，無法定位：{rec.getMessage()}"
    )
    assert rec.exc_info is not None, (
        "告警缺少 traceback（exc_info），無法定位到失敗行號"
    )


# ── T3: flush 失敗必須 rollback ──────────────────────────────────────

class _CommitRaisesConn:
    """commit() 拋鎖錯誤的連線替身；記錄 rollback 是否被呼叫。"""

    def __init__(self, inner: sqlite3.Connection) -> None:
        self._inner = inner
        self.rolled_back = False
        self.closed = False

    def commit(self):
        raise sqlite3.OperationalError("database is locked")

    def rollback(self):
        self.rolled_back = True
        return self._inner.rollback()

    def close(self):
        self.closed = True
        return self._inner.close()

    def execute(self, *a, **kw):
        return self._inner.execute(*a, **kw)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_T3_flush_rolls_back_when_commit_fails(tmp_path):
    """commit 失敗 ⇒ 必須 rollback 且 pending 歸零（否則寫鎖永久被持有）。

    事故鏈: flush() 的 commit 拋 "database is locked" 時舊實作讓例外直接外冒，
    `_pending_writes` 仍 > 0 ⇒ 寫交易永遠開啟 ⇒ 該 agent 的 graph.sqlite 寫鎖
    被永久持有 ⇒ 任何其他連線都寫不進去 ⇒ 100% 停擺。
    """
    store = GraphStore(db_path=tmp_path / "g.sqlite")
    store.add_fact(_fact())
    assert store._pending_writes == 1, "前置條件：應有 1 筆 pending"

    poisoned = _CommitRaisesConn(store._conn)
    store._conn = poisoned

    with pytest.raises(sqlite3.OperationalError):
        store.flush()

    assert poisoned.rolled_back is True, (
        "commit 失敗後沒有 rollback ⇒ 寫交易仍開啟 ⇒ 寫鎖被永久持有"
    )
    assert store._pending_writes == 0, (
        "commit 失敗後 _pending_writes 未歸零 ⇒ flush 迴圈會反覆假裝有 pending"
    )


# ── T4: 鎖錯誤自癒 ───────────────────────────────────────────────────

class _LockOnFirstExecute:
    """第一次 execute 拋鎖錯誤、之後轉發給真實連線的替身。"""

    def __init__(self, inner: sqlite3.Connection) -> None:
        self._inner = inner
        self.calls = 0

    def execute(self, *a, **kw):
        self.calls += 1
        if self.calls == 1:
            raise sqlite3.OperationalError("database is locked")
        return self._inner.execute(*a, **kw)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_T4_lock_error_rebuilds_connection_and_retries(tmp_path):
    """鎖錯誤 ⇒ 重建連線後重試一次（不是把錯誤原樣往外丟）。"""
    store = GraphStore(db_path=tmp_path / "g.sqlite")
    original = store._conn
    poisoned = _LockOnFirstExecute(original)
    store._conn = poisoned

    goals = store.get_goals("agent_x")  # 第一次 execute 拋鎖錯誤

    assert goals == [], "重試後的讀取結果應為空清單（此 DB 無 goal）"
    assert store._conn is not original, (
        "鎖錯誤後沒有重建連線 ⇒ 同一條壞掉的連線會讓後續每次存取都拋鎖錯誤"
    )


def test_T4b_non_lock_error_is_not_swallowed(tmp_path):
    """非鎖錯誤必須照舊上拋（自癒不得變成吞例外）。"""
    store = GraphStore(db_path=tmp_path / "g.sqlite")

    class _Boom:
        def execute(self, *a, **kw):
            raise sqlite3.OperationalError("no such table: nope")

        def __getattr__(self, name):
            return getattr(store._conn, name)

    real = store._conn
    store._conn = _Boom()
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        store.get_goals("agent_x")
    store._conn = real


# ── T5: 等待預算顯式化 ───────────────────────────────────────────────

def _explicit_busy_timeout_calls(source_path: Path) -> list[int]:
    """從原始碼 AST 找出 **顯式** 發出的 `PRAGMA busy_timeout=<n>`。

    為什麼不能用「查 PRAGMA busy_timeout 的值」當守衛：sqlite3.connect(timeout=X)
    **本來就會**把 busy_timeout 設成 X*1000 ⇒ 值斷言在拿掉顯式 PRAGMA 之後
    仍然通過 ⇒ 守衛無牙（變異 M5/M6 實測 NOT RED）。
    契約的真正內容是「等待預算被 **寫成** 一條明確的 PRAGMA，而不是倚賴直譯器
    預設」⇒ 守衛必須檢查原始碼有沒有那條陳述。

    來源檔錨定在 **本測試檔所屬的 source tree**（parents[2]/src/...），
    而不是 `inspect.getsourcefile()`：後者取決於 import 解析結果，
    當環境中另有同名 `src` 套件優先時會讀到別的樹，守衛就會失真。
    """
    import ast

    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    found: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "execute"):
            continue
        if not node.args:
            continue
        arg = node.args[0]
        if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
            continue
        text = arg.value.strip().lower().replace(" ", "")
        # 🔴 樣式必須與 text 用**同一種**正規化（否則「pragma busy_timeout=」的空格
        #    會被 text 移除、比對卻帶空格 ⇒ 恆不匹配 ⇒ 這個守衛會恆紅，
        #    進而讓「拿掉 PRAGMA」的變異 M5/M6 呈現**假陽性**）。
        if not text.startswith("pragma busy_timeout=".replace(" ", "")):
            continue
        found.append(int(text.split("=", 1)[1]))
    return found


def _source_tree(relative: str) -> Path:
    """本測試所屬 source tree 下的檔案（src/memory/sage/graph_store.py 等）。"""
    return REPO_ROOT / relative


def test_T5_graph_store_sets_explicit_busy_timeout(tmp_path):
    """GraphStore 必須 **顯式** 發出 busy_timeout PRAGMA（值 10000 ms）。

    （timeout=10.0 這個契約值本身不動；只是把它寫成顯式 PRAGMA。）
    """
    src = _source_tree("src/memory/sage/graph_store.py")
    found = _explicit_busy_timeout_calls(src)
    assert 10000 in found, (
        f"GraphStore 未顯式設定 busy_timeout=10000（實得 {found}）"
        f"；等待預算不可倚賴 connect(timeout=) 的隱含預設；src={src}"
    )


def test_T5b_memory_store_sets_explicit_busy_timeout(tmp_path):
    """MemoryStore 必須 **顯式** 發出 busy_timeout PRAGMA（值 5000 ms）。

    （等值於 Python 預設，但契約必須寫下來 —— 與 src/agent/emotion.py:370 同款。）
    """
    src = _source_tree("src/memory/store.py")
    found = _explicit_busy_timeout_calls(src)
    assert 5000 in found, (
        f"MemoryStore 未顯式設定 busy_timeout=5000（實得 {found}）"
        f"；等待預算不可倚賴 connect(timeout=) 的隱含預設"
    )
