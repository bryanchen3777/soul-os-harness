# -*- coding: utf-8 -*-
"""FEAT-GRAPHSTORE-WRITE-CONTENTION 守衛測試 — 行程內寫鎖競爭（8 筆 W1～W8）。

背景（2026-10-24 10:36 實測，詳見 logs/FEAT-GRAPHSTORE-WRITE-CONTENTION-REPORT.md）:
  同一個 `data/memory/<agent>/graph.sqlite` 在**同一個行程內**會被多個
  `GraphStore` 實例開啟（生產: 48 個 live 實例 / 50 個 agent DB 路徑）。
  未滿批的寫交易（`_pending_writes < batch_size` ⇒ 不 commit）會**持續持有該 DB
  的寫鎖** ⇒ 另一條連線寫同一個 DB 會一路等到 `busy_timeout`(10s) 耗盡 ⇒
  `database is locked`。生產實測: 寫鎖被**連續持有 ≥20.7 秒**。

本檔的 8 筆守衛各自對應一條變異（P1～P7），每條變異必須轉紅。

  W1 沙盒重現（機制敏感度）: 關掉仲裁後，兩個實例同 DB 必須重現 busy/locked
  W2 同實例不變: `@_locked` 仍把同一實例內的寫入串行化
  W3 修正有效: 開啟仲裁後，同一情境**不得**再出現 database is locked
  W4 有界等待 ＋ fail-closed: 等待上界 < busy_timeout；取不到就放棄（不排隊）
  W5 共享生命週期: 一個元件 close() 不得關掉別元件仍在用的連線
  W6 既有契約未動: busy_timeout 仍 10000；flush() rollback 語意仍在；schema 未變
  W7 fail-silent: 寫入失敗不得冒泡成排程器主循環中斷
  W8 0 新副作用: 不得新增定時器／背景 task／額外連線

🔺 **W1 與 W3 為什麼是同一個情境的兩面**:
  工單 §3 的 W1 要求「兩個實例同 DB ⇒ 必須觀察到 busy/locked」，W3 要求
  「套用修正 ⇒ 不再出現 database is locked」。兩者若都用「開著修正的
  GraphStore 層」來斷言，會**互相矛盾**（讓 B 寫成功正是修正的目的 ⇒ W1 必然
  轉紅）。因此本檔把 **W1 的仲裁關掉**（monkeypatch `_arbiter_prepare_write`
  為 no-op，等同修正前的舊機制）來斷言「機制本身仍然可重現」（守衛有牙），
  再由 **W3 開著仲裁**斷言「修正有效」。兩者合起來才是完整證據鏈。

邊界（工單 §5）:
  * 0 subprocess / 0 os.spawn* / 0 os.system / 0 os.popen
  * 0 生產資料：全部寫在 tmp_path
  * 0 啟動伺服器 / 0 綁生產埠 / 0 真實 LLM / 0 網路
"""
from __future__ import annotations

import logging
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.memory.sage import graph_store as gs  # noqa: E402
from src.memory.sage.graph_store import (  # noqa: E402
    GraphStore,
    GraphStoreWriteBusy,
    _SCHEMA_VERSION,
)
from src.memory.sage.models import Fact  # noqa: E402

#: busy_timeout 的明文契約（毫秒）。不得調高。
BUSY_TIMEOUT_MS = 10000
BUSY_TIMEOUT_SECS = BUSY_TIMEOUT_MS / 1000.0


def _fact(fid: str = "f1", obj: str = "b") -> Fact:
    return Fact(
        fact_id=fid,
        subject="a",
        predicate="p",
        object=obj,
        timestamp=1000.0,
        weight=1.0,
        source="test",
        session_id="s",
    )


def _two_stores(db_path: Path):
    """同一 DB 路徑上的兩個實例（模擬 SAGE provider store ＋ goal 掃描 store）。"""
    a = GraphStore(db_path=db_path)
    b = GraphStore(db_path=db_path)
    return a, b


# ── W1: 沙盒重現（機制敏感度）────────────────────────────────────────

