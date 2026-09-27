"""tests/soul/test_decay_worker_settlement.py — 證明 2：**真評估器**的隔離結算。

## 被證明的命題

衰減 worker 接縫（`src/agent/decay_worker.py`）在**真實**評估器上，於
**拋棄式的資料根與 DB** 內，逐條證明下列行為：

  - 旗標 OFF ⇒ 不排 worker、worker 本體回 `FLAG_OFF`、零寫入。
  - 排程：空閒時排入、在途時略過（同時在途 ≤ 1）。
  - **七個角色逐一被評估**（`due` / `not-due` 的邊界）。
  - ledger 冪等（同一輪評估不重複扣）。
  - **每角色邊界取消**：旗標在首名角色在途時 set ⇒ 其餘六名完全不被評估。

## 🔴 為什麼一定要「真」評估器

工單明文：**假的評估器不得被當成結算證據**。一個 stub 只能證明「stub 被呼叫
了」，不能證明「扣減真的發生、ledger 真的落盤、due 真的推進」。因此本檔：

  - 用**真的** `EmotionEngine`（指向 tmp DB），
  - 用**真的** `decay_evaluate_eligible_agents`
    （`src/agent/emotion.py` 的公開 API），
  - 只以 `should_stop` 回呼（公開注入點）製造取消交錯。

唯一被替換的是「**何時**觸發取消」，那是被測語意的**輸入**，不是被測物。

## 🔴 建立順序（工單 §4.3：先建立拋棄式根，再載入依賴）

`src/agent/emotion.py` 在**匯入當下**會呼叫 `data_root()`（模組層級 singleton，
`src/paths.py:26,40-48` 快取）。若在設定 `SOUL_OS_DATA_DIR` **之前**就 import
emotion，資料根就已定型在 repo `data/` —— 之後再設定也沒有用。
故本檔在最上方、任何 `src.*` 匯入之前，先建立並釘住拋棄式根。

## 隔離紀律

  - 全程 tmp DB；**不碰** `data/**`、**不啟動**服務、**不綁**任何埠、
    **不殺**任何行程、**不載入** `.env`。

跑法：

    .venv\\Scripts\\python.exe -m pytest -q tests/soul/test_decay_worker_settlement.py
"""
from __future__ import annotations

# ═════════════════════════════════════════════════════════════
# 🔴 步驟 1：**先**建立拋棄式資料根，**再**載入任何 src.* 依賴
# ═════════════════════════════════════════════════════════════
# 這必須在任何 `from src... import ...` 之前執行：`src/paths.py` 的
# `_DATA_ROOT` 是模組層級快取，而 `src/agent/emotion.py` 匯入時就會呼叫
# `data_root()`。順序錯了，隔離就是假的（而且不會報錯，只會靜默汙染）。
import os as _os
import sys as _sys
import tempfile as _tempfile
from pathlib import Path as _Path

_REPO_ROOT = _Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_REPO_ROOT))

#: 本模組專屬的拋棄式資料根（行程級；比 fixture 更早、更穩）。
_SETTLEMENT_ROOT = _Path(_tempfile.mkdtemp(prefix="soul_decay_settlement_"))
_os.environ["SOUL_OS_DATA_DIR"] = str(_SETTLEMENT_ROOT)
# 剝除可能的生產憑證：本檔不需要它們，缺席時若實作仍能運作才叫真隔離。
for _key in list(_os.environ):
    if _key.startswith(("OPENAI_", "ANTHROPIC_", "DEEPSEEK_", "GEMINI_", "TELEGRAM_", "TG_")):
        _os.environ.pop(_key, None)

# ── 步驟 2：此時才載入依賴 ───────────────────────────────────
import asyncio  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402

import src.agent.decay_worker as dw  # noqa: E402
import src.agent.emotion as emotion_mod  # noqa: E402
from src.agent.emotion import (  # noqa: E402
    DECAY_STEP,
    DECAY_WINDOW_HOURS,
    EmotionEngine,
    decay_evaluate_eligible_agents,
)
from src.paths import data_root  # noqa: E402

DAY = DECAY_WINDOW_HOURS * 3600.0
T0 = 1_700_000_000.0  # 固定基準時刻（epoch seconds）

