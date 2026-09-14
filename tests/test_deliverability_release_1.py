"""
tests/test_deliverability_release_1.py — DELIVERABILITY-RELEASE-1（票 5：交付鏈可送達，最小放行集 R2）

事實權威：`docs/TRANSMIT-DELIVERABILITY-MAP.md`（`7cb552d`）＋ 票 3 本輪已證結構。

本票是 **Owner 已授權的產品語意變更**。Owner 裁定逐字原文：

> 放寬 4h 硬阻斷 ＋ (a) 先唯讀量測 longing 曲線 (b) 單調遞增則加每角色每日上限 1 則
> (c) 不變量 `daily_proactive_cap(silence=3d) == daily_proactive_cap(silence=3h)` 測試釘死
> (d) TA-2 措辭逐字不動、TA-2 狀態不得參與發起判定

對應本檔的四組斷言：

  【Step 1】G5（4h 鎖 A）**不再中斷流程**，只留一行有界 INFO 放行痕跡；
            且在 `last_recv_ts = 16h 前`（舊版必被 G5 擋下的情境）下，
            流程**確實抵達決策層**（`_inner_life_gate_check` / `_decision_check`
            被呼叫，`[SM-3 Decision]` 真的輸出）。
  【Step 2】(a) longing 曲線唯讀量測 ＋ 單調性判定；(b) 每角色每日上限 = 1 則；
            (c) 不變量 `cap(3d) == cap(3h)` 釘死（含 AST 級「上限不看沉默」）。
  【Step 4】(d) TA-2 措辭**逐字**不動 ＋ 發起判定路徑**不讀取** TA-2 狀態。
  【邊界】 0 服務重啟 / 0 `data/**` 生產寫入（全部落在 conftest 的 tmp 無菌室）。
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agent.emotion import compute_longing
from src.paths import data_root
from src.soul.proactive_policy import (
    PROACTIVE_DM_DAILY_CAP_PER_AGENT,
    daily_proactive_cap,
)
from src.soul.scheduler import LONGING_THRESHOLD, SoulScheduler
from src.timezone_utils import now_local

ROOT = Path(__file__).resolve().parent.parent
SCHEDULER_SRC_PATH = ROOT / "src" / "soul" / "scheduler.py"
SCHEDULER_LOGGER = "soul_os.soul.scheduler"

#: 有界落盤上限（與 scheduler._LOG_LINE_LIMIT 一致）
BOUNDED_LIMIT = 200

#: 白名單唯一角色（`scripts/run_server.py:1071` `proactive_agents=["agent_ruka"]`）
WHITELIST_AGENT = "agent_ruka"
#: `configs/default.yaml:18` agent_ruka intimacy_level
RUKA_INTIMACY = 60

#: 沉默時長取樣點（1h / 3h / 6h / 12h / 1d / 2d / 3d）
SILENCE_SAMPLES_MINUTES = [60, 180, 360, 720, 1440, 2880, 4320]


# ────────────────────────────────────────────────────────────────────
# 共用：決定性 scheduler ＋ 隔離資料根
# ────────────────────────────────────────────────────────────────────

def _write_bryan_last_seen(hours_ago: float) -> None:
    """在**隔離**資料根寫 bryan_last_seen.json（conftest 的 tmp 無菌室，非生產）。"""
    state_dir = data_root() / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_recv_ts": (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(),
        "last_recv_agent": "agent_rem",
        "last_recv_preview": "test",
    }
    (state_dir / "bryan_last_seen.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _deterministic_scheduler() -> SoulScheduler:
    """決定性 scheduler：關閉 G3 冷卻／G4 靜音／想念門檻，只留待測閘門。"""
    sched = SoulScheduler(
        bus=MagicMock(),
        proactive_agents=[WHITELIST_AGENT],
        proactive_dm_cooldown_seconds=0,
        quiet_hours_start=0,
        quiet_hours_end=0,
    )
    sched._all_agents = [WHITELIST_AGENT]
    sched._is_quiet_hours = lambda now: False  # type: ignore[method-assign]
    sched._get_agent_longing = lambda agent_id: 0.99  # type: ignore[method-assign]
    return sched


def _records(caplog) -> List[str]:
    return [r.getMessage() for r in caplog.records]


def _capturing_bus():
    """回傳 (bus, published)：bus.publish 為 async 且會記錄事件。"""
    bus = MagicMock()
    published: List[Any] = []

    async def _publish(event):
        published.append(event)

    bus.publish = _publish
    return bus, published


def _stmt_line(fn: ast.AST, predicate) -> int:
    """在 fn 內找出第一個滿足 predicate 的節點，回傳其行號。"""
    for node in ast.walk(fn):
        if predicate(node):
            return node.lineno
    raise AssertionError("找不到符合條件的節點")


def _is_constant_containing(token: str):
    def _p(node: ast.AST) -> bool:
        return isinstance(node, ast.Constant) and isinstance(node.value, str) and token in node.value
    return _p


def _is_await_call_attr(name: str):
    def _p(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Await)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == name
        )
    return _p


def _is_call_attr(name: str):
    def _p(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == name
        )
    return _p


def _is_if_containing(token: str, src: str):
    def _p(node: ast.AST) -> bool:
        return isinstance(node, ast.If) and token in (ast.get_source_segment(src, node) or "")
    return _p


def _assert_all_bounded(messages: List[str]) -> None:
    for m in messages:
        assert len(m) <= BOUNDED_LIMIT, f"log 行超過 {BOUNDED_LIMIT} 字元: {len(m)} :: {m}"


def _fire_proactive_dm_ast() -> ast.AsyncFunctionDef:
    src = SCHEDULER_SRC_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_fire_proactive_dm":
            return node
    raise AssertionError("scheduler.py 找不到 _fire_proactive_dm")


# ────────────────────────────────────────────────────────────────────
# 【Step 1】G5 不再中斷流程
# ────────────────────────────────────────────────────────────────────

class TestStep1_G5NoLongerBlocks:
    """G5 由「硬阻斷」改為「量測 + 有界 INFO 放行痕跡」。"""

    def test_s1a_g5_if_block_contains_no_return(self):
        """AST 釘死：G5 的 `if` 區塊內**不得有任何 return**（不再 early-return）。"""
        fn = _fire_proactive_dm_ast()
        src = SCHEDULER_SRC_PATH.read_text(encoding="utf-8")
        hits = []
        for node in ast.walk(fn):
            if isinstance(node, ast.If):
                seg = ast.get_source_segment(src, node) or ""
                if "PROACTIVE_DM_BRYAN_INACTIVE_HOURS" in seg:
                    hits.append((node, seg))
        assert len(hits) == 1, f"G5 的 if 區塊應恰好 1 個, 實際 {len(hits)}"
        node, seg = hits[0]
        assert not any(isinstance(n, ast.Return) for n in ast.walk(node)), (
            "G5 不得再中斷流程（區塊內出現 return）"
        )
        # 放行痕跡：gate=G5 且語意為「放行／不再阻斷」
        assert "gate=G5" in seg
        assert "放行" in seg or "不再阻斷" in seg
        # 舊的「skip」語意必須從 G5 區塊消失（G3／G4 仍保留該字樣，故此處只看本區塊）
        assert "skip (不 publish AGENCY_TRIGGER、不觸發 LLM)" not in seg

    def test_s1b_g5_passthrough_log_is_bounded_and_precedes_publish(self, caplog):
        """16h 情境：G5 留痕、單行 <= 200，且痕跡在 publish 之前。"""
        _write_bryan_last_seen(hours_ago=16)
        sched = _deterministic_scheduler()
        calls: List[str] = []

        async def _stub_publish(agent_id, trigger_type="proactive_dm", extra=None):
            calls.append(agent_id)

        sched._publish_agency_trigger = _stub_publish  # type: ignore[method-assign]

        with caplog.at_level(logging.INFO, logger=SCHEDULER_LOGGER):
            asyncio.run(sched._fire_proactive_dm())

        msgs = _records(caplog)
        hit = [m for m in msgs if "gate=G5" in m]
        assert len(hit) == 1, f"G5 必須留一行放行痕跡: {msgs}"
        assert "16.0h" in hit[0], f"痕跡必須記錄觀察到的 inactivity 小時數: {hit[0]}"
        assert not any("不可送達" in m and "G5" in m for m in msgs), (
            "G5 不得再產生『不可送達 → skip』語意"
        )
        _assert_all_bounded(msgs)
        assert calls == [WHITELIST_AGENT], "16h 情境下仍必須 publish（不再被 G5 擋）"

    def test_s1c_16h_reaches_decision_layer_inner_life_and_decision_called(self):
        """16h 情境：`_inner_life_gate_check` 與 `_decision_check` **都被呼叫到**。

        這兩個函式是 `_publish_agency_trigger` 的內部唯一呼叫點；票 3 已證
        「G5 在 publish 之前」→ 舊版這兩個永遠不會被執行。此處以 spy 記錄，
        並讓**真實的** `_publish_agency_trigger` 跑完（不 stub）。
        """
        _write_bryan_last_seen(hours_ago=16)
        sched = _deterministic_scheduler()
        seen: List[str] = []

        async def _spy_gate(agent_id):
            seen.append(f"gate:{agent_id}")
            return True

        async def _spy_decision(agent_id):
            seen.append(f"decision:{agent_id}")
            return True

        sched._inner_life_gate_check = _spy_gate  # type: ignore[method-assign]
        sched._decision_check = _spy_decision  # type: ignore[method-assign]
        sched._bus, published = _capturing_bus()

        asyncio.run(sched._fire_proactive_dm())

        assert seen == [f"gate:{WHITELIST_AGENT}", f"decision:{WHITELIST_AGENT}"], (
            f"16h 情境必須抵達決策層（G10 → G11）, 實際 {seen}"
        )
        assert len(published) == 1, "決策層放行後必須 publish 一次 AGENCY_TRIGGER"
        assert published[0].payload["trigger_type"] == "proactive_dm"

    def test_s1d_16h_emits_sm3_decision_line_with_real_decision_check(self, caplog):
        """16h 情境 + **真實** `_publish_agency_trigger` / `_decision_check`：
        `[SM-3 Decision]` 真的輸出。

        這是工單驗收條文「或 `[SM-3 Decision]` 會輸出」的直接證明 ——
        隔離資料根無 pending motive，故預期走 F1（无 pending motive → skip publish）。
        """
        _write_bryan_last_seen(hours_ago=16)
        sched = _deterministic_scheduler()
        sched._bus, _published = _capturing_bus()

        with caplog.at_level(logging.INFO):
            asyncio.run(sched._fire_proactive_dm())

        msgs = _records(caplog)
        sm3 = [m for m in msgs if "[SM-3 Decision]" in m]
        assert sm3, f"16h 情境必須真的輸出 [SM-3 Decision]（G5 放行後才可能）: {msgs}"
        assert any("无 pending motive" in m for m in sm3), (
            f"隔離資料根無 pending motive → 預期 F1 分支: {sm3}"
        )

    def test_s1e_g5_constants_and_reader_untouched(self):
        """常數與讀取語意**不得**改動（仍被 router M0.5 / goals 兩處使用）。"""
        from src.io.channels.bryan_state import PROACTIVE_DM_BRYAN_INACTIVE_HOURS

        assert PROACTIVE_DM_BRYAN_INACTIVE_HOURS == 4.0
        sched_src = SCHEDULER_SRC_PATH.read_text(encoding="utf-8")
        # G5 仍讀同一個常數（只把「中斷」拿掉，量測與讀取語意不變）
        assert "from src.io.channels.bryan_state import PROACTIVE_DM_BRYAN_INACTIVE_HOURS" in sched_src
        assert "read_bryan_last_seen" in sched_src
        # router 的 M0.5 鎖 B（死碼）**本票不動**
        router_src = (ROOT / "src" / "io" / "channels" / "router.py").read_text(encoding="utf-8")
        assert "PROACTIVE_DM_BRYAN_INACTIVE_HOURS" in router_src

    def test_s1f_g3_g4_still_block(self, caplog):
        """G3 冷卻窗與 G4 靜音時段**不得**被本票放寬（只放寬 G5）。"""
        sched = _deterministic_scheduler()
        sched._last_proactive_dm_time = now_local()
        sched.proactive_dm_cooldown_seconds = 7200
        with caplog.at_level(logging.INFO, logger=SCHEDULER_LOGGER):
            asyncio.run(sched._fire_proactive_dm())
        assert any("gate=G3" in m for m in _records(caplog)), "G3 仍必須阻擋"

        caplog.clear()
        sched2 = _deterministic_scheduler()
        sched2._is_quiet_hours = lambda now: True  # type: ignore[method-assign]
        with caplog.at_level(logging.INFO, logger=SCHEDULER_LOGGER):
            asyncio.run(sched2._fire_proactive_dm())
        assert any("gate=G4" in m for m in _records(caplog)), "G4 仍必須阻擋"


# ────────────────────────────────────────────────────────────────────
# 【Step 2】(a) longing 曲線量測與單調性
# ────────────────────────────────────────────────────────────────────

class TestStep2a_LongingCurve:
    """(a) 唯讀量測 longing 對沉默時長的曲線，並判定單調性。"""

    def test_s2a1_curve_values_are_the_measured_table(self):
        """釘死量測到的曲線（ruka, intimacy=60）。"""
        expected = {
            60: 0.0250,
            180: 0.0750,
            360: 0.1500,
            720: 0.3000,
            1440: 0.6000,
            2880: 0.6000,
            4320: 0.6000,
        }
        for minutes, want in expected.items():
            got = compute_longing(RUKA_INTIMACY, minutes)
            assert got == pytest.approx(want, abs=1e-9), f"沉默 {minutes}min → {got} != {want}"

    def test_s2a2_curve_is_monotone_non_decreasing(self):
        """單調性判定：**無任何遞減區間**（單調不減）。"""
        values = [compute_longing(RUKA_INTIMACY, m) for m in SILENCE_SAMPLES_MINUTES]
        assert all(values[i] <= values[i + 1] for i in range(len(values) - 1)), values
        # 全譜掃描：0 → 30 天，任兩點不得出現下降
        grid = list(range(0, 30 * 24 * 60 + 1, 30))
        vals = [compute_longing(RUKA_INTIMACY, m) for m in grid]
        assert all(vals[i] <= vals[i + 1] for i in range(len(vals) - 1))
        # 飽和邊界：>= 24h 之後為定值（silence_factor clamp 1.0）
        assert compute_longing(RUKA_INTIMACY, 1440) == compute_longing(RUKA_INTIMACY, 4320)

    def test_s2a3_all_canonical_agents_monotone_and_threshold_crossings(self):
        """全體角色皆單調不減；門檻跨越點與 `scheduler.py:84-85` 註解逐值相符。"""
        ints = {
            "agent_yua": 80,
            "agent_ruka": 60,
            "agent_akane": 50,
            "agent_rem": 45,
            "agent_ram": 40,
            "agent_mahiru": 45,
            "agent_anna": 55,
        }
        for agent, intimacy in ints.items():
            vals = [compute_longing(intimacy, m) for m in SILENCE_SAMPLES_MINUTES]
            assert all(vals[i] <= vals[i + 1] for i in range(len(vals) - 1)), (agent, vals)
        # `scheduler.py:84-85` 註解逐值: Ruka(60) 12h / Yua(80) 9h / Ram(40) 18h
        assert compute_longing(60, 720) == pytest.approx(LONGING_THRESHOLD)
        assert compute_longing(80, 540) == pytest.approx(LONGING_THRESHOLD)
        assert compute_longing(40, 1080) == pytest.approx(LONGING_THRESHOLD)


# ────────────────────────────────────────────────────────────────────
# 【Step 2】(b)(c) 每角色每日上限 ＝ 1 則 ＋ 不變量
# ────────────────────────────────────────────────────────────────────

class TestStep2bc_DailyCap:
    """(b) 單調遞增 → 實作每角色每日上限 1 則；(c) 不變量測試釘死。"""

    def test_s2b1_owner_invariant_cap_3d_equals_cap_3h(self):
        """🔴 Owner 裁定 (c) 逐字不變量。"""
        cap_3h = daily_proactive_cap(silence_minutes=3 * 60)
        cap_3d = daily_proactive_cap(silence_minutes=3 * 24 * 60)
        assert cap_3d == cap_3h, "上限不得隨沉默時長變動（長沉默不可變成騷擾許可證）"
        assert cap_3d == 1
        assert PROACTIVE_DM_DAILY_CAP_PER_AGENT == 1

    def test_s2b2_cap_constant_across_entire_silence_spectrum(self):
        """沉默譜全掃（含跨月）＋ None：上限恆為 1，**不得有任何上升**。"""
        spectrum = [0, 1, 60, 180, 360, 720, 1440, 2880, 4320, 10080, 43200, 525600]
        assert {daily_proactive_cap(m) for m in spectrum} == {1}
        assert daily_proactive_cap(None) == 1

    def test_s2b3_cap_function_does_not_read_silence_argument(self):
        """AST 級釘死：`daily_proactive_cap` 函式體**不得引用** `silence_minutes`。"""
        import src.soul.proactive_policy as policy

        src = inspect.getsource(policy)
        tree = ast.parse(src)
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "daily_proactive_cap"
        )
        names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
        assert "silence_minutes" not in names, (
            "上限不得以沉默時長為輸入 —— 否則 cap(3d) != cap(3h) 的風險回歸"
        )
        # 函式體必須只有一個 return，且回傳常數
        returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
        assert len(returns) == 1
        assert isinstance(returns[0].value, ast.Name)
        assert returns[0].value.id == "PROACTIVE_DM_DAILY_CAP_PER_AGENT"

    def test_s2b4_second_send_same_local_day_is_blocked(self, caplog):
        """行為驗證：同一本地日第二次發起被 G7b 擋下（上限 = 1）。"""
        sched = _deterministic_scheduler()
        published: List[str] = []

        async def _stub_publish(agent_id, trigger_type="proactive_dm", extra=None):
            published.append(agent_id)

        sched._publish_agency_trigger = _stub_publish  # type: ignore[method-assign]
        sched._get_recent_shareable_activity = lambda a: None  # type: ignore[method-assign]

        with caplog.at_level(logging.INFO, logger=SCHEDULER_LOGGER):
            asyncio.run(sched._fire_proactive_dm())
            first_msgs = _records(caplog)
            caplog.clear()
            asyncio.run(sched._fire_proactive_dm())
            second_msgs = _records(caplog)

        assert published == [WHITELIST_AGENT], (
            f"每日上限 1 則 → 同一天只能發起一次, 實際 {published}"
        )
        assert any(
            "proactive_dm 每日配額使用" in m and "gate=G7b" in m and "1/1" in m
            for m in first_msgs
        ), f"第一次發起必須留配額使用痕跡: {first_msgs}"
        assert any(
            "每日上限已達" in m and "gate=G7b" in m for m in second_msgs
        ), f"第二次必須留 G7b 阻擋痕跡: {second_msgs}"
        _assert_all_bounded(first_msgs + second_msgs)

    def test_s2b5_cap_is_per_character_not_global(self, caplog):
        """上限是**每角色**1 則：ruka 用完不影響 yua 的額度。"""
        sched = SoulScheduler(
            bus=MagicMock(),
            proactive_agents=["agent_ruka", "agent_yua"],
            proactive_dm_cooldown_seconds=0,
            quiet_hours_start=0,
            quiet_hours_end=0,
        )
        sched._all_agents = ["agent_ruka", "agent_yua"]
        sched._is_quiet_hours = lambda now: False  # type: ignore[method-assign]
        sched._get_agent_longing = lambda agent_id: 0.99  # type: ignore[method-assign]
        sched._get_recent_shareable_activity = lambda a: None  # type: ignore[method-assign]
        published: List[str] = []

        async def _stub_publish(agent_id, trigger_type="proactive_dm", extra=None):
            published.append(agent_id)

        sched._publish_agency_trigger = _stub_publish  # type: ignore[method-assign]

        sched._proactive_dm_daily[WHITELIST_AGENT] = (
            now_local().date().isoformat(),
            1,
        )
        assert sched._proactive_daily_cap_allows("agent_yua") is True
        assert sched._proactive_daily_cap_allows("agent_ruka") is False

    def test_s2b6_cap_resets_on_new_local_day(self):
        """跨本地日 → 配額重置（日期鍵語意）。"""
        sched = _deterministic_scheduler()
        sched._proactive_dm_daily[WHITELIST_AGENT] = ("1999-01-01", 1)
        assert sched._proactive_daily_cap_allows(WHITELIST_AGENT) is True

    def test_s2b7_g7b_sits_between_longing_and_publish(self):
        """程式碼順序（AST 行號）：G7（longing）→ G7b（每日上限）→ publish。

        以 AST 行號而非字串索引 —— G5 的註解區塊內也出現過
        `await self._publish_agency_trigger(...)` 字樣，字串索引會誤命中。
        """
        src = SCHEDULER_SRC_PATH.read_text(encoding="utf-8")
        fn = _fire_proactive_dm_ast()
        l_longing = _stmt_line(fn, _is_constant_containing("觸發主動傳訊"))
        l_cap = _stmt_line(fn, _is_if_containing("gate=G7b", src))
        l_publish = _stmt_line(fn, _is_await_call_attr("_publish_agency_trigger"))
        assert l_longing < l_cap < l_publish, (
            f"G7b 必須介於想念門檻與 publish 之間: longing={l_longing} "
            f"cap={l_cap} publish={l_publish}"
        )

    def test_s2b8_cap_counts_attempts_fail_safe_after_publish(self):
        """語意釘死：配額記在 publish **之後**（G10/G11 擋下也照樣消耗 → 只會少發）。"""
        fn = _fire_proactive_dm_ast()
        l_publish = _stmt_line(fn, _is_await_call_attr("_publish_agency_trigger"))
        l_record = _stmt_line(fn, _is_call_attr("_proactive_daily_cap_record"))
        assert l_publish < l_record, (
            f"配額必須記在 publish 之後: publish={l_publish} record={l_record}"
        )


# ────────────────────────────────────────────────────────────────────
# 【Step 4】(d) TA-2 邊界：措辭逐字不動 ＋ 判定路徑不讀 TA-2 狀態
# ────────────────────────────────────────────────────────────────────

#: TA-2 三態措辭（**逐字**；任何一個字元改動都會讓本測試紅）
TA2_WORDING_CALM = "一切如常，你們的互動節奏一如往常，這並不代表需要主動聯絡。"
TA2_WORDING_TENSION = (
    "距離上次與 Bryan 對話已有明顯間隔，具有存在感，"
    "這份在意讓你想起過去那些對話，但這絕不代表必須主動聯絡。"
)
TA2_WORDING_RESOLVED = "雖然許久未聯絡，但那份珍惜仍在心中，這並不代表必須主動聯絡。"

#: T1 防線那一行（內嵌於三態措辭，禁止被移除或改寫）
TA2_T1_DEFENSE_LINE = "但這絕不代表必須主動聯絡"

#: 發起判定路徑（G5 之後到 G11 之間的全部函式）
_SEND_PATH_FUNCS = (
    "_fire_proactive_dm",
    "_publish_agency_trigger",
    "_inner_life_gate_check",
    "_decision_check",
    "_proactive_daily_cap_allows",
    "_proactive_daily_cap_record",
    "_get_agent_longing",
)

#: 任何「TA-2 狀態」識別字（一旦出現在發起判定路徑，即構成「沉默 → 發訊」資料流交集）
_TA2_STATE_TOKENS = (
    "temporal_phenomenology",
    "classify_temporal_state",
    "format_temporal_anchor",
    "BondEvidence",
    "_read_bond_evidence",
    "STATE_CALM",
    "STATE_TENSION",
    "STATE_RESOLVED",
    "_RELATION_TIMELINE_BY_STATE",
    "temporal_state",
    "bond_evidence",
)


class TestStep4_TA2Boundary:
    """(d) TA-2 措辭逐字不動；TA-2 狀態不得參與發起判定。"""

    def test_s4a_ta2_wording_is_verbatim(self):
        """三態措辭與狀態值**逐字**不動。"""
        from src.soul import temporal_phenomenology as tp

        assert tp.STATE_CALM == "无感"
        assert tp.STATE_TENSION == "牵挂"
        assert tp.STATE_RESOLVED == "释然"
        assert tp._RELATION_TIMELINE_BY_STATE[tp.STATE_CALM] == TA2_WORDING_CALM
        assert tp._RELATION_TIMELINE_BY_STATE[tp.STATE_TENSION] == TA2_WORDING_TENSION
        assert tp._RELATION_TIMELINE_BY_STATE[tp.STATE_RESOLVED] == TA2_WORDING_RESOLVED

    def test_s4b_t1_defense_line_present_in_every_state(self):
        """T1 防線那一行必須逐字存在於**三態每一態**（不可退讓）。"""
        from src.soul import temporal_phenomenology as tp

        for state, wording in tp._RELATION_TIMELINE_BY_STATE.items():
            assert "必須主動聯絡" in wording or "需要主動聯絡" in wording, (state, wording)
        assert TA2_T1_DEFENSE_LINE in tp._RELATION_TIMELINE_BY_STATE[tp.STATE_TENSION]

    def test_s4c_ta2_anchor_output_verbatim(self, monkeypatch):
        """`format_temporal_anchor()` 三行輸出格式與第三行逐字不動。"""
        from src.soul import temporal_phenomenology as tp

        monkeypatch.setattr(tp, "_read_bond_evidence", lambda agent_id: tp.BondEvidence(True, "P3"))
        now = 1_800_000_000  # 固定時刻 → 決定性
        out = tp.format_temporal_anchor("agent_ruka", now - 2 * 24 * 3600, now)
        lines = out.split("\n")
        assert lines[0] == "[TEMPORAL ANCHOR]"
        assert lines[1].startswith("- 時間座標：")
        assert lines[2].startswith("- 體感經驗：")
        assert lines[3] == f"- 關係時序：{TA2_WORDING_TENSION}"
        assert TA2_T1_DEFENSE_LINE in lines[3]

    def test_s4d_send_path_source_has_no_ta2_symbol(self):
        """靜態斷言：發起判定路徑 7 個函式的原始碼**完全不出現**任何 TA-2 識別字。"""
        for name in _SEND_PATH_FUNCS:
            src = inspect.getsource(getattr(SoulScheduler, name))
            for token in _TA2_STATE_TOKENS:
                assert token not in src, (
                    f"{name} 引用了 TA-2 識別字 {token!r} → "
                    f"構成「沉默 → 發訊」資料流交集（越界）"
                )

    def test_s4e_send_path_ast_has_no_ta2_import(self):
        """AST 斷言：發起判定路徑不得 import TA-2 模組，也不得讀取 TA-2 狀態欄位。"""
        src = SCHEDULER_SRC_PATH.read_text(encoding="utf-8")
        fn = _fire_proactive_dm_ast()
        body_src = ast.get_source_segment(src, fn) or ""
        body_tree = ast.parse(body_src)
        for node in ast.walk(body_tree):
            if isinstance(node, ast.ImportFrom):
                assert "temporal" not in (node.module or ""), node.module
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert "temporal" not in a.name, a.name
            if isinstance(node, ast.Attribute):
                assert node.attr not in _TA2_STATE_TOKENS, node.attr
            if isinstance(node, ast.Subscript):
                seg = ast.get_source_segment(body_src, node) or ""
                for token in _TA2_STATE_TOKENS:
                    assert token not in seg, seg

    def test_s4f_ta2_bomb_does_not_change_initiation(self, caplog):
        """動態斷言：把 TA-2 三個入口全部炸掉，16h 情境的發起流程**完全不變**。

        若 TA-2 狀態真的參與發起判定，炸掉它必然改變行為；實測行為不變
        （仍抵達決策層、仍 publish 一次）→ 證明發起判定不依賴 TA-2 狀態。
        """
        from src.soul import temporal_phenomenology as tp

        def _boom(*args, **kwargs):
            raise RuntimeError("TA-2 bomb")

        _write_bryan_last_seen(hours_ago=16)
        sched = _deterministic_scheduler()
        calls: List[str] = []

        async def _spy_gate(agent_id):
            calls.append(f"gate:{agent_id}")
            return True

        async def _spy_decision(agent_id):
            calls.append(f"decision:{agent_id}")
            return True

        sched._inner_life_gate_check = _spy_gate  # type: ignore[method-assign]
        sched._decision_check = _spy_decision  # type: ignore[method-assign]
        sched._bus, published = _capturing_bus()

        import src.soul.decision as decision_mod

        bombs = {
            tp: ("classify_temporal_state", "format_temporal_anchor", "_read_bond_evidence"),
            decision_mod: ("_build_temporal_anchor",),
        }
        saved = {}
        try:
            for mod, names in bombs.items():
                for n in names:
                    saved[(mod, n)] = getattr(mod, n)
                    setattr(mod, n, _boom)
            with caplog.at_level(logging.INFO, logger=SCHEDULER_LOGGER):
                asyncio.run(sched._fire_proactive_dm())
        finally:
            for (mod, n), orig in saved.items():
                setattr(mod, n, orig)

        assert calls == [f"gate:{WHITELIST_AGENT}", f"decision:{WHITELIST_AGENT}"], (
            f"TA-2 全炸後發起流程必須完全不變, 實際 {calls}"
        )
        assert len(published) == 1, "TA-2 全炸後仍必須 publish 一次"
        assert any("gate=G5" in m for m in _records(caplog))


# ────────────────────────────────────────────────────────────────────
# 【Step 5】新 binding constraint 的觀測證據（本票**不動** router）
# ────────────────────────────────────────────────────────────────────

class TestStep5_NewBindingConstraintEvidence:
    """Step 5 預測的證據。

    ⚠️ 本類別是 **characterization test（現況刻畫）**，**不是**對 router 鎖 B 的背書。
    本票依工單明文「不得改動 `router.py:250-265` 的死碼鎖 B」而**未動它**；
    這些測試只用來證明「G5 放寬後鎖 B 重新變得可達」這個事實，供 Owner 決定後續票。
    後續票若放寬 G20，**本類別理當一起改**（屬該票的合法變更）。
    """

    def test_s5a_g20_source_unchanged_and_still_4h_hard(self):
        """鎖 B（G20）原始碼未被本票改動，仍是 4h 硬阻斷。"""
        src = (ROOT / "src" / "io" / "channels" / "router.py").read_text(encoding="utf-8")
        assert 'event_reason == "proactive_dm"' in src
        assert "hours_since > PROACTIVE_DM_BRYAN_INACTIVE_HOURS" in src
        assert "proactive_dm THROTTLED" in src
        # 本票未新增／移除任何 return（鎖 B 仍會在超時後丟棄訊息）
        assert 'if target_channel == "telegram":' in src

    def test_s5b_g20_drops_proactive_dm_when_bryan_away_16h(self):
        """🔴 事實：Bry 離開 16h 時，送到 router 的 proactive_dm **會被 G20 丟棄**。

        這正是本票要放行的情境（Bry 離開 > 4h）。故結論：
        **scheduler 側的放寬是「必要但不充分」** —— 端到端送達還需處理 G20。
        """
        from src.eventbus.schema import EventPriority, EventType, SoulEvent
        from src.io.channels.router import ChannelRouter

        router = ChannelRouter(bus=MagicMock())
        router._bryan_last_seen = datetime.now(timezone.utc) - timedelta(hours=16)
        sent: List[Any] = []

        class _Adapter:
            async def send(self, **kwargs):
                sent.append(kwargs)
                return True

        router._adapters["telegram"] = _Adapter()

        event = SoulEvent(
            event_type=EventType.AGENT_SPEAK,
            source=WHITELIST_AGENT,
            target="user_bryan",
            priority=EventPriority.NORMAL,
            payload={
                "agent_id": WHITELIST_AGENT,
                "text": "test",
                "reason": "proactive_dm",
                "target_channel": "telegram",
                "target_user_id": 12345,
            },
        )
        asyncio.run(router._on_agent_speak(event))
        assert sent == [], (
            "現況（未動鎖 B）：Bry 離開 > 4h 的 proactive_dm 仍被 G20 丟棄 → "
            "scheduler 側放寬是必要但不充分"
        )

    def test_s5c_g5_source_and_g20_source_share_the_same_constant(self):
        """兩個 4h 鎖嚴格同源（同常數 `PROACTIVE_DM_BRYAN_INACTIVE_HOURS`）。"""
        from src.io.channels.bryan_state import PROACTIVE_DM_BRYAN_INACTIVE_HOURS

        sched_src = SCHEDULER_SRC_PATH.read_text(encoding="utf-8")
        router_src = (ROOT / "src" / "io" / "channels" / "router.py").read_text(encoding="utf-8")
        for src in (sched_src, router_src):
            assert "PROACTIVE_DM_BRYAN_INACTIVE_HOURS" in src
        assert PROACTIVE_DM_BRYAN_INACTIVE_HOURS == 4.0


# ────────────────────────────────────────────────────────────────────
# 通用：0 frozen contract / 紅線
# ────────────────────────────────────────────────────────────────────

class TestRedLines:
    def test_no_frozen_contract_file_touched_by_this_ticket(self):
        """本票改動檔不得落在 frozen contract 清單內。"""
        frozen = {
            "src/agency/trigger_handler.py",
            "src/agency/inner_life_gate.py",
            "src/soul/decision.py",
            "src/soul/decision_trace.py",
            "src/inner_life/elevation_adapter.py",
            "src/io/channels/router.py",
        }
        touched = {
            "src/soul/scheduler.py",
            "src/soul/proactive_policy.py",
            "tests/test_deliverability_release_1.py",
            "tests/test_proactive_dm_deliverability.py",
            "tests/test_observability_completeness_1.py",
            "logs/ENGINEERING_STATE.md",
        }
        assert not (touched & frozen), f"觸碰 frozen contract: {touched & frozen}"

    def test_no_new_timer_or_loop(self):
        """0 新定時器：既有 30s wake 基線不變。"""
        src = SCHEDULER_SRC_PATH.read_text(encoding="utf-8")
        assert src.count("asyncio.sleep(30)") == 2

    def test_scheduler_ast_guardrail_return_false_count_unchanged(self):
        """`count("return False")` guardrail 不被污染（_decision_check 仍為 3）。"""
        src = SCHEDULER_SRC_PATH.read_text(encoding="utf-8")
        a = src.index("async def _decision_check(")
        b = src.index("async def _execute_internal_action(")
        assert src[a:b].count("return False") == 3

    def test_proactive_policy_module_is_pure(self):
        """`proactive_policy.py` 必須是純函式模組：0 I/O / 0 時間 / 0 隨機 / 0 LLM。"""
        import src.soul.proactive_policy as policy

        src = inspect.getsource(policy)
        tree = ast.parse(src)
        allowed = {"__future__", "typing"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert a.name in allowed, f"未預期 import: {a.name}"
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "") in allowed, f"未預期 import: {node.module}"
        for banned in ("open(", "datetime", "random", "json", "data_root", "logger"):
            assert banned not in src, f"純函式模組不得出現 {banned}"