def test_W1_sandbox_reproduces_busy_locked_when_arbitration_off(tmp_path, monkeypatch, caplog):
    """關掉仲裁 ⇒ 兩個實例同 DB 必須重現 busy/locked（否則守衛本身失效）。

    同時量測**持鎖時長**: 持鎖者未滿批（`_pending_writes < batch_size`）⇒
    寫交易不 commit ⇒ 寫鎖持續被持有 ⇒ 等方必須一路耗盡 busy_timeout 才爆。
    這正是生產 10:36 的「鎖撐過 10 秒才浮現」特徵。
    """
    db_path = tmp_path / "graph.sqlite"
    a, b = _two_stores(db_path)
    try:
        # 等同修正前的舊機制：兩個實例各自為政、沒有寫入仲裁。
        monkeypatch.setattr(GraphStore, "_arbiter_prepare_write", lambda self: None)

        for i in range(3):            # 3 < batch_size(20) ⇒ 不 commit ⇒ 寫鎖被持有
            a.add_fact(_fact(f"hold_{i}"))
        assert a._pending_writes == 3, "前置條件: 應有未滿批的 pending 寫交易"

        t_wall = time.time()
        t0 = time.perf_counter()
        with caplog.at_level(logging.WARNING, logger="soul_os.sage.graph_store"):
            with pytest.raises(sqlite3.OperationalError) as excinfo:
                b.add_fact(_fact("waiter"))
        elapsed = time.perf_counter() - t0

        assert "locked" in str(excinfo.value).lower(), (
            f"應重現 database is locked，實得 {excinfo.value!r}"
        )

        # 持鎖時長: 自癒重試前的第一次失敗必須「幾乎等於一個 busy_timeout 視窗」
        # ⇒ 證明重現了「鎖撐過 10 秒才浮現」這個關鍵特徵。
        # 🔴 這個量測刻意 **不** 依賴 busy_timeout 契約值（那是 W6 的守衛）:
        #    只斷言「第一次失敗落在 0.5×~45 秒之間」⇒ P5（調高 busy_timeout）
        #    不會連帶打紅本守衛 ⇒ 兩條守衛互不污染。
        heals = [r for r in caplog.records if "dropped_pending=" in r.getMessage()]
        # 第一次失敗的延遲 ＝ **持鎖時長**（等方被阻塞了多久）。
        # 🔴 不可依賴「自癒日誌一定存在」：自癒是 `@_locked` 裝飾器的一部分，
        #    若該裝飾器被拿掉（變異 P7）自癒就不會發生 ⇒ 硬要自癒日誌會讓
        #    P7 連帶打紅本守衛 ⇒ **弱變異**。故：優先用自癒時間戳，沒有就退回
        #    「例外逃逸的總耗時」（同樣代表等方被阻塞的時長）。
        if heals:
            first_fail_delay = heals[0].created - t_wall
        else:
            first_fail_delay = elapsed
        assert first_fail_delay >= BUSY_TIMEOUT_SECS * 0.5, (
            f"持鎖時長 {first_fail_delay:.3f}s 遠小於 busy_timeout"
            f"({BUSY_TIMEOUT_SECS}s) ⇒ 未重現『鎖撐過 10 秒』的關鍵特徵"
        )
        assert first_fail_delay <= 45.0, (
            f"持鎖時長 {first_fail_delay:.3f}s 超出預期量級"
        )
    finally:
        for s in (a, b):
            try:
                s.close()
            except Exception:
                pass


# ── W2: 同實例不變 ────────────────────────────────────────────────

