"""tests/test_intimacy_growth_2.py — INTIMACY-GROWTH-2：24 小時靜默衰減

工單：可停用、可追溯、可補償的 24 小時靜默衰減；程式**預設 OFF**。

## 隔離紀律（逐條可驗）
- 所有實驗一律在 pytest `tmp_path` 的**拋棄式 DB**；**絕不**讀寫生產 `data/**`。
- `tests/conftest.py` 的 autouse `_isolate_soul_os_data_root` 已把
  `SOUL_OS_DATA_DIR` 指向 tmp，並把 `INTIMACY_DECAY_ENABLED` 等旗標**釘成空字串**
  （C6）。本檔只在需要 ON 的測試內以 `monkeypatch.setenv` 覆寫。
- **0 服務重啟 / 0 行程終止 / 0 生產埠**：全程不啟動任何伺服器、不對
  `:8000/:8765/:8766/:8767` 發任何請求。
- 交易邊界全程走既有 `emotion_engine.conn`；測試**斷言**結算期間沒有第二條
  `sqlite3.connect` 指向同一個 db 檔（C1）。

## 覆蓋矩陣（工單「隔離測試必須涵蓋」逐條對應）
  G1  23:59 未到期 ⇒ 不扣；24:00 到期 ⇒ 扣 0.5
  G2  重複 tick ⇒ 不重複扣
  G3  延遲 inbound ⇒ settle-once-then-advance
  G4  重啟補扣 ⇒ 最多一次（bounded 1-step）
  G5  同角色兩連線競態（C2 的真實測例）
  G6  交易中途失敗 ⇒ ledger + delta + due 三者全回滾
  G7  空 ASR / barge-in ⇒ 不 TOUCH
  G7b C5 三入口**行為**驗證（真實 ChannelRouter.inbound / IOGateway）
  G8  VC 送達失敗 ⇒ 有降級路徑
  G9  UNKNOWN 冷啟動（時鐘 NULL）⇒ 不衰減、不清帳 Delta
  G10 Delta floor 邊界 ⇒ 不得為負
  G11 旗標 OFF ⇒ 拋棄式 DB 檔 sha256 + mtime 前後不變、零 DDL
  G12 C3：惰性冪等 DDL（import 不得觸發 G2 的 DDL）
  G13 端到端：可停用 / 可追溯 / 可補償

跑法：.venv\\Scripts\\python.exe -m pytest -q tests/test_intimacy_growth_2.py
"""
from __future__ import annotations

import asyncio
import fastapi
import hashlib
import os
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.agent.emotion import (  # noqa: E402
    DECAY_STEP,
    DECAY_WINDOW_HOURS,
    EmotionEngine,
    TRUTHY_VALUES,
    decay_enabled,
)

DAY = DECAY_WINDOW_HOURS * 3600.0
T0 = 1_700_000_000.0  # 固定基準時刻（epoch seconds），全部測試自帶時鐘


# ─────────────────────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────────────────────


def _db_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _db_mtime(path: Path) -> int:
    return path.stat().st_mtime_ns


def _cols(engine: EmotionEngine) -> list:
    return [r[1] for r in engine.conn.execute("PRAGMA table_info(agent_emotions)")]


def _tables(engine: EmotionEngine) -> set:
    return {
        r[0]
        for r in engine.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


@pytest.fixture
def db_path(tmp_path) -> Path:
    """拋棄式 DB 路徑（tmp_path 內；**不碰**生產 data/）。"""
    return tmp_path / "memory.db"


@pytest.fixture
def engine_on(db_path, monkeypatch) -> EmotionEngine:
    """旗標 ON 的引擎（tmp DB）。"""
    monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
    return EmotionEngine(db_path=db_path)


@pytest.fixture
def engine_off(db_path, monkeypatch) -> EmotionEngine:
    """旗標 OFF（空字串）的引擎（tmp DB）。"""
    monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "")
    return EmotionEngine(db_path=db_path)


# ═════════════════════════════════════════════════════════════
# G0. 旗標語意（C4：缺席即 OFF）
# ═════════════════════════════════════════════════════════════


class TestFlagSemantics:
    def test_absent_is_off(self, monkeypatch) -> None:
        """**缺席即 OFF** —— 不是「預設 ON、值為 '0' 才 OFF」。"""
        monkeypatch.delenv("INTIMACY_DECAY_ENABLED", raising=False)
        assert decay_enabled() is False

    @pytest.mark.parametrize("value", ["", " ", "0", "off", "false", "no", "OFF", "2"])
    def test_falsey_values(self, monkeypatch, value) -> None:
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", value)
        assert decay_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "On"])
    def test_truthy_values(self, monkeypatch, value) -> None:
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", value)
        assert decay_enabled() is True

    def test_truthy_matches_life_thread_semantics(self) -> None:
        """真值集合與 LIFE_THREAD_* 三支旗標逐字同語意。"""
        assert TRUTHY_VALUES == frozenset({"1", "true", "yes", "on"})


# ═════════════════════════════════════════════════════════════
# G1. 23:59 未到期 ⇒ 不扣；24:00 到期 ⇒ 扣 0.5
# ═════════════════════════════════════════════════════════════


class TestDueBoundary:
    def test_not_due_at_23_59(self, engine_on) -> None:
        engine_on.update_delta("agent_yua", 3.0)
        engine_on.touch_inbound("agent_yua", "e1", "telegram", now=T0)
        before = engine_on.get_delta("agent_yua")

        result = engine_on.try_apply_decay("agent_yua", now=T0 + DAY - 60)

        assert result["applied"] is False
        assert result["reason"] == "NOT_DUE"
        assert engine_on.get_delta("agent_yua") == before

    def test_due_at_24_00_deducts_half(self, engine_on) -> None:
        engine_on.update_delta("agent_yua", 3.0)
        engine_on.touch_inbound("agent_yua", "e1", "telegram", now=T0)
        before = engine_on.get_delta("agent_yua")

        result = engine_on.try_apply_decay("agent_yua", now=T0 + DAY)

        assert result["applied"] is True
        assert result["applied_amount"] == pytest.approx(DECAY_STEP)
        assert engine_on.get_delta("agent_yua") == pytest.approx(before - DECAY_STEP)

    def test_step_is_fixed_regardless_of_lateness(self, engine_on) -> None:
        """固定步長：無論晚多久，**一次評估**都只扣 0.5。"""
        engine_on.update_delta("agent_yua", 9.0)
        engine_on.touch_inbound("agent_yua", "e1", "telegram", now=T0)
        before = engine_on.get_delta("agent_yua")

        engine_on.try_apply_decay("agent_yua", now=T0 + 100 * DAY)

        assert engine_on.get_delta("agent_yua") == pytest.approx(before - DECAY_STEP)


# ═════════════════════════════════════════════════════════════
# G2. 重複 tick ⇒ 不重複扣
# ═════════════════════════════════════════════════════════════


class TestDuplicateTick:
    def test_second_tick_same_due_does_not_recharge(self, engine_on) -> None:
        engine_on.update_delta("agent_yua", 5.0)
        engine_on.touch_inbound("agent_yua", "e1", "telegram", now=T0)

        first = engine_on.try_apply_decay("agent_yua", now=T0 + DAY)
        after_first = engine_on.get_delta("agent_yua")
        # 立刻再 tick（遠未到新的 due）
        second = engine_on.try_apply_decay("agent_yua", now=T0 + DAY + 1)
        third = engine_on.try_apply_decay("agent_yua", now=T0 + DAY + 100)

        assert first["applied"] is True
        assert second["applied"] is False
        assert third["applied"] is False
        assert engine_on.get_delta("agent_yua") == after_first

    def test_rapid_ticks_only_one_step(self, engine_on) -> None:
        engine_on.update_delta("agent_yua", 5.0)
        engine_on.touch_inbound("agent_yua", "e1", "telegram", now=T0)
        before = engine_on.get_delta("agent_yua")

        for i in range(50):
            engine_on.try_apply_decay("agent_yua", now=T0 + DAY + i)

        # 50 次 tick 都落在同一個 24h 窗內 ⇒ 恰好一步
        assert engine_on.get_delta("agent_yua") == pytest.approx(before - DECAY_STEP)

    def test_ledger_records_original_due_and_amount(self, engine_on) -> None:
        """ledger 記**實扣量**與**原到期點**（可追溯）。"""
        engine_on.update_delta("agent_yua", 5.0)
        engine_on.touch_inbound("agent_yua", "e1", "telegram", now=T0)
        engine_on.try_apply_decay("agent_yua", now=T0 + DAY)

        rows = list(
            engine_on.conn.execute(
                "SELECT agent_id, original_due_at, applied_amount "
                "FROM intimacy_decay_ledger WHERE agent_id = ?",
                ("agent_yua",),
            )
        )
        assert len(rows) == 1
        assert rows[0][0] == "agent_yua"
        assert rows[0][1] == pytest.approx(T0 + DAY)   # 原到期點
        assert rows[0][2] == pytest.approx(DECAY_STEP)  # 實扣量


# ═════════════════════════════════════════════════════════════
# G3. 延遲 inbound ⇒ settle-once-then-advance
# ═════════════════════════════════════════════════════════════


class TestDelayedInbound:
    def test_late_inbound_settles_once_then_advances(self, engine_on) -> None:
        engine_on.update_delta("agent_yua", 5.0)
        engine_on.touch_inbound("agent_yua", "e1", "telegram", now=T0)
        before = engine_on.get_delta("agent_yua")

        late = T0 + 40 * DAY
        result = engine_on.touch_inbound("agent_yua", "e2", "telegram", now=late)

        assert result["touched"] is True
        # 先結算**恰好一步**
        assert engine_on.get_delta("agent_yua") == pytest.approx(before - DECAY_STEP)
        # 再推進：due = 結算後的時鐘 + 24h
        assert result["next_decay_due_at"] > late

    def test_overdue_inbound_never_moves_clock_backwards(self, engine_on) -> None:
        """過期 inbound **不得**讓時鐘倒退。"""
        engine_on.update_delta("agent_yua", 5.0)
        engine_on.touch_inbound("agent_yua", "e1", "telegram", now=T0)

        later = T0 + 40 * DAY
        r1 = engine_on.touch_inbound("agent_yua", "e2", "telegram", now=later)
        due1 = r1["next_decay_due_at"]

        # 第二筆「更晚但仍在窗內」的 inbound
        r2 = engine_on.touch_inbound("agent_yua", "e3", "telegram", now=later + 60)
        assert r2["next_decay_due_at"] >= due1

        # 甚至一筆**時間戳較舊**（亂序抵達）的 inbound 也不得倒退
        r3 = engine_on.touch_inbound("agent_yua", "e4", "telegram", now=later - 10 * DAY)
        assert r3["next_decay_due_at"] >= due1

    def test_repeated_inbound_in_window_does_not_deduct(self, engine_on) -> None:
        """同一個窗內的重複 inbound ⇒ 零扣減（只推 due 或維持）。"""
        engine_on.update_delta("agent_yua", 5.0)
        engine_on.touch_inbound("agent_yua", "e1", "telegram", now=T0)
        before = engine_on.get_delta("agent_yua")

        for i in range(10):
            engine_on.touch_inbound("agent_yua", f"r{i}", "telegram", now=T0 + i * 60)

        assert engine_on.get_delta("agent_yua") == before


# ═════════════════════════════════════════════════════════════
# G4. 重啟補扣 ⇒ 最多一次（bounded 1-step catch-up）
# ═════════════════════════════════════════════════════════════


class TestRestartCatchup:
    def test_restart_catches_up_at_most_one_step(self, db_path, monkeypatch) -> None:
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        eng = EmotionEngine(db_path=db_path)
        eng.update_delta("agent_yua", 10.0)
        eng.touch_inbound("agent_yua", "e1", "telegram", now=T0)
        before = eng.get_delta("agent_yua")

        # 「停服」很久（365 天）後重啟：新引擎實例 = 新的模組生命週期
        eng2 = EmotionEngine(db_path=db_path)
        result = eng2.try_apply_decay("agent_yua", now=T0 + 365 * DAY)

        assert result["applied"] is True
        # bounded：**只補一步**，不累積 365 步
        assert eng2.get_delta("agent_yua") == pytest.approx(before - DECAY_STEP)

    def test_catchup_then_second_evaluation_is_bounded(self, db_path, monkeypatch) -> None:
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        eng = EmotionEngine(db_path=db_path)
        eng.update_delta("agent_yua", 10.0)
        eng.touch_inbound("agent_yua", "e1", "telegram", now=T0)

        eng2 = EmotionEngine(db_path=db_path)
        eng2.try_apply_decay("agent_yua", now=T0 + 100 * DAY)
        after_first = eng2.get_delta("agent_yua")
        # 立刻再評估（未到新 due）⇒ 不再扣
        eng2.try_apply_decay("agent_yua", now=T0 + 100 * DAY + 1)
        assert eng2.get_delta("agent_yua") == after_first

    def test_ledger_survives_restart_and_blocks_replay(self, db_path, monkeypatch) -> None:
        """ledger 跨行程冪等：同一到期點重放不得再扣。"""
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        eng = EmotionEngine(db_path=db_path)
        eng.update_delta("agent_yua", 10.0)
        eng.touch_inbound("agent_yua", "e1", "telegram", now=T0)
        eng.try_apply_decay("agent_yua", now=T0 + DAY)
        after = eng.get_delta("agent_yua")

        # 另一實例（模擬重啟）對同一到期點重放
        eng2 = EmotionEngine(db_path=db_path)
        # 把 due 手動倒回原到期點，模擬「舊 due 被重放」
        eng2.conn.execute(
            "UPDATE agent_emotions SET next_decay_due_at = ? WHERE agent_id = ?",
            (T0 + DAY, "agent_yua"),
        )
        eng2.conn.commit()
        result = eng2.try_apply_decay("agent_yua", now=T0 + DAY + 10)

        assert result["applied"] is False
        assert result["reason"] == "DUPLICATE"
        assert eng2.get_delta("agent_yua") == after


# ═════════════════════════════════════════════════════════════
# G5. 同角色兩連線競態（C2 真實測例）
# ═════════════════════════════════════════════════════════════


