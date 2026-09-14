"""
tests/test_router_lockb_release_1.py — ROUTER-LOCKB-RELEASE-1
（票 6：移除 router 端「在場鎖 B」，讓 `proactive_dm` 在 telegram fallback 情境下真的送出）

事實權威：`docs/TRANSMIT-DELIVERABILITY-MAP.md`（G20）＋ `cac7c74`/`53bb1b3`（票 5 實測）。

## 病灶（承接票 5，直接採信其已證結構）

`src/io/channels/router.py` 的 M0.5「在場鎖 B」與 `scheduler.py` 的 G5 **逐字同源**：
同一個 4h 常數（`src/io/channels/bryan_state.py:34` 的單一事實來源）、同信號源
`data/state/bryan_last_seen.json`、同 4h 門檻。它自 2026-08-29 起因 G5 先擋而
**不可達**；票 5 放寬 G5 後它**重新可達且必然命中**：

    Bry 離開 > 4h ⇒ web WS 連線數 0 ⇒ router fallback 改走 telegram
                  ⇒ 鎖 B 條件成立 ⇒ 舊行為直接中斷送出（訊息靜默丟棄）

**必然命中**是關鍵 —— 不是機率事件。最壞組合是「付了 LLM 成本、訊息卻被靜默丟棄」。

## 本票處置（Owner 已授權裁定的落實，不是新語意）

Owner 已裁定「放寬 4h 在場要求」（產品語意變更，已知悉），而鎖 B 實作的是
**同一條規則**。故移除鎖 B 的 early-return，只留一行**有界觀測痕跡**，
使該政策只存在於單一位置（scheduler 側的 `gate=G5` 放行痕跡）。

## 本檔四組斷言

  【Step 1】鎖 B 已移除：router 內對該 4h 常數的引用 = 0（AST ＋ 原文皆 0）、
            `THROTTLED` 字樣 = 0、unused import 已刪、痕跡行有界且語意為「放行」。
  【Step 2】🔴 端到端送達：Bry 離開 16h ＋ web 0 conn（telegram fallback）
            ＋ `reason="proactive_dm"` ⇒ `adapter.send` **真的被呼叫**、
            參數正確、送達行含 `reason=proactive_dm`；
            且**不存在**以「Bry 在場與否」為條件的丟棄路徑（AST 級）。
  【Step 3】封頂不變：每日上限仍 1、G4 靜音時段、G3 冷卻窗皆仍有效。
           移除在場鎖 ≠ 解除頻率節制。
  【邊界】  0 服務重啟 / 0 `data/**` 生產寫入（全部落在 conftest 的 tmp 無菌室）。

## 一項刻意的設計決定（見 §Step 1 的 test_s1c）

痕跡行**不再**以「舊 4h 門檻」為條件，而是隻要「telegram 通道 ＋
`reason=proactive_dm` ＋ 已知 `bryan_last_seen`」就記一行。理由：票 6 的驗收要求
router 內對該常數的引用 **= 0**，因此不能在 router 內重新引入 4h 比較；否則就是
把「同源雙鎖」的病灶再種回去。代價是有界且極小 —— `proactive_dm` 受 G7b
每日上限 1 則約束（本檔 `TestStep3CapsUnchanged` 釘死），故每日最多多 1 行 INFO。
"""
from __future__ import annotations

import ast
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.paths import data_root
from src.soul.proactive_policy import (
    PROACTIVE_DM_DAILY_CAP_PER_AGENT,
    daily_proactive_cap,
)
from src.soul.scheduler import SoulScheduler
from src.timezone_utils import now_local

ROOT = Path(__file__).resolve().parent.parent
ROUTER_SRC_PATH = ROOT / "src" / "io" / "channels" / "router.py"
SCHEDULER_SRC_PATH = ROOT / "src" / "soul" / "scheduler.py"
BRYAN_STATE_PATH = ROOT / "src" / "io" / "channels" / "bryan_state.py"

ROUTER_LOGGER = "soul_os.channels.router"
SCHEDULER_LOGGER = "soul_os.soul.scheduler"

#: 有界落盤上限（與 router._LOG_LINE_LIMIT / scheduler._LOG_LINE_LIMIT 一致）
BOUNDED_LIMIT = 200

#: 被移除的 4h 常數名（僅存在於本測試，用來證明 router 內已 0 引用）
INACTIVE_CONST = "PROACTIVE_DM_BRYAN_INACTIVE_HOURS"

#: 白名單唯一角色（`scripts/run_server.py:1071` `proactive_agents=["agent_ruka"]`）
WHITELIST_AGENT = "agent_ruka"