def test_W2_same_instance_writes_stay_serialised(tmp_path, monkeypatch):
    """同一實例內的寫入仍被 `@_locked` 串行化（KI-008 語意未破）。

    偵測方式: 在 networkx 雙寫點插一個「進入/離開」計數器，
    兩執行緒若**重疊** ⇒ 序列化失效。實作無關（不依賴任何內部欄位）。
    """
    db_path = tmp_path / "graph.sqlite"
    store = GraphStore(db_path=db_path)
    try:
        state = {"cur": 0, "max": 0}
        guard = threading.Lock()
        original = GraphStore._add_to_graph

        def _probe(self, fact):
            with guard:
                state["cur"] += 1
                state["max"] = max(state["max"], state["cur"])
            try:
                time.sleep(0.05)      # 放大重疊窗口
                return original(self, fact)
            finally:
                with guard:
                    state["cur"] -= 1

        monkeypatch.setattr(GraphStore, "_add_to_graph", _probe)

        errors: list[Exception] = []

        def _writer(i: int) -> None:
            try:
                store.add_fact(_fact(f"w{i}"))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=_writer, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"併發寫入拋例外: {errors[:3]}"
        assert state["max"] == 1, (
            f"同一實例內的寫入重疊（max concurrent={state['max']}）"
            f"⇒ @_locked 序列化失效"
        )
    finally:
        store.close()


# ── W3: 修正有效 ──────────────────────────────────────────────────

def test_W3_fix_removes_lock_contention(tmp_path):
    """套用修正 ⇒ 同一情境**不得**再出現 database is locked。

    同一個情境：實例 A 持未滿批寫交易（寫鎖被持有），實例 B 寫入。
    修正後 B 應在**遠小於 busy_timeout** 的時間內完成（不撞鎖、不重試）。
    """
    db_path = tmp_path / "graph.sqlite"
    a, b = _two_stores(db_path)
    try:
        for i in range(3):
            a.add_fact(_fact(f"hold_{i}"))
        assert a._pending_writes == 3, "前置條件: A 應持未滿批寫交易"

        t0 = time.perf_counter()
        b.add_fact(_fact("waiter"))
        elapsed = time.perf_counter() - t0

        assert elapsed < BUSY_TIMEOUT_SECS, (
            f"寫入耗時 {elapsed:.3f}s ≥ busy_timeout({BUSY_TIMEOUT_SECS}s) "
            f"⇒ 仍在撞 sqlite 的 busy 等待"
        )
        assert elapsed < 2.0, (
            f"寫入耗時 {elapsed:.3f}s ≥ 2.0s ⇒ 寫鎖沒有被即時釋放"
        )
    finally:
        for s in (a, b):
            try:
                s.close()
            except Exception:
                pass


# ── W4: 有界等待 ＋ fail-closed ───────────────────────────────────

def test_W4_wait_is_bounded_and_fails_closed(tmp_path, monkeypatch):
    """等待必須**有界**（< busy_timeout）且取不到時 **fail-closed**（不排隊）。

    情境: 持鎖者（A）的鎖被另一個執行緒佔住 ⇒ 仲裁請不到它 commit。
    期望: 競爭者（B）在**遠小於 busy_timeout** 的時間內**放棄**並丟出
    可處理例外（不是等 10 秒、不是 "database is locked"、不是排隊）。
    """
    assert gs._PATH_ARBITER_WAIT_SECS < BUSY_TIMEOUT_SECS, (
        f"仲裁等待上界 {gs._PATH_ARBITER_WAIT_SECS}s 必須 < busy_timeout "
        f"({BUSY_TIMEOUT_SECS}s) ⇒ 否則 30s tick 會疊加"
    )

    db_path = tmp_path / "graph.sqlite"
    a, b = _two_stores(db_path)
    holder_release = threading.Event()
    try:
        a.add_fact(_fact("hold"))
        assert a._pending_writes == 1, "前置條件: A 應持未滿批寫交易"

        # 佔住 A 的鎖（模擬「持鎖者很忙」）
        def _hog() -> None:
            a._lock.acquire()
            try:
                holder_release.wait(timeout=60)
            finally:
                a._lock.release()

        hog = threading.Thread(target=_hog, daemon=True)
        hog.start()
        time.sleep(0.1)

        outcome: dict[str, object] = {}

        def _contend() -> None:
            t0 = time.perf_counter()
            try:
                # 🔴 直接呼叫仲裁原語（不經由寫入路徑）⇒ 本守衛只測「仲裁本身
                #    的有界＋fail-closed」⇒ P1（拿掉寫入路徑的仲裁）不會打紅本檔。
                a._release_write_lock_for(b)
                outcome["exc"] = None
            except BaseException as exc:  # noqa: BLE001
                outcome["exc"] = exc
            outcome["elapsed"] = time.perf_counter() - t0

        worker = threading.Thread(target=_contend, daemon=True)
        worker.start()
        worker.join(timeout=BUSY_TIMEOUT_SECS + 5)

        assert not worker.is_alive(), (
            f"競爭者超過 {BUSY_TIMEOUT_SECS + 5}s 仍未結束 ⇒ 等待是**無界**的"
            f"（排隊）"
        )
        exc = outcome.get("exc")
        assert exc is not None, "取不到仲裁時必須 fail-closed（丟出可處理例外）"
        assert isinstance(exc, GraphStoreWriteBusy), (
            f"取不到仲裁時必須丟出 fail-closed 例外，實得 {exc!r}"
        )
        assert not isinstance(exc, sqlite3.OperationalError), (
            f"不得冒泡成 sqlite 的鎖錯誤（那代表又去撞 busy_timeout）: {exc!r}"
        )
        elapsed = float(outcome["elapsed"])
        assert elapsed < BUSY_TIMEOUT_SECS, (
            f"fail-closed 耗時 {elapsed:.3f}s ≥ busy_timeout({BUSY_TIMEOUT_SECS}s)"
        )
    finally:
        holder_release.set()
        hog.join(timeout=5)
        for s in (a, b):
            try:
                s.close()
            except Exception:
                pass