#: 期望的七個合格角色（**獨立於** emotion 的常數書寫，見既有測試的理由）。
ELIGIBLE_AGENTS = (
    "agent_yua",
    "agent_ruka",
    "agent_ram",
    "agent_mahiru",
    "agent_anna",
    "agent_miku",
    "agent_aoi",
)


# ─────────────────────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────────────────────


def _ledger_count(engine: EmotionEngine, agent_id: str) -> int:
    cur = engine.conn.execute(
        "SELECT COUNT(*) FROM intimacy_decay_ledger WHERE agent_id = ?",
        (agent_id,),
    )
    return int(cur.fetchone()[0])


def _seed_overdue(engine: EmotionEngine, agent_id: str, delta: float, due: float) -> None:
    """把角色鋪成「已逾期且 Delta 非零」。"""
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


def _reset_worker() -> None:
    """worker 全域狀態歸零（含行程級取消旗標 —— 見檔尾說明）。"""
    task = getattr(dw, "_DECAY_WORKER_TASK", None)
    if task is not None and not task.done():
        task.cancel()
    dw._DECAY_WORKER_TASK = None
    dw._DECAY_WORKER_CANCEL.clear()


@pytest.fixture
def db_path(tmp_path) -> _Path:
    """拋棄式 DB 路徑（tmp_path 內）。"""
    return tmp_path / "memory.db"


@pytest.fixture
def engine_on(db_path, monkeypatch) -> EmotionEngine:
    """旗標 ON 的**真**引擎（tmp DB）。"""
    monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
    return EmotionEngine(db_path=db_path)


@pytest.fixture(autouse=True)
def _clean_worker_state():
    """每個測試前後都把 worker 狀態歸零。

    🔴 接縫遷移後 `_DECAY_WORKER_CANCEL` 是**行程級單例**，而生產語意是
    「一旦 set 就永不清除」（進程即將結束）。測試之間必須顯式清理，
    否則先前跑過 shutdown 的測試會讓後續測試全部拿 `False`（實測過的
    連鎖假紅）。**生產程式碼不因此改動。**
    """
    _reset_worker()
    yield
    _reset_worker()


# ═════════════════════════════════════════════════════════════
# 前提：隔離真的生效
# ═════════════════════════════════════════════════════════════


def test_isolation_root_is_disposable_not_production(tmp_path) -> None:
    """🔴 前提：`data_root()` 必須落在**拋棄式根**，**不得**是 repo `data/`。

    若這一條紅，本檔其餘所有「零汙染」宣稱全部不成立。

    🔴 **兩個層級的隔離，兩者都合法**：

      1. 本模組層級：在任何 `src.*` 匯入**之前**先釘住
         `SOUL_OS_DATA_DIR`（見檔頭），保護**匯入期**的 `data_root()` 呼叫。
      2. `tests/conftest.py` 的 autouse `_isolate_soul_os_data_root`：每個
         測試各自把根指向 `tmp_path` 下的新目錄，並 `reset_data_root()`
         清快取。這是**更嚴**的隔離，且同樣是拋棄式的。

    故本斷言鎖的是**真正的性質** ——「落在 tmp 底下、且絕非 repo `data/`」——
    而不是「恰好等於本模組釘的那一個路徑」。後者會與 conftest 的合法隔離
    互相打架（第一版就是這樣寫的，實測紅）。
    """
    import tempfile as _tf

    resolved = data_root().resolve()
    tmp_root = _Path(_tf.gettempdir()).resolve()
    production = (_REPO_ROOT / "data").resolve()

    assert resolved != production, (
        "🔴 資料根竟指向 repo data/ —— 隔離失敗"
    )
    assert resolved.is_relative_to(tmp_root) or resolved.is_relative_to(
        _SETTLEMENT_ROOT.resolve()
    ), (
        f"🔴 資料根不在 tmp 底下，隔離不成立：{resolved}"
    )
    # 正向：本模組釘的根本身必須是 tmp（證明檔頭的「先釘根」確實生效）。
    assert _SETTLEMENT_ROOT.resolve().is_relative_to(tmp_root), (
        f"模組層級的拋棄式根不在 tmp 底下：{_SETTLEMENT_ROOT.resolve()}"
    )
    # 匯入期就已生效的證據：SETTLEMENT_ROOT 底下若有檔案，全部屬於本測試。
    assert str(tmp_path.resolve()).startswith(str(tmp_root)), (
        "pytest 的 tmp_path 不在系統 tmp 底下 —— 前置假設不成立"
    )


