"""SAGE-INTIMACY-1B — 親密度結算遷移與 SAGE 質量乘數實裝。

範圍：
  T1-T3  質量乘數 + base_step → **真實路徑驅動**（真建 MemoryMiddleware →
         `_on_agent_speak(AGENT_SPEAK)` → 捕獲 `create_managed_task(_commit_async())`
         → await → 攔截 `emotion_engine.update_delta` 實際收到的 `gain`）
  T4     unknown:* 守衛：真實路徑下不呼叫 emotion_engine.update_delta
  T5     `_get_base_intimacy` 讀 config 正確值 + fail-safe 回 50.0
  T6     consciousness.py 原始碼/AST 斷言（intimacy_delta == 0.0；mood_delta 仍在）

FUP 補強 (SAGE-INTIMACY-1B-FUP, 2026-09-25)：
  前一版的 T1-T4 以 `exec()` 執行從 middleware 抽出的原始碼片段，且
  `base_step` 是在**測試檔內**自行計算（`0.5 * q_mult`）⇒ 生產碼的
  `base_step = 0.5 * quality_multiplier` 被突變成 `= 0.5` 時測試**不會變紅**
  （假覆蓋）。本版改為真實路徑驅動，斷言**實際傳入 `update_delta` 的 `gain`**
  （`gain` 才是輸出；`base_step` 只是內部變數）。

  gain 推導：`eff = _get_base_intimacy(agent) + emotion_engine.get_delta(agent)`
             `gain = max(0.01, base_step * (1 - (eff/100)**2))`
  為求**精確值**斷言，本檔把 base 固定為 50.0 且 delta 固定為 0.0
  ⇒ `eff = 50.0`，`damping = 0.75`，`gain = 0.75 * base_step`：
    T1 fact=0, tagged=0  ⇒ base_step=0.5  ⇒ gain=0.375
    T2 fact=3, tagged=0  ⇒ base_step=0.75 ⇒ gain=0.5625
    T3 fact=1, tagged=2  ⇒ base_step=1.0  ⇒ gain=0.75

隔離：全部 tmp_path + SOUL_OS_DATA_DIR，零 LLM / 零網路 / 零真實 DB 寫入。
"""
from __future__ import annotations

import ast
import asyncio
import os
from pathlib import Path

import pytest

sys_path_root = Path(__file__).resolve().parent.parent
import sys  # noqa: E402

sys.path.insert(0, str(sys_path_root))

from src.eventbus.bus import SoulEventBus  # noqa: E402
from src.eventbus.schema import EventType, SoulEvent  # noqa: E402
from src.paths import reset_data_root  # noqa: E402

import src.agent.emotion as emotion_mod  # noqa: E402
import src.memory.middleware as mw_mod  # noqa: E402

REPO_ROOT = sys_path_root
MIDDLEWARE = REPO_ROOT / "src" / "memory" / "middleware.py"
CONSCIOUSNESS = REPO_ROOT / "src" / "agent" / "consciousness.py"

# 測試用固定基準：讓 gain 可精確斷言（見 module docstring 的推導）
FIXED_BASE_INTIMACY = 50.0
FIXED_DELTA = 0.0
DAMPING_AT_50 = 1.0 - (FIXED_BASE_INTIMACY / 100.0) ** 2  # == 0.75


# ───────────────────────────────────────────────────────────
# 共用：真實路徑驅動 harness
# ───────────────────────────────────────────────────────────