#: Bry 的 TG user id（測試用固定值）
BRY_TG_USER = 12345


# ────────────────────────────────────────────────────────────────────
# 共用工具
# ────────────────────────────────────────────────────────────────────

def _router_src() -> str:
    return ROUTER_SRC_PATH.read_text(encoding="utf-8")


def _msgs(caplog, logger_name: str) -> List[str]:
    return [r.getMessage() for r in caplog.records if r.name == logger_name]


def _assert_bounded(messages: List[str]) -> None:
    for m in messages:
        assert len(m) <= BOUNDED_LIMIT, f"log 行超過 {BOUNDED_LIMIT} 字元: {len(m)} :: {m}"


class _RecordingAdapter:
    """記錄 `send()` 的具名參數（本票的核心證據：訊息真的被送出）。"""

    channel_id = "telegram"

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def send(self, agent_id, text, user_id):
        self.calls.append({"agent_id": agent_id, "text": text, "user_id": user_id})
        return True


def _make_router(bryan_hours_ago: float | None, adapter: _RecordingAdapter):
    """建立決定性 router：Bry 離開 N 小時 ＋ web WS 連線數 0（telegram fallback 必然成立）。

    `bryan_hours_ago=None` 代表冷啟動（`bryan_last_seen.json` 不存在）。

    ⚠️ `_should_push_to_bry`（G22，Stage 4.3 分級）在本檔被固定為放行：
    它與「Bry 在場與否」無關，且在生產中對 `agent_ruka` 恆真
    （`count >= 1` ⇒ `_PUSH_PROB_ACTIVE = 1.0`）。本票要隔離的變數是鎖 B。
    """
    from src.io.channels.router import ChannelRouter

    router = ChannelRouter(bus=MagicMock())
    router._adapters = {"telegram": adapter}
    router._gateway_manager = MagicMock()
    router._gateway_manager.count = 0          # Bry 不在 web
    router._last_tg_user_global = BRY_TG_USER  # ⇒ fallback 必然改走 telegram
    router._last_tg_user = {}
    router._should_push_to_bry = lambda agent_id: True  # 去掉 G22 隨機性
    if bryan_hours_ago is None:
        router._bryan_last_seen = None
    else:
        router._bryan_last_seen = datetime.now(timezone.utc) - timedelta(hours=bryan_hours_ago)
    return router


def _fallback_speak_event(text: str = "今天想跟你說一件小事。") -> Any:
    """模擬 scheduler/LLM 端發出的主動訊息：`target_channel="web"` ＋ `reason="proactive_dm"`。

    這正是生產中的實況 —— proxy 發 AGENT_SPEAK 時帶 `reason="proactive_dm"`，
    預設目標通道是 web；Bry 不在 web 時由 router fallback 改寫成 telegram。
    """
    from src.eventbus.schema import EventPriority, EventType, SoulEvent

    return SoulEvent(
        event_type=EventType.AGENT_SPEAK,
        source=WHITELIST_AGENT,
        target="user_bryan",
        priority=EventPriority.NORMAL,
        payload={
            "agent_id": WHITELIST_AGENT,
            "text": text,
            "reason": "proactive_dm",
            "target_channel": "web",
        },
    )


def _fire_proactive_dm_ast() -> ast.AsyncFunctionDef:
    src = SCHEDULER_SRC_PATH.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_fire_proactive_dm":
            return node
    raise AssertionError("scheduler.py 找不到 _fire_proactive_dm")


def _deterministic_scheduler() -> SoulScheduler:
    """決定性 scheduler：關閉 G3 冷卻／G4 靜音／想念門檻，用於「封頂不變」對照。"""
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


# ────────────────────────────────────────────────────────────────────
# 【Step 1】鎖 B 已移除
# ────────────────────────────────────────────────────────────────────

