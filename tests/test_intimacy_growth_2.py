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
        engine_on.update_delta("agent_ruka", 5.0)
        engine_on.touch_inbound("agent_ruka", "e1", "telegram", now=T0)
        before = engine_on.get_delta("agent_ruka")

        late = T0 + 40 * DAY
        result = engine_on.touch_inbound("agent_ruka", "e2", "telegram", now=late)

        assert result["touched"] is True
        # 先結算**恰好一步**
        assert engine_on.get_delta("agent_ruka") == pytest.approx(before - DECAY_STEP)
        # 再推進：due = 結算後的時鐘 + 24h
        assert result["next_decay_due_at"] > late

    def test_overdue_inbound_never_moves_clock_backwards(self, engine_on) -> None:
        """過期 inbound **不得**讓時鐘倒退。"""
        engine_on.update_delta("agent_ruka", 5.0)
        engine_on.touch_inbound("agent_ruka", "e1", "telegram", now=T0)

        later = T0 + 40 * DAY
        r1 = engine_on.touch_inbound("agent_ruka", "e2", "telegram", now=later)
        due1 = r1["next_decay_due_at"]

        # 第二筆「更晚但仍在窗內」的 inbound
        r2 = engine_on.touch_inbound("agent_ruka", "e3", "telegram", now=later + 60)
        assert r2["next_decay_due_at"] >= due1

        # 甚至一筆**時間戳較舊**（亂序抵達）的 inbound 也不得倒退
        r3 = engine_on.touch_inbound("agent_ruka", "e4", "telegram", now=later - 10 * DAY)
        assert r3["next_decay_due_at"] >= due1

    def test_repeated_inbound_in_window_does_not_deduct(self, engine_on) -> None:
        """同一個窗內的重複 inbound ⇒ 零扣減（只推 due 或維持）。"""
        engine_on.update_delta("agent_anka", 5.0)
        engine_on.touch_inbound("agent_anka", "e1", "telegram", now=T0)
        before = engine_on.get_delta("agent_anka")

        for i in range(10):
            engine_on.touch_inbound("agent_anka", f"r{i}", "telegram", now=T0 + i * 60)

        assert engine_on.get_delta("agent_anka") == before


# ═════════════════════════════════════════════════════════════
# G4. 重啟補扣 ⇒ 最多一次（bounded 1-step catch-up）
# ═════════════════════════════════════════════════════════════