# ═════════════════════════════════════════════════════════════
# (a) 旗標 OFF
# ═════════════════════════════════════════════════════════════


class TestFlagOff:
    """旗標 OFF ⇒ 不排 worker、worker 本體回 FLAG_OFF、零寫入。"""

    @pytest.mark.asyncio
    async def test_flag_off_schedules_nothing(self, db_path, monkeypatch) -> None:
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "")
        engine = EmotionEngine(db_path=db_path)
        _seed_overdue(engine, "agent_yua", delta=5.0, due=T0 + DAY)
        monkeypatch.setattr(emotion_mod, "emotion_engine", engine)

        scheduled = 0
        for _ in range(5):
            if emotion_mod.decay_enabled():
                if dw._maybe_schedule_decay_worker():
                    scheduled += 1
            await asyncio.sleep(0.01)
        assert scheduled == 0, "旗標 OFF 竟排入了 worker"
        assert dw._DECAY_WORKER_TASK is None, "旗標 OFF 竟建立了 worker task"

    @pytest.mark.asyncio
    async def test_flag_off_worker_body_returns_flag_off_and_writes_nothing(
        self, db_path, monkeypatch
    ) -> None:
        """worker **本體**被硬呼叫 ⇒ 仍必須回 FLAG_OFF 且零寫入。"""
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "")
        engine = EmotionEngine(db_path=db_path)
        _seed_overdue(engine, "agent_yua", delta=5.0, due=T0 + DAY)
        monkeypatch.setattr(emotion_mod, "emotion_engine", engine)

        before_ledger = _ledger_count(engine, "agent_yua")
        before_delta = engine.get_delta("agent_yua")

        res = await dw._decay_worker_main()

        assert res["reason"] == "FLAG_OFF", f"OFF 時 worker 竟做了事：{res}"
        assert res["evaluated"] == 0
        assert _ledger_count(engine, "agent_yua") == before_ledger, "OFF 竟寫了 ledger"
        assert engine.get_delta("agent_yua") == pytest.approx(before_delta), (
            "OFF 竟動了 delta"
        )


# ═════════════════════════════════════════════════════════════
# (b) 排程
# ═════════════════════════════════════════════════════════════


class TestScheduling:
    """排程契約：空閒排入、在途略過、同時在途 ≤ 1。"""

    @pytest.mark.asyncio
    async def test_schedules_when_idle(self, db_path, monkeypatch) -> None:
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        engine = EmotionEngine(db_path=db_path)
        _seed_overdue(engine, "agent_yua", delta=5.0, due=T0 + DAY)
        monkeypatch.setattr(emotion_mod, "emotion_engine", engine)

        assert dw._maybe_schedule_decay_worker() is True, "空閒時應排入 worker"
        assert dw._DECAY_WORKER_TASK is not None, "排入後竟無 worker 參照"
        await _drain()

    @pytest.mark.asyncio
    async def test_at_most_one_worker_in_flight(self, monkeypatch) -> None:
        """🔴 併發計數：連續觸發 50 輪，同時在途 worker ≤ 1。"""
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")

        in_flight = 0
        peak = 0
        gate = threading.Event()

        def _slow() -> dict:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            gate.wait(5.0)
            in_flight -= 1
            return {"evaluated": 0, "applied": 0, "reason": "EVALUATED", "results": {}}

        # 🔴 這裡替換的是**執行函式**（讓 worker 佔住），不是評估器 ——
        #    被證明的「同時在途 ≤ 1」是**排程**語意，與結算無關。
        monkeypatch.setattr(dw, "_run_decay_evaluation_sync", _slow)

        scheduled = 0
        for _ in range(50):
            if dw._maybe_schedule_decay_worker():
                scheduled += 1
            await asyncio.sleep(0.005)

        assert scheduled == 1, f"應恰排入 1 個 worker，實得 {scheduled}"
        assert peak <= 1, f"🔴 在途 worker 併發數 {peak} > 1"
        gate.set()
        await _drain()
        assert in_flight == 0, "worker 未收斂"

    @pytest.mark.asyncio
    async def test_shutdown_blocks_further_scheduling(self, monkeypatch) -> None:
        """shutdown 後不得再排入（Q3 的閘門契約）。"""
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        gate = threading.Event()
        started = threading.Event()

        def _block() -> dict:
            started.set()
            gate.wait(5.0)
            return {"evaluated": 0, "applied": 0, "reason": "EVALUATED", "results": {}}

        monkeypatch.setattr(dw, "_run_decay_evaluation_sync", _block)
        try:
            assert dw._maybe_schedule_decay_worker() is True
            for _ in range(200):
                await asyncio.sleep(0.01)
                if started.is_set():
                    break
            assert started.is_set(), "worker 未進入評估函式"

            await dw._shutdown_decay_worker()
            assert dw._maybe_schedule_decay_worker() is False, (
                "🔴 shutdown 後仍排入了第二個 worker"
            )
        finally:
            gate.set()