class TestStep1LockBRemoved:
    """鎖 B（G20）由「4h 硬阻斷」改為「移除阻斷 ＋ 有界放行痕跡」。"""

    def test_s1a_router_references_to_the_4h_constant_are_zero(self):
        """🔴 驗收 2：router 內對該 4h 常數的引用 = 0（AST 級 ＋ 原文級）。"""
        src = _router_src()
        tree = ast.parse(src)

        # (a) 原文級：連註解都不得出現該識別字（避免任何形式的「再種回去」）
        assert src.count(INACTIVE_CONST) == 0, (
            f"router.py 仍出現 {INACTIVE_CONST} 共 {src.count(INACTIVE_CONST)} 次"
        )
        # (b) AST 級：Name / Attribute 皆 0
        name_hits = [
            n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == INACTIVE_CONST
        ]
        attr_hits = [
            n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == INACTIVE_CONST
        ]
        assert name_hits == [], f"router.py 仍有 Name 引用: {[n.lineno for n in name_hits]}"
        assert attr_hits == [], f"router.py 仍有 Attribute 引用: {[n.lineno for n in attr_hits]}"

    def test_s1b_unused_import_removed_but_reader_semantics_kept(self):
        """unused import 已刪；`read_bryan_last_seen` / `touch_bryan_last_seen` 語意仍在。"""
        src = _router_src()
        # 被移除的是「常數」那條 import（bryan_state 仍被讀寫函式使用）
        assert f"import {INACTIVE_CONST}" not in src
        assert "from src.io.channels.bryan_state import read_bryan_last_seen" in src
        assert "from src.io.channels.bryan_state import touch_bryan_last_seen" in src
        # 常數本身不得改動（scheduler G5 與 goals 仍在用）
        assert "PROACTIVE_DM_BRYAN_INACTIVE_HOURS = 4.0" in BRYAN_STATE_PATH.read_text(encoding="utf-8")

    def test_s1c_old_throttle_wording_is_gone(self):
        """`THROTTLED` 字樣必須從 router 完全消失（語意已變）。"""
        src = _router_src()
        assert src.count("THROTTLED") == 0
        assert "bryan_last_seen=" not in src, "舊行的欄位 dump 也應一起消失"

    def test_s1d_16h_telegram_emits_bounded_release_trace(self, caplog):
        """16h 情境：留一行**有界**放行痕跡，含觀察到的小時數，且不含 `THROTTLED`。"""
        adapter = _RecordingAdapter()
        router = _make_router(bryan_hours_ago=16, adapter=adapter)

        with caplog.at_level(logging.INFO, logger=ROUTER_LOGGER):
            asyncio.run(router._on_agent_speak(_fallback_speak_event()))

        msgs = _msgs(caplog, ROUTER_LOGGER)
        trace = [m for m in msgs if "在場鎖 B 已移除" in m]
        assert len(trace) == 1, f"必須恰好一行放行痕跡: {msgs}"
        line = trace[0]
        assert "proactive_dm" in line
        assert "放行" in line
        assert "telegram" in line, "痕跡必須指出事實上的通道"
        assert "16.0h" in line, f"痕跡必須記錄觀察到的 inactivity 小時數: {line}"
        assert "THROTTLED" not in line
        _assert_bounded(msgs)

    def test_s1e_cold_start_still_sends_and_keeps_no_trace(self, caplog):
        """冷啟動（無 `bryan_last_seen`）語意不變：照送，且無小時數可報故不留痕。"""
        adapter = _RecordingAdapter()
        router = _make_router(bryan_hours_ago=None, adapter=adapter)

        with caplog.at_level(logging.INFO, logger=ROUTER_LOGGER):
            asyncio.run(router._on_agent_speak(_fallback_speak_event()))

        assert len(adapter.calls) == 1, "冷啟動不得被任何在場判定阻擋"
        assert [m for m in _msgs(caplog, ROUTER_LOGGER) if "在場鎖 B 已移除" in m] == []

    def test_s1f_dream_and_event_exemptions_untouched(self):
        """夢境／事件豁免（`source in (dream, event)`）不得被本票連帶影響。"""
        src = _router_src()
        assert 'event_source not in ("dream", "event")' in src


# ────────────────────────────────────────────────────────────────────
# 【Step 2】🔴 端到端送達（本票核心驗收）
# ────────────────────────────────────────────────────────────────────