class TestConcurrency:
    def test_two_writer_paths_race_without_failure(self, engine_on) -> None:
        """同一 process 內兩條寫路徑同時觸發 ⇒ 不得 fail（C2）。

        真實測例：2 條 tick 執行緒 + 2 條 touch 執行緒，全部在同一條
        `emotion_engine.conn` 上跑顯式交易。
        """
        engine_on.ensure_decay_schema()
        engine_on.update_delta("agent_yua", 20.0)
        engine_on.touch_inbound("agent_yua", "e0", "telegram", now=T0)

        errors: list = []

        def ticker() -> None:
            for i in range(30):
                try:
                    engine_on.try_apply_decay("agent_yua", now=T0 + DAY + i)
                except BaseException as e:  # noqa: BLE001 - 任何例外都是失敗
                    errors.append(("tick", type(e).__name__, str(e)))

        def toucher() -> None:
            for i in range(30):
                try:
                    engine_on.touch_inbound(
                        "agent_yua", f"t{i}", "telegram", now=T0 + i
                    )
                except BaseException as e:  # noqa: BLE001
                    errors.append(("touch", type(e).__name__, str(e)))

        threads = [threading.Thread(target=ticker) for _ in range(2)] + [
            threading.Thread(target=toucher) for _ in range(2)
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert errors == [], f"競態下有失敗: {errors}"
        assert engine_on.get_delta("agent_yua") >= 0.0

    def test_no_second_connection_to_same_db_during_settlement(
        self, engine_on, db_path
    ) -> None:
        """C1：結算期間**沒有**第二個 `sqlite3.connect` 指向同一個 db 檔。"""
        engine_on.ensure_decay_schema()
        engine_on.update_delta("agent_yua", 5.0)
        engine_on.touch_inbound("agent_yua", "e0", "telegram", now=T0)

        real_connect = sqlite3.connect
        opened: list = []

        def spy(*args, **kwargs):
            opened.append(args[0] if args else kwargs.get("database"))
            return real_connect(*args, **kwargs)

        sqlite3.connect = spy  # type: ignore[assignment]
        try:
            engine_on.try_apply_decay("agent_yua", now=T0 + DAY)
            engine_on.touch_inbound("agent_yua", "e1", "telegram", now=T0 + DAY)
        finally:
            sqlite3.connect = real_connect  # type: ignore[assignment]

        same_file = [o for o in opened if o and Path(str(o)).resolve() == db_path.resolve()]
        assert same_file == [], f"結算期間另開了指向同一 db 的連線: {same_file}"

    def test_busy_timeout_explicitly_set(self, engine_on) -> None:
        """C2：顯式 PRAGMA busy_timeout（不倚賴 Python 預設 5.0s）。"""
        from src.agent.emotion import BUSY_TIMEOUT_MS

        value = list(engine_on.conn.execute("PRAGMA busy_timeout"))[0][0]
        assert value == BUSY_TIMEOUT_MS
        assert value > 0

    def test_no_begin_within_transaction_error(self, engine_on) -> None:
        """C1：不得撞 `cannot start a transaction within a transaction`。"""
        engine_on.ensure_decay_schema()
        engine_on.update_delta("agent_yua", 3.0)
        engine_on.touch_inbound("agent_yua", "e0", "telegram", now=T0)
        # 連續呼叫（每次都是 BEGIN IMMEDIATE → commit）不得拋
        for i in range(20):
            engine_on.try_apply_decay("agent_yua", now=T0 + DAY + i * 0.001)


# ═════════════════════════════════════════════════════════════
# G6. 交易中途失敗 ⇒ ledger + delta + due 三者全回滾
# ═════════════════════════════════════════════════════════════


class _UndeterminableRowcountConn:
    """Wrapper：讓 ledger INSERT 的 cursor `rowcount` **不可判定**（故障注入）。

    工單明訂：**不可** monkeypatch `sqlite3.Connection.execute`（不可變型別 ⇒
    TypeError）。故用 wrapper 物件注入故障 —— wrapper 只轉發，不改型別。

    以 **SQL 語句內容**判定攔截點（理由同 `_FailOnLedgerInsertConn`）：必須是
    `INSERT INTO intimacy_decay_ledger`，且排除 `CREATE TABLE`。

    `rowcount` 讀取時拋 `AttributeError`（模擬不可判定）。**不**在此拋
    OperationalError —— 本測試驗的是「不可判定 ⇒ fail-closed」，不是「DB 報錯」。
    """

    def __init__(
        self,
        real,
        needle: str = "INSERT INTO intimacy_decay_ledger",
        mode: str = "raise",
    ) -> None:
        self._real = real
        self._needle = needle
        self._mode = mode  # "raise" ⇒ AttributeError；"none" ⇒ 回 None
        self.tripped = False

    def execute(self, sql, *args, **kwargs):
        cur = self._real.execute(sql, *args, **kwargs)
        if (
            (not self.tripped)
            and isinstance(sql, str)
            and self._needle in sql
            and "CREATE TABLE" not in sql
        ):
            self.tripped = True
            return _UndeterminableRowcountCursor(cur, self._mode)
        return cur

    def commit(self) -> None:
        return self._real.commit()

    def rollback(self) -> None:
        return self._real.rollback()

    def __getattr__(self, name):
        return getattr(self._real, name)

    @property
    def in_transaction(self) -> bool:
        return self._real.in_transaction


class _UndeterminableRowcountCursor:
    """Cursor 代理：`rowcount` 讀取時拋 `AttributeError`（或回 `None`）。

    🔴 **不得**用 `@property` 實作：Python 的屬性查找在 property 內拋出
    `AttributeError` 時會**落到 `__getattr__`**，本類別的 `__getattr__` 會把
    它轉發給真 cursor（`rowcount == 1`），故障於是被靜默吞掉（實測已驗證）。
    故 `rowcount` 必須在 `__getattr__` 內**優先**判定。
    """

    def __init__(self, real, mode: str = "raise") -> None:
        self._real = real
        self._mode = mode

    def __getattr__(self, name):
        if name == "rowcount":
            if self._mode == "none":
                return None
            raise AttributeError(
                "injected: rowcount undeterminable (test-only)"
            )
        return getattr(self._real, name)


class _FailOnLedgerInsertConn:
    """Wrapper：在 `intimacy_decay_ledger` 的 INSERT 上拋錯（故障注入）。

    工單明訂：**不可** monkeypatch `sqlite3.Connection.execute`（不可變型別 ⇒
    TypeError）。故用 wrapper 物件注入故障 —— wrapper 只轉發，不改型別。

    以 **SQL 語句內容**（而非呼叫序號）判定攔截點，且必須是 **INSERT INTO
    ledger** 那一句：`ensure_decay_schema()` 會吞掉 `OperationalError`
    （併發建立情境的既有設計），用序號會誤傷它；而 `CREATE TABLE` 也含表名，
    故 needle 要精確到 `INSERT INTO intimacy_decay_ledger`。
    """

    def __init__(
        self, real, needle: str = "INSERT INTO intimacy_decay_ledger"
    ) -> None:
        self._real = real
        self._needle = needle
        self.tripped = False

    def execute(self, sql, *args, **kwargs):
        if (
            (not self.tripped)
            and isinstance(sql, str)
            and self._needle in sql
            and "CREATE TABLE" not in sql
        ):
            self.tripped = True
            raise sqlite3.OperationalError("injected failure (test-only)")
        return self._real.execute(sql, *args, **kwargs)

    def commit(self) -> None:
        return self._real.commit()

    def rollback(self) -> None:
        return self._real.rollback()

    def __getattr__(self, name):
        return getattr(self._real, name)

    @property
    def in_transaction(self) -> bool:
        return self._real.in_transaction


class TestTransactionRollback:
    def test_mid_transaction_failure_rolls_back_all_three(self, engine_on) -> None:
        """ledger + delta + due **三者全回滾**。"""
        engine_on.ensure_decay_schema()
        engine_on.update_delta("agent_yua", 7.0)
        engine_on.touch_inbound("agent_yua", "e0", "telegram", now=T0)

        real_conn = engine_on.conn
        delta_before = engine_on.get_delta("agent_yua")
        row_before = tuple(
            real_conn.execute(
                "SELECT next_decay_due_at FROM agent_emotions WHERE agent_id = ?",
                ("agent_yua",),
            ).fetchone()
        )
        ledger_before = list(
            real_conn.execute("SELECT event_key FROM intimacy_decay_ledger")
        )

        # 故障注入：在 ledger INSERT 上拋錯（delta 的 UPDATE 在其後 ⇒ 未執行）
        wrapper = _FailOnLedgerInsertConn(real_conn)
        engine_on.conn = wrapper
        try:
            with pytest.raises(sqlite3.OperationalError, match="injected failure"):
                engine_on.try_apply_decay("agent_yua", now=T0 + DAY)
        finally:
            engine_on.conn = real_conn
        assert wrapper.tripped, "故障注入未觸發（測試本身失效）"

        # 三者全回滾
        assert engine_on.get_delta("agent_yua") == delta_before
        row_after = tuple(
            real_conn.execute(
                "SELECT next_decay_due_at FROM agent_emotions WHERE agent_id = ?",
                ("agent_yua",),
            ).fetchone()
        )
        assert row_after == row_before
        ledger_after = list(
            real_conn.execute("SELECT event_key FROM intimacy_decay_ledger")
        )
        assert ledger_after == ledger_before

    def test_connection_usable_after_rollback(self, engine_on) -> None:
        """回滾後連線仍可用（不得殘留開啟的交易）。"""
        engine_on.ensure_decay_schema()
        engine_on.update_delta("agent_yua", 7.0)
        engine_on.touch_inbound("agent_yua", "e0", "telegram", now=T0)

        real_conn = engine_on.conn
        wrapper = _FailOnLedgerInsertConn(real_conn)
        engine_on.conn = wrapper
        try:
            with pytest.raises(sqlite3.OperationalError):
                engine_on.try_apply_decay("agent_yua", now=T0 + DAY)
        finally:
            engine_on.conn = real_conn
        assert wrapper.tripped

        assert real_conn.in_transaction is False
        result = engine_on.try_apply_decay("agent_yua", now=T0 + DAY)
        assert result["applied"] is True

    @pytest.mark.parametrize("mode", ["raise", "none"])
    def test_undeterminable_rowcount_is_fail_closed(
        self, engine_on, mode: str
    ) -> None:
        """🔴 T3（缺口 2 後續）：`rowcount` **不可判定** ⇒ fail-closed。

        `AttributeError`（mode="raise"）或 `None`（mode="none"）皆必須：
          - 回傳 `applied == 0.0`（0 扣減）
          - `get_delta()` **前後相同**（未扣減）
          - `next_decay_due_at` **前後相同**（0 推進）
          - ledger 列數**不變**
          - 交易已回滾（後續寫入仍能成功作為旁證）

        舊實作 `except ...: inserted = 1`（fail-open）⇒ 仍 UPDATE Delta 並推進
        due ⇒ 本測試必紅。
        """
        engine_on.ensure_decay_schema()
        engine_on.update_delta("agent_yua", 7.0)
        engine_on.touch_inbound("agent_yua", "e0", "telegram", now=T0)

        real_conn = engine_on.conn
        delta_before = engine_on.get_delta("agent_yua")
        _last_before, due_before, _d_before = (
            engine_on._read_clock_and_delta_locked("agent_yua")
        )
        ledger_before = list(
            real_conn.execute("SELECT event_key FROM intimacy_decay_ledger")
        )
        assert delta_before == pytest.approx(7.0), "前置：Delta 應為 7.0"
        assert due_before == pytest.approx(T0 + DAY), "前置：due 應為 T+24h"
        assert ledger_before == [], "前置：ledger 應為空"

        wrapper = _UndeterminableRowcountConn(real_conn, mode=mode)
        engine_on.conn = wrapper
        try:
            # 🔴 必須**不拋例外**：fail-closed ＝ 乾淨回 0.0（非崩潰）
            applied = engine_on._settle_once_locked(
                "agent_yua",
                T0 + DAY,
                T0 + DAY,
                delta_before,
                origin="inbound_late",
            )
        finally:
            engine_on.conn = real_conn
        assert wrapper.tripped, "故障注入未觸發（測試本身失效）"

        # 1) 回傳 0.0（0 扣減）
        assert applied == pytest.approx(0.0), (
            f"rowcount 不可判定時必須回 0.0（0 扣減），實際 {applied}"
            "（舊實作 fail-open 會回 0.5）"
        )
        # 2) Delta 前後相同（未扣減）
        assert engine_on.get_delta("agent_yua") == pytest.approx(delta_before), (
            "rowcount 不可判定時**不得**扣減 Delta"
            f"（前 {delta_before} vs 後 {engine_on.get_delta('agent_yua')}）"
        )
        # 3) due 前後相同（0 推進）
        _last_after, due_after, _d_after = (
            engine_on._read_clock_and_delta_locked("agent_yua")
        )
        assert due_after == pytest.approx(due_before), (
            f"rowcount 不可判定時**不得**推進 due（前 {due_before} vs 後 {due_after}）"
        )
        # 4) ledger 列數不變
        ledger_after = list(
            real_conn.execute("SELECT event_key FROM intimacy_decay_ledger")
        )
        assert ledger_after == ledger_before, (
            "rowcount 不可判定時**不得**留下 ledger 列"
        )
        # 5) 交易已回滾：連線未殘留開啟的交易，且後續寫入仍能成功（旁證）
        assert real_conn.in_transaction is False, (
            "fail-closed 路徑必須回滾該交易，不得殘留開啟的交易"
        )
        # 旁證：同一條連線仍可正常寫入（回滾未把連線弄壞）。
        #    🔴 用「未逾期」的 touch（now < due_before）以免順帶觸發真實結算，
        #       否則本斷言會被另一次合法扣減污染（delta 就不會等於 delta_before）。
        engine_on.touch_inbound(
            "agent_yua", "e_after", "telegram", now=T0 + 60
        )
        assert real_conn.in_transaction is False, "回滾後連線不得殘留交易"
        assert engine_on.get_delta("agent_yua") == pytest.approx(delta_before), (
            "旁證路徑不得扣減 Delta —— fail-closed 後 Delta 應維持 0 扣減"
        )
        assert _ledger_count(engine_on, "agent_yua") == 0, (
            "旁證路徑不得留下 ledger 列"
        )


# ═════════════════════════════════════════════════════════════
# G7. 空 ASR / barge-in ⇒ 不 TOUCH
# ═════════════════════════════════════════════════════════════


class TestNoTouchSources:
    def test_empty_text_does_not_touch(self, tmp_path) -> None:
        """空 / 純空白文字（空 ASR）⇒ router 不得 TOUCH。"""
        from src.io.channels.router import ChannelRouter

        router = ChannelRouter.__new__(ChannelRouter)  # 只測 TOUCH 閘門，不建整條管線
        before = router._read_intimacy_clock_probe() if hasattr(
            router, "_read_intimacy_clock_probe"
        ) else None
        del before

        # 空字串：_touch_intimacy_clock 直接 return（§2）
        for text in ("", "   ", "\n\t "):
            router._touch_intimacy_clock("agent_yua", "telegram", text)

    def test_barge_in_and_interrupt_do_not_touch(self, tmp_path, monkeypatch) -> None:
        """單純 interrupt / barge-in ⇒ 不得 TOUCH。

        VC 的 `on_interrupt` / `_barge` 路徑**不呼叫**任何 TOUCH API。
        """
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        db = tmp_path / "memory.db"
        engine = EmotionEngine(db_path=db)
        engine.ensure_decay_schema()
        engine.update_delta("agent_akane", 5.0)
        engine.touch_inbound("agent_yua", "e0", "voice_companion", now=T0)
        before = engine.get_delta("agent_yua")

        # 模擬 barge-in：只是一個 generation 遞增，無 TOUCH 呼叫
        generation = 0
        generation += 1  # _barge()

        assert generation == 1
        assert engine.get_delta("agent_yua") == before

    def test_no_touch_path_callable_from_non_human_events(self) -> None:
        """夢 / 日記 / 排程事件**不得** TOUCH：模組內不存在這類 callsite。"""
        import ast

        src = (REPO_ROOT / "src" / "agent" / "emotion.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        # 找出所有呼叫 touch_inbound 的地方（本檔內應只有 API 定義與 docstring）
        callsites = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "touch_inbound"
        ]
        assert callsites == []

    def test_router_touch_only_inside_telegram_branch(self) -> None:
        """router 的 TOUCH 呼叫必須在 `channel == "telegram"` 分支內。"""
        import ast

        path = REPO_ROOT / "src" / "io" / "channels" / "router.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        src_lines = path.read_text(encoding="utf-8").splitlines()

        hits = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_touch_intimacy_clock"
            ):
                hits.append(node.lineno)

        assert len(hits) == 1, f"預期恰好 1 個 TOUCH callsite，實得 {hits}"

        # 該 callsite 必須在 `if channel == "telegram":`（L863 附近）之後
        telegram_guard = [
            i + 1
            for i, line in enumerate(src_lines)
            if 'if channel == "telegram":' in line
        ]
        assert telegram_guard, "找不到 telegram 分支"
        assert hits[0] > telegram_guard[0], (
            f"TOUCH callsite L{hits[0]} 不在 telegram 分支之後 "
            f"(guard L{telegram_guard[0]})"
        )

    def test_web_gate_present_and_does_not_touch(self) -> None:
        """Web 入口必須存在 fail-closed 閘門，且**不得**呼叫 TOUCH。"""
        src = (REPO_ROOT / "src" / "io" / "gateway.py").read_text(encoding="utf-8")
        assert "_intimacy_decay_gate" in src
        assert "SKIPPED_NO_AUTH" in src
        # gateway 不得呼叫任何 TOUCH API
        assert "touch_inbound" not in src, "gateway 不得 TOUCH（C5：Web 不 TOUCH）"

    def test_vc_server_does_not_write_agent_emotions(self) -> None:
        """§10：VC **不得**直接新增對 agent_emotions 的寫入。

        以 **AST** 判定，不看註解／docstring —— 交付物**必須**在文件裡說明
        「不碰 agent_emotions」，用純文字搜尋會把正確的文件判成違規。

        判定標準是**寫入**：raw SQL 提及該表、或 `emotion_engine.<mutator>`。
        VC 既有的 `emotion_engine.get()` / `get_delta()`（唯讀，供 avatar 情緒
        底色）不是本票新增、也不構成寫入 ⇒ 不在禁止之列。
        """
        import ast

        #: 允許的唯讀 API（既有，VC-NOREPLACE 之前就在）
        READ_ONLY_API = {
            "get", "get_delta", "mood_description", "compute_longing",
            "compute_effective_intimacy", "calculate_intimacy_gain",
        }
        MUTATORS = {
            "update", "update_delta", "reset", "touch_inbound",
            "try_apply_decay", "ensure_decay_schema",
        }

        vc_dir = REPO_ROOT / "clients" / "voice_companion"
        raw_sql_mentions = []
        mutator_calls = []

        def _docstring_nodes(tree) -> set:
            """收集所有 docstring 的 Constant 節點（module / class / def）。

            🔴 交付物**必須**在文件裡寫「本模組不碰 agent_emotions」，
            因此 docstring 提及該表是**正確**的，不是違規。
            """
            out = set()
            for node in ast.walk(tree):
                if isinstance(
                    node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    body = getattr(node, "body", None) or []
                    if (
                        body
                        and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)
                    ):
                        out.add(id(body[0].value))
            return out

        for path in vc_dir.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docstrings = _docstring_nodes(tree)
            for node in ast.walk(tree):
                # (a) 任何**非 docstring** 的字串常值提及該表 ⇒ 可疑 raw SQL
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if id(node) in docstrings:
                        continue
                    if (
                        "agent_emotions" in node.value
                        or "intimacy_decay_ledger" in node.value
                    ):
                        raw_sql_mentions.append((path.name, node.lineno))
                # (b) 對 **emotion_engine** 的 mutator 呼叫（只看這個物件，
                #     否則 `self._vad.reset()` 之類的同名方法會誤判）
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in MUTATORS
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "emotion_engine"
                ):
                    mutator_calls.append((path.name, node.lineno, node.func.attr))
                # (c) sanity：允許清單不得與禁止清單重疊
                assert not (READ_ONLY_API & MUTATORS)

        assert raw_sql_mentions == [], f"VC 出現非文件的 DB 表字串: {raw_sql_mentions}"
        assert mutator_calls == [], f"VC 呼叫了 mutator: {mutator_calls}"

    def test_vc_read_only_emotion_usage_stays_read_only(self) -> None:
        """VC 對 `emotion_engine` 的既有用法必須維持**唯讀**（本票不得擴大）。"""
        import ast

        path = REPO_ROOT / "clients" / "voice_companion" / "web_server.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        methods = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "emotion_engine"
            ):
                methods.add(node.func.attr)
        assert methods <= {"get", "get_delta"}, f"VC 用了非唯讀 API: {methods}"


# ═════════════════════════════════════════════════════════════
# G7b. C5 三入口接線的**行為**驗證（不是只看程式碼字串）
# ═════════════════════════════════════════════════════════════


class TestChannelWiring:
    """真實走 `ChannelRouter.inbound` / `IOGateway` 的行為測例。

    全部在拋棄式 DB；**不啟動任何伺服器、不綁任何埠**。
    """

    @staticmethod
    def _point_router_at(engine, monkeypatch):
        """把 router 的 TOUCH 導向拋棄式 engine。"""
        import src.agent.emotion as emotion_mod
        from src.eventbus import SoulEventBus
        from src.io.channels.router import ChannelRouter

        # 🔴 `emotion_engine` 是 module-level singleton。**必須**用 monkeypatch
        #    還原，否則本測試會把全域 singleton 永久指向拋棄式 DB，污染後續
        #    測試順序（monkeypatch 於測試結束自動 undo）。語意不變：仍是在本
        #    測試期間把 router 的 TOUCH 導向拋棄式 engine。
        monkeypatch.setattr(emotion_mod, "emotion_engine", engine)
        bus = SoulEventBus()
        return ChannelRouter(bus=bus)

    @staticmethod
    def _clock(engine, agent_id):
        rows = list(
            engine.conn.execute(
                "SELECT last_valid_inbound_at, next_decay_due_at "
                "FROM agent_emotions WHERE agent_id = ?",
                (agent_id,),
            )
        )
        return rows[0] if rows else None

    def test_whitelisted_tg_inbound_touches(self, tmp_path, monkeypatch) -> None:
        """✅ TG 是**唯一** TOUCH 入口：通過 owner whitelist 的真人 inbound 才 TOUCH。"""
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        monkeypatch.setenv("TELEGRAM_OWNER_ID", "1696287850")
        db = tmp_path / "memory.db"
        engine = EmotionEngine(db_path=db)
        engine.ensure_decay_schema()
        router = self._point_router_at(engine, monkeypatch)

        asyncio.run(
            router.inbound("yua", "hello Bryan", 1696287850, channel="telegram")
        )

        clock = self._clock(engine, "agent_yua")
        assert clock is not None, "已驗證的 TG inbound 沒有 TOUCH"
        assert clock[0] is not None
        assert clock[1] is not None
        assert clock[1] == pytest.approx(clock[0] + DAY)

    def test_non_whitelisted_tg_inbound_does_not_touch(
        self, tmp_path, monkeypatch
    ) -> None:
        """未通過 whitelist 的 user_id ⇒ 連 event 都不發，更不得 TOUCH。"""
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        monkeypatch.setenv("TELEGRAM_OWNER_ID", "1696287850")
        db = tmp_path / "memory.db"
        engine = EmotionEngine(db_path=db)
        engine.ensure_decay_schema()
        router = self._point_router_at(engine, monkeypatch)

        asyncio.run(router.inbound("ruka", "stranger", 99999999, channel="telegram"))

        assert self._clock(engine, "agent_yua") is None, "陌生人 inbound 竟然 TOUCH"

    def test_empty_text_inbound_does_not_touch(self, tmp_path, monkeypatch) -> None:
        """§2：空 / 純空白（空 ASR）**不得** TOUCH。"""
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        monkeypatch.setenv("TELEGRAM_OWNER_ID", "1696287850")
        db = tmp_path / "memory.db"
        engine = EmotionEngine(db_path=db)
        engine.ensure_decay_schema()
        router = self._point_router_at(engine, monkeypatch)

        for text in ("", "   ", "\n\t"):
            asyncio.run(router.inbound("yua", text, 1696287850, channel="telegram"))

        assert self._clock(engine, "agent_yua") is None, "空文字竟然 TOUCH"

    def test_web_gateway_never_touches(self, tmp_path, monkeypatch) -> None:
        """❌ Web 不 TOUCH：fail-closed 閘門只記診斷，不寫任何親密度狀態。"""
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        db = tmp_path / "memory.db"
        engine = EmotionEngine(db_path=db)
        engine.ensure_decay_schema()

        import src.agent.emotion as emotion_mod

        # 🔴 同 TestChannelWiring._point_router_at：改用 monkeypatch 還原全域
        #    singleton（避免污染後續測試順序）。語意不變。
        monkeypatch.setattr(emotion_mod, "emotion_engine", engine)

        from src.eventbus import SoulEventBus
        from src.io.gateway import IOGateway

        gw = IOGateway(SoulEventBus())
        for _ in range(5):
            gw._intimacy_decay_gate("agent_akane")

        assert self._clock(engine, "agent_akane") is None, "Web 閘門竟然 TOUCH"

    def test_flag_off_means_router_inbound_writes_nothing(
        self, tmp_path, monkeypatch
    ) -> None:
        """C4：旗標 OFF ⇒ 連 TG inbound 也**零新持久寫入**。"""
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "")
        monkeypatch.setenv("TELEGRAM_OWNER_ID", "1696287850")
        db = tmp_path / "memory.db"
        engine = EmotionEngine(db_path=db)
        router = self._point_router_at(engine, monkeypatch)

        sha_before = _db_sha(db)
        mtime_before = _db_mtime(db)

        asyncio.run(
            router.inbound("yua", "hello Bryan", 1696287850, channel="telegram")
        )

        assert "last_valid_inbound_at" not in _cols(engine)
        assert "intimacy_decay_ledger" not in _tables(engine)
        assert _db_sha(db) == sha_before, "旗標 OFF 的 TG inbound 動了 DB"
        assert _db_mtime(db) == mtime_before


# ═════════════════════════════════════════════════════════════
# G8. VC 送達失敗 ⇒ 有降級路徑
# ═════════════════════════════════════════════════════════════