async def _drain(timeout: float = 60.0) -> None:
    task = getattr(dw, "_DECAY_WORKER_TASK", None)
    if task is None or task.done():
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
        pass


# ═════════════════════════════════════════════════════════════
# (c) 七個角色逐一被評估：due / not-due
# ═════════════════════════════════════════════════════════════


class TestSevenRolesEvaluatedIndividually:
    """🔴 **真**評估器：七個角色逐一被評估，due / not-due 邊界精確。"""

    def test_all_seven_due_are_settled_one_by_one(
        self, db_path, monkeypatch
    ) -> None:
        """七名全部逾期 ⇒ 每名各扣一步、各寫 1 筆 ledger。"""
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        engine = EmotionEngine(db_path=db_path)
        for aid in ELIGIBLE_AGENTS:
            _seed_overdue(engine, aid, delta=5.0, due=T0 + DAY)
        monkeypatch.setattr(emotion_mod, "emotion_engine", engine)

        res = decay_evaluate_eligible_agents(now=T0 + 2 * DAY)

        assert res["evaluated"] == 7, f"應評估 7 名角色：{res}"
        assert res["applied"] == 7, f"7 名皆逾期應全數扣減：{res}"
        assert res["failed"] == 0, f"不應有失敗：{res}"
        for aid in ELIGIBLE_AGENTS:
            assert _ledger_count(engine, aid) == 1, f"{aid} 未寫 ledger"
            assert engine.get_delta(aid) == pytest.approx(5.0 - DECAY_STEP), (
                f"{aid} 未恰扣一步"
            )

    def test_due_boundary_exact(self, db_path, monkeypatch) -> None:
        """🔴 due 邊界：due-1s 不扣、due 當下扣（逐位元邊界）。"""
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        engine = EmotionEngine(db_path=db_path)
        _seed_overdue(engine, "agent_yua", delta=5.0, due=T0 + DAY)
        monkeypatch.setattr(emotion_mod, "emotion_engine", engine)

        # 還差 1 秒 ⇒ 不扣
        res = decay_evaluate_eligible_agents(now=T0 + DAY - 1.0)
        assert res["applied"] == 0, f"未到期竟扣了：{res}"
        assert _ledger_count(engine, "agent_yua") == 0, "未到期竟寫 ledger"
        assert engine.get_delta("agent_yua") == pytest.approx(5.0)

        # 恰到期 ⇒ 扣
        res = decay_evaluate_eligible_agents(now=T0 + DAY)
        assert res["applied"] == 1, f"已到期竟未扣：{res}"
        assert _ledger_count(engine, "agent_yua") == 1
        assert engine.get_delta("agent_yua") == pytest.approx(5.0 - DECAY_STEP)

    def test_not_due_roles_are_left_untouched(self, db_path, monkeypatch) -> None:
        """混合情境：3 名逾期、4 名未逾期 ⇒ 只有前者被扣。"""
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        engine = EmotionEngine(db_path=db_path)
        due_agents = ELIGIBLE_AGENTS[:3]
        not_due_agents = ELIGIBLE_AGENTS[3:]
        for aid in due_agents:
            _seed_overdue(engine, aid, delta=5.0, due=T0 + DAY)
        for aid in not_due_agents:
            _seed_overdue(engine, aid, delta=5.0, due=T0 + 10 * DAY)
        monkeypatch.setattr(emotion_mod, "emotion_engine", engine)

        res = decay_evaluate_eligible_agents(now=T0 + 2 * DAY)

        assert res["evaluated"] == 7, f"仍應評估全部 7 名：{res}"
        assert res["applied"] == 3, f"應恰 3 名被扣：{res}"
        for aid in due_agents:
            assert _ledger_count(engine, aid) == 1, f"{aid} 逾期未扣"
        for aid in not_due_agents:
            assert _ledger_count(engine, aid) == 0, f"{aid} 未逾期竟被扣"
            assert engine.get_delta(aid) == pytest.approx(5.0), f"{aid} 的 delta 被動到"