class _StubProvider:
    """受控 provider：post_reply_commit 回傳指定三態 dict，零 LLM。"""

    def __init__(self, result: dict):
        self.result = result
        self.calls: list = []

    async def post_reply_commit(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


class _CapturedGain:
    """攔截 emotion_engine.update_delta 的實際呼叫。"""

    def __init__(self):
        self.calls: list = []

    def __call__(self, agent_id, gain):
        self.calls.append((agent_id, gain))
        return gain

    @property
    def gains(self) -> list:
        return [g for _, g in self.calls]


def _isolate_data_root(tmp_path: Path) -> None:
    """把 data_root() 指向 tmp，避免碰生產 data/**。"""
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()


def _restore_data_root() -> None:
    os.environ.pop("SOUL_OS_DATA_DIR", None)
    reset_data_root()


@pytest.fixture
def real_path(tmp_path, monkeypatch):
    """建立**真實 MemoryMiddleware** 並把結算路徑驅動起來。

    回傳 (run, captured, stub_provider)：
      run(commit_result) -> list[(agent_id, gain)] 實際傳入 update_delta 的引數
    """
    _isolate_data_root(tmp_path)

    # monkeypatch src.agent.emotion.emotion_engine 的兩個方法。
    # 注意：middleware 在結算區塊「函式內」`from src.agent.emotion import
    # emotion_engine` ⇒ 取得的是同一個單例物件，patch 其屬性即可命中。
    monkeypatch.setattr(
        emotion_mod.emotion_engine, "get_delta", lambda aid: FIXED_DELTA
    )
    captured = _CapturedGain()
    monkeypatch.setattr(emotion_mod.emotion_engine, "update_delta", captured)

    bus = SoulEventBus()
    mw = mw_mod.MemoryMiddleware(bus=bus, data_dir=str(tmp_path / "memory"))

    # 固定 base intimacy，讓 gain 可精確斷言（真實 _get_base_intimacy 由 T5 覆蓋）
    monkeypatch.setattr(
        mw, "_get_base_intimacy", lambda aid: FIXED_BASE_INTIMACY, raising=False
    )

    holder: list = []
    real_create = mw_mod.create_managed_task

    def _capturing_create(coro, **kwargs):
        task = real_create(coro, **kwargs)
        holder.append(task)
        return task

    monkeypatch.setattr(mw_mod, "create_managed_task", _capturing_create)

    stub_holder: list = []

    # Phase 4 寫入節流（同 agent 5s 內只寫一次）會讓同一 fixture 內的連續 run
    # 只有第一次真的走到 commit ⇒ 關掉節流，讓每個 run 都是獨立的真實驗證。
    monkeypatch.setattr(mw, "COMMIT_COOLDOWN_SECS", 0.0)
    _seq = {"n": 0}

    def run(commit_result, agent_id="agent_akane", text="測試回覆",
            target_user_id="bryan"):
        stub = _StubProvider(commit_result)
        stub_holder.append(stub)
        # 讓 _on_agent_speak 內 `provider = self._get_provider(agent_id)` 拿到 stub，
        # 完全不碰真實 SAGELiteProvider / graph.sqlite / LLM。
        monkeypatch.setattr(mw, "_get_provider", lambda aid: stub, raising=False)
        mw._last_commit.clear()
        holder.clear()
        # 只回傳「本次 run」新增的結算呼叫（captured 是跨 run 累積的）
        mark = len(captured.calls)
        # 每個 run 用唯一 session_id + target_user_id：
        #   - 避開 _pending_user_text 的 session 級殘留（同 session 只配對一次）
        #   - 讓 source_pair 唯一，_capturing 的 relationships manager 不會累積同名配對
        _seq["n"] += 1
        session_id = f"s_intimacy_1b_{_seq['n']}"
        target_user_id = f"{target_user_id}_{_seq['n']}"

        event = SoulEvent(
            event_type=EventType.AGENT_SPEAK,
            source=agent_id,
            payload={
                "agent_id": agent_id,
                "text": text,
                "target_user_id": target_user_id,
            },
            session_id=session_id,
        )

        async def _drive():
            await mw._on_agent_speak(event)
            assert holder, (
                "AGENT_SPEAK 未產生受管任務 —— 真實路徑沒有被驅動（"
                "可能是節流或提前 return）"
            )
            for task in holder:
                await task

        asyncio.run(_drive())
        return list(captured.calls[mark:])

    try:
        yield run, captured, stub_holder
    finally:
        _restore_data_root()


# ───────────────────────────────────────────────────────────
# T1-T3：質量乘數對照表（真實路徑，斷言 gain 實際值）
# ───────────────────────────────────────────────────────────

class TestT1T3_QualityMultiplierRealPath:
    def test_t1_no_sediment(self, real_path):
        """fact=0, tagged=0 → q_mult=1.0, base_step=0.5 → gain=0.375"""
        run, _, _ = real_path
        calls = run({"fact_count": 0, "tagged_count": 0})
        assert len(calls) == 1, f"應恰好結算一次，實得 {calls}"
        agent_id, gain = calls[0]
        assert agent_id == "agent_akane"
        assert gain == pytest.approx(0.75 * 0.5), (
            f"T1 gain 應為 base_step(0.5) * damping(0.75) = 0.375，實得 {gain}"
        )

    def test_t2_facts_only(self, real_path):
        """fact=3, tagged=0 → q_mult=1.5, base_step=0.75 → gain=0.5625"""
        run, _, _ = real_path
        calls = run({"fact_count": 3, "tagged_count": 0})
        assert len(calls) == 1, f"應恰好結算一次，實得 {calls}"
        _, gain = calls[0]
        assert gain == pytest.approx(0.75 * 0.75), (
            f"T2 gain 應為 base_step(0.75) * damping(0.75) = 0.5625，實得 {gain}"
        )

    def test_t3_tagged_wins(self, real_path):
        """fact=1, tagged=2 → q_mult=2.0, base_step=1.0 → gain=0.75（tagged 優先）"""
        run, _, _ = real_path
        calls = run({"fact_count": 1, "tagged_count": 2})
        assert len(calls) == 1, f"應恰好結算一次，實得 {calls}"
        _, gain = calls[0]
        assert gain == pytest.approx(0.75 * 1.0), (
            f"T3 gain 應為 base_step(1.0) * damping(0.75) = 0.75，實得 {gain}"
        )

    def test_t3b_monotonic_ordering(self, real_path):
        """對照組：三態 gain 必須嚴格遞增（證明三條分支真的不同）。"""
        run, _, _ = real_path
        g0 = run({"fact_count": 0, "tagged_count": 0})[0][1]
        g1 = run({"fact_count": 3, "tagged_count": 0})[0][1]
        g2 = run({"fact_count": 1, "tagged_count": 2})[0][1]
        assert g0 < g1 < g2, f"gain 必須隨質量遞增，實得 {g0} / {g1} / {g2}"

    def test_t1_empty_dict_treated_as_zero(self, real_path):
        """commit_result 為空 dict → 走 else 分支（q_mult=1.0）。"""
        run, _, _ = real_path
        calls = run({})
        assert len(calls) == 1, f"應恰好結算一次，實得 {calls}"
        assert calls[0][1] == pytest.approx(0.375)


# ───────────────────────────────────────────────────────────
# T4：unknown:* 守衛 — 真實路徑不得呼叫 update_delta
# ───────────────────────────────────────────────────────────

class TestT4_UnknownGuardRealPath:
    def test_t4a_guard_source_present(self):
        """守衛必須存在且只包 intimacy 區塊（不得 return 整個 _commit_async）。"""
        src = MIDDLEWARE.read_text(encoding="utf-8")
        assert 'agent_id.startswith("agent_")' in src
        assert "skip intimacy for non-agent id" in src

    def test_t4b_unknown_id_does_not_call_update_delta(self, real_path):
        """unknown:telegram → 真實路徑下 update_delta 一次都不得被呼叫。"""
        run, _, _ = real_path
        calls = run(
            {"fact_count": 5, "tagged_count": 5},
            agent_id="unknown:telegram",
        )
        assert calls == [], f"unknown id 不得結算，卻呼叫了 update_delta: {calls}"

    def test_t4c_real_agent_id_does_call_update_delta(self, real_path):
        """對照組：agent_* → 必須真的呼叫 update_delta（證明 T4b 非同義反覆）。"""
        run, _, _ = real_path
        calls = run({"fact_count": 1, "tagged_count": 1})
        assert len(calls) == 1, f"agent_* 應結算一次，實得 {len(calls)}: {calls}"
        assert calls[0][0] == "agent_akane"

    def test_t4d_unknown_id_still_commits_memory(self, real_path, monkeypatch):
        """守衛不得中斷 post_reply_commit（只跳過 intimacy 區塊）。"""
        run, _, stub_holder = real_path
        run({"fact_count": 0, "tagged_count": 0}, agent_id="unknown:web")
        assert stub_holder, "provider 未被呼叫"
        assert len(stub_holder[-1].calls) == 1, (
            "unknown id 仍必須完成 post_reply_commit（守衛只跳過 intimacy）"
        )


# ───────────────────────────────────────────────────────────
# T5：_get_base_intimacy 讀 config + fail-safe
# ───────────────────────────────────────────────────────────

class TestT5_BaseIntimacyReader:
    def _make_mw(self):
        from src.memory.middleware import MemoryMiddleware
        return MemoryMiddleware.__new__(MemoryMiddleware)

    def test_t5a_reads_config_values(self):
        """實讀 configs/default.yaml：agent_yua=80, agent_ram=40, agent_aoi=46。"""
        mw = self._make_mw()
        assert mw._get_base_intimacy("agent_yua") == 80.0
        assert mw._get_base_intimacy("agent_ram") == 40.0
        assert mw._get_base_intimacy("agent_aoi") == 46.0

    def test_t5b_unknown_agent_falls_back_to_50(self):
        mw = self._make_mw()
        assert mw._get_base_intimacy("agent_does_not_exist") == 50.0

    def test_t5c_failsafe_on_config_error(self, monkeypatch):
        """config 載入爆掉 → 回 50.0，且不得拋出。"""
        import configs.loader as loader_mod

        def _boom(*a, **k):
            raise RuntimeError("config boom")

        monkeypatch.setattr(loader_mod, "load_config", _boom)
        mw = self._make_mw()
        assert mw._get_base_intimacy("agent_yua") == 50.0


# ───────────────────────────────────────────────────────────
# T6：consciousness.py 原始碼/AST 斷言
# ───────────────────────────────────────────────────────────

class TestT6_ConsciousnessSource:
    def _user_message_update_call(self) -> ast.Call:
        """AST 找出 USER_MESSAGE 路徑的 emotion_engine.update(...) 呼叫節點。

        ⚠️ 檔內有兩個 emotion_engine.update 呼叫點（L200 USER_MESSAGE、
        L325 silence/decay）。必須鎖定「帶 intimacy_delta」或緊接 L195-196
        註解的那一個，否則會誤取 decay 站點。
        """
        tree = ast.parse(CONSCIOUSNESS.read_text(encoding="utf-8"))
        candidates = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if (isinstance(fn, ast.Attribute) and fn.attr == "update"
                    and isinstance(fn.value, ast.Name)
                    and fn.value.id == "emotion_engine"):
                candidates.append(node)
        assert candidates, "找不到 emotion_engine.update(...) 呼叫"

        # USER_MESSAGE 站點＝唯一「帶 intimacy_delta 關鍵字」的那一個。
        for node in candidates:
            if any(k.arg == "intimacy_delta" for k in node.keywords):
                return node
        raise AssertionError(
            "找不到 USER_MESSAGE 路徑的 emotion_engine.update(...)（缺 intimacy_delta）"
        )

    def test_t6a_intimacy_delta_is_zero(self):
        call = self._user_message_update_call()
        kw = {k.arg: k.value for k in call.keywords if k.arg}
        assert "intimacy_delta" in kw, "必須顯式寫入 intimacy_delta=0.0"
        value = kw["intimacy_delta"]
        assert isinstance(value, ast.Constant) and value.value == 0.0, (
            f"intimacy_delta 必須為 0.0（舊語義 0.3 已停寫），實得 {ast.dump(value)}"
        )

    def test_t6b_mood_delta_preserved(self):
        call = self._user_message_update_call()
        kw = {k.arg: k.value for k in call.keywords if k.arg}
        assert "mood_delta" in kw, "心情更新不得斷（mood_delta 必須保留）"

    def test_t6c_old_dynamic_block_removed(self):
        """L207-218 舊結算區塊已移除 ⇒ 該檔不得再呼叫 update_delta。"""
        src = CONSCIOUSNESS.read_text(encoding="utf-8")
        assert "emotion_engine.update_delta" not in src

    def test_t6d_unused_imports_removed(self):
        src = CONSCIOUSNESS.read_text(encoding="utf-8")
        assert "compute_effective_intimacy" not in src
        assert "calculate_intimacy_gain" not in src
        # 保留的兩個名字必須仍在
        assert "emotion_engine" in src
        assert "SENSITIVITY" in src

    def test_t6e_stale_comment_updated(self):
        """L196 舊治理註解（intimacy_delta=0.3 為舊語義）措辭須同步更新。"""
        src = CONSCIOUSNESS.read_text(encoding="utf-8")
        assert "intimacy_delta=0.3 為舊語義" not in src, "殘留過時治理文字"