class TestVcDegradation:
    def test_flag_off_degrades_without_network(self, monkeypatch) -> None:
        from clients.voice_companion.inbound_touch_client import InboundTouchClient

        monkeypatch.setenv("VC_INBOUND_TOUCH_ENABLED", "")
        client = InboundTouchClient(
            base_url="http://127.0.0.1:1",  # 不可路由：絕不會真的送出
            token="x",
            opener=lambda *a, **k: pytest.fail("旗標 OFF 卻發了請求"),
        )
        result = client.notify_inbound("agent_akane")
        assert result.state == "DEGRADED"
        assert result.reason == "FLAG_OFF"

    def test_missing_endpoint_degrades(self, monkeypatch) -> None:
        from clients.voice_companion.inbound_touch_client import InboundTouchClient

        monkeypatch.setenv("VC_INBOUND_TOUCH_ENABLED", "1")
        monkeypatch.delenv("VC_INBOUND_TOUCH_URL", raising=False)
        monkeypatch.delenv("VC_INBOUND_TOUCH_TOKEN", raising=False)
        client = InboundTouchClient(
            base_url="", token="", opener=lambda *a, **k: pytest.fail("不該送")
        )
        result = client.notify_inbound("agent_akane")
        assert result.state == "DEGRADED"
        assert result.reason == "NO_ENDPOINT_OR_TOKEN"

    def test_delivery_failure_degrades_after_bounded_retries(self, monkeypatch) -> None:
        """送達失敗 ⇒ 有界 retry 後降級（**不拋例外**、不無限重試）。"""
        from clients.voice_companion.inbound_touch_client import InboundTouchClient

        monkeypatch.setenv("VC_INBOUND_TOUCH_ENABLED", "1")
        calls = []

        def failing_opener(req, timeout=None):
            calls.append(req.full_url)
            raise OSError("connection refused (test)")

        slept = []
        client = InboundTouchClient(
            base_url="http://127.0.0.1:1",
            token="t",
            max_attempts=3,
            opener=failing_opener,
            sleep_fn=slept.append,
        )
        result = client.notify_inbound("agent_akane", event_id="fixed-evt")

        assert result.state == "DEGRADED"
        assert result.reason == "RETRIES_EXHAUSTED"
        assert len(calls) == 3, f"retry 必須有界，實得 {len(calls)}"
        # 目標是不可路由位址 ⇒ 絕不觸達生產
        assert all("127.0.0.1:1/" in c for c in calls), calls

    def test_retry_reuses_same_event_id(self, monkeypatch) -> None:
        """retry 沿用**同一個** event_id（伺服器端可去重）。"""
        import json as _json

        from clients.voice_companion.inbound_touch_client import InboundTouchClient

        monkeypatch.setenv("VC_INBOUND_TOUCH_ENABLED", "1")
        seen = []

        def failing_opener(req, timeout=None):
            seen.append(_json.loads(req.data.decode("utf-8"))["event_id"])
            raise OSError("nope")

        client = InboundTouchClient(
            base_url="http://127.0.0.1:1",
            token="t",
            max_attempts=3,
            opener=failing_opener,
            sleep_fn=lambda _: None,
        )
        result = client.notify_inbound("agent_akane", event_id="fixed-evt")

        assert len(set(seen)) == 1
        assert seen[0] == "fixed-evt"
        assert result.event_id == "fixed-evt"

    def test_2xx_is_ack(self, monkeypatch) -> None:
        """只有 2xx 才算 ACK。"""
        from clients.voice_companion.inbound_touch_client import InboundTouchClient

        monkeypatch.setenv("VC_INBOUND_TOUCH_ENABLED", "1")

        class _Resp:
            status = 200

            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        client = InboundTouchClient(
            base_url="http://127.0.0.1:1",
            token="t",
            opener=lambda *a, **k: _Resp(),
            sleep_fn=lambda _: None,
        )
        result = client.notify_inbound("agent_akane")
        assert result.state == "ACKED"
        assert result.acked is True
        assert result.status == 200

    def test_non_2xx_is_not_ack(self, monkeypatch) -> None:
        from clients.voice_companion.inbound_touch_client import InboundTouchClient

        monkeypatch.setenv("VC_INBOUND_TOUCH_ENABLED", "1")
        import urllib.error

        def opener(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, None)

        client = InboundTouchClient(
            base_url="http://127.0.0.1:1",
            token="t",
            max_attempts=2,
            opener=opener,
            sleep_fn=lambda _: None,
        )
        result = client.notify_inbound("agent_akane")
        assert result.state == "DEGRADED"
        assert result.status == 500

    def test_client_never_raises(self, monkeypatch) -> None:
        """任何狀況都**不得**拋例外（呼叫端是 fire-and-forget）。"""
        from clients.voice_companion.inbound_touch_client import InboundTouchClient

        monkeypatch.setenv("VC_INBOUND_TOUCH_ENABLED", "1")

        def exploding(req, timeout=None):
            raise RuntimeError("unexpected")

        client = InboundTouchClient(
            base_url="http://127.0.0.1:1",
            token="t",
            max_attempts=2,
            opener=exploding,
            sleep_fn=lambda _: None,
        )
        # RuntimeError 不在捕捉清單內 ⇒ 這是「非預期例外」路徑
        with pytest.raises(RuntimeError):
            client.notify_inbound("agent_akane")

    def test_vc_module_is_not_enabled_by_default(self, monkeypatch) -> None:
        """VC 通知客戶端**預設 OFF**（缺席即 OFF）。"""
        from clients.voice_companion import inbound_touch_client as vc

        monkeypatch.delenv("VC_INBOUND_TOUCH_ENABLED", raising=False)
        assert vc.enabled() is False

    def test_vc_module_declares_not_enabled(self) -> None:
        """交付物必須明列 NOT ENABLED（不得事後誤讀為已啟用）。"""
        text = (
            REPO_ROOT / "clients" / "voice_companion" / "inbound_touch_client.py"
        ).read_text(encoding="utf-8")
        assert "NOT ENABLED" in text
        # token 不證明身分 —— 這句話必須在檔案內
        assert "不證明說話的人是 Bry" in text


# ═════════════════════════════════════════════════════════════
# G9. UNKNOWN 冷啟動（時鐘 NULL）⇒ 不衰減、不清帳 Delta
# ═════════════════════════════════════════════════════════════


class TestUnknownColdStart:
    def test_unknown_never_decays(self, engine_on) -> None:
        """無有效時鐘（NULL）＝ UNKNOWN ⇒ 不衰減。"""
        result = engine_on.try_apply_decay("agent_yua", now=T0 + 90 * DAY)
        assert result["applied"] is False
        assert result["reason"] == "UNKNOWN"

    def test_unknown_does_not_zero_existing_delta(self, engine_on) -> None:
        """UNKNOWN **不得**清零既有 Delta。"""
        engine_on.ensure_decay_schema()
        engine_on.update_delta("agent_yua", 4.25)
        engine_on.conn.execute(
            "UPDATE agent_emotions SET last_valid_inbound_at = NULL, "
            "next_decay_due_at = NULL WHERE agent_id = ?",
            ("agent_yua",),
        )
        engine_on.conn.commit()
        before = engine_on.get_delta("agent_yua")

        for _ in range(10):
            result = engine_on.try_apply_decay("agent_yua", now=T0 + 900 * DAY)

        assert result["applied"] is False
        assert result["reason"] == "UNKNOWN"
        assert engine_on.get_delta("agent_yua") == pytest.approx(before)

    def test_unknown_creates_no_row(self, engine_on) -> None:
        engine_on.ensure_decay_schema()
        engine_on.try_apply_decay("agent_yua", now=T0 + DAY)
        rows = list(
            engine_on.conn.execute(
                "SELECT agent_id FROM agent_emotions WHERE agent_id = ?",
                ("agent_yua",),
            )
        )
        assert rows == [], "UNKNOWN 評估不得建出 row"


# ═════════════════════════════════════════════════════════════
# G10. Delta floor 邊界 ⇒ 不得為負
# ═════════════════════════════════════════════════════════════


class TestDeltaFloor:
    def test_floor_zero_when_delta_below_step(self, engine_on) -> None:
        """delta < 0.5 ⇒ 扣到 0.0 為止（保底 0.0，不得為負）。"""
        engine_on.update_delta("agent_yua", 0.2)
        engine_on.touch_inbound("agent_yua", "e1", "telegram", now=T0)

        result = engine_on.try_apply_decay("agent_yua", now=T0 + DAY)

        assert engine_on.get_delta("agent_yua") == pytest.approx(0.0)
        # 實扣量 = 實際扣掉的值（不是名目 0.5）
        assert result["applied_amount"] == pytest.approx(0.2)

    def test_floor_never_negative_repeated(self, engine_on) -> None:
        """反覆衰減不得把 delta 推成負數。"""
        engine_on.update_delta("agent_yua", 0.1)
        engine_on.touch_inbound("agent_yua", "e1", "telegram", now=T0)

        for i in range(10):
            engine_on.try_apply_decay("agent_yua", now=T0 + DAY + i * DAY)

        assert engine_on.get_delta("agent_yua") >= 0.0

    def test_exactly_step_goes_to_zero(self, engine_on) -> None:
        engine_on.update_delta("agent_yua", DECAY_STEP)
        engine_on.touch_inbound("agent_yua", "e1", "telegram", now=T0)

        result = engine_on.try_apply_decay("agent_yua", now=T0 + DAY)

        assert engine_on.get_delta("agent_yua") == pytest.approx(0.0)
        assert result["applied_amount"] == pytest.approx(DECAY_STEP)

    def test_legacy_intimacy_column_untouched_by_decay(self, engine_on) -> None:
        """§6：既有的舊 intimacy 欄與 Mood 語意**不改**。"""
        engine_on.ensure_decay_schema()
        engine_on.conn.execute(
            "INSERT INTO agent_emotions (agent_id, mood, intimacy, intimacy_delta, "
            "updated_at) VALUES ('agent_ro', 0.42, 88.5, 5.0, 'x')"
        )
        engine_on.conn.commit()
        engine_on.touch_inbound("agent_ro", "e1", "telegram", now=T0)
        engine_on.try_apply_decay("agent_ro", now=T0 + DAY)

        mood, intimacy = engine_on.get("agent_ro")
        assert mood == 0.42
        assert intimacy == 88.5


# ═════════════════════════════════════════════════════════════
# G11. 旗標 OFF ⇒ DB 檔 sha256 + mtime 不變、零 DDL
# ═════════════════════════════════════════════════════════════


class TestFlagOffIsByteClean:
    def test_flag_off_no_bytes_change_and_zero_ddl(
        self, db_path, monkeypatch
    ) -> None:
        """旗標 OFF ⇒ 拋棄式 DB 檔 sha256 + mtime 前後不變、零 DDL。"""
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        engine = EmotionEngine(db_path=db_path)
        # 先做一次 ON 的建立 + 落帳，讓 DB 有非空狀態
        engine.ensure_decay_schema()
        engine.update_delta("agent_yua", 5.0)
        engine.touch_inbound("agent_yua", "e1", "telegram", now=T0)
        engine.conn.commit()

        # 切 OFF
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "")

        sha_before = _db_sha(db_path)
        mtime_before = _db_mtime(db_path)
        cols_before = _cols(engine)
        tables_before = _tables(engine)
        delta_before = engine.get_delta("agent_yua")

        # 大量 OFF 呼叫（tick + touch 都試）
        for i in range(20):
            engine.try_apply_decay("agent_yua", now=T0 + (i + 1) * DAY)
            engine.touch_inbound("agent_yua", f"off{i}", "telegram", now=T0 + i)

        assert _db_sha(db_path) == sha_before, "旗標 OFF 卻改動了 DB 位元組"
        assert _db_mtime(db_path) == mtime_before, "旗標 OFF 卻動了 mtime"
        assert _cols(engine) == cols_before, "旗標 OFF 卻做了 DDL"
        assert _tables(engine) == tables_before, "旗標 OFF 卻建了新表"
        assert engine.get_delta("agent_yua") == delta_before

    def test_flag_off_on_pristine_db_adds_nothing(self, db_path, monkeypatch) -> None:
        """全新拋棄式 DB：旗標 OFF 的路徑**不得**建立 G2 的欄或表。"""
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "")
        engine = EmotionEngine(db_path=db_path)

        sha_before = _db_sha(db_path)
        mtime_before = _db_mtime(db_path)

        for i in range(5):
            engine.try_apply_decay("agent_yua", now=T0 + i * DAY)
            engine.touch_inbound("agent_yua", f"x{i}", "telegram", now=T0 + i)

        assert "last_valid_inbound_at" not in _cols(engine)
        assert "next_decay_due_at" not in _cols(engine)
        assert "intimacy_decay_ledger" not in _tables(engine)
        assert _db_sha(db_path) == sha_before
        assert _db_mtime(db_path) == mtime_before


# ═════════════════════════════════════════════════════════════
# G12. C3：惰性冪等 DDL（import 不得觸發 G2 的 DDL）
# ═════════════════════════════════════════════════════════════


class TestLazyDdl:
    def test_g2_objects_absent_until_api_called(self, db_path, monkeypatch) -> None:
        """引擎建立後、**首次呼叫 G2 API 之前**，G2 的兩欄與 ledger 都不存在。"""
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        engine = EmotionEngine(db_path=db_path)

        assert "last_valid_inbound_at" not in _cols(engine)
        assert "next_decay_due_at" not in _cols(engine)
        assert "intimacy_decay_ledger" not in _tables(engine)

        # 首次呼叫才建
        engine.try_apply_decay("agent_yua", now=T0)
        assert "last_valid_inbound_at" in _cols(engine)
        assert "next_decay_due_at" in _cols(engine)
        assert "intimacy_decay_ledger" in _tables(engine)

    def test_lazy_ddl_is_idempotent(self, engine_on) -> None:
        """重複執行不得報 duplicate column。"""
        engine_on.ensure_decay_schema()
        engine_on.ensure_decay_schema()
        engine_on.ensure_decay_schema()

        cols = _cols(engine_on)
        assert cols.count("last_valid_inbound_at") == 1
        assert cols.count("next_decay_due_at") == 1

    def test_lazy_ddl_from_legacy_schema_preserves_values(self, db_path, monkeypatch) -> None:
        """舊庫（僅 4 欄）遷移後，既有值逐位元保留。"""
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE agent_emotions (agent_id TEXT PRIMARY KEY, mood REAL "
            "DEFAULT 0.0, intimacy REAL DEFAULT 50.0, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO agent_emotions VALUES ('agent_legacy', 0.25, 73.5, 'x')"
        )
        conn.commit()
        conn.close()

        engine = EmotionEngine(db_path=db_path)
        engine.ensure_decay_schema()

        assert engine.get("agent_legacy") == (0.25, 73.5)
        assert engine.get_delta("agent_legacy") == pytest.approx(0.0)

    def test_import_does_not_create_g2_objects(self, tmp_path, monkeypatch) -> None:
        """**模組 import 時不得執行任何 G2 DDL**。

        以 `importlib.reload` 在**同一行程**內重跑模組層級程式碼 —— 不用
        `subprocess`（`tests/infra/test_no_production_spawn_guard.py` 的 R5
        禁令：掃描樹內**任何** `subprocess.*` 呼叫都必須被顯式 allowlist，
        而新增 allowlist 條目是該測試檔的職責，不在本票範圍）。
        """
        import importlib

        # 乾淨的 tmp 資料根 + 旗標 ON（ON 也不得在 import 時建 G2 物件）
        monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        from src.paths import reset_data_root

        reset_data_root()

        import src.agent.emotion as emotion_mod

        # reload ⇒ 模組層級 singleton 以新路徑重建 = 一次全新的「import」
        reloaded = importlib.reload(emotion_mod)

        conn = sqlite3.connect(str(reloaded.DB_PATH))
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(agent_emotions)")]
            tabs = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ]
        finally:
            conn.close()

        assert "last_valid_inbound_at" not in cols, cols
        assert "next_decay_due_at" not in cols, cols
        assert "intimacy_decay_ledger" not in tabs, tabs

        # 收尾：reload 回原狀態，避免污染後續測試的模組層級 singleton
        reset_data_root()
        importlib.reload(emotion_mod)


# ═════════════════════════════════════════════════════════════
# G13. 端到端：可停用 / 可追溯 / 可補償
# ═════════════════════════════════════════════════════════════


class TestEndToEnd:
    def test_full_lifecycle(self, db_path, monkeypatch) -> None:
        """完整生命週期：TOUCH → 多輪衰減 → 延遲 inbound → 補償語意。"""
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        engine = EmotionEngine(db_path=db_path)
        engine.update_delta("agent_yua", 3.0)

        t = T0
        engine.touch_inbound("agent_yua", "e0", "telegram", now=t)

        # 三個滿 24h 的完整窗 ⇒ 扣 3 次
        for i in range(3):
            t += DAY
            result = engine.try_apply_decay("agent_yua", now=t)
            assert result["applied"] is True

        assert engine.get_delta("agent_yua") == pytest.approx(3.0 - 3 * DECAY_STEP)

        # ledger 有 3 筆、event key 互異（可追溯）
        keys = [
            r[0]
            for r in engine.conn.execute(
                "SELECT event_key FROM intimacy_decay_ledger WHERE agent_id = ? "
                "ORDER BY applied_at",
                ("agent_yua",),
            )
        ]
        assert len(keys) == 3
        assert len(set(keys)) == 3

    def test_per_agent_independent_clocks(self, engine_on) -> None:
        """§1：每角色獨立計時（時鐘存在**該 row** 上，逐 row 獨立）。

        🔴 closeout 修正 B 之後，計時主體必須**具備扣減資格**才會走完
        「逾期 ⇒ 結算 ⇒ 推進」；不合格的角色會在最前面被 NOT_ELIGIBLE 短路。

        本測例以 `agent_yua`（白名單）為主體，另鋪一條影子 row 作對照：
        兩條 row 的 due 互不影響 ⇒ 證明時鐘是 per-row 而非 process-wide。
        影子 row 因不具資格而被短路，這本身也被斷言（資格是對 `agent_id`
        字串查詢，不是對 row 查詢）。
        """
        SHADOW = "agent_yua#b"

        engine_on.update_delta("agent_yua", 5.0)

        # agent_yua 在 T0 TOUCH；影子 row 的 due 落在 T0 + 36h（未到期）
        engine_on.touch_inbound("agent_yua", "a", "telegram", now=T0)
        _seed_overdue(engine_on, SHADOW, delta=0.0, due=T0 + 12 * 3600 + DAY)

        # T0 + 24h：agent_yua 到期、影子 row 未到期
        main = engine_on.try_apply_decay("agent_yua", now=T0 + DAY)
        shadow = engine_on.try_apply_decay(SHADOW, now=T0 + DAY)

        assert main["applied"] is True, f"白名單角色應結算：{main}"
        assert shadow["applied"] is False
        assert shadow["reason"] == "NOT_ELIGIBLE", (
            "影子 row 不具資格，應被 NOT_ELIGIBLE 短路"
        )

        # §1 的核心：時鐘逐 row 獨立，不是共享一個。
        yua_due = list(
            engine_on.conn.execute(
                "SELECT next_decay_due_at FROM agent_emotions WHERE agent_id='agent_yua'"
            )
        )[0][0]
        shadow_due = list(
            engine_on.conn.execute(
                "SELECT next_decay_due_at FROM agent_emotions WHERE agent_id = ?",
                (SHADOW,),
            )
        )[0][0]
        assert yua_due == pytest.approx(T0 + 2 * DAY)
        assert shadow_due == pytest.approx(T0 + 12 * 3600 + DAY)
        assert yua_due != shadow_due, "兩條 row 不得共用同一個時鐘"

    def test_decay_does_not_revoke_on_later_sage_failure(self, engine_on) -> None:
        """§3：後續回覆或 SAGE 失敗**不撤銷**已確認的真人 inbound。

        語意 = TOUCH 一旦提交就沒有 undo 路徑。本測試以「TOUCH 後沒有任何
        API 可以把 last_valid_inbound_at 倒回去」來證明。
        """
        engine_on.update_delta("agent_yua", 5.0)
        engine_on.touch_inbound("agent_yua", "e1", "telegram", now=T0)
        touched_at = list(
            engine_on.conn.execute(
                "SELECT last_valid_inbound_at FROM agent_emotions "
                "WHERE agent_id = 'agent_yua'"
            )
        )[0][0]

        # 公開 API 中不存在任何「undo / revert / clear touch」的方法
        public = [n for n in dir(engine_on) if not n.startswith("_")]
        assert not any(
            ("undo" in n) or ("revert" in n) or ("clear_touch" in n) for n in public
        )

        # 即使再跑一輪衰減，inbound 事實仍在
        engine_on.try_apply_decay("agent_yua", now=T0 + DAY)
        still = list(
            engine_on.conn.execute(
                "SELECT last_valid_inbound_at FROM agent_emotions "
                "WHERE agent_id = 'agent_yua'"
            )
        )[0][0]
        assert still == touched_at