# ═════════════════════════════════════════════════════════════
# (d) ledger 冪等
# ═════════════════════════════════════════════════════════════


class TestLedgerIdempotency:
    """同一輪評估重複觸發 ⇒ 不得重複扣。"""

    def test_second_round_does_not_double_decay(self, db_path, monkeypatch) -> None:
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        engine = EmotionEngine(db_path=db_path)
        _seed_overdue(engine, "agent_yua", delta=5.0, due=T0 + DAY)
        monkeypatch.setattr(emotion_mod, "emotion_engine", engine)

        first = decay_evaluate_eligible_agents(now=T0 + DAY)
        assert first["applied"] == 1, f"首輪應扣：{first}"
        assert _ledger_count(engine, "agent_yua") == 1
        after_first = engine.get_delta("agent_yua")

        second = decay_evaluate_eligible_agents(now=T0 + DAY + 1)
        assert second["applied"] == 0, f"第二輪不應再扣：{second}"
        assert _ledger_count(engine, "agent_yua") == 1, "第二輪又寫了 ledger"
        assert engine.get_delta("agent_yua") == pytest.approx(after_first), (
            "第二輪又扣了一次"
        )

    @pytest.mark.asyncio
    async def test_worker_path_is_idempotent_across_rounds(
        self, db_path, monkeypatch
    ) -> None:
        """🔴 **經過真實 worker 路徑**跑兩輪 ⇒ 仍只扣一次（端到端冪等）。"""
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        engine = EmotionEngine(db_path=db_path)
        _seed_overdue(engine, "agent_yua", delta=5.0, due=T0 + DAY)
        monkeypatch.setattr(emotion_mod, "emotion_engine", engine)

        assert dw._maybe_schedule_decay_worker() is True
        await _drain()
        assert _ledger_count(engine, "agent_yua") == 1, "首輪 worker 未結算"

        assert dw._maybe_schedule_decay_worker() is True
        await _drain()
        assert _ledger_count(engine, "agent_yua") == 1, (
            "第二輪 worker 竟重複結算（冪等被破壞）"
        )
        assert engine.get_delta("agent_yua") == pytest.approx(5.0 - DECAY_STEP)


# ═════════════════════════════════════════════════════════════
# (e) 每角色邊界取消
# ═════════════════════════════════════════════════════════════