class TestStep2EndToEndDelivery:
    """Bry 離開 16h ＋ web 無連線（telegram fallback）＋ `proactive_dm` ⇒ 真的送出。"""

    def test_s2a_16h_telegram_fallback_really_delivers(self, caplog):
        """🔴 核心：舊版必被鎖 B 丟棄的情境，現在 `adapter.send` **真的被呼叫**。

        生產實況：Bry 離開 > 4h ⇒ web WS 連線數 0 ⇒ fallback 改走 telegram
        ⇒ （舊）鎖 B 條件成立 ⇒ 訊息靜默丟棄。此處逐項斷言送達。
        """
        adapter = _RecordingAdapter()
        router = _make_router(bryan_hours_ago=16, adapter=adapter)
        text = "今天想跟你說一件小事。"

        # 先自證「這正是舊鎖必然命中的情境」：觀察到的小時數 > 舊 4h 門檻
        observed_hours = (
            datetime.now(timezone.utc) - router._bryan_last_seen
        ).total_seconds() / 3600.0
        assert observed_hours > 4.0, (
            f"前提不成立：觀測 {observed_hours:.2f}h 未超過舊 4h 門檻，本測試失去意義"
        )

        with caplog.at_level(logging.INFO, logger=ROUTER_LOGGER):
            asyncio.run(router._on_agent_speak(_fallback_speak_event(text)))

        # (1) 🔴 訊息真的被送出（票 5 的 test_s5b 在此為 0 送達）
        assert adapter.calls == [
            {"agent_id": "ruka", "text": text, "user_id": BRY_TG_USER}
        ], f"16h/telegram fallback 情境必須真的送出, 實際 {adapter.calls}"

        msgs = _msgs(caplog, ROUTER_LOGGER)

        # (2) 送達行存在且帶既有 reason 欄位（票 3 已加）
        sent = [m for m in msgs if "sent to" in m]
        assert len(sent) == 1, f"應恰好一行送達行: {msgs}"
        assert sent[0].startswith(
            f"[ChannelRouter:telegram] sent to {BRY_TG_USER} from ruka: "
        ), sent[0]
        assert "reason=proactive_dm" in sent[0], sent[0]

        # (3) 送達行與放行痕跡都不出現舊的丟棄語意
        assert not any("THROTTLED" in m for m in msgs)
        _assert_bounded(msgs)

    def test_s2b_web_zero_conn_fallback_targets_global_last_tg_user(self, caplog):
        """fallback 改寫本身未被本票改動：目標＝全域 last_tg_user、通道＝telegram。"""
        adapter = _RecordingAdapter()
        router = _make_router(bryan_hours_ago=16, adapter=adapter)

        with caplog.at_level(logging.INFO, logger=ROUTER_LOGGER):
            asyncio.run(router._on_agent_speak(_fallback_speak_event()))

        msgs = _msgs(caplog, ROUTER_LOGGER)
        fb = [m for m in msgs if "fallback telegram" in m]
        assert len(fb) == 1, f"fallback 痕跡應保留 (既有行為): {msgs}"
        assert adapter.calls[0]["user_id"] == BRY_TG_USER

    def test_s2c_no_drop_path_conditioned_on_bryan_presence(self):
        """🔴 驗收 2（反向斷言）：**不得**有任何以「Bry 在場與否」為條件的丟棄路徑。

        AST 級：router 內任何 `if` 只要條件提到 `_bryan_last_seen`，
        其區塊內就**不得出現任何中斷語句**。
        """
        src = _router_src()
        tree = ast.parse(src)
        offenders: List[int] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            seg = ast.get_source_segment(src, node) or ""
            if "_bryan_last_seen" not in seg:
                continue
            if any(isinstance(x, ast.Return) for x in ast.walk(node)):
                offenders.append(node.lineno)
        assert offenders == [], (
            f"router.py 仍有以 Bry 在場與否為條件的丟棄路徑, 行號 {offenders}"
        )

    def test_s2d_only_scheduler_observation_trace_remains_in_delivery_path(self):
        """🔴 驗收 2：4h 政策在**發送判定路徑**中只剩 scheduler 的觀測痕跡一處。

        發送判定路徑 = `router.py`（發送判定）＋ `scheduler._fire_proactive_dm`（G5 痕跡）。
        `src/goals/*` 對同常數的引用屬**生成側**（B 軸種子抑制），不是送達判定，
        本票依工單不得改動。
        """
        # (a) router：0 引用（已由 test_s1a 釘死, 此處再以「發送判定路徑」角度確認）
        assert _router_src().count(INACTIVE_CONST) == 0

        # (b) scheduler：恰好 1 處，且是「量測 ＋ 放行」而非中斷
        src = SCHEDULER_SRC_PATH.read_text(encoding="utf-8")
        fn = _fire_proactive_dm_ast()
        hits = []
        for node in ast.walk(fn):
            if isinstance(node, ast.If):
                seg = ast.get_source_segment(src, node) or ""
                if INACTIVE_CONST in seg:
                    hits.append((node, seg))
        assert len(hits) == 1, f"G5 的 if 區塊應恰好 1 個, 實際 {len(hits)}"
        node, seg = hits[0]
        assert not any(isinstance(n, ast.Return) for n in ast.walk(node)), (
            "G5 不得再中斷流程"
        )
        assert "gate=G5" in seg and ("放行" in seg or "不再阻斷" in seg)

    def test_s2e_generation_side_references_are_out_of_delivery_path(self):
        """生成側（`src/goals/*`）仍使用同常數 —— 本票**不得**動它們。"""
        for rel in ("src/goals/motive_provider.py", "src/goals/seed_provider.py"):
            assert INACTIVE_CONST in (ROOT / rel).read_text(encoding="utf-8"), rel

    def test_s2f_delivery_is_not_random_for_ruka(self):
        """生產中 G22 對 `agent_ruka` 為 vacuous（`count>=1` ⇒ prob 1.0），故非新瓶頸。"""
        src = _router_src()
        assert "_PUSH_PROB_ACTIVE = 1.0" in src
        assert "_PUSH_PROB_COLD_START = 1.0/3.0" in src