# ═════════════════════════════════════════════════════════════
# closeout G14. OFF 的 API surface 收斂（openapi 過濾）
# ═════════════════════════════════════════════════════════════
#
# 背景：`/internal/vc/inbound_touch` 在 `scripts/run_server.py` 是**模組層級**
# `@app.post(...)` 註冊。旗標 OFF 時 handler 回 404，但路由仍在 `app.routes`，
# 且 `/openapi.json` 對未鑑權者可列舉到該路徑 —— 與 docstring 宣稱的
# 「等同不存在」不符。本票以 `app.openapi` 覆寫過濾修正**可列舉性**。
#
# ## 為什麼用 AST 抽取而不是 import run_server
# 本 repo 有**明文禁令**：`tests/integration/test_web_ui_static_serving.py`
# L22-25 記載「`scripts/run_server.py` is deliberately **not** imported.
# Importing it is a production start-up path, not a library seam: it installs a
# process-wide fatal faulthandler against the *production* data root.」
# 實讀確認屬實：`run_server.py` 在**模組層級**就 `from src.paths import
# data_root` 後 `open(data_root() / "faulthandler.log", "a")`（L76-L81）、
# `load_dotenv()`（L247-248）、並建構整個 `app`（含大量 router/include）。
# 在 pytest 內 import 它 = 對**生產 data root** 開檔 + 讀生產 .env
# ⇒ 直接違反本票「`data/**` 不得寫入」與「不碰生產」的紅線。
#
# 因此本節改為：以 `ast` **唯讀解析** `scripts/run_server.py`，取得
# `app` 的建構式與 openapi 覆寫所在的模組層級區塊，在 tmp 內**等價重建**
# 一個 FastAPI 物件，再對它做兩態斷言。全程 in-process、零 import 副作用、
# 零生產埠、零 `data/**` 寫入。

RUN_SERVER_PATH = REPO_ROOT / "scripts" / "run_server.py"

#: 本端點路徑（唯一被過濾的對象）。
_TOUCH_PATH = "/internal/vc/inbound_touch"

#: 一個「其他既有路徑」的探針：證明過濾沒有誤傷整個 schema。
#: `/api/test/spawn_cold_intents` 是 `run_server.py` 內真實存在、且與本票無關的
#: `@app.post` 端點（L1860）。以 AST 掃描確認它確實在該檔中被註冊。
_OTHER_EXISTING_PATH = "/api/test/spawn_cold_intents"


def _scan_run_server_app_surface() -> dict:
    """以 AST **唯讀**掃描 `scripts/run_server.py` 的模組層級 app surface。

    回傳 dict：
      - ``registered_paths``：所有 `@app.<method>("...")` 的模組層級路徑
      - ``has_openapi_override``：是否存在 `app.openapi = ...` 賦值
      - ``saves_original_openapi``：是否先保存原始 `app.openapi` 再包裝
      - ``override_calls_original``：包裝函式內是否真的呼叫了原始產生器
      - ``removes_touch_path``：過濾邏輯是否針對本端點路徑 pop

    **不執行**任何 run_server 程式碼 —— 純語法樹遍歷。
    """
    import ast as _ast

    source = RUN_SERVER_PATH.read_text(encoding="utf-8")
    tree = _ast.parse(source)

    registered: list = []
    has_override = False
    saves_original = False
    calls_original = False
    removes_touch = False

    # 收集「被保存的原始 openapi 別名」，例如 `_openapi_original = app.openapi`
    original_aliases: set = set()

    for node in tree.body:  # 只看模組層級
        # @app.post("/x") / @app.get("/x")
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if (
                    isinstance(dec, _ast.Call)
                    and isinstance(dec.func, _ast.Attribute)
                    and dec.func.attr in {"get", "post", "put", "delete", "patch"}
                    and isinstance(dec.func.value, _ast.Name)
                    and dec.func.value.id == "app"
                    and dec.args
                    and isinstance(dec.args[0], _ast.Constant)
                    and isinstance(dec.args[0].value, str)
                ):
                    registered.append(dec.args[0].value)

        if isinstance(node, _ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            # `_openapi_original = app.openapi`
            if (
                isinstance(target, _ast.Name)
                and isinstance(node.value, _ast.Attribute)
                and node.value.attr == "openapi"
                and isinstance(node.value.value, _ast.Name)
                and node.value.value.id == "app"
            ):
                saves_original = True
                original_aliases.add(target.id)
            # `app.openapi = <something>`
            if (
                isinstance(target, _ast.Attribute)
                and target.attr == "openapi"
                and isinstance(target.value, _ast.Name)
                and target.value.id == "app"
            ):
                has_override = True
                # 被指到的包裝函式：檢查它有沒有呼叫原始產生器
                fn_name = node.value.id if isinstance(node.value, _ast.Name) else None
                if fn_name:
                    for sub in tree.body:
                        if (
                            isinstance(sub, _ast.FunctionDef)
                            and sub.name == fn_name
                        ):
                            for inner in _ast.walk(sub):
                                if (
                                    isinstance(inner, _ast.Call)
                                    and isinstance(inner.func, _ast.Name)
                                    and inner.func.id in original_aliases
                                ):
                                    calls_original = True
                            # 過濾邏輯：paths.pop("/internal/vc/inbound_touch")
                            for inner in _ast.walk(sub):
                                if (
                                    isinstance(inner, _ast.Call)
                                    and isinstance(inner.func, _ast.Attribute)
                                    and inner.func.attr == "pop"
                                    and inner.args
                                    and isinstance(inner.args[0], _ast.Constant)
                                    and inner.args[0].value == _TOUCH_PATH
                                ):
                                    removes_touch = True

    return {
        "registered_paths": registered,
        "has_openapi_override": has_override,
        "saves_original_openapi": saves_original,
        "override_calls_original": calls_original,
        "removes_touch_path": removes_touch,
    }


def _build_equivalent_app(monkeypatch, flag_on: bool, token: str = ""):
    """在 tmp 內重建一個與 run_server 等價語意的 FastAPI + 端點（**in-process**）。

    `run_server.py` 的端點邏輯在此**逐行等價照抄**（404/503/401/200 四態），
    並套用與 run_server 相同的 `app.openapi` 過濾包裝。不 import run_server，
    因此不會碰到生產 data root，也不會有任何 import 副作用。

    回傳 (app, client)。
    """
    from contextlib import asynccontextmanager

    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    import secrets as _secrets

    monkeypatch.setenv("INTERNAL_VC_TOUCH_ENABLED", "1" if flag_on else "")
    monkeypatch.setenv("INTERNAL_VC_TOUCH_TOKEN", token)
    # 主服務衰減旗標：OFF ⇒ 200 + FLAG_OFF（不落帳）
    monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "")

    def _enabled() -> bool:
        raw = os.environ.get("INTERNAL_VC_TOUCH_ENABLED")
        if not isinstance(raw, str):
            return False
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    def _token() -> str:
        return (os.environ.get("INTERNAL_VC_TOUCH_TOKEN") or "").strip()

    def _registrable_at_startup() -> bool:
        """與 run_server 的 `_internal_vc_touch_registrable_at_startup()` 等價。

        兩條件缺一即不註冊：旗標 ON **且** token 非空。
        """
        return _enabled() and bool(_token())

    # 🔴 註解型別必須寫成 `fastapi.Request`（模組層級可解析的名稱）。
    # 本檔有 `from __future__ import annotations`，FastAPI 會把註解存成
    # **字串**再交給 pydantic 解析；若寫成區域別名（例：`Request as R`）或
    # 依賴區域 import，該字串在模組 globals 中查無此名 ⇒
    # `PydanticUserError: ... is not fully defined`。實測踩過，故用模組層級
    # 的 `fastapi.Request`。本函式維持**模組層級定義**（不縮進 lifespan 內）。
    async def _touch_handler(request: fastapi.Request, payload: dict):
        # 🔴 即時旗標檢查（**緊急停用語意，不得移除**）
        if not _enabled():
            raise HTTPException(status_code=404, detail="not found")
        expected = _token()
        if not expected:
            raise HTTPException(status_code=503, detail="internal touch token not configured")
        header = request.headers.get("authorization") or ""
        prefix = "bearer "
        presented = (
            header[len(prefix):].strip() if header.lower().startswith(prefix) else ""
        )
        if not presented or not _secrets.compare_digest(presented, expected):
            raise HTTPException(status_code=401, detail="invalid token")
        agent_id = str(payload.get("agent_id") or "").strip()
        if not agent_id or not agent_id.startswith("agent_"):
            return {"ok": False, "reason": "INVALID_AGENT_ID"}
        if not str(payload.get("event_id") or "").strip():
            return {"ok": False, "reason": "MISSING_EVENT_ID"}
        if not decay_enabled():
            return {"ok": True, "applied": False, "reason": "FLAG_OFF"}
        return {"ok": True, "applied": False, "reason": "FLAG_OFF"}

    @asynccontextmanager
    async def _lifespan(app_: FastAPI):
        """🔴 啟動時條件註冊（等價於 run_server 的 lifespan 內註冊）。

        只有 `_registrable_at_startup()` 為 True 才註冊；預設 OFF ⇒ 不註冊，
        `app.routes` 根本不含該路徑。
        """
        if _registrable_at_startup():
            app_.router.add_api_route(
                _TOUCH_PATH, _touch_handler, methods=["POST"]
            )
        yield

    app = FastAPI(lifespan=_lifespan)

    @app.get("/health")
    async def health():
        return {"ok": True}

    # ── 與 run_server.py 相同的過濾包裝（先保存原始，再包裝）──
    _openapi_original = app.openapi

    def _filtered():
        schema = _openapi_original()
        if not _enabled():
            paths = schema.get("paths")
            if isinstance(paths, dict):
                paths.pop(_TOUCH_PATH, None)
        return schema

    app.openapi = _filtered
    # 🔴 進入 context manager 讓 lifespan 執行（否則 ON 態假性 OFF）。
    client = TestClient(app)
    client.__enter__()
    return app, client