class TestPerRoleBoundaryCancel:
    """🔴 取消在**角色與角色之間**生效：首名在途時 set ⇒ 其餘六名完全不被評估。"""

    def test_cancel_during_first_agent_stops_remaining_six(
        self, db_path, monkeypatch
    ) -> None:
        """以真評估器 + 真引擎，只控制「何時 set 取消旗標」。

        評估順序由 `sorted(INTIMACY_DECAY_ELIGIBLE_AGENTS)` 決定 ⇒ 首名
        `agent_anna`。受控交錯（不用 sleep，全靠 `threading.Event`）：

          1. 七名全鋪成「已逾期且 delta 非零」，記下 baseline。
          2. `try_apply_decay` 包成 wrapper：首名先 `first_entered.set()`
             再 `release_first.wait()` 卡住；其餘角色走真實實作。
          3. `decay_evaluate_eligible_agents(should_stop=...)` 在背景執行緒跑。
          4. 主執行緒等到 `first_entered` ⇒ set 取消旗標 ⇒ 放行首名。
          5. 首名結束 ⇒ 迴圈頂端看到旗標 ⇒ `break`，其餘六名連
             `try_apply_decay` 都不會被呼叫。

        **主斷言**＝其餘六名零寫入且不在 `results` 內（首名可能已扣 —— 這是
        既有語意，**不**斷言它沒扣）。
        """
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")

        engine = EmotionEngine(db_path=db_path)
        engine.ensure_decay_schema()

        order = sorted(ELIGIBLE_AGENTS)
        first_agent, remaining = order[0], order[1:]
        assert first_agent == "agent_anna", (
            f"評估順序的假設已變動（首名 {first_agent!r}）—— 本測試需重新校準"
        )
        assert len(remaining) == 6, f"其餘角色數非預期：{remaining}"

        base_delta = 5.0
        for aid in order:
            _seed_overdue(engine, aid, delta=base_delta, due=T0 + DAY)
        monkeypatch.setattr(emotion_mod, "emotion_engine", engine)

        before_ledger = {aid: _ledger_count(engine, aid) for aid in order}
        before_delta = {aid: engine.get_delta(aid) for aid in order}
        assert all(v == 0 for v in before_ledger.values())

        first_entered = threading.Event()
        release_first = threading.Event()
        cancel_flag = threading.Event()

        real_try_apply = engine.try_apply_decay
        call_log: list = []

        def _wrapped_try_apply(agent_id, now=None):
            call_log.append(agent_id)
            if agent_id == first_agent and len(call_log) == 1:
                first_entered.set()
                release_first.wait(10.0)
            return real_try_apply(agent_id, now=now)

        monkeypatch.setattr(engine, "try_apply_decay", _wrapped_try_apply)

        result_box: dict = {}

        def _runner() -> None:
            try:
                result_box["res"] = decay_evaluate_eligible_agents(
                    now=T0 + 2 * DAY, should_stop=cancel_flag.is_set
                )
            except Exception as e:  # noqa: BLE001
                result_box["err"] = e

        t = threading.Thread(target=_runner, name="settlement-cancel-probe")
        t.start()

        assert first_entered.wait(10.0), "首名角色未進入 try_apply_decay（前提不成立）"
        cancel_flag.set()          # 取消意圖：下一角色邊界生效
        release_first.set()        # 放行首名
        t.join(timeout=30.0)
        assert not t.is_alive(), "評估執行緒未收斂"
        assert "err" not in result_box, f"評估拋錯：{result_box.get('err')}"

        res = result_box["res"]

        # 🔴 主要斷言 1：其餘六名**完全沒進 results**
        for aid in remaining:
            assert aid not in res["results"], (
                f"🔴 取消後 {aid} 竟出現在 results（角色邊界閘門失效）"
            )
        # 🔴 主要斷言 2：其餘六名零寫入（ledger 與 delta 逐項未變）
        for aid in remaining:
            assert _ledger_count(engine, aid) == before_ledger[aid], (
                f"🔴 取消後 {aid} 竟寫了 ledger"
            )
            assert engine.get_delta(aid) == pytest.approx(before_delta[aid]), (
                f"🔴 取消後 {aid} 的 delta 被動到"
            )
        # 🔴 主要斷言 3：其餘六名連 try_apply_decay 都沒被呼叫
        assert call_log == [first_agent], (
            f"🔴 try_apply_decay 被呼叫的角色非預期：{call_log}"
        )
        # 如實：首名**可能**已扣（既有語意，不在本函式可修正範圍）
        assert first_agent in res["results"], "首名應已開始（前提不成立）"

    def test_cancel_before_any_agent_yields_cancelled(
        self, db_path, monkeypatch
    ) -> None:
        """一個角色都還沒評估就取消 ⇒ `reason == "CANCELLED"`、零寫入。"""
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        engine = EmotionEngine(db_path=db_path)
        for aid in ELIGIBLE_AGENTS:
            _seed_overdue(engine, aid, delta=5.0, due=T0 + DAY)
        monkeypatch.setattr(emotion_mod, "emotion_engine", engine)

        res = decay_evaluate_eligible_agents(
            now=T0 + 2 * DAY, should_stop=lambda: True
        )
        assert res["reason"] == "CANCELLED", f"零角色就取消應回 CANCELLED：{res}"
        assert res["evaluated"] == 0 and res["applied"] == 0
        assert res["results"] == {}
        for aid in ELIGIBLE_AGENTS:
            assert _ledger_count(engine, aid) == 0, f"{aid} 竟被寫入"

    def test_cancel_flag_reaches_sync_runner_through_the_seam(
        self, db_path, monkeypatch
    ) -> None:
        """🔴 端到端：**旗標 set 在接縫模組上** ⇒ 同步評估器回 CANCELLED 且不碰 DB。

        這是「取消意圖確實可達同步函式」的最強可證訊號 —— 被測的是接縫
        那段程式碼本身（不是代理訊號），且以**真**引擎確認它沒被碰。
        """
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "1")
        engine = EmotionEngine(db_path=db_path)
        _seed_overdue(engine, "agent_yua", delta=5.0, due=T0 + DAY)
        monkeypatch.setattr(emotion_mod, "emotion_engine", engine)

        dw._DECAY_WORKER_CANCEL.set()
        res = dw._run_decay_evaluation_sync()

        assert res["reason"] == "CANCELLED", f"旗標已 set 竟未回 CANCELLED：{res}"
        assert res["evaluated"] == 0 and res["applied"] == 0
        assert _ledger_count(engine, "agent_yua") == 0, "🔴 已取消竟仍結算"
        assert engine.get_delta("agent_yua") == pytest.approx(5.0), (
            "🔴 已取消竟仍扣了 delta"
        )