# ── W5: 共享生命週期 ─────────────────────────────────────────────

def test_W5_close_does_not_kill_other_instances_connection(tmp_path):
    """一個元件 close() **不得**關掉同一路徑上其它元件仍在用的連線。

    對應變異 P4: close() 無條件把同路徑其它實例的連線一起關掉。
    """
    db_path = tmp_path / "graph.sqlite"
    a, b = _two_stores(db_path)
    try:
        b.add_fact(_fact("b_fact"))
        a.close()

        # B 的連線仍必須可用（可寫、可讀）
        b.add_fact(_fact("b_fact_2"))
        b.flush()
        cnt = b._get_conn().execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        assert cnt == 2, f"B 應仍看得到自己寫的 2 筆，實得 {cnt}"
    finally:
        for s in (a, b):
            try:
                s.close()
            except Exception:
                pass


# ── W6: 既有契約未動 ─────────────────────────────────────────────

def test_W6_existing_contracts_unchanged(tmp_path):
    """busy_timeout 仍 10000；flush() 的 rollback 語意仍在；schema 未變。"""
    db_path = tmp_path / "graph.sqlite"
    store = GraphStore(db_path=db_path)
    try:
        # (a) busy_timeout 契約值
        bt = store._get_conn().execute("PRAGMA busy_timeout").fetchone()[0]
        assert bt == BUSY_TIMEOUT_MS, (
            f"busy_timeout 應為 {BUSY_TIMEOUT_MS} ms，實得 {bt}"
        )
        # (b) 明文 PRAGMA 仍在原始碼裡（不得只倚賴 connect(timeout=) 的隱含預設）
        src = (REPO_ROOT / "src/memory/sage/graph_store.py").read_text(encoding="utf-8")
        assert "PRAGMA busy_timeout=10000" in src, (
            "原始碼必須保留顯式的 PRAGMA busy_timeout=10000"
        )
        # (c) flush() 的 rollback 語意仍在（GOAL-DB-LOCKED-1）
        store.add_fact(_fact("pending_1"))
        assert store._pending_writes == 1

        class _CommitRaises:
            def __init__(self, inner):
                self._inner = inner
                self.rolled_back = False

            def commit(self):
                raise sqlite3.OperationalError("database is locked")

            def rollback(self):
                self.rolled_back = True
                return self._inner.rollback()

            def close(self):
                return self._inner.close()

            def execute(self, *a, **kw):
                return self._inner.execute(*a, **kw)

            def __getattr__(self, name):
                return getattr(self._inner, name)

        poisoned = _CommitRaises(store._conn)
        store._conn = poisoned
        with pytest.raises(sqlite3.OperationalError):
            store.flush()
        assert poisoned.rolled_back is True, "flush commit 失敗後必須 rollback"
        assert store._pending_writes == 0, "flush commit 失敗後 pending 必須歸零"
    finally:
        store.close()

    # (d) schema 契約未變
    assert _SCHEMA_VERSION == 9, f"_SCHEMA_VERSION 不得被改動，實得 {_SCHEMA_VERSION}"