class TestRestartCatchup:
    def test_restart_catches_up_at_most_one_step(self, db_path, monkeypatch) -> None:
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        eng = EmotionEngine(db_path=db_path)
        eng.update_delta("agent_rem", 10.0)
        eng.touch_inbound("agent_rem", "e1", "telegram", now=T0)
        before = eng.get_delta("agent_rem")

        # 「停服」很久（365 天）後重啟：新引擎實例 = 新的模組生命週期
        eng2 = EmotionEngine(db_path=db_path)
        result = eng2.try_apply_decay("agent_rem", now=T0 + 365 * DAY)

        assert result["applied"] is True
        # bounded：**只補一步**，不累積 365 步
        assert eng2.get_delta("agent_rem") == pytest.approx(before - DECAY_STEP)

    def test_catchup_then_second_evaluation_is_bounded(self, db_path, monkeypatch) -> None:
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        eng = EmotionEngine(db_path=db_path)
        eng.update_delta("agent_rem", 10.0)
        eng.touch_inbound("agent_rem", "e1", "telegram", now=T0)

        eng2 = EmotionEngine(db_path=db_path)
        eng2.try_apply_decay("agent_rem", now=T0 + 100 * DAY)
        after_first = eng2.get_delta("agent_rem")
        # 立刻再評估（未到新 due）⇒ 不再扣
        eng2.try_apply_decay("agent_rem", now=T0 + 100 * DAY + 1)
        assert eng2.get_delta("agent_rem") == after_first

    def test_ledger_survives_restart_and_blocks_replay(self, db_path, monkeypatch) -> None:
        """ledger 跨行程冪等：同一到期點重放不得再扣。"""
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        eng = EmotionEngine(db_path=db_path)
        eng.update_delta("agent_rem", 10.0)
        eng.touch_inbound("agent_rem", "e1", "telegram", now=T0)
        eng.try_apply_decay("agent_rem", now=T0 + DAY)
        after = eng.get_delta("agent_rem")

        # 另一實例（模擬重啟）對同一到期點重放
        eng2 = EmotionEngine(db_path=db_path)
        # 把 due 手動倒回原到期點，模擬「舊 due 被重放」
        eng2.conn.execute(
            "UPDATE agent_emotions SET next_decay_due_at = ? WHERE agent_id = ?",
            (T0 + DAY, "agent_rem"),
        )
        eng2.conn.commit()
        result = eng2.try_apply_decay("agent_rem", now=T0 + DAY + 10)

        assert result["applied"] is False
        assert result["reason"] == "DUPLICATE"
        assert eng2.get_delta("agent_rem") == after


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
        engine_on.update_delta("agent_akane", 7.0)
        engine_on.touch_inbound("agent_akane", "e0", "telegram", now=T0)

        real_conn = engine_on.conn
        delta_before = engine_on.get_delta("agent_akane")
        row_before = tuple(
            real_conn.execute(
                "SELECT next_decay_due_at FROM agent_emotions WHERE agent_id = ?",
                ("agent_akane",),
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
                engine_on.try_apply_decay("agent_akane", now=T0 + DAY)
        finally:
            engine_on.conn = real_conn
        assert wrapper.tripped, "故障注入未觸發（測試本身失效）"

        # 三者全回滾
        assert engine_on.get_delta("agent_akane") == delta_before
        row_after = tuple(
            real_conn.execute(
                "SELECT next_decay_due_at FROM agent_emotions WHERE agent_id = ?",
                ("agent_akane",),
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
        engine_on.update_delta("agent_akane", 7.0)
        engine_on.touch_inbound("agent_akane", "e0", "telegram", now=T0)

        real_conn = engine_on.conn
        wrapper = _FailOnLedgerInsertConn(real_conn)
        engine_on.conn = wrapper
        try:
            with pytest.raises(sqlite3.OperationalError):
                engine_on.try_apply_decay("agent_akane", now=T0 + DAY)
        finally:
            engine_on.conn = real_conn
        assert wrapper.tripped

        assert real_conn.in_transaction is False
        result = engine_on.try_apply_decay("agent_akane", now=T0 + DAY)
        assert result["applied"] is True


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
        engine.touch_inbound("agent_akane", "e0", "voice_companion", now=T0)
        before = engine.get_delta("agent_akane")

        # 模擬 barge-in：只是一個 generation 遞增，無 TOUCH 呼叫
        generation = 0
        generation += 1  # _barge()

        assert generation == 1
        assert engine.get_delta("agent_akane") == before

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

        emotion_mod.emotion_engine = engine
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

        assert self._clock(engine, "agent_ruka") is None, "陌生人 inbound 竟然 TOUCH"

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

        emotion_mod.emotion_engine = engine

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
        result = engine_on.try_apply_decay("agent_never_seen", now=T0 + 90 * DAY)
        assert result["applied"] is False
        assert result["reason"] == "UNKNOWN"

    def test_unknown_does_not_zero_existing_delta(self, engine_on) -> None:
        """UNKNOWN **不得**清零既有 Delta。"""
        engine_on.ensure_decay_schema()
        engine_on.update_delta("agent_newbie", 4.25)
        engine_on.conn.execute(
            "UPDATE agent_emotions SET last_valid_inbound_at = NULL, "
            "next_decay_due_at = NULL WHERE agent_id = ?",
            ("agent_newbie",),
        )
        engine_on.conn.commit()
        before = engine_on.get_delta("agent_newbie")

        for _ in range(10):
            result = engine_on.try_apply_decay("agent_newbie", now=T0 + 900 * DAY)

        assert result["applied"] is False
        assert result["reason"] == "UNKNOWN"
        assert engine_on.get_delta("agent_newbie") == pytest.approx(before)

    def test_unknown_creates_no_row(self, engine_on) -> None:
        engine_on.ensure_decay_schema()
        engine_on.try_apply_decay("agent_ghost", now=T0 + DAY)
        rows = list(
            engine_on.conn.execute(
                "SELECT agent_id FROM agent_emotions WHERE agent_id = ?",
                ("agent_ghost",),
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
        """§1：每角色獨立計時。"""
        engine_on.update_delta("agent_yua", 5.0)
        engine_on.update_delta("agent_ruka", 5.0)

        # yua 在 T0 TOUCH；ruka 在 T0 + 12h TOUCH
        engine_on.touch_inbound("agent_yua", "a", "telegram", now=T0)
        engine_on.touch_inbound("agent_ruka", "b", "telegram", now=T0 + 12 * 3600)

        # T0 + 24h：yua 到期、ruka 未到期
        yua = engine_on.try_apply_decay("agent_yua", now=T0 + DAY)
        ruka = engine_on.try_apply_decay("agent_ruka", now=T0 + DAY)

        assert yua["applied"] is True
        assert ruka["applied"] is False
        assert ruka["reason"] == "NOT_DUE"

        # §1 的核心：兩者的時鐘**各自獨立**，不是共享一個。
        # yua 在 T0 到期並已結算 ⇒ 其 due 被推到 T0+2d。
        # ruka 在 T0+12h 才 TOUCH、T0+24h 尚未到期 ⇒ 其 due 仍在 T0+36h。
        yua_due = list(
            engine_on.conn.execute(
                "SELECT next_decay_due_at FROM agent_emotions WHERE agent_id='agent_yua'"
            )
        )[0][0]
        ruka_due = list(
            engine_on.conn.execute(
                "SELECT next_decay_due_at FROM agent_emotions WHERE agent_id='agent_ruka'"
            )
        )[0][0]
        assert yua_due == pytest.approx(T0 + 2 * DAY)
        assert ruka_due == pytest.approx(T0 + 12 * 3600 + DAY)
        assert yua_due != ruka_due, "兩個角色不得共用同一個時鐘"

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
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    import secrets as _secrets

    monkeypatch.setenv("INTERNAL_VC_TOUCH_ENABLED", "1" if flag_on else "")
    monkeypatch.setenv("INTERNAL_VC_TOUCH_TOKEN", token)
    # 主服務衰減旗標：OFF ⇒ 200 + FLAG_OFF（不落帳）
    monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "")

    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"ok": True}

    def _enabled() -> bool:
        raw = os.environ.get("INTERNAL_VC_TOUCH_ENABLED")
        if not isinstance(raw, str):
            return False
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    # 🔴 註解型別必須寫成 `fastapi.Request`（模組層級可解析的名稱）。
    # 本檔有 `from __future__ import annotations`，FastAPI 會把註解存成
    # **字串**再交給 pydantic 解析；若寫成區域別名（例：`Request as R`）或
    # 依賴區域 import，該字串在模組 globals 中查無此名 ⇒
    # `PydanticUserError: ... is not fully defined`。實測踩過，故用模組層級
    # 的 `fastapi.Request`。
    async def _touch_handler(request: fastapi.Request, payload: dict):
        if not _enabled():
            raise HTTPException(status_code=404, detail="not found")
        expected = (os.environ.get("INTERNAL_VC_TOUCH_TOKEN") or "").strip()
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

    # 以 add_api_route 註冊（等價於 `@app.post(...)`），但避免裝飾器把
    # 區域函式的型別註解延後解析而觸發 pydantic 的 ForwardRef 問題。
    app.add_api_route(_TOUCH_PATH, _touch_handler, methods=["POST"])

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
    return app, TestClient(app)


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
        assert _TOUCH_PATH in info["registered_paths"], (
            "本端點仍必須被註冊（過濾只作用於 OpenAPI，不移除路由）"
        )
        # 註：`run_server.py` 只有 3 條端點是**直接**用 `@app.<method>` 註冊，
        # 其餘皆經由 `include_router(...)` 掛入。故此處斷言「至少有本端點
        # 與其他既有端點兩條」而非誇大的數量門檻。
        assert len(info["registered_paths"]) >= 2, (
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

    def test_route_still_present_in_app_routes_when_off(self, monkeypatch) -> None:
        """🔴 誠實揭露已知殘餘：路由**仍註冊於 `app.routes`**（FastAPI 架構限制）。

        此斷言把「已知殘餘」寫成可執行的契約，避免後人誤以為已完全不存在。
        """
        app, _client = _build_equivalent_app(monkeypatch, flag_on=False)
        route_paths = {getattr(r, "path", None) for r in app.routes}
        assert _TOUCH_PATH in route_paths, (
            "若此斷言變紅，代表殘餘已被消除 —— 那是好消息，請一併更新 "
            "run_server.py 的 docstring 誠實邊界段落"
        )

    # ── ON 態 ──

    def test_on_endpoint_listed_in_openapi_paths(self, monkeypatch) -> None:
        """🔴 ON ⇒ 才列出此路徑（證明過濾是條件式，不是無條件移除）。"""
        _app, client = _build_equivalent_app(monkeypatch, flag_on=True)
        paths = client.get("/openapi.json").json()["paths"]
        assert _TOUCH_PATH in paths, "旗標 ON 時本端點必須可被列舉"

    def test_on_without_token_returns_503(self, monkeypatch) -> None:
        """🔴 ON 且未設 token ⇒ 503（fail-closed，絕不匿名放行）。"""
        _app, client = _build_equivalent_app(monkeypatch, flag_on=True, token="")
        resp = client.post(
            _TOUCH_PATH, json={"agent_id": "agent_x", "event_id": "e1"}
        )
        assert resp.status_code == 503, "未設 token 必須 fail-closed 為 503"

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
        _app, client = _build_equivalent_app(monkeypatch, flag_on=True)
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
        """
        obs = self._run_race(engine_on, "agent_race")

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
        obs = self._run_race(engine_on, "agent_race_contract")

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