# ═════════════════════════════════════════════════════════════
# (f) 反空洞：stub 失效必須轉紅
# ═════════════════════════════════════════════════════════════
#
# 🔴 本檔的 stub 全部 re-target 到 `src.agent.decay_worker`。下列測試獨立
#    驗證「這些 re-target 真的有效」：弄壞/解綁 stub ⇒ 斷言必須翻面。


class TestAntiSilentVacuity:
    """🔴 證明本檔的 stub 不是綁在沒人讀的名字上。"""

    @pytest.mark.asyncio
    async def test_retargeted_sync_stub_is_read(self, monkeypatch) -> None:
        """把 stub 設成哨兵 ⇒ 經真實 worker 路徑必須拿到哨兵值。"""
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "")
        sentinel = {
            "evaluated": 0,
            "applied": 0,
            "reason": "STUB_WAS_READ",
            "results": {},
        }
        monkeypatch.setattr(dw, "_run_decay_evaluation_sync", lambda: sentinel)
        res = await dw._decay_worker_main()
        assert res["reason"] == "STUB_WAS_READ", (
            "🔴 stub 未被讀取 —— 測試會空洞地通過"
        )

    @pytest.mark.asyncio
    async def test_unbinding_stub_restores_real_behaviour(self, monkeypatch) -> None:
        """🔴 stub invalidation turns it red：解綁後哨兵必須消失（回到真實實作）。"""
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "")
        sentinel = {
            "evaluated": 0,
            "applied": 0,
            "reason": "STUB_WAS_READ",
            "results": {},
        }
        monkeypatch.setattr(dw, "_run_decay_evaluation_sync", lambda: sentinel)
        assert (await dw._decay_worker_main())["reason"] == "STUB_WAS_READ"

        monkeypatch.undo()
        monkeypatch.setenv("INTIMACY_DECAY_ENABLED", "")
        res = await dw._decay_worker_main()
        assert res["reason"] != "STUB_WAS_READ", (
            "🔴 解綁 stub 後哨兵仍在 —— 該名字不是行為來源"
        )
        assert res["reason"] == "FLAG_OFF", f"解綁後應回真實實作：{res}"

    @pytest.mark.asyncio
    async def test_task_global_stub_is_read(self, monkeypatch) -> None:
        """🔴 `_DECAY_WORKER_TASK` 必須是排入邏輯**真正讀取**的那一個。"""
        class _FakeTask:
            def done(self) -> bool:
                return False

        fake = _FakeTask()
        dw._DECAY_WORKER_TASK = fake
        try:
            assert dw._maybe_schedule_decay_worker() is False, (
                "🔴 權威 `_DECAY_WORKER_TASK` 未被讀取 —— 在途 guard 失效"
            )
            assert dw._DECAY_WORKER_TASK is fake
        finally:
            dw._DECAY_WORKER_TASK = None