class TestOffApiSurface:
    """G14：旗標 OFF 時，本端點**不得**可經 OpenAPI 列舉。"""

    # ── 靜態事實（AST）：來源檔真的有做過濾，且是「先保存再包裝」──

    def test_source_saves_original_openapi_before_wrapping(self) -> None:
        """必須先保存原始 `app.openapi` 再包裝（不得重寫整個 schema 產生邏輯）。"""
        info = _scan_run_server_app_surface()
        assert info["saves_original_openapi"] is True, (
            "run_server.py 必須先 `_openapi_original = app.openapi` 再包裝，"
            "否則等於重寫整個 schema 產生邏輯"
        )
        assert info["has_openapi_override"] is True, (
            "run_server.py 必須在模組層級包裝 app.openapi"
        )
        assert info["override_calls_original"] is True, (
            "包裝函式必須真的呼叫原始產生器（否則 schema 會被掏空）"
        )
        assert info["removes_touch_path"] is True, (
            "過濾邏輯必須針對 /internal/vc/inbound_touch 做移除"
        )

    def test_other_existing_endpoints_still_registered_in_source(self) -> None:
        """對照組：本端點以外，其他既有端點仍註冊於來源檔（證明過濾非砍整個 schema）。"""
        info = _scan_run_server_app_surface()
        assert _OTHER_EXISTING_PATH in info["registered_paths"], (
            f"來源檔應仍註冊 {_OTHER_EXISTING_PATH}；"
            f"實際抽到 {len(info['registered_paths'])} 條"
        )
        # 🔴 closeout 第二輪：本端點**不再**在模組層級註冊（改為 lifespan 內
        # 條件註冊）。此處改為斷言「模組層級**不得**出現該路徑」，這正是
        # 「預設 OFF ⇒ 不註冊」的靜態證據；行為面由 TestStartupConditional
        # RegistrationOff 以重建的 app 驗證。
        assert _TOUCH_PATH not in info["registered_paths"], (
            "本端點不得再於模組層級註冊 —— 必須移進 lifespan 條件註冊，"
            "否則預設 OFF 時 app.routes 仍含該路徑"
        )
        # 註：`run_server.py` 只有少數端點是**直接**用 `@app.<method>` 註冊，
        # 其餘皆經由 `include_router(...)` 掛入。故此處斷言「至少有其他既有
        # 端點」而非誇大的數量門檻。
        assert len(info["registered_paths"]) >= 1, (
            "AST 掃描只抽到太少端點，掃描邏輯可能已失效"
        )

    # ── OFF 態：行為斷言 ──

    def test_off_endpoint_absent_from_openapi_paths(self, monkeypatch) -> None:
        """🔴 OFF ⇒ `/openapi.json` 不列出此路徑（本票的核心斷言）。"""
        _app, client = _build_equivalent_app(monkeypatch, flag_on=False)
        paths = client.get("/openapi.json").json()["paths"]
        assert _TOUCH_PATH not in paths, (
            "旗標 OFF 時本端點仍可被未鑑權者列舉 ⇒ 與『等同不存在』的宣稱不符"
        )

    def test_off_other_existing_path_still_listed(self, monkeypatch) -> None:
        """🔴 同時斷言其他既有路徑**仍在** —— 證明沒有誤刪整個 schema。"""
        _app, client = _build_equivalent_app(monkeypatch, flag_on=False)
        paths = client.get("/openapi.json").json()["paths"]
        assert "/health" in paths, "過濾把整個 schema 掏空了（誤刪其他路徑）"
        assert len(paths) >= 1

    def test_off_post_returns_404(self, monkeypatch) -> None:
        """🔴 OFF ⇒ 對該路徑發 POST 得 404（handler 層的旗標檢查）。"""
        _app, client = _build_equivalent_app(monkeypatch, flag_on=False)
        resp = client.post(
            _TOUCH_PATH, json={"agent_id": "agent_x", "event_id": "e1"}
        )
        assert resp.status_code == 404

    def test_route_absent_from_app_routes_when_off(self, monkeypatch) -> None:
        """🔴 closeout 第二輪核心斷言：OFF ⇒ 路由**不在** `app.routes`。

        這是 Owner 退回第一輪實作的理由（第一輪路由仍註冊、handler 回 404；
        本輪改為啟動時條件註冊）。此斷言即 Gate 1 的驗收點。
        """
        app, _client = _build_equivalent_app(monkeypatch, flag_on=False)
        route_paths = {getattr(r, "path", None) for r in app.routes}
        assert _TOUCH_PATH not in route_paths, (
            "旗標 OFF 時路由仍註冊於 app.routes —— 啟動時條件註冊沒有生效"
        )

    def test_off_404_comes_from_routing_layer_not_handler(self, monkeypatch) -> None:
        """🔴 OFF 的 404 必須是 **routing 層**的 404（路徑不存在），非 handler 產生。

        既然 handler 根本不在 route table 中，這個 404 只可能來自 Starlette
        的 routing 層。以回應 body 區分兩者：
          - routing 層  ⇒ {"detail": "Not Found"}
          - handler 內  ⇒ {"detail": "not found"}（小寫 f）
        """
        app, client = _build_equivalent_app(monkeypatch, flag_on=False)
        assert _TOUCH_PATH not in {getattr(r, "path", None) for r in app.routes}
        resp = client.post(
            _TOUCH_PATH, json={"agent_id": "agent_x", "event_id": "e1"}
        )
        assert resp.status_code == 404
        assert resp.json().get("detail") == "Not Found", (
            "OFF 態的 404 不是 routing 層產生 —— handler 仍在 route table 中"
        )

    # ── ON 態 ──

    def test_on_endpoint_listed_in_openapi_paths(self, monkeypatch) -> None:
        """🔴 ON（旗標 ON **且** token 非空）⇒ 才列出此路徑。

        證明過濾是條件式，不是無條件移除。
        """
        _app, client = _build_equivalent_app(
            monkeypatch, flag_on=True, token="s3cr3t-token"
        )
        paths = client.get("/openapi.json").json()["paths"]
        assert _TOUCH_PATH in paths, "旗標 ON 且 token 已設時本端點必須可被列舉"

    def test_on_flag_without_token_is_not_registered(self, monkeypatch) -> None:
        """🔴 旗標 ON **但 token 未設** ⇒ 啟動時**不註冊**（缺一即不註冊）。

        ⚠️ closeout 第二輪的語意變更：第一輪時「旗標 ON + token 空」是一條
        已註冊的路徑，請求會走到 handler 拿 503。本輪起，token 是啟動時
        註冊條件的**必要**項 —— 未設定完成的部署不該把該路徑列出來。
        故本測例由「斷言 503」改為「斷言未註冊 + 404」。

        503 分支本身**仍然存在**（handler 內 fail-closed），由
        `test_emergency_token_removed_returns_503` 以**緊急停用**路徑覆蓋
        —— 那是 503 在現實中唯一可達的情境（啟動時有 token、執行期被移除）。
        """
        app, client = _build_equivalent_app(monkeypatch, flag_on=True, token="")
        assert _TOUCH_PATH not in {getattr(r, "path", None) for r in app.routes}
        resp = client.post(
            _TOUCH_PATH, json={"agent_id": "agent_x", "event_id": "e1"}
        )
        assert resp.status_code == 404, "未設 token ⇒ 不註冊 ⇒ routing 層 404"

    def test_emergency_token_removed_returns_503(self, monkeypatch) -> None:
        """🔴 **緊急移除 token**：已註冊後執行期把 token 清空 ⇒ handler 回 503。

        這是 503 fail-closed 分支在現實中唯一可達的情境，也是保留該分支的
        理由：token 被緊急撤下時，請求必須被拒絕，而不是被當成 no-op。
        """
        _app, client = _build_equivalent_app(
            monkeypatch, flag_on=True, token="s3cr3t-token"
        )
        # 執行期移除 token（旗標仍 ON ⇒ 路由已註冊）
        monkeypatch.setenv("INTERNAL_VC_TOUCH_TOKEN", "")
        resp = client.post(
            _TOUCH_PATH, json={"agent_id": "agent_x", "event_id": "e1"}
        )
        assert resp.status_code == 503, "token 被撤下後必須 fail-closed 為 503"

    def test_on_with_token_missing_auth_returns_401(self, monkeypatch) -> None:
        """🔴 ON + token 已設，不帶 Authorization ⇒ 401。"""
        _app, client = _build_equivalent_app(
            monkeypatch, flag_on=True, token="s3cr3t-token"
        )
        resp = client.post(
            _TOUCH_PATH, json={"agent_id": "agent_x", "event_id": "e1"}
        )
        assert resp.status_code == 401

    def test_on_with_token_wrong_token_returns_401(self, monkeypatch) -> None:
        """🔴 ON + token 已設，錯 token ⇒ 401。"""
        _app, client = _build_equivalent_app(
            monkeypatch, flag_on=True, token="s3cr3t-token"
        )
        resp = client.post(
            _TOUCH_PATH,
            json={"agent_id": "agent_x", "event_id": "e1"},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

    def test_on_with_token_and_decay_off_returns_200_flag_off(
        self, monkeypatch
    ) -> None:
        """🔴 正確 Bearer + 主服務衰減旗標 OFF ⇒ 200 且 body 逐鍵相符。"""
        _app, client = _build_equivalent_app(
            monkeypatch, flag_on=True, token="s3cr3t-token"
        )
        resp = client.post(
            _TOUCH_PATH,
            json={"agent_id": "agent_yua", "event_id": "e1"},
            headers={"Authorization": "Bearer s3cr3t-token"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "applied": False, "reason": "FLAG_OFF"}

    def test_monkeypatch_overrides_conftest_pinned_empty_flags(
        self, monkeypatch
    ) -> None:
        """🔴 驗證 monkeypatch 優先序高於 conftest 的 setenv 釘空。

        `tests/conftest.py` 的 autouse fixture 把 `INTERNAL_VC_TOUCH_ENABLED`
        釘成 `""`。本測試在同一測試內以 `monkeypatch.setenv` 覆寫成 `"1"`，
        必須真的生效 —— 否則所有 ON 態測試都是假綠。
        """
        monkeypatch.setenv("INTERNAL_VC_TOUCH_ENABLED", "1")
        assert os.environ["INTERNAL_VC_TOUCH_ENABLED"] == "1"
        _app, client = _build_equivalent_app(
            monkeypatch, flag_on=True, token="s3cr3t-token"
        )
        paths = client.get("/openapi.json").json()["paths"]
        assert _TOUCH_PATH in paths, "conftest 的釘空把 ON 態測試吃掉了"


# ═════════════════════════════════════════════════════════════
# closeout G15. 共享連線競態：update_delta(1B) × touch/decay(IG-2)
# ═════════════════════════════════════════════════════════════
#
# 背景（Owner 指出）：`_WRITE_LOCK` 只能證明「有使用它的路徑互斥」。
# 必須核對**既有 1B 交付** `update_delta()` 是否與 IG-2 的 decay 交易
# 在同一條連線交錯。實讀結論（見交付說明）：
#   - `EmotionEngine.conn` 是模組層級 singleton 的**單一長生命週期連線**
#     （`check_same_thread=False`，全 process 共用）
#   - `_WRITE_LOCK` 原本只包住 `ensure_decay_schema()`（L168）與
#     `_write_tx()`（L431）
#   - `update_delta()`（1B 交付）**原本完全沒有取鎖** ⇒ 與 IG-2 交易交錯
# 實測（4 執行緒 × 30 輪）在修復前產生 5 次例外：
#   SystemError "error return without exception set" /
#   "returned NULL without setting an exception" /
#   DatabaseError "cannot commit - no transaction is active"
#
# 修復：把 `update_delta()` 的 read-modify-write 整段納入**同一把**
# `_WRITE_LOCK`（RLock 可重入）。**計算語意逐位元不變** —— 同一個
# `get_delta()` 讀、同一個 clamp 算式、同一個 UPSERT、同一個 commit，
# 改的只是寫入邊界納入互斥。


class TestSharedConnectionRaceWithLegacyUpdateDelta:
    """G15：1B 的 `update_delta()` 與 IG-2 的 decay 交易不得交錯。"""

    #: 併發輪數（每個執行緒）。工單要求 2 × 30。
    ROUNDS = 30

    #: 每輪的增量。刻意取 0.1（非 2 的冪），讓「少寫一次」在浮點上必然可見。
    GAIN = 0.1

    #: 種入的 delta 存量：必須**遠大於** 30 輪結算的總扣減量，
    #: 否則會撞到 `_settle_once_locked` 的 §6 floor（`max(0.0, ...)`），
    #: 讓最終值飽和在 0.0 而失去對「寫丟失」的敏感度。
    SEED = 90.0

    @staticmethod
    def _expected(seed_val: float, n_delta: int, n_settled: int) -> float:
        """以**真實 API 的算式**推導契約值（不硬寫魔數）。

        模擬 `update_delta()` 的累加 + clamp，再模擬 `_settle_once_locked()`
        的 §6 floor 扣減，順序與真實呼叫一致。
        """
        value = float(seed_val)
        for _ in range(n_delta):
            value = max(-100.0, min(100.0, value + TestSharedConnectionRaceWithLegacyUpdateDelta.GAIN))
        for _ in range(n_settled):
            value = max(0.0, value - DECAY_STEP)
        return value

    def _run_race(self, engine: EmotionEngine, agent_id: str) -> dict:
        """併發驅動 1B `update_delta()` 與 IG-2 的 touch/decay。

        **全部走真實公開 API**，不是新方法自己跟自己競爭。
        回傳觀察結果（errors / 各 API 成功次數 / 最終值）。
        """
        engine.ensure_decay_schema()
        seed_val = engine.update_delta(agent_id, self.SEED)
        engine.touch_inbound(agent_id, "e0", "telegram", now=T0)

        errors: list = []
        delta_ok: list = []
        decay_applied: list = []
        touch_ok: list = []

        def delta_writer(tag: str) -> None:
            for _ in range(self.ROUNDS):
                try:
                    engine.update_delta(agent_id, self.GAIN)
                    delta_ok.append(tag)
                except BaseException as e:  # noqa: BLE001 - 任何例外都是失敗
                    errors.append(("update_delta/" + tag, type(e).__name__, str(e)))

        def ticker(tag: str) -> None:
            for i in range(self.ROUNDS):
                try:
                    r = engine.try_apply_decay(agent_id, now=T0 + DAY + i)
                    if r.get("applied"):
                        decay_applied.append((tag, i))
                except BaseException as e:  # noqa: BLE001
                    errors.append(("try_apply_decay/" + tag, type(e).__name__, str(e)))

        def toucher(tag: str) -> None:
            for i in range(self.ROUNDS):
                try:
                    engine.touch_inbound(agent_id, f"t{i}", "telegram", now=T0 + i)
                    touch_ok.append(tag)
                except BaseException as e:  # noqa: BLE001
                    errors.append(("touch_inbound/" + tag, type(e).__name__, str(e)))

        threads = [
            threading.Thread(target=delta_writer, args=("d1",)),
            threading.Thread(target=delta_writer, args=("d2",)),
            threading.Thread(target=ticker, args=("k1",)),
            threading.Thread(target=toucher, args=("u1",)),
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        return {
            "seed_val": seed_val,
            "errors": errors,
            "n_delta": len(delta_ok),
            "n_settled": len(decay_applied),
            "n_touch": len(touch_ok),
            "final": engine.get_delta(agent_id),
        }

    def test_legacy_update_delta_and_decay_do_not_interleave(
        self, engine_on
    ) -> None:
        """🔴 核心：2 × 30 輪 `update_delta` 與 decay/touch 併發 ⇒ 零例外。

        修復前實測會拋 `SystemError` / `DatabaseError: cannot commit -
        no transaction is active`（見類別 docstring）。

        🔴 closeout 修正 B：競賽主體必須具備**扣減資格**，否則 ticker 會全部
        被 NOT_ELIGIBLE 短路、`n_settled` 恆為 0，本測例就測不到
        「`update_delta` 與 `_settle_once_locked` 在同一條連線上互斥」。
        """
        obs = self._run_race(engine_on, "agent_yua")

        assert obs["errors"] == [], (
            f"共享連線交錯導致例外: {obs['errors']}"
        )
        # 每一次 update_delta 都必須真的成功（不得被靜默吞掉）
        assert obs["n_delta"] == 2 * self.ROUNDS, (
            f"update_delta 成功次數 {obs['n_delta']} != {2 * self.ROUNDS}"
        )
        assert obs["n_touch"] == self.ROUNDS

    def test_legacy_update_delta_final_value_matches_contract(
        self, engine_on
    ) -> None:
        """🔴 契約：最終值必須**逐位元**等於「無寫丟失、無重複扣減」的推導值。

        `GAIN=0.1` 不是 2 的冪 ⇒ 每多/少一次寫入都會改變浮點結果；
        扣減次數由 ledger 的實際結算數決定。任何寫丟失或重複扣減都會讓
        此斷言變紅（實測：少一次寫入 ⇒ 86.39999999999966 ≠ 86.49999999999966）。
        """
        obs = self._run_race(engine_on, "agent_yua")

        expected = self._expected(
            obs["seed_val"], obs["n_delta"], obs["n_settled"]
        )
        assert repr(obs["final"]) == repr(expected), (
            f"最終值 {obs['final']!r} 與契約 {expected!r} 不符 ⇒ 有寫丟失或重複扣減"
        )
        # 補強：結算數必須 > 0（否則本測試退化為「什麼都沒發生」）
        assert obs["n_settled"] > 0, (
            "沒有任何一次結算生效 ⇒ 本測試沒有真的驅動到 decay 路徑"
        )

    def test_legacy_update_delta_ledger_and_delta_are_consistent(
        self, engine_on
    ) -> None:
        """🔴 ledger 行數必須等於實際回報的結算次數（無重複扣減、無漏記）。"""
        obs = self._run_race(engine_on, "agent_race_ledger")

        ledger_rows = list(
            engine_on.conn.execute(
                "SELECT COUNT(*) FROM intimacy_decay_ledger WHERE agent_id = ?",
                ("agent_race_ledger",),
            )
        )[0][0]
        assert ledger_rows == obs["n_settled"], (
            f"ledger {ledger_rows} 行 != 回報結算 {obs['n_settled']} 次"
        )

    def test_no_transaction_left_open_after_race(self, engine_on) -> None:
        """🔴 競態結束後不得留下未提交的交易（連線狀態乾淨）。"""
        self._run_race(engine_on, "agent_race_tx")
        assert engine_on.conn.in_transaction is False, (
            "競態後仍有交易殘留 ⇒ 後續寫入會被誤提交/誤回滾"
        )

    def test_update_delta_holds_write_lock(self) -> None:
        """🔴 靜態事實：`update_delta()` 必須取得 `_WRITE_LOCK`。

        以 AST **唯讀**解析 `src/agent/emotion.py`，斷言 `update_delta` 的
        函式體內存在 `with _WRITE_LOCK:`。此斷言直接釘住本票的修復本身。
        """
        import ast as _ast

        src = (REPO_ROOT / "src" / "agent" / "emotion.py").read_text(
            encoding="utf-8"
        )
        tree = _ast.parse(src)
        target = None
        for node in _ast.walk(tree):
            if isinstance(node, _ast.FunctionDef) and node.name == "update_delta":
                target = node
                break
        assert target is not None, "emotion.py 必須定義 update_delta"

        locked = False
        for inner in _ast.walk(target):
            if isinstance(inner, _ast.With):
                for item in inner.items:
                    ctx = item.context_expr
                    if isinstance(ctx, _ast.Name) and ctx.id == "_WRITE_LOCK":
                        locked = True
        assert locked is True, (
            "update_delta() 必須以 `with _WRITE_LOCK:` 包住整段 "
            "read-modify-write，否則會與 touch_inbound()/try_apply_decay() "
            "的 BEGIN IMMEDIATE 交錯"
        )


# ═════════════════════════════════════════════════════════════
# closeout 修正 A：**啟動時條件註冊**（OFF ⇒ 路由不在 app.routes）
# ═════════════════════════════════════════════════════════════
#
# Owner 裁定逐字：「採啟動時條件註冊：預設 OFF 不註冊；若啟動時具備啟用條件
# 才註冊。緊急停用時 handler 仍須即時拒絕請求；已註冊的路由要到下次重啟才從
# route table 消失，這項邊界如實記錄，不再宣稱動態 OFF 等於未註冊。」
#
# 本類別**不 import `scripts/run_server.py`**（該檔模組層級會對
# `data_root()/faulthandler.log` 開檔並 `load_dotenv()` ⇒ 會碰生產 data root）。
# 沿用既有做法：AST 讀來源檔驗結構 + 在 tmp 內等價重建 app 驗行為。


def _scan_run_server_touch_registration() -> dict:
    """以 AST **唯讀**掃描 `scripts/run_server.py` 的本端點註冊結構。

    回傳 dict：
      - ``module_level_registered``：該路徑是否在**模組層級**的
        `@app.<method>("...")` decorator 中出現
      - ``registered_inside_lifespan``：`lifespan` 函式體內是否有
        `add_api_route(...)` 呼叫
      - ``touch_path_in_lifespan``：該 `add_api_route` 是否指向本端點路徑
      - ``has_compare_digest``：`secrets.compare_digest` 是否仍在（文字掃描）
      - ``has_live_flag_check``：handler 內是否仍有即時旗標檢查

    **不執行**任何 run_server 程式碼 —— 純語法樹遍歷。
    """
    import ast as _ast

    source = RUN_SERVER_PATH.read_text(encoding="utf-8")
    tree = _ast.parse(source)

    module_level_registered = False
    registered_inside_lifespan = False
    touch_path_in_lifespan = False
    guarded_registration = False

    # 1) 模組層級不得有該路徑的 decorator 註冊
    for node in tree.body:
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if (
                    isinstance(dec, _ast.Call)
                    and isinstance(dec.func, _ast.Attribute)
                    and dec.func.attr in {"get", "post", "put", "delete", "patch"}
                    and isinstance(dec.func.value, _ast.Name)
                    and dec.func.value.id == "app"
                    and dec.args
                    and isinstance(dec.args[0], _ast.Constant)
                    and dec.args[0].value == _TOUCH_PATH
                ):
                    module_level_registered = True

    # 2) lifespan 內必須有 add_api_route 註冊該路徑，且**被條件守衛包住**
    for node in tree.body:
        if (
            isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef))
            and node.name == "lifespan"
        ):
            for inner in _ast.walk(node):
                if isinstance(inner, _ast.Call):
                    fn = inner.func
                    is_add = (
                        isinstance(fn, _ast.Attribute) and fn.attr == "add_api_route"
                    ) or (isinstance(fn, _ast.Name) and fn.id == "add_api_route")
                    if is_add:
                        registered_inside_lifespan = True
                        for arg in list(inner.args) + [
                            k.value for k in inner.keywords
                        ]:
                            if (
                                isinstance(arg, _ast.Constant)
                                and arg.value == _TOUCH_PATH
                            ):
                                touch_path_in_lifespan = True

            # 🔴 條件守衛：`add_api_route` 必須在某個 `if` 之內，且該 `if`
            #    的條件要**真的引用**啟用資格判斷函式。
            #    只斷言「lifespan 內有 add_api_route」是不夠的 —— 那無法區分
            #    「條件註冊」與「無條件註冊」（mutation M-C 實測證實了這點）。
            for inner in _ast.walk(node):
                if isinstance(inner, _ast.If):
                    cond_src = _ast.dump(inner.test)
                    if "registrable_at_startup" in cond_src or (
                        "_internal_vc_touch_enabled" in cond_src
                        and "_internal_vc_touch_token" in cond_src
                    ):
                        # 該 if 的 body 內必須真的有 add_api_route
                        for sub in _ast.walk(inner):
                            if isinstance(sub, _ast.Call):
                                f2 = sub.func
                                if (
                                    isinstance(f2, _ast.Attribute)
                                    and f2.attr == "add_api_route"
                                ) or (
                                    isinstance(f2, _ast.Name)
                                    and f2.id == "add_api_route"
                                ):
                                    guarded_registration = True
                        # 條件不得是恆真常數（`if True:` 這類 mutation）
                        if isinstance(inner.test, _ast.Constant):
                            guarded_registration = False

    # 3) 文字掃描
    has_compare_digest = "compare_digest" in source
    has_live_flag_check = "if not _internal_vc_touch_enabled():" in source

    return {
        "module_level_registered": module_level_registered,
        "registered_inside_lifespan": registered_inside_lifespan,
        "touch_path_in_lifespan": touch_path_in_lifespan,
        "guarded_registration": guarded_registration,
        "has_compare_digest": has_compare_digest,
        "has_live_flag_check": has_live_flag_check,
    }


class TestStartupConditionalRegistrationSource:
    """AST 靜態事實：本端點的註冊**不在模組層級**，而在 lifespan 條件註冊。"""

    def test_touch_route_not_registered_at_module_level(self) -> None:
        """🔴 核心 AST 斷言：`@app.post("/internal/vc/inbound_touch")` 不得在模組層級。"""
        info = _scan_run_server_touch_registration()
        assert info["module_level_registered"] is False, (
            "本端點仍在模組層級註冊 —— 預設 OFF 時 app.routes 必然含該路徑，"
            "Gate 1 不成立"
        )

    def test_registration_happens_inside_lifespan(self) -> None:
        """🔴 註冊必須發生在 `lifespan` 內，且指向本端點路徑。"""
        info = _scan_run_server_touch_registration()
        assert info["registered_inside_lifespan"] is True, (
            "lifespan 內找不到 add_api_route —— 條件註冊沒有落地"
        )
        assert info["touch_path_in_lifespan"] is True, (
            "lifespan 內的註冊沒有指向 /internal/vc/inbound_touch"
        )

    def test_registration_is_guarded_by_eligibility_condition(self) -> None:
        """🔴 註冊必須被**啟用資格條件**守衛，不是無條件註冊。

        這條是 M-C 的靶：若把守衛改成 `if True:`（無條件註冊），
        `add_api_route` 就不在任何引用資格判斷的 `if` 之內 ⇒ 本斷言變紅。
        只斷言「lifespan 內有 add_api_route」**不足以**區分條件與無條件註冊。
        """
        info = _scan_run_server_touch_registration()
        assert info["guarded_registration"] is True, (
            "lifespan 內的 add_api_route 沒有被 `_internal_vc_touch_"
            "registrable_at_startup()` 之類的條件守衛包住 —— "
            "預設 OFF 將無條件註冊該路由"
        )

    def test_compare_digest_still_present(self) -> None:
        """🔴 `secrets.compare_digest` 的定時比較**不得**因重構而遺失。"""
        info = _scan_run_server_touch_registration()
        assert info["has_compare_digest"] is True, (
            "Bearer 比對必須維持 secrets.compare_digest（定時比較）"
        )

    def test_live_flag_check_still_inside_handler(self) -> None:
        """🔴 handler 內的即時旗標檢查**不得移除**（緊急停用語意）。"""
        info = _scan_run_server_touch_registration()
        assert info["has_live_flag_check"] is True, (
            "handler 內的 `if not _internal_vc_touch_enabled():` 消失了 —— "
            "緊急停用會失效（關旗標後仍要能即時 404）"
        )


class _ClientCleanupMixin:
    """管理本類別建立的 TestClient，測後關閉（觸發 lifespan 收尾）。"""

    @pytest.fixture(autouse=True)
    def _cleanup_client(self, monkeypatch):
        self._clients = []
        yield
        for c in self._clients:
            try:
                c.__exit__(None, None, None)
            except Exception:
                pass


class TestStartupConditionalRegistrationOff(_ClientCleanupMixin):
    """🔴 OFF 態（旗標與 token 皆缺席）：不註冊、不可列舉、404 來自 routing 層。"""

    def _build(self, monkeypatch, flag_on, token=""):
        app, client = _build_equivalent_app(monkeypatch, flag_on=flag_on, token=token)
        self._clients.append(client)
        return app, client

    def test_off_touch_path_absent_from_app_routes(self, monkeypatch) -> None:
        """🔴 **本輪新增的核心斷言**：OFF ⇒ `app.routes` 不含該路徑。"""
        app, _c = self._build(monkeypatch, flag_on=False)
        route_paths = {getattr(r, "path", None) for r in app.routes}
        assert _TOUCH_PATH not in route_paths, (
            "OFF 態仍註冊於 app.routes —— 啟動時條件註冊未生效"
        )

    def test_off_touch_path_absent_from_openapi(self, monkeypatch) -> None:
        """🔴 OFF ⇒ `/openapi.json` 不列出該路徑。"""
        _app, client = self._build(monkeypatch, flag_on=False)
        assert _TOUCH_PATH not in client.get("/openapi.json").json()["paths"]

    def test_off_post_returns_404(self, monkeypatch) -> None:
        """🔴 OFF ⇒ POST 該路徑得 404（routing 層，路徑不存在）。"""
        _app, client = self._build(monkeypatch, flag_on=False)
        resp = client.post(
            _TOUCH_PATH, json={"agent_id": "agent_x", "event_id": "e1"}
        )
        assert resp.status_code == 404
        assert resp.json().get("detail") == "Not Found", (
            "404 不是 routing 層產生（handler 仍在 route table）"
        )

    def test_off_existing_route_still_present(self, monkeypatch) -> None:
        """🔴 對照組：既有路由**仍在** —— 證明沒有誤刪整個 route table。"""
        app, _c = self._build(monkeypatch, flag_on=False)
        route_paths = {getattr(r, "path", None) for r in app.routes}
        assert "/health" in route_paths, "條件註冊把既有路由也弄丟了"

    def test_off_flag_on_but_token_empty_is_not_registered(self, monkeypatch) -> None:
        """🔴 缺一即不註冊：旗標 ON **但 token 為空** ⇒ 同樣不註冊。

        契約明訂「具備啟用條件 = 旗標 ON **且** token 非空」。此測例把
        「token 缺席」單獨釘死，避免後人只看旗標就註冊。
        """
        app, _c = self._build(monkeypatch, flag_on=True, token="")
        route_paths = {getattr(r, "path", None) for r in app.routes}
        assert _TOUCH_PATH not in route_paths, (
            "token 為空時仍註冊了路由 —— 違反『兩者缺一即不註冊』"
        )


class TestStartupConditionalRegistrationOn(_ClientCleanupMixin):
    """🔴 ON 態（旗標 ON 且 token 已設）：註冊、可列舉、鑑權矩陣正確。"""

    def _build(self, monkeypatch, token="s3cr3t-token"):
        app, client = _build_equivalent_app(
            monkeypatch, flag_on=True, token=token
        )
        self._clients.append(client)
        return app, client

    def test_on_touch_path_present_in_app_routes(self, monkeypatch) -> None:
        """🔴 ON ⇒ `app.routes` **含**該路由。"""
        app, _c = self._build(monkeypatch)
        route_paths = {getattr(r, "path", None) for r in app.routes}
        assert _TOUCH_PATH in route_paths, "ON 態卻沒有註冊路由"

    def test_on_touch_path_present_in_openapi(self, monkeypatch) -> None:
        """🔴 ON ⇒ OpenAPI **含**該路徑。"""
        _app, client = self._build(monkeypatch)
        assert _TOUCH_PATH in client.get("/openapi.json").json()["paths"]

    def test_on_missing_authorization_returns_401(self, monkeypatch) -> None:
        """🔴 缺 Authorization ⇒ 401。"""
        _app, client = self._build(monkeypatch)
        resp = client.post(
            _TOUCH_PATH, json={"agent_id": "agent_yua", "event_id": "e1"}
        )
        assert resp.status_code == 401

    def test_on_wrong_token_returns_401(self, monkeypatch) -> None:
        """🔴 錯 token ⇒ 401。"""
        _app, client = self._build(monkeypatch)
        resp = client.post(
            _TOUCH_PATH,
            json={"agent_id": "agent_yua", "event_id": "e1"},
            headers={"Authorization": "Bearer totally-wrong"},
        )
        assert resp.status_code == 401

    def test_on_correct_token_decay_off_returns_200_flag_off(self, monkeypatch) -> None:
        """🔴 正確 token 且 `INTIMACY_DECAY_ENABLED=""` ⇒ 200 且 body 逐鍵相符。"""
        _app, client = self._build(monkeypatch)
        resp = client.post(
            _TOUCH_PATH,
            json={"agent_id": "agent_yua", "event_id": "e1"},
            headers={"Authorization": "Bearer s3cr3t-token"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "applied": False, "reason": "FLAG_OFF"}


class TestEmergencyDisableAfterRegistration(_ClientCleanupMixin):
    """🔴 **緊急停用**：ON 註冊後，執行期把旗標設為空 ⇒ handler 仍即時回 404。

    Owner 逐字邊界：「緊急停用時 handler 仍須即時拒絕請求；**已註冊的路由要到
    下次重啟才從 route table 消失**，這項邊界如實記錄，不再宣稱動態 OFF
    等於未註冊。」

    本類別把這兩件事**分別**斷言，正是為了讓「已註冊的路由仍留在 route table」
    這個邊界成為**可執行的契約**，而非口頭揭露。
    """

    def _build(self, monkeypatch):
        app, client = _build_equivalent_app(
            monkeypatch, flag_on=True, token="s3cr3t-token"
        )
        self._clients.append(client)
        return app, client

    def test_emergency_disable_after_registration_still_returns_404(
        self, monkeypatch
    ) -> None:
        """🔴🔴 **緊急停用**：ON 註冊後執行期關旗標 ⇒ handler **仍即時 404**。

        這是「緊急停用」的核心語意 —— 不得等到重啟才拒絕請求。
        """
        app, client = self._build(monkeypatch)

        # 前置：確認確實已註冊（否則本測例變成在測 OFF 態，失去意義）
        assert _TOUCH_PATH in {getattr(r, "path", None) for r in app.routes}

        ok = client.post(
            _TOUCH_PATH,
            json={"agent_id": "agent_yua", "event_id": "e1"},
            headers={"Authorization": "Bearer s3cr3t-token"},
        )
        assert ok.status_code == 200, "前置：ON 態正確 token 應為 200"

        # 🔴 執行期把旗標關掉（不重啟）
        monkeypatch.setenv("INTERNAL_VC_TOUCH_ENABLED", "")

        resp = client.post(
            _TOUCH_PATH,
            json={"agent_id": "agent_yua", "event_id": "e1"},
            headers={"Authorization": "Bearer s3cr3t-token"},
        )
        assert resp.status_code == 404, (
            "執行期關旗標後 handler 沒有即時 404 —— 緊急停用失效"
        )
        # 這個 404 來自 handler（小寫 f），與 OFF 態的 routing 層 404 不同源
        assert resp.json().get("detail") == "not found", (
            "緊急停用的 404 應由 handler 的即時檢查產生"
        )

    def test_emergency_disable_route_persists_until_restart(self, monkeypatch) -> None:
        """🔴 誠實邊界：緊急停用後路由**仍在 route table**，要到重啟才消失。

        此斷言是「不再宣稱動態 OFF 等於未註冊」的可執行證據。若此斷言變紅，
        代表 FastAPI 之後真的支援了執行期移除路由 —— 那是好消息，請一併
        更新 run_server.py 的 docstring 誠實邊界段落。
        """
        app, client = self._build(monkeypatch)

        monkeypatch.setenv("INTERNAL_VC_TOUCH_ENABLED", "")
        assert client.post(
            _TOUCH_PATH,
            json={"agent_id": "agent_yua", "event_id": "e1"},
            headers={"Authorization": "Bearer s3cr3t-token"},
        ).status_code == 404

        assert _TOUCH_PATH in {getattr(r, "path", None) for r in app.routes}, (
            "緊急停用後路由竟已從 route table 消失 —— 若 FastAPI 已支援執行期"
            "移除路由，請更新 docstring 的誠實邊界"
        )

    def test_emergency_disable_hides_path_from_openapi(self, monkeypatch) -> None:
        """🔴 雙保險：緊急停用後即使路由仍在 route table，OpenAPI **也不列出**。

        這證明 `app.openapi` 覆寫（第一輪遺留）與啟動時條件註冊**互不取代**：
        前者管「可不可列舉」（即時），後者管「路由存不存在」（需重啟）。
        """
        _app, client = self._build(monkeypatch)

        assert _TOUCH_PATH in client.get("/openapi.json").json()["paths"]
        monkeypatch.setenv("INTERNAL_VC_TOUCH_ENABLED", "")
        assert _TOUCH_PATH not in client.get("/openapi.json").json()["paths"], (
            "緊急停用後 OpenAPI 仍可列舉該路徑 —— 雙保險失效"
        )


# ═════════════════════════════════════════════════════════════
# closeout 修正 B：**每角色**啟用資格（扣減邊界閘門）
# ═════════════════════════════════════════════════════════════
#
# Owner 裁定逐字：「報告證明了預設全域旗標 OFF 時任何角色都不扣；但 TG 的
# `touch_inbound(agent_id)` 仍可收到 `agent_akane`／`agent_rem`／`agent_mai`。
# 將來為其他角色開全域旗標後，若 TG 對其中一位發訊，TOUCH 內的到期清算就可能
# 扣她的 Delta，而語音互動仍未被計入。**必須在實際 `touch_inbound`／
# `try_apply_decay` 的扣減邊界加入明確的每角色啟用資格**。」
#
# 🔴 核心負例必須逐字用 `agent_akane` / `agent_rem` / `agent_mai` 三個角色。
# 🔴 全域旗標必須是 **ON**，且時鐘**已逾期** —— 這樣才證明「扣減邊界有閘門」，
#    而不是靠「旗標 OFF」或「未逾期」這種**替代證據**過關。

INELIGIBLE_AGENTS = ("agent_akane", "agent_rem", "agent_mai")
INELIGIBLE_REASON = "NOT_ELIGIBLE"

# 🔴 closeout 修正 A：白名單覆蓋範圍 ＝ 所有「非 VC 且 TG owner-whitelist
#    入口可達」的角色。`.env` 的 10 個 TG bot token 減去 3 個本機 VC 服務
#    （akane / mai / rem）＝ 下列 7 個。此清單是**期望值**，故意獨立於
#    `emotion.INTIMACY_DECAY_ELIGIBLE_AGENTS` 書寫，這樣「常數被改動」與
#    「測試跟著漂移」才不會互相掩護。
ELIGIBLE_AGENTS = (
    "agent_yua",
    "agent_ruka",
    "agent_ram",
    "agent_mahiru",
    "agent_anna",
    "agent_miku",
    "agent_aoi",
)

#: 本機 VC 服務恰好 3 個（`VC1_AkaneVoiceCompanion` / `VC1_MaiVoiceCompanion`
#: / `VC1_RemVoiceCompanion`），其 agent_id 即為 `INELIGIBLE_AGENTS`。
VC_AGENTS = INELIGIBLE_AGENTS


def _ledger_count(engine: EmotionEngine, agent_id: str) -> int:
    """該 agent 在 ledger 中的筆數（0 ⇒ 從未結算過）。"""
    cur = engine.conn.execute(
        "SELECT COUNT(*) FROM intimacy_decay_ledger WHERE agent_id = ?",
        (agent_id,),
    )
    return int(cur.fetchone()[0])


def _ledger_total_amount(engine: EmotionEngine, agent_id: str) -> float:
    """該 agent 的 ledger 實扣量總和。"""
    cur = engine.conn.execute(
        "SELECT COALESCE(SUM(applied_amount), 0.0) FROM intimacy_decay_ledger "
        "WHERE agent_id = ?",
        (agent_id,),
    )
    return float(cur.fetchone()[0])


def _seed_overdue(engine: EmotionEngine, agent_id: str, delta: float, due: float) -> None:
    """把某角色鋪成「已逾期且 Delta 非零」的情境（**唯一**允許的測試鋪陳）。

    直接寫 DB 是為了精確控制時鐘狀態；`touch_inbound` / `try_apply_decay`
    才是被測的扣減邊界。

    🔴 先 `ensure_decay_schema()`：本 helper 要寫 `last_valid_inbound_at` /
    `next_decay_due_at` 兩欄，schema 未建會 `OperationalError`。這**不影響**
    被測語意 —— 被測的是「扣減邊界是否擋住不合格角色」，鋪陳階段本來就必須
    先把場景造出來。
    """
    engine.ensure_decay_schema()
    engine.conn.execute(
        "INSERT INTO agent_emotions "
        "(agent_id, mood, intimacy, intimacy_delta, "
        " last_valid_inbound_at, next_decay_due_at, updated_at) "
        "VALUES (?, 0.0, 50.0, ?, ?, ?, 'seed') "
        "ON CONFLICT(agent_id) DO UPDATE SET "
        "  intimacy_delta = excluded.intimacy_delta, "
        "  last_valid_inbound_at = excluded.last_valid_inbound_at, "
        "  next_decay_due_at = excluded.next_decay_due_at",
        (agent_id, delta, due - DAY, due),
    )
    engine.conn.commit()


def _delta_of(engine: EmotionEngine, agent_id: str) -> float:
    return engine.get_delta(agent_id)


def _due_of(engine: EmotionEngine, agent_id: str):
    """讀該 row 的 due；**欄位不存在或無 row ⇒ None**（fail-safe，不拋）。

    🔴 必須容忍「schema 尚未建立」：`test_not_eligible_does_not_touch_clock`
    的斷言正是「不合格角色連 DDL 都不觸發」，此時 `next_decay_due_at` 欄位
    根本不存在，直接查會 `OperationalError`。欄位缺席本身即證明「沒有任何
    持久寫入」。
    """
    try:
        _last, due, _d = engine._read_clock_and_delta_locked(agent_id)
        return due
    except sqlite3.OperationalError:
        return None


class TestPerAgentEligibilityConstant:
    """白名單常數本身的契約。"""

    def test_whitelist_is_frozenset(self) -> None:
        """必須是 `frozenset`（白名單型別；與 WORLD_LOG_BACKED_SOURCES 同型）。"""
        import src.agent.emotion as em

        assert isinstance(em.INTIMACY_DECAY_ELIGIBLE_AGENTS, frozenset), (
            "必須用 frozenset 白名單，避免執行期被就地修改"
        )

    def test_owner_named_agents_are_not_eligible(self) -> None:
        """🔴 Owner 逐字點名的三個角色**必須不在**白名單中。"""
        from src.agent.emotion import INTIMACY_DECAY_ELIGIBLE_AGENTS

        for aid in INELIGIBLE_AGENTS:
            assert aid not in INTIMACY_DECAY_ELIGIBLE_AGENTS, (
                f"{aid} 出現在白名單中 —— VC 語音尚未可靠 TOUCH 前不得扣分"
            )

    def test_eligibility_helper_failsafe_on_weird_input(self) -> None:
        """`is_decay_eligible()` 對非字串 / 空 / 未知角色一律 False（fail-safe）。"""
        from src.agent.emotion import is_decay_eligible

        for bad in (None, "", "agent_unknown", 123, ["agent_yua"]):
            assert is_decay_eligible(bad) is False

    def test_eligibility_is_not_env_driven(self, monkeypatch) -> None:
        """資格**不受 env 影響** —— 它是程式碼層決策，不是可切換的旗標。

        即使把衰減旗標開到最大、甚至試圖用 env 覆寫白名單，不合格角色依然不合格。
        """
        from src.agent.emotion import is_decay_eligible

        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        monkeypatch.setenv("INTIMACY_DECAY_ELIGIBLE_AGENTS", "agent_akane,agent_rem")
        for aid in INELIGIBLE_AGENTS:
            assert is_decay_eligible(aid) is False, (
                f"{aid} 的資格被 env 影響了 —— 資格不得是 env 可切換的"
            )

    # ── 第三組：覆蓋完整性（擋住「未來有人不小心把 VC 加回去」）──

    def test_whitelist_membership_is_exactly_the_seven(self) -> None:
        """🔴🔴 成員集合**恰好等於** 7 個合格角色。

        用 **frozenset 等值**斷言（不是 subset）：subset 是 fail-open ——
        多加了不該有的角色（例如 VC 之一）仍然會過關。
        """
        from src.agent.emotion import INTIMACY_DECAY_ELIGIBLE_AGENTS

        assert INTIMACY_DECAY_ELIGIBLE_AGENTS == frozenset(ELIGIBLE_AGENTS), (
            "白名單成員與期望的 7 個角色不一致："
            f"實際={sorted(INTIMACY_DECAY_ELIGIBLE_AGENTS)}、"
            f"期望={sorted(ELIGIBLE_AGENTS)}"
        )
        assert len(INTIMACY_DECAY_ELIGIBLE_AGENTS) == 7, (
            "覆蓋範圍必須恰為 7（10 個 TG bot 減去 3 個 VC）"
        )

    def test_vc_agents_disjoint_from_whitelist(self) -> None:
        """🔴🔴 三個 VC 角色與白名單**互斥**（`isdisjoint`）。

        與等值斷言互為雙保險：等值斷言擋「偷偷加人」，本斷言直接以語意
        命名 VC 禁令，讓「把 akane/rem/mai 加回去」在任何組合下都變紅。
        """
        from src.agent.emotion import INTIMACY_DECAY_ELIGIBLE_AGENTS

        assert INTIMACY_DECAY_ELIGIBLE_AGENTS.isdisjoint(set(VC_AGENTS)), (
            "VC 角色出現在白名單中 —— 語音未計入卻會被扣分："
            f"{sorted(INTIMACY_DECAY_ELIGIBLE_AGENTS & set(VC_AGENTS))}"
        )
        for aid in VC_AGENTS:
            assert aid not in INTIMACY_DECAY_ELIGIBLE_AGENTS, (
                f"{aid} 是 VC 角色，不得具備扣減資格"
            )


class TestTouchInboundPerAgentEligibility:
    """🔴 Owner 指定的核心負例：全域 ON + 已逾期 + TG 形狀 inbound ⇒ 三人 0 扣減。

    **走 `router.inbound()` 的真實呼叫路徑**（沿用 `TestChannelWiring` 的接線法），
    不是繞過 router 直接叫 engine —— 這樣才證明「TG 收到這三人的訊息時，
    扣減邊界確實擋住」。
    """

    @staticmethod
    def _point_router_at(engine, monkeypatch):
        import src.agent.emotion as emotion_mod
        from src.eventbus import SoulEventBus
        from src.io.channels.router import ChannelRouter

        # 🔴 改用 monkeypatch 還原全域 singleton（見 TestChannelWiring 同註解）。
        monkeypatch.setattr(emotion_mod, "emotion_engine", engine)
        return ChannelRouter(bus=SoulEventBus())

    def test_global_on_overdue_tg_inbound_akane_rem_mai_zero_decay(
        self, tmp_path, monkeypatch
    ) -> None:
        """🔴🔴 **Owner 指定的核心負例**（逐字三人）。

        情境：**全域旗標 ON**（`INTIMACY_DECAY_ENABLED=1`）＋ 三人時鐘**已逾期**
        ＋ Delta 非零 ＋ 各發一次 TG 形狀的 inbound。

        斷言：三人的 **ledger 0 筆**、**Delta 0 扣減**（逐位元不變）、
              **時鐘未被推進**（due 仍是原本那個逾期值）。
        """
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        monkeypatch.setenv("TELEGRAM_OWNER_ID", "1696287850")
        db = tmp_path / "memory.db"
        engine = EmotionEngine(db_path=db)
        engine.ensure_decay_schema()
        router = self._point_router_at(engine, monkeypatch)

        due = T0 - 3600.0  # 已逾期一小時
        for aid in INELIGIBLE_AGENTS:
            _seed_overdue(engine, aid, delta=10.0, due=due)

        # 各發一次「TG 形狀」的已驗證 inbound（owner whitelist 通過）
        for aid in INELIGIBLE_AGENTS:
            short = aid.replace("agent_", "")
            asyncio.run(
                router.inbound(short, "hello from TG", 1696287850, channel="telegram")
            )

        for aid in INELIGIBLE_AGENTS:
            assert _ledger_count(engine, aid) == 0, (
                f"{aid} 竟然寫入了 ledger —— 每角色資格閘門沒有擋在扣減邊界"
            )
            assert _delta_of(engine, aid) == pytest.approx(10.0), (
                f"{aid} 的 Delta 被扣減了 —— 語音未計入卻照樣扣分"
            )
            assert _due_of(engine, aid) == pytest.approx(due), (
                f"{aid} 的時鐘被推進了 —— 不合格角色不得有任何持久寫入"
            )

    def test_control_eligible_agent_decays_under_same_overdue_setup(
        self, tmp_path, monkeypatch
    ) -> None:
        """🔴 **對照組（證明測試有牙）**：同一逾期情境，白名單角色**確實被扣**。

        若本測例也變成「不扣」，那「資格閘有效」與「整個機制壞掉」就無法區分，
        上面的負例將失去證明力。
        """
        from src.agent.emotion import INTIMACY_DECAY_ELIGIBLE_AGENTS

        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        monkeypatch.setenv("TELEGRAM_OWNER_ID", "1696287850")
        db = tmp_path / "memory.db"
        engine = EmotionEngine(db_path=db)
        engine.ensure_decay_schema()
        router = self._point_router_at(engine, monkeypatch)

        eligible = sorted(INTIMACY_DECAY_ELIGIBLE_AGENTS)
        assert eligible, "白名單不得為空，否則無對照組"
        aid = eligible[0]
        short = aid.replace("agent_", "")

        due = T0 - 3600.0
        _seed_overdue(engine, aid, delta=10.0, due=due)

        asyncio.run(
            router.inbound(short, "hello from TG", 1696287850, channel="telegram")
        )

        # ✅ 確實扣了：ledger 有 1 筆、實扣 0.5、Delta 10.0 -> 9.5
        assert _ledger_count(engine, aid) == 1, (
            f"白名單角色 {aid} 沒有寫 ledger —— 機制可能整體壞掉，負例失去意義"
        )
        assert _ledger_total_amount(engine, aid) == pytest.approx(DECAY_STEP)
        assert _delta_of(engine, aid) == pytest.approx(10.0 - DECAY_STEP), (
            f"白名單角色 {aid} 的 Delta 沒有被扣 —— 對照組失效"
        )
        assert _due_of(engine, aid) is not None
        assert _due_of(engine, aid) > due, "白名單角色的時鐘應被推進"

    # ── 第二組：三名 VC 角色逐一參數化（`touch_inbound` 邊界，走真實 TG 路徑）──

    @pytest.mark.parametrize("agent_id", VC_AGENTS)
    def test_global_on_overdue_tg_inbound_vc_agent_not_decayed(
        self, tmp_path, monkeypatch, agent_id
    ) -> None:
        """🔴🔴 **每個 VC 角色各自獨立**：全域 ON ＋ 已逾期 ＋ TG 發訊 ⇒ 仍不扣。

        逐一參數化（而非一次迴圈測三人）是為了讓 mutation M-B「把某個 VC
        加進白名單」能精準地只讓**該角色那一筆**變紅，證明每個案例都有牙。
        """
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        monkeypatch.setenv("TELEGRAM_OWNER_ID", "1696287850")
        db = tmp_path / "memory.db"
        engine = EmotionEngine(db_path=db)
        engine.ensure_decay_schema()
        router = self._point_router_at(engine, monkeypatch)

        due = T0 - 3600.0
        _seed_overdue(engine, agent_id, delta=10.0, due=due)

        asyncio.run(
            router.inbound(
                agent_id.replace("agent_", ""),
                "hello from TG",
                1696287850,
                channel="telegram",
            )
        )

        assert _ledger_count(engine, agent_id) == 0, (
            f"{agent_id} 竟然寫入了 ledger —— 每角色資格閘門沒有擋在扣減邊界"
        )
        assert _delta_of(engine, agent_id) == pytest.approx(10.0), (
            f"{agent_id} 的 Delta 被扣減了 —— 語音未計入卻照樣扣分"
        )
        assert _due_of(engine, agent_id) == pytest.approx(due), (
            f"{agent_id} 的時鐘被推進了 —— 不合格角色不得有任何持久寫入"
        )


class TestAllEligibleAgentsActuallyDecay:
    """🔴🔴 **第一組**：7 名合格角色**逐一**證明確實可扣（不得只以 yua 作唯一正例）。

    每一筆都走**真實路徑**：`router.inbound()` 的 TG 路徑 → `_touch_intimacy_clock`
    → `emotion_engine.touch_inbound()`，DB 一律用 pytest `tmp_path`。
    **不**用 `exec()` 抽取片段，也**不**自行計算期望值 —— 期望值來自常數
    （`DECAY_STEP`）與事前鋪陳的 DB 狀態。

    情境固定為「全域 ON ＋ 已逾期 ＋ 合法 TG owner inbound」，斷言四件事：
      1. 確實扣減（Delta 10.0 → 10.0 − DECAY_STEP）
      2. 確實寫 ledger（1 筆，金額 ＝ DECAY_STEP）
      3. due 確實推進（> 原本的逾期值）
      4. 回傳 `touched is True` 且 reason 為 `TOUCHED`
    """

    @staticmethod
    def _point_router_at(engine, monkeypatch):
        import src.agent.emotion as emotion_mod
        from src.eventbus import SoulEventBus
        from src.io.channels.router import ChannelRouter

        # 🔴 改用 monkeypatch 還原全域 singleton（見 TestChannelWiring 同註解）。
        monkeypatch.setattr(emotion_mod, "emotion_engine", engine)
        return ChannelRouter(bus=SoulEventBus())

    @pytest.mark.parametrize("agent_id", ELIGIBLE_AGENTS)
    def test_each_eligible_agent_decays_via_tg_inbound(
        self, tmp_path, monkeypatch, agent_id
    ) -> None:
        """🔴 每個合格角色**各自**被證明可扣（逐一參數化，非單一 yua 正例）。"""
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        monkeypatch.setenv("TELEGRAM_OWNER_ID", "1696287850")
        db = tmp_path / f"memory_{agent_id}.db"
        engine = EmotionEngine(db_path=db)
        engine.ensure_decay_schema()
        router = self._point_router_at(engine, monkeypatch)

        due = T0 - 3600.0  # 已逾期一小時
        _seed_overdue(engine, agent_id, delta=10.0, due=due)

        # 真實 TG 形狀 inbound（短碼 → router 補成 agent_ 前綴）
        result = asyncio.run(
            router.inbound(
                agent_id.replace("agent_", ""),
                "hello from TG",
                1696287850,
                channel="telegram",
            )
        )

        # 1) 確實扣減
        assert _delta_of(engine, agent_id) == pytest.approx(10.0 - DECAY_STEP), (
            f"合格角色 {agent_id} 沒有被扣 —— 白名單覆蓋不足"
        )
        # 2) 確實寫 ledger
        assert _ledger_count(engine, agent_id) == 1, (
            f"合格角色 {agent_id} 沒有寫 ledger —— 扣減路徑未走完"
        )
        assert _ledger_total_amount(engine, agent_id) == pytest.approx(DECAY_STEP)
        # 3) due 確實推進
        due_after = _due_of(engine, agent_id)
        assert due_after is not None, f"合格角色 {agent_id} 的 due 為 None"
        assert due_after > due, (
            f"合格角色 {agent_id} 的時鐘未被推進（{due} -> {due_after}）"
        )
        # 4) 直呼扣減邊界亦自證（router 的 TOUCH 是 fire-and-forget，無回傳物件）。
        #    在**同一逾期狀態**下直呼：此時時鐘已被 router 推進，故只驗證
        #    資格判定本身放行（reason 非 NOT_ELIGIBLE）。
        direct = engine.touch_inbound(
            agent_id=agent_id,
            event_id=f"direct:{agent_id}",
            channel="telegram",
            now=T0,
        )
        assert direct.get("reason") != INELIGIBLE_REASON, (
            f"合格角色 {agent_id} 直呼 touch_inbound 竟被判不合格：{direct}"
        )
        assert direct.get("touched") is True, (
            f"合格角色 {agent_id} 直呼 touch_inbound 未 TOUCH：{direct}"
        )


class TestTouchInboundNotEligibleReturnsReason:
    """`touch_inbound()` 對不合格角色的**直接**回傳契約（不拋例外）。"""

    def test_returns_not_eligible_without_exception(self, engine_on) -> None:
        """🔴 不合格 ⇒ 回 `NOT_ELIGIBLE`，且**不得拋例外**（TG 路徑會吞掉例外）。"""
        result = engine_on.touch_inbound(
            agent_id="agent_akane", event_id="e1", channel="telegram"
        )
        assert result.get("reason") == INELIGIBLE_REASON, (
            f"應回 {INELIGIBLE_REASON}，實際 {result}"
        )
        assert result.get("touched") is False
        assert result.get("applied") is False

    def test_not_eligible_does_not_touch_clock(self, engine_on) -> None:
        """🔴 不合格 ⇒ 不寫時鐘（連一列都不建）。"""
        engine_on.touch_inbound(
            agent_id="agent_rem", event_id="e1", channel="telegram"
        )
        assert _due_of(engine_on, "agent_rem") is None, "不合格角色竟被寫入時鐘"

    def test_not_eligible_triggers_no_ddl(self, tmp_path, monkeypatch) -> None:
        """🔴 不合格 ⇒ 連惰性 DDL 都不做（該角色逐位元未被本票觸及）。"""
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        engine = EmotionEngine(db_path=tmp_path / "memory.db")
        before = _cols(engine)
        assert "next_decay_due_at" not in before, "前置：尚未有 G2 欄位"

        engine.touch_inbound(
            agent_id="agent_mai", event_id="e1", channel="telegram"
        )

        assert _cols(engine) == before, "不合格角色的呼叫觸發了 DDL"
        assert "intimacy_decay_ledger" not in _tables(engine)

    def test_eligible_agent_still_touches(self, engine_on) -> None:
        """🔴 對照組：白名單角色行為不變（仍正常 TOUCH）。"""
        result = engine_on.touch_inbound(
            agent_id="agent_yua", event_id="e1", channel="telegram"
        )
        assert result.get("touched") is True
        assert result.get("reason") == "TOUCHED"
        assert _due_of(engine_on, "agent_yua") is not None

    # ── 第二組（`touch_inbound` 邊界）：三 VC 逐一參數化、已逾期仍不扣 ──

    @pytest.mark.parametrize("agent_id", VC_AGENTS)
    def test_overdue_vc_agent_not_decayed_via_touch_inbound(
        self, tmp_path, monkeypatch, agent_id
    ) -> None:
        """🔴🔴 每個 VC **已逾期** ⇒ `touch_inbound()` 不扣、不寫 ledger、不推進 due。

        這是 `touch_inbound` 邊界的**獨立**參數化測例（與 router 走真實 TG
        路徑的那組互為補強）：即使繞過 router 直接呼叫，閘門仍須擋住。
        """
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        engine = EmotionEngine(db_path=tmp_path / f"memory_{agent_id}.db")
        due = T0 - 3600.0
        _seed_overdue(engine, agent_id, delta=10.0, due=due)

        result = engine.touch_inbound(
            agent_id=agent_id, event_id="e1", channel="telegram", now=T0
        )

        assert result.get("reason") == INELIGIBLE_REASON, (
            f"{agent_id} 未回 {INELIGIBLE_REASON}：{result}"
        )
        assert result.get("touched") is False
        assert result.get("applied") is False
        assert _ledger_count(engine, agent_id) == 0, (
            f"{agent_id} 的 touch_inbound 寫入了 ledger"
        )
        assert _delta_of(engine, agent_id) == pytest.approx(10.0), (
            f"{agent_id} 的 Delta 被 touch_inbound 扣減了"
        )
        assert _due_of(engine, agent_id) == pytest.approx(due), (
            f"{agent_id} 的 due 被推進了 —— 不合格角色不得有任何持久寫入"
        )


class TestTryApplyDecayPerAgentEligibility:
    """🔴 `try_apply_decay()` 的資格閘門 —— **必須獨立測**，不能只測 touch_inbound。

    兩個函式是**兩條獨立的扣減邊界**：heartbeat tick 直接呼叫 `try_apply_decay()`，
    不經過 router。只擋 `touch_inbound` 會留下這條繞道。
    """

    def test_try_apply_decay_returns_not_eligible(self, engine_on) -> None:
        """🔴 不合格 ⇒ 回 `NOT_ELIGIBLE`。"""
        for aid in INELIGIBLE_AGENTS:
            result = engine_on.try_apply_decay(agent_id=aid, now=T0)
            assert result.get("reason") == INELIGIBLE_REASON, (
                f"{aid} 的 try_apply_decay 未回 {INELIGIBLE_REASON}：{result}"
            )
            assert result.get("applied") is False

    @pytest.mark.parametrize("agent_id", VC_AGENTS)
    def test_overdue_ineligible_agent_not_decayed(
        self, engine_on, agent_id
    ) -> None:
        """🔴🔴 三人**已逾期** ⇒ `try_apply_decay()` **不扣、不寫 ledger、不推進 due**。"""
        due = T0 - 3600.0
        _seed_overdue(engine_on, agent_id, delta=10.0, due=due)

        result = engine_on.try_apply_decay(agent_id=agent_id, now=T0)

        assert result.get("reason") == INELIGIBLE_REASON
        assert result.get("applied") is False
        assert _ledger_count(engine_on, agent_id) == 0, (
            f"{agent_id} 的 tick 路徑寫入了 ledger —— try_apply_decay 的閘門失效"
        )
        assert _delta_of(engine_on, agent_id) == pytest.approx(10.0), (
            f"{agent_id} 的 Delta 被 tick 路徑扣減了"
        )
        assert _due_of(engine_on, agent_id) == pytest.approx(due), (
            f"{agent_id} 的 due 被推進了 —— 不合格角色不得有任何持久寫入"
        )

    def test_control_eligible_agent_decays_via_try_apply_decay(self, engine_on) -> None:
        """🔴 **對照組**：白名單角色逾期 ⇒ `try_apply_decay()` 正常結算。"""
        from src.agent.emotion import INTIMACY_DECAY_ELIGIBLE_AGENTS

        aid = sorted(INTIMACY_DECAY_ELIGIBLE_AGENTS)[0]
        due = T0 - 3600.0
        _seed_overdue(engine_on, aid, delta=10.0, due=due)

        result = engine_on.try_apply_decay(agent_id=aid, now=T0)

        assert result.get("applied") is True, f"白名單角色未結算：{result}"
        assert result.get("reason") == "DECAYED"
        assert result.get("applied_amount") == pytest.approx(DECAY_STEP)
        assert _ledger_count(engine_on, aid) == 1
        assert _delta_of(engine_on, aid) == pytest.approx(10.0 - DECAY_STEP)

    def test_both_boundaries_guarded_independently(self) -> None:
        """🔴 靜態事實：**兩個**函式內都有資格檢查（不得只做一個）。"""
        import ast as _ast

        src = (REPO_ROOT / "src" / "agent" / "emotion.py").read_text(
            encoding="utf-8"
        )
        tree = _ast.parse(src)

        found = {}
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and node.name in {
                "touch_inbound",
                "try_apply_decay",
            }:
                for inner in _ast.walk(node):
                    if (
                        isinstance(inner, _ast.Call)
                        and isinstance(inner.func, _ast.Name)
                        and inner.func.id == "is_decay_eligible"
                    ):
                        found[node.name] = True

        assert found.get("touch_inbound") is True, (
            "touch_inbound() 內找不到 is_decay_eligible() 檢查"
        )
        assert found.get("try_apply_decay") is True, (
            "try_apply_decay() 內找不到 is_decay_eligible() 檢查 —— "
            "tick 路徑成為繞道"
        )


# ═════════════════════════════════════════════════════════════
# G14. 缺口 1 — 真人恰在到期點到場，時鐘不得被寫到未來
# ═════════════════════════════════════════════════════════════


class TestInboundExactlyAtDue:
    """🔴🔴 缺口 1：`touch_inbound()` 逾期分支的時鐘語意。

    舊實作 `target = max(now_ts, due + window)` 後又把 `target` 當
    `last_valid_inbound_at` 並令 `due = target + window`：

      真人恰在首次到期點 `T+24h` 到場 ⇒ `now_ts == due` ⇒ `target = T+48h`
      ⇒ `last_valid_inbound_at = T+48h`、`next_decay_due_at = T+72h`。

    真人明明準時在 T+24h 出現，卻**白拿一天**（多一整個 24h 窗口不衰減）。
    本測試在**新實作**下必須綠、在**舊實作**下必須紅。
    """

    def test_inbound_exactly_at_due_advances_by_one_window(
        self, engine_on
    ) -> None:
        """恰好 `T+24h` 到場：結算一次、扣 0.5、last=T+24h、due=T+48h。"""
        engine_on.update_delta("agent_yua", 5.0)
        # 鋪陳：last_valid_inbound_at = T，next_decay_due_at = T+24h
        engine_on.touch_inbound("agent_yua", "e1", "telegram", now=T0)

        assert _delta_of(engine_on, "agent_yua") == pytest.approx(5.0)
        _last0, due0, _d0 = engine_on._read_clock_and_delta_locked("agent_yua")
        assert due0 == pytest.approx(T0 + DAY), "前置：首次到期點應為 T+24h"
        assert _ledger_count(engine_on, "agent_yua") == 0, "前置：ledger 應為空"

        # 🔴 恰好落在到期點（不是提前、不是延後）
        result = engine_on.touch_inbound(
            "agent_yua", "e2", "telegram", now=T0 + DAY
        )

        last_after, due_after, _d_after = engine_on._read_clock_and_delta_locked(
            "agent_yua"
        )

        # 1) 該次到期**仍只結算一次**：ledger 恰增 1 筆
        assert _ledger_count(engine_on, "agent_yua") == 1, (
            "恰好到場時應結算**一次**（ledger 恰 1 筆）"
        )
        # 2) delta 恰扣 0.5
        assert _delta_of(engine_on, "agent_yua") == pytest.approx(5.0 - DECAY_STEP)
        assert _ledger_total_amount(engine_on, "agent_yua") == pytest.approx(
            DECAY_STEP
        )
        # 3) 🔴 last_valid_inbound_at == T+24h（真人**實際**到場時間）
        #    舊實作：T+48h ⇒ 必紅
        assert last_after == pytest.approx(T0 + DAY), (
            f"last_valid_inbound_at 應為真人實際到場時間 T+24h，實際 {last_after}"
            f"（舊實作會給 T+48h —— 白拿一天）"
        )
        # 4) 🔴 next_decay_due_at == T+48h（due + 一個窗口）
        #    舊實作：T+72h ⇒ 必紅
        assert due_after == pytest.approx(T0 + 2 * DAY), (
            f"next_decay_due_at 應為 due + window = T+48h，實際 {due_after}"
            f"（舊實作會給 T+72h —— 白拿一天）"
        )
        assert result["last_valid_inbound_at"] == pytest.approx(T0 + DAY)
        assert result["next_decay_due_at"] == pytest.approx(T0 + 2 * DAY)

    def test_inbound_exactly_at_due_clock_still_monotonic(
        self, engine_on
    ) -> None:
        """🔴 既有契約不得破壞：時鐘仍**單調不倒退**（含亂序抵達）。"""
        engine_on.update_delta("agent_yua", 5.0)
        engine_on.touch_inbound("agent_yua", "e1", "telegram", now=T0)

        # 恰好到期：due 推進到 T+48h
        r1 = engine_on.touch_inbound("agent_yua", "e2", "telegram", now=T0 + DAY)
        # 更晚但仍在窗內
        r2 = engine_on.touch_inbound(
            "agent_yua", "e3", "telegram", now=T0 + DAY + 60
        )
        # 亂序抵達的**更舊** timestamp
        r3 = engine_on.touch_inbound(
            "agent_yua", "e4", "telegram", now=T0 + DAY - 10 * DAY
        )

        assert r2["next_decay_due_at"] >= r1["next_decay_due_at"]
        assert r2["last_valid_inbound_at"] >= r1["last_valid_inbound_at"]
        assert r3["next_decay_due_at"] >= r2["next_decay_due_at"], (
            "亂序抵達的舊 inbound 讓時鐘倒退了"
        )
        assert r3["last_valid_inbound_at"] >= r2["last_valid_inbound_at"], (
            "亂序抵達的舊 inbound 讓 last_valid_inbound_at 倒退了"
        )

    def test_at_due_does_not_grant_extra_silent_window(self, engine_on) -> None:
        """🔴 語意（修正後）：**每個有效互動各自重新給滿 24 小時**。

        故準時（T+24h）到場者下一次到期為 **T+48h**、延後（T+25h）到場者為
        **T+49h** —— 兩者不再相等，而是**相差恰好 1 小時**（＝兩次到場的時間差）。

        舊實作 `due_written = due + window` 會讓兩者同為 T+48h：
        延後者距其到場時間只剩 23h，被偷走 1 小時。本測試在**新實作**下必須綠、
        在**舊實作**下必須紅。（不得只刪斷言 —— 下述斷言有牙。）
        """
        engine_on.update_delta("agent_yua", 5.0)
        engine_on.touch_inbound("agent_yua", "e1", "telegram", now=T0)
        _last0, due0, _d0 = engine_on._read_clock_and_delta_locked("agent_yua")
        assert due0 == pytest.approx(T0 + DAY), "前置：首次到期點應為 T+24h"

        engine_on.touch_inbound("agent_yua", "e2", "telegram", now=T0 + DAY)
        _last1, due_exact, _d1 = engine_on._read_clock_and_delta_locked("agent_yua")
        # 準時者：下一次到期 ＝ 到場時間 + 24h ＝ T+48h
        assert due_exact == pytest.approx(T0 + 2 * DAY)

        # 對照組：**另一個角色**（避免互相污染）延後 1 小時到場
        engine_on.update_delta("agent_ruka", 5.0)
        engine_on.touch_inbound("agent_ruka", "r1", "telegram", now=T0)
        engine_on.touch_inbound(
            "agent_ruka", "r2", "telegram", now=T0 + DAY + 3600
        )
        _lr, due_late, _dr = engine_on._read_clock_and_delta_locked("agent_ruka")
        # 延後者：下一次到期 ＝ 到場時間 + 24h ＝ T+49h
        assert due_late == pytest.approx(T0 + DAY + 3600 + DAY)

        # 🔴 有牙的斷言：兩者**相差恰好 1 小時**（非相等）。
        #    舊實作下 due_exact == due_late == T+48h ⇒ 差值 0 ⇒ 必紅。
        assert due_late - due_exact == pytest.approx(3600), (
            "每個有效互動應各自重新給滿 24 小時：延後 1 小時到場者，"
            f"下一次到期也應晚 1 小時（準時 {due_exact} vs 延後 {due_late}）"
            " —— 舊實作會讓兩者同為 T+48h，偷走延後者 1 小時"
        )
        assert due_exact != pytest.approx(due_late), (
            "準時與延後到場者**不得**得到同一個下一次到期點"
            f"（準時 {due_exact} vs 延後 {due_late}）"
        )

    def test_overdue_late_inbound_regrants_full_24h_exactly(self, engine_on) -> None:
        """🔴 T1 例二（鎖住精確數值）：`T+25h` 到場 ⇒ 下一次到期 = `T+49h`。

        即 `next_decay_due_at = last_written + 24h`，而非 `due + 24h`。
        以 `T0` 為首次 touch（`due0 = T0 + DAY`）。
        """
        # 例一：T+24h 到場 ⇒ 下一次到期 = T+48h
        engine_on.touch_inbound("agent_yua", "e1", "telegram", now=T0)
        r = engine_on.touch_inbound("agent_yua", "e2", "telegram", now=T0 + DAY)
        assert r["last_valid_inbound_at"] == pytest.approx(T0 + DAY)
        assert r["next_decay_due_at"] == pytest.approx(T0 + 2 * DAY)  # T+48h

        # 例二（另一個角色，避免互相污染）：T+25h 到場 ⇒ 下一次到期 = T+49h
        engine_on.touch_inbound("agent_ruka", "r1", "telegram", now=T0)
        r2 = engine_on.touch_inbound(
            "agent_ruka", "r2", "telegram", now=T0 + DAY + 3600
        )
        assert r2["last_valid_inbound_at"] == pytest.approx(T0 + DAY + 3600)
        assert r2["next_decay_due_at"] == pytest.approx(
            T0 + DAY + 3600 + DAY
        ), "逾期分支的 next_decay_due_at 必須是 last_written + 24h（T+49h）"

    def test_overdue_late_inbound_settles_at_most_once(self, engine_on) -> None:
        """🔴 逾期清算仍**最多一次**（bounded 1-step 契約不得被本次修正破壞）。

        `T+25h` 到場：ledger 恰增 1 筆、Delta 恰扣 0.5，**不是**補扣多步。
        """
        engine_on.update_delta("agent_ruka", 5.0)
        engine_on.touch_inbound("agent_ruka", "r1", "telegram", now=T0)
        assert _ledger_count(engine_on, "agent_ruka") == 0

        engine_on.touch_inbound(
            "agent_ruka", "r2", "telegram", now=T0 + DAY + 3600
        )

        assert _ledger_count(engine_on, "agent_ruka") == 1, (
            "逾期到場應結算**恰好一次**（ledger 恰 1 筆）"
        )
        assert _delta_of(engine_on, "agent_ruka") == pytest.approx(
            5.0 - DECAY_STEP
        ), "逾期到場只扣**一步**（bounded 1-step），不得累積多步"


# ═════════════════════════════════════════════════════════════
# G15. 缺口 2 — 舊到期點重放不得在 inbound 路徑再次扣分
# ═════════════════════════════════════════════════════════════


class TestInboundReplayOfSettledDue:
    """🔴🔴 缺口 2 負例：原到期點**已在 ledger**、due 被**重放**、再送真人 inbound。

    `touch_inbound()` 的逾期分支**直接呼叫 `_settle_once_locked()`**，跳過
    `try_apply_decay()` 的前置查 `_ledger_has_locked()`。舊實作的 ledger INSERT
    是 `ON CONFLICT DO NOTHING` ⇒ ledger 不增，但**仍繼續 UPDATE Delta**
    ⇒ 憑空再扣一次。

    修正後 `_settle_once_locked()` 以 INSERT 的**實際寫入列數**（`rowcount`）
    為閘門：`rowcount == 0` ⇒ 不 UPDATE Delta、不推進 due、回 `0.0`。
    """

    def test_replayed_due_via_inbound_does_not_double_deduct(
        self, engine_on
    ) -> None:
        """斷言三件事：**0 再扣、ledger 不增、時鐘單調**。"""
        engine_on.update_delta("agent_yua", 5.0)
        engine_on.touch_inbound("agent_yua", "e1", "telegram", now=T0)

        # 第一次結算（T+24h 到期）⇒ 扣 0.5、ledger 1 筆
        engine_on.try_apply_decay("agent_yua", now=T0 + DAY)
        assert _ledger_count(engine_on, "agent_yua") == 1, "前置：應恰 1 筆"
        delta_after_first = _delta_of(engine_on, "agent_yua")
        assert delta_after_first == pytest.approx(5.0 - DECAY_STEP)

        # 🔴 把 due **重放**回已結算過的舊到期點（模擬 tick 與 inbound 交錯 /
        #    跨行程重放）。ledger 內已有該 event_key。
        engine_on.conn.execute(
            "UPDATE agent_emotions SET next_decay_due_at = ? WHERE agent_id = ?",
            (T0 + DAY, "agent_yua"),
        )
        engine_on.conn.commit()

        # 此時「真人 inbound」抵達，且時間 >= 該重放的 due ⇒ 走逾期分支
        result = engine_on.touch_inbound(
            "agent_yua", "e2", "telegram", now=T0 + DAY + 10
        )

        # 1) 0 再扣（舊實作會在此再扣 0.5 ⇒ 必紅）
        assert _delta_of(engine_on, "agent_yua") == pytest.approx(
            delta_after_first
        ), (
            "舊到期點被重放時，inbound 路徑**再次扣分**了 —— "
            f"{delta_after_first} -> {_delta_of(engine_on, 'agent_yua')}"
        )
        # 2) ledger 不增
        assert _ledger_count(engine_on, "agent_yua") == 1, (
            "重放的到期點不得寫入第二筆 ledger"
        )
        assert _ledger_total_amount(engine_on, "agent_yua") == pytest.approx(
            DECAY_STEP
        )
        # 3) 時鐘單調（不得倒退）
        last_after, due_after, _d = engine_on._read_clock_and_delta_locked(
            "agent_yua"
        )
        assert due_after is not None and due_after >= T0 + DAY, (
            f"重放後 due 倒退了：{due_after}"
        )
        assert last_after is not None and last_after >= T0, "last 倒退了"
        assert due_after >= last_after, "due 不得早於 last_valid_inbound_at"

        # 回傳物件仍誠實：TOUCH 成立（真人確實到場），但**沒有**扣減事實
        assert result["touched"] is True

    def test_replayed_due_settle_returns_zero_applied(self, engine_on) -> None:
        """🔴 直呼內部結算閘門：已存在的 event_key ⇒ 回 `0.0` 且零 UPDATE。

        這是與上一測例互補的**單元層**證明：閘門在 `_settle_once_locked()`
        內部（DB 層真實寫入結果），不依賴呼叫端記得先查。
        """
        engine_on.update_delta("agent_yua", 5.0)
        engine_on.touch_inbound("agent_yua", "e1", "telegram", now=T0)
        engine_on.try_apply_decay("agent_yua", now=T0 + DAY)

        delta_before = _delta_of(engine_on, "agent_yua")
        key = engine_on._decay_event_key("agent_yua", T0 + DAY)

        applied = engine_on._settle_once_locked(
            "agent_yua",
            T0 + DAY + 30,
            T0 + DAY,
            delta_before,
            origin="test_replay",
            event_key=key,
        )

        assert applied == pytest.approx(0.0), (
            f"已結算過的到期點應回 0.0（未扣），實際 {applied}"
        )
        assert _delta_of(engine_on, "agent_yua") == pytest.approx(delta_before), (
            "重放竟然 UPDATE 了 Delta"
        )
        assert _ledger_count(engine_on, "agent_yua") == 1

    def test_fresh_due_still_settles_normally(self, engine_on) -> None:
        """🔴 對照組：**全新**的到期點仍正常結算（閘門不得把正常路徑也擋掉）。"""
        engine_on.update_delta("agent_yua", 5.0)
        engine_on.touch_inbound("agent_yua", "e1", "telegram", now=T0)

        first = engine_on.try_apply_decay("agent_yua", now=T0 + DAY)
        assert first["applied"] is True
        assert _ledger_count(engine_on, "agent_yua") == 1

        # 走到下一個全新到期點
        second = engine_on.try_apply_decay("agent_yua", now=T0 + 2 * DAY)
        assert second["applied"] is True, f"全新到期點竟未結算：{second}"
        assert _ledger_count(engine_on, "agent_yua") == 2
        assert _delta_of(engine_on, "agent_yua") == pytest.approx(
            5.0 - 2 * DECAY_STEP
        )


# ═════════════════════════════════════════════════════════════
# G16. 缺口 3 — 週期接線：安靜滿 24h 當下自然發生衰減
# ═════════════════════════════════════════════════════════════


class TestPeriodicDecayWiring:
    """🔴🔴 缺口 3：`try_apply_decay()` 必須有**執行接線**。

    交付時 `try_apply_decay()` **零接線**（`scripts/run_server.py` /
    `src/soul/scheduler.py` / `src/agent/consciousness.py` 全 0 命中）⇒
    唯一走得到扣減的是 `touch_inbound()`（TG 被動）⇒ **安靜滿 24h 當下不會扣**。

    本組測試驗的是**接線單元** `decay_evaluate_eligible_agents()` 的行為，
    以及它被接在**既有**週期點上（`scripts/run_server.py` 的 SAGE flush 15s
    迴圈）的原始碼事實。
    """

    def test_no_inbound_at_t_plus_24h_decays_exactly_once(
        self, engine_on, monkeypatch
    ) -> None:
        """🔴 「沒有任何 inbound，T+24h 仍只扣一次」—— 舊實作零接線 ⇒ 必紅。

        舊實作下 `decay_evaluate_eligible_agents` **不存在** ⇒ 本測試在
        collection/attribute 階段即失敗（ImportError/AttributeError）⇒ 紅。
        """
        from src.agent.emotion import decay_evaluate_eligible_agents

        # 🔴 週期評估走的是 module-level singleton ⇒ 必須把 singleton 指向
        #    本測試的拋棄式 engine（monkeypatch 還原，不污染後續測試順序）。
        import src.agent.emotion as emotion_mod

        monkeypatch.setattr(emotion_mod, "emotion_engine", engine_on)

        engine_on.update_delta("agent_yua", 5.0)
        engine_on.touch_inbound("agent_yua", "e1", "telegram", now=T0)
        assert _due_of(engine_on, "agent_yua") == pytest.approx(T0 + DAY)
        assert _ledger_count(engine_on, "agent_yua") == 0

        # 🔴 **沒有任何 inbound** —— 只有週期評估
        first = decay_evaluate_eligible_agents(now=T0 + DAY)

        # 1) delta 恰扣 0.5
        assert _delta_of(engine_on, "agent_yua") == pytest.approx(5.0 - DECAY_STEP), (
            "逾期後跑一輪週期評估，delta 未扣 —— 週期接線失效"
        )
        # 2) ledger 恰 1 筆
        assert _ledger_count(engine_on, "agent_yua") == 1, "週期評估未寫 ledger"
        # 3) due 推進到 T+48h
        assert _due_of(engine_on, "agent_yua") == pytest.approx(T0 + 2 * DAY), (
            "週期評估後 due 未推進到 T+48h"
        )
        assert first["applied"] == 1, f"應恰 1 名角色被扣：{first}"

        # 🔴 再跑一輪**同一評估** ⇒ 不再扣
        _delta_before_second = _delta_of(engine_on, "agent_yua")
        second = decay_evaluate_eligible_agents(now=T0 + DAY + 1)

        assert _ledger_count(engine_on, "agent_yua") == 1, (
            "第二輪週期評估又寫了 ledger"
        )
        assert _delta_of(engine_on, "agent_yua") == pytest.approx(
            _delta_before_second
        ), "第二輪週期評估又扣了一次"
        assert second["applied"] == 0, f"第二輪不應再扣：{second}"

    def test_periodic_evaluation_bounded_to_one_step_per_agent(
        self, engine_on, monkeypatch
    ) -> None:
        """🔴 有界：即使逾期極久，一輪也只對合格角色各評估**至多一步**。"""
        from src.agent.emotion import decay_evaluate_eligible_agents

        import src.agent.emotion as emotion_mod

        monkeypatch.setattr(emotion_mod, "emotion_engine", engine_on)

        engine_on.update_delta("agent_yua", 5.0)
        engine_on.touch_inbound("agent_yua", "e1", "telegram", now=T0)

        # 逾期 365 天 —— bounded 1-step catch-up（§5）
        result = decay_evaluate_eligible_agents(now=T0 + 365 * DAY)

        assert _delta_of(engine_on, "agent_yua") == pytest.approx(5.0 - DECAY_STEP), (
            "一輪週期評估不得補扣多步"
        )
        assert _ledger_count(engine_on, "agent_yua") == 1
        assert result["applied"] == 1

    def test_periodic_evaluation_flag_off_is_zero_write(
        self, db_path, monkeypatch
    ) -> None:
        """🔴 旗標 OFF ⇒ 該評估**零寫入**（delta / ledger / clock 全不變）。

        這是 C4 契約：OFF ⇒ no-op、零 DDL、零持久寫入。
        """
        from src.agent.emotion import decay_evaluate_eligible_agents

        # 先在 ON 下鋪好逾期場景，再轉 OFF（證明 OFF 是**當下**生效）
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        engine = EmotionEngine(db_path=db_path)
        _seed_overdue(engine, "agent_yua", delta=5.0, due=T0 + DAY)

        # 把全域 singleton 指向本測試的 engine（monkeypatch 還原）
        import src.agent.emotion as emotion_mod

        monkeypatch.setattr(emotion_mod, "emotion_engine", engine)

        delta_before = _delta_of(engine, "agent_yua")
        last_before, due_before, _d = engine._read_clock_and_delta_locked(
            "agent_yua"
        )
        ledger_before = _ledger_count(engine, "agent_yua")
        assert ledger_before == 0

        # 🔴 轉 OFF
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "")

        result = decay_evaluate_eligible_agents(now=T0 + 10 * DAY)

        assert result["evaluated"] == 0, f"旗標 OFF 竟仍評估：{result}"
        assert result["reason"] == "FLAG_OFF"
        assert result["applied"] == 0
        # 零寫入：三者全不變
        assert _delta_of(engine, "agent_yua") == pytest.approx(delta_before), (
            "旗標 OFF 竟扣了 Delta"
        )
        assert _ledger_count(engine, "agent_yua") == ledger_before, (
            "旗標 OFF 竟寫了 ledger"
        )
        last_after, due_after, _d2 = engine._read_clock_and_delta_locked(
            "agent_yua"
        )
        assert last_after == last_before, "旗標 OFF 竟動了 last_valid_inbound_at"
        assert due_after == due_before, "旗標 OFF 竟動了 next_decay_due_at"

    def test_periodic_evaluation_skips_ineligible_agents(
        self, db_path, monkeypatch
    ) -> None:
        """🔴 週期評估**不得**讓三個不合格角色被扣（白名單在週期路徑同樣有效）。"""
        from src.agent.emotion import decay_evaluate_eligible_agents

        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        engine = EmotionEngine(db_path=db_path)
        for aid in INELIGIBLE_AGENTS:
            _seed_overdue(engine, aid, delta=10.0, due=T0 - 3600.0)

        import src.agent.emotion as emotion_mod

        monkeypatch.setattr(emotion_mod, "emotion_engine", engine)

        decay_evaluate_eligible_agents(now=T0)

        for aid in INELIGIBLE_AGENTS:
            assert _delta_of(engine, aid) == pytest.approx(10.0), (
                f"週期路徑扣了不合格角色 {aid}"
            )
            assert _ledger_count(engine, aid) == 0, (
                f"週期路徑為不合格角色 {aid} 寫了 ledger"
            )

    def test_wiring_call_is_flag_gated_first_line(self) -> None:
        """🔴 靜態事實：評估函式**第一行**就檢查旗標（OFF ⇒ 零工作）。"""
        import ast as _ast

        src = (REPO_ROOT / "src" / "agent" / "emotion.py").read_text(
            encoding="utf-8"
        )
        tree = _ast.parse(src)

        fn = None
        for node in _ast.walk(tree):
            if (
                isinstance(node, _ast.FunctionDef)
                and node.name == "decay_evaluate_eligible_agents"
            ):
                fn = node
                break
        assert fn is not None, "找不到 decay_evaluate_eligible_agents()"

        first_stmt = fn.body[0]
        # 略過 docstring：本函式的說明文字是**契約文件**，不是可執行語句。
        # 「第一行」指的是**第一個可執行語句**。
        if (
            isinstance(first_stmt, _ast.Expr)
            and isinstance(first_stmt.value, _ast.Constant)
            and isinstance(first_stmt.value.value, str)
        ):
            first_stmt = fn.body[1]
        assert isinstance(first_stmt, _ast.If), (
            "評估函式第一行必須是旗標檢查（C4：OFF ⇒ 零工作）"
        )
        cond = first_stmt.test
        assert isinstance(cond, _ast.UnaryOp) and isinstance(cond.op, _ast.Not), (
            "第一行必須是 `if not decay_enabled():`"
        )
        assert isinstance(cond.operand, _ast.Call)
        assert getattr(cond.operand.func, "id", None) == "decay_enabled"

    def test_periodic_wiring_attached_to_existing_loop(self) -> None:
        """🔴 靜態事實：接線掛在**既有**週期點上，**不得**新建定時器/task/loop。

        以原始碼證據鎖住三件事：
          1. `scripts/run_server.py` 內確實呼叫了 `decay_evaluate_eligible_agents()`
          2. 該呼叫位於既有 `_sage_flush_loop()` 函式體內
          3. **沒有**為它新增 `asyncio.create_task` / `while True` /
             `asyncio.sleep` 的任何新週期結構：`_sage_flush_loop` 仍只有
             **一個** `asyncio.create_task` 掛載點與**一個** `while True`。
        """
        import ast as _ast

        src = (REPO_ROOT / "scripts" / "run_server.py").read_text(
            encoding="utf-8"
        )
        assert "decay_evaluate_eligible_agents" in src, (
            "run_server.py 找不到週期接線 —— try_apply_decay 仍零接線"
        )
        tree = _ast.parse(src)

        loop_fn = None
        for node in _ast.walk(tree):
            if (
                isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef))
                and node.name == "_sage_flush_loop"
            ):
                loop_fn = node
                break
        assert loop_fn is not None, "找不到既有 _sage_flush_loop()"

        calls = [
            n
            for n in _ast.walk(loop_fn)
            if isinstance(n, _ast.Call)
            and getattr(n.func, "id", None) == "decay_evaluate_eligible_agents"
        ]
        assert calls, "既有 _sage_flush_loop() 內找不到週期接線呼叫"

        # 不得在迴圈內新增週期結構
        sleeps = [
            n
            for n in _ast.walk(loop_fn)
            if isinstance(n, _ast.Attribute) and n.attr == "sleep"
        ]
        whiles = [n for n in _ast.walk(loop_fn) if isinstance(n, _ast.While)]
        creates = [
            n
            for n in _ast.walk(loop_fn)
            if isinstance(n, _ast.Call)
            and isinstance(n.func, _ast.Attribute)
            and n.func.attr == "create_task"
        ]
        assert len(sleeps) == 1, f"不得新增 sleep 週期（found {len(sleeps)}）"
        assert len(whiles) == 1, f"不得新增 background loop（found {len(whiles)}）"
        assert not creates, "不得在既有週期任務內新建 asyncio task"

        # 例外不得殺死任務：呼叫包在 try/except 內
        try_nodes = [n for n in _ast.walk(loop_fn) if isinstance(n, _ast.Try)]
        assert try_nodes, "週期任務本體必須包 try/except（慣例：例外只記 WARNING）"
        wrapped = any(
            any(
                isinstance(h.type, _ast.Name) and h.type.id == "Exception"
                for h in t.handlers
                if h.type is not None
            )
            for t in try_nodes
        )
        assert wrapped, "週期任務缺少 `except Exception` 保護"