# ────────────────────────────────────────────────────────────────────
# 【Step 3】封頂不變（移除在場鎖 ≠ 解除頻率節制）
# ────────────────────────────────────────────────────────────────────

class TestStep3CapsUnchanged:
    """🔴 驗收 3：每日上限仍 1、G4 靜音時段、G3 冷卻窗皆仍有效。"""

    def test_s3a_daily_proactive_cap_is_still_one(self):
        """每日主動上限仍為 **1 則/角色/日**，且與沉默時長無關。"""
        assert PROACTIVE_DM_DAILY_CAP_PER_AGENT == 1
        for minutes in (0, 60, 180, 360, 720, 1440, 2880, 4320, 43200, None):
            assert daily_proactive_cap(minutes) == 1, f"silence={minutes} 的上限不得 > 1"
        # 不變量（Owner 裁定 (c)）：長沉默不得變成騷擾許可證
        assert daily_proactive_cap(4320) == daily_proactive_cap(180)

    def test_s3b_g4_quiet_hours_still_blocks(self, caplog):
        """G4 靜音時段（23:00-08:00）仍必須阻擋並且留痕。"""
        sched = _deterministic_scheduler()
        sched._is_quiet_hours = lambda now: True  # type: ignore[method-assign]
        with caplog.at_level(logging.INFO, logger=SCHEDULER_LOGGER):
            asyncio.run(sched._fire_proactive_dm())
        assert any("gate=G4" in m for m in _msgs(caplog, SCHEDULER_LOGGER)), "G4 仍必須阻擋"

    def test_s3c_g3_cooldown_still_blocks(self, caplog):
        """G3 冷卻窗（2h）仍必須阻擋並且留痕。"""
        sched = _deterministic_scheduler()
        sched._last_proactive_dm_time = now_local()
        sched.proactive_dm_cooldown_seconds = 7200
        with caplog.at_level(logging.INFO, logger=SCHEDULER_LOGGER):
            asyncio.run(sched._fire_proactive_dm())
        assert any("gate=G3" in m for m in _msgs(caplog, SCHEDULER_LOGGER)), "G3 仍必須阻擋"

    def test_s3d_quiet_hours_window_is_still_23_to_08(self):
        """靜音窗界線不得被本票改動。"""
        sched = _deterministic_scheduler()
        assert sched._is_quiet_hours is not None  # 對照：本檔 test 內以 lambda 覆寫
        src = SCHEDULER_SRC_PATH.read_text(encoding="utf-8")
        assert "23:00-08:00" in src
        assert "proactive_dm_cooldown_seconds: int = 7200" in src

    def test_s3e_router_side_has_no_frequency_cap_of_its_own(self):
        """router 端本票未新增任何頻率閘門（0 行為變更, 只有一行 log）。"""
        src = _router_src()
        assert "PROACTIVE_DM_DAILY_CAP_PER_AGENT" not in src
        assert "daily_proactive_cap" not in src


# ────────────────────────────────────────────────────────────────────
# 邊界：0 生產資料寫入 / 有界落盤
# ────────────────────────────────────────────────────────────────────

class TestBoundaryDiscipline:
    def test_b1_all_new_log_lines_are_bounded(self, caplog):
        """本票新增的痕跡行 <= 200 字元, 且不 dump 物件。"""
        adapter = _RecordingAdapter()
        router = _make_router(bryan_hours_ago=16, adapter=adapter)
        with caplog.at_level(logging.INFO, logger=ROUTER_LOGGER):
            asyncio.run(router._on_agent_speak(_fallback_speak_event()))
        msgs = _msgs(caplog, ROUTER_LOGGER)
        _assert_bounded(msgs)
        trace = [m for m in msgs if "在場鎖 B 已移除" in m][0]
        assert "{'" not in trace and "datetime." not in trace, "不得整段 dump 物件"

    def test_b2_tests_run_against_isolated_data_root(self):
        """conftest 的 autouse 隔離生效：資料根不在生產 `data/`。"""
        root = str(data_root())
        assert "soul_data_root" in root or "tmp" in root.lower(), f"未隔離: {root}"