# ── W7: fail-silent ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_W7_write_failure_does_not_break_scheduler_main_loop(monkeypatch, caplog):
    """寫入失敗（fail-closed 例外）不得冒泡成排程器主循環中斷。

    對應變異 P3: 讓失敗時的例外**冒泡**（`except Exception` 接不住）。
    """
    import src.goals.motive_provider as mp
    import src.goals.seed_provider as sp
    import src.social.relation_settlement as rs
    from src.soul.scheduler import SoulScheduler

    class _StubProvider:
        def __init__(self, agent_id):
            self.agent_id = agent_id

        def apply_interrupt_signals(self, now=None):
            raise GraphStoreWriteBusy("[GraphStore] 寫入仲裁失敗（fail-closed）: 測試")

        def scheduled_wakeup_scan(self, now=None):
            raise GraphStoreWriteBusy("[GraphStore] 寫入仲裁失敗（fail-closed）: 測試")

        async def scan_seeds(self, now=None):
            raise GraphStoreWriteBusy("[GraphStore] 寫入仲裁失敗（fail-closed）: 測試")

        def _load_state(self):
            return None

        def _save_state(self, state):
            return None

    monkeypatch.setattr(
        mp.GoalMotiveProvider, "for_agent", staticmethod(lambda a: _StubProvider(a))
    )
    monkeypatch.setattr(
        sp.GoalSeedProvider, "for_agent", staticmethod(lambda a: _StubProvider(a))
    )
    monkeypatch.setattr(rs, "settle_relations", lambda *a, **kw: {})

    class _StubScheduler:
        _all_agents = ["agent_a"]

    with caplog.at_level(logging.WARNING, logger="soul_os.soul.scheduler"):
        # 🔴 不得拋出 ⇒ 主循環不得被中斷
        await SoulScheduler._goal_scan_all(_StubScheduler())

    warns = [
        r for r in caplog.records
        if "主循环扫描异常" in r.getMessage()
    ]
    assert warns, "fail-closed 告警應被記錄（便於事後歸因）"


# ── W8: 0 新副作用 ───────────────────────────────────────────────

def test_W8_no_new_timers_tasks_or_connections(tmp_path, monkeypatch):
    """修正不得新增定時器／背景 task／額外連線。

    (a) 連線數: 同一 DB 路徑開 N 個實例 ⇒ 只應有 N 條 sqlite 連線。
    (b) 執行緒數: 建立實例與做一次競爭寫入前後，threading.active_count() 不變。
    """
    db_path = tmp_path / "graph.sqlite"

    connects: list[str] = []
    real_connect = sqlite3.connect

    def _counting_connect(target=None, *args, **kwargs):
        if target is None:
            target = kwargs.get("database")
        connects.append(str(target))
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", _counting_connect)

    threads_before = threading.active_count()
    a, b = _two_stores(db_path)
    try:
        # 🔺 刻意做**無競爭**的寫入：若這裡有競爭，鎖錯誤自愈會重建連線
        #    （既有 GOAL-DB-LOCKED-1 行為）⇒ 多算一條連線 ⇒ 本守衛會被
        #    P1（拿掉寫入路徑的仲裁）連帶打紅 ⇒ **弱變異**。
        a.add_fact(_fact("one"))

        assert len(connects) == 2, (
            f"同一 DB 路徑開 2 個實例卻建立了 {len(connects)} 條連線"
            f"（connects={connects}）⇒ 修正新增了額外連線"
        )
        assert all("graph.sqlite" in c for c in connects), (
            f"不應對其它檔案開連線: {connects}"
        )
        assert threading.active_count() == threads_before, (
            f"執行緒數變了（{threads_before} → {threading.active_count()}）"
            f"⇒ 修正新增了背景 task/計時器"
        )
    finally:
        for s in (a, b):
            try:
                s.close()
            except Exception:
                pass
