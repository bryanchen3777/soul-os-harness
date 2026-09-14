"""
tests/test_observability_completeness_1.py — OBSERVABILITY-COMPLETENESS-1（票 3：觀測完整性）

本票核心目的：讓今晚的實測窗口「失敗也能被歸因」。五個工作項：

  【A】送達歸因 —— 從 log 就能判別「這則是主動 DM 還是回覆」
       機制：`router.py` 送達主判據（D1 `[ChannelRouter:{ch}] sent to ...`）與送達
       失敗行（D2 `send failed`）補上 payload **既有** 的 `reason` 欄位。
       ⚠️ 事實修正：工單假設「AGENT_SPEAK payload 缺 reason」，實測 `proxy.py`
       唯一發布點早已寫入 `"reason": reason`（payload 本來就有）；真正的缺口是
       **router 的送達行沒把它印出來**（map §6.3 ND-4）。因此本票不需要動任何
       payload —— 只讀既有欄位，比工單「首選」更小、0 payload 變更。

  【B】G10 假宣告 —— `inner_life_gate.py` 定義 logger 但 0 次呼叫，
       呼叫端 `scheduler.py` 的註解卻宣稱 "Gate already logged observability"。
       處置：在判定點（4 態：EMITTED／GATED／UNAVAILABLE／FAILURE）補有界 log。

  【C】四個「阻擋但不留痕」的閘門（map §8 逐字點名 G3／G4／G9′／G10）：
       G3 冷卻窗／G4 靜音時段（原 logger.debug → 生產 INFO 級不落盤）、
       G9′ 活動讀取的三條 return None（其中兩條靜默）、
       G9 活動 enrichment 未帶活動（去重 vs 沒活動原本不可區分）。

  【D】`[SM-3 Decision]` 0 筆異常 —— 根因診斷 + 四行可觸發且可捕捉的證明。

  【E】五分鐘 log 空窗 —— **唯讀診斷，不改任何 runtime 行為**；
       結論見 `logs/ENGINEERING_STATE.md` 登記（本測試檔 0 相關斷言，避免把
       一次性生產診斷寫成回歸閘門）。

通用驗收（本檔釘死）：
  1. 有界落盤鐵律：本票新增的每一行 log 不得整段 dump 物件，單行一律 <= 200 字元。
  2. 0 行為變更：判定／送達／阻擋的**判定結果**逐位元不變（每改動點一組斷言）。
  3. 0 frozen contract 變更（Agency 4 stages／TriggerEnvelope／InnerLifeEvent／
     4 handlers／SAGE 寫入邏輯／SubmissionGate／SI-2.1 三防線／DecisionResult）。

紅線：0 服務重啟 / 0 `data/**` 生產寫入（全部落在 SOUL_OS_DATA_DIR 隔離目錄）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agency.inner_life_gate import (
    GateDecision,
    GateResult,
    _gate_proactive_dm_impl,
    gate_proactive_dm,
)
from src.io.channels.router import ChannelRouter
from src.paths import data_root, reset_data_root
from src.soul.decision import DecisionResult
from src.soul.motive import MotiveEngine, new_motive_id, now_utc_iso
from src.soul.scheduler import SoulScheduler
from src.timezone_utils import now_local

ROOT = Path(__file__).resolve().parent.parent

SCHEDULER_LOGGER = "soul_os.soul.scheduler"
GATE_LOGGER = "soul_os.agency.inner_life_gate"
ROUTER_LOGGER = "soul_os.channels.router"

# 有界落盤鐵律（本票新增的每一行都必須 <= 此值）
BOUNDED_LIMIT = 200


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────

def _isolated_data_root(tmp_path: Path) -> Path:
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()
    return data_root()


def _restore_data_root() -> None:
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


@pytest.fixture
def isolated_root(tmp_path: Path):
    data_dir = _isolated_data_root(tmp_path)
    yield data_dir
    _restore_data_root()


def _records_for(caplog: pytest.LogCaptureFixture, logger_name: str) -> List[str]:
    return [r.getMessage() for r in caplog.records if r.name == logger_name]


def _assert_all_bounded(messages: List[str], limit: int = BOUNDED_LIMIT) -> None:
    """有界落盤鐵律：每一行都 <= limit 且不含整段物件 dump。"""
    assert messages, "預期至少一行 log"
    for m in messages:
        assert len(m) <= limit, f"log 行超過 {limit} 字元 ({len(m)}): {m[:80]}..."
        assert "\n" not in m, f"log 行不得含換行 (物件 dump 跡象): {m[:80]}..."


# ────────────────────────────────────────────────────────────────────
# 【A】送達歸因：從 log 就能判別 proactive DM vs 回覆
# ────────────────────────────────────────────────────────────────────

class _FakeAdapter:
    """最小 ChannelAdapter 替身（記錄 send 參數, 不觸網）。"""

    def __init__(self, channel_id: str = "telegram", ok: bool = True):
        self.channel_id = channel_id
        self.ok = ok
        self.calls: List[tuple] = []

    async def send(self, agent_id, text, user_id):
        self.calls.append((agent_id, text, user_id))
        return self.ok


def _make_router(adapter: _FakeAdapter) -> ChannelRouter:
    """建立完全決定性的 router（隔離外部狀態, 不依賴生產 data/state）。"""
    router = ChannelRouter(bus=None)
    router._adapters = {adapter.channel_id: adapter}
    # G20 4h 鎖 B 不參與本節（本節驗的是「送出後那行 log」）
    router._bryan_last_seen = None
    router._last_tg_user_global = None
    router._last_tg_user = {}
    router._gateway_manager = None
    # Stage 4.3 TG 分級：固定放行, 去掉隨機性
    router._should_push_to_bry = lambda agent_id: True
    return router


def _speak_event(payload_extra: Dict[str, Any]) -> Any:
    from src.eventbus.schema import EventPriority, EventType, SoulEvent

    payload = {
        "text": "今天想跟你說一件小事。",
        "agent_id": "agent_ruka",
        "target_channel": "telegram",
        "target_user_id": "12345",
    }
    payload.update(payload_extra)
    return SoulEvent(
        event_type=EventType.AGENT_SPEAK,
        source="agent_ruka",
        target="broadcast",
        priority=EventPriority.NORMAL,
        payload=payload,
    )


class TestADeliveryAttribution:
    """【A】送達行可自證 proactive / reply；缺省時既有行為逐位元不變。"""

    def test_a1_proactive_dm_delivery_line_carries_reason(self, caplog):
        adapter = _FakeAdapter()
        router = _make_router(adapter)
        event = _speak_event({"reason": "proactive_dm"})

        with caplog.at_level(logging.INFO, logger=ROUTER_LOGGER):
            asyncio.run(router._on_agent_speak(event))

        msgs = _records_for(caplog, ROUTER_LOGGER)
        sent = [m for m in msgs if "sent to" in m]
        assert len(sent) == 1, f"應恰好一行送達行: {msgs}"
        line = sent[0]
        # 送達歸因：agent + 通道 + 收件人 + 文字指紋 + reason 全在同一行
        assert line.startswith(
            "[ChannelRouter:telegram] sent to 12345 from ruka: "
        ), line
        assert "reason=proactive_dm" in line, line
        _assert_all_bounded(msgs)

    def test_a2_reply_delivery_line_is_distinguishable(self, caplog):
        adapter = _FakeAdapter()
        router = _make_router(adapter)
        event = _speak_event({"reason": "user_message"})

        with caplog.at_level(logging.INFO, logger=ROUTER_LOGGER):
            asyncio.run(router._on_agent_speak(event))

        line = [m for m in _records_for(caplog, ROUTER_LOGGER) if "sent to" in m][0]
        assert "reason=user_message" in line
        assert "reason=proactive_dm" not in line, "必須能區分 proactive 與 reply"

    def test_a3_reason_absent_is_additive_only(self, caplog):
        """⚠️ 缺省不變：payload 無 reason 時, 既有前綴逐字不變, 只多一個空 reason 欄位。"""
        adapter = _FakeAdapter()
        router = _make_router(adapter)
        event = _speak_event({})  # 無 reason 鍵

        with caplog.at_level(logging.INFO, logger=ROUTER_LOGGER):
            asyncio.run(router._on_agent_speak(event))

        line = [m for m in _records_for(caplog, ROUTER_LOGGER) if "sent to" in m][0]
        # 改動前既有格式逐字保留（前綴 identity）
        legacy_prefix = (
            "[ChannelRouter:telegram] sent to 12345 from ruka: "
            f"{'今天想跟你說一件小事。'!r}"
        )
        assert line == legacy_prefix + " reason=", line

    def test_a4_reason_none_does_not_crash(self, caplog):
        """reason 顯式 None（非字串）也不得影響送達。"""
        adapter = _FakeAdapter()
        router = _make_router(adapter)
        event = _speak_event({"reason": None})

        with caplog.at_level(logging.INFO, logger=ROUTER_LOGGER):
            asyncio.run(router._on_agent_speak(event))

        assert len(adapter.calls) == 1
        line = [m for m in _records_for(caplog, ROUTER_LOGGER) if "sent to" in m][0]
        assert line.endswith(" reason="), line

    def test_a5_send_failure_line_carries_reason(self, caplog):
        """D2（否定證據）也要可歸因。"""
        adapter = _FakeAdapter(ok=False)
        router = _make_router(adapter)
        event = _speak_event({"reason": "proactive_dm"})

        with caplog.at_level(logging.INFO, logger=ROUTER_LOGGER):
            asyncio.run(router._on_agent_speak(event))

        msgs = _records_for(caplog, ROUTER_LOGGER)
        failed = [m for m in msgs if "send failed" in m]
        assert len(failed) == 1, msgs
        assert "reason=proactive_dm" in failed[0]
        _assert_all_bounded(msgs)

    def test_a6_zero_behaviour_change_send_args_and_count(self, caplog):
        """0 行為變更：adapter.send 的參數與呼叫次數與改動前一致。"""
        adapter = _FakeAdapter()
        router = _make_router(adapter)
        event = _speak_event({"reason": "proactive_dm"})

        with caplog.at_level(logging.INFO, logger=ROUTER_LOGGER):
            asyncio.run(router._on_agent_speak(event))

        # agent_id 前綴被 strip、user_id 被 int()、text 原樣 —— 全部與改動前一致
        assert adapter.calls == [("ruka", "今天想跟你說一件小事。", 12345)]

    def test_a7_bounded_for_pathological_input(self, caplog):
        """有界落盤：極長 text + 極長 reason 仍 <= 200 字元, 且不 dump 物件。"""
        adapter = _FakeAdapter()
        router = _make_router(adapter)
        event = _speak_event({"reason": "x" * 5000, "text": "崩" * 5000})

        with caplog.at_level(logging.INFO, logger=ROUTER_LOGGER):
            asyncio.run(router._on_agent_speak(event))

        _assert_all_bounded(_records_for(caplog, ROUTER_LOGGER))

    def test_a8_bounded_log_helper_is_identity_for_short_text(self):
        """有界 helper 對短訊息必須 identity 回傳（既有 log 逐字不變的機制保證）。"""
        from src.io.channels.router import _bounded_log

        short = "[ChannelRouter:telegram] sent to 1 from ruka: 'hi' reason=user_message"
        assert len(short) < BOUNDED_LIMIT
        assert _bounded_log(short) == short
        long = "A" * 500
        out = _bounded_log(long)
        assert len(out) == BOUNDED_LIMIT
        assert out.endswith("...")


# ────────────────────────────────────────────────────────────────────
# 【B】G10：inner_life_gate 判定點補 log（4 態）＋ 判定不變
# ────────────────────────────────────────────────────────────────────

def _trace_record(agent_id: str, ts: str, event_id: str = "e" * 32) -> Dict[str, Any]:
    return {
        "event_id": event_id,
        "session_id": "s",
        "correlation_id": "c",
        "parent_event_id": None,
        "ts": ts,
        "provenance": {"trigger_type": "agent_reply", "actor_id": agent_id},
    }


class _FakeTraceReader:
    def __init__(self, records: List[Dict[str, Any]]):
        self._records = records

    def query_by_ts_range(self, start: str, end: str) -> List[Dict[str, Any]]:
        return list(self._records)


class _RaisingTraceReader:
    def query_by_ts_range(self, start: str, end: str):
        raise RuntimeError("boom-" + "Z" * 4000)


class TestBG10GateObservability:
    """【B】G10 判定點 4 態各自產生一行可辨識的有界 log。"""

    _NOW = datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc)

    def _drive(self, reader, caplog, **kw):
        with caplog.at_level(logging.INFO, logger=GATE_LOGGER):
            return gate_proactive_dm(
                agent_id="agent_ruka",
                now=self._NOW,
                trace_reader=reader,
                **kw,
            )

    def test_b1_emitted_logged(self, caplog):
        # last event 60 min ago >= 30 min threshold → EMITTED
        r = self._drive(
            _FakeTraceReader([_trace_record("agent_ruka", "2026-09-13T11:00:00+00:00")]),
            caplog,
        )
        assert r.decision == GateDecision.EMITTED
        msgs = _records_for(caplog, GATE_LOGGER)
        assert len(msgs) == 1, msgs
        assert "decision=emitted" in msgs[0]
        assert "agent=agent_ruka" in msgs[0]
        _assert_all_bounded(msgs)

    def test_b2_gated_logged_with_reason(self, caplog):
        # last event 5 min ago < 30 min → GATED（阻擋, 必須留痕）
        r = self._drive(
            _FakeTraceReader([_trace_record("agent_ruka", "2026-09-13T11:55:00+00:00")]),
            caplog,
        )
        assert r.decision == GateDecision.GATED
        msgs = _records_for(caplog, GATE_LOGGER)
        assert len(msgs) == 1, msgs
        assert "decision=gated_inner_life_activity" in msgs[0]
        assert "threshold" in msgs[0], "阻擋理由必須在場"
        _assert_all_bounded(msgs)

    def test_b3_unavailable_logged_at_info(self, caplog):
        """UNAVAILABLE 若留 debug 則生產 INFO 級不落盤 → 此處必須是 INFO。"""
        r = self._drive(_FakeTraceReader([]), caplog)
        assert r.decision == GateDecision.UNAVAILABLE
        msgs = _records_for(caplog, GATE_LOGGER)
        assert len(msgs) == 1, msgs
        assert "decision=gate_unavailable" in msgs[0]
        assert caplog.records[-1].levelno == logging.INFO
        _assert_all_bounded(msgs)

    def test_b4_failure_logged_as_warning(self, caplog):
        r = self._drive(_RaisingTraceReader(), caplog)
        assert r.decision == GateDecision.FAILURE
        msgs = _records_for(caplog, GATE_LOGGER)
        assert len(msgs) == 1, msgs
        assert "decision=gate_failure" in msgs[0]
        assert caplog.records[-1].levelno == logging.WARNING
        _assert_all_bounded(msgs)

    def test_b5_malformed_record_failure_logged(self, caplog):
        rec = _trace_record("agent_ruka", "2026-09-13T11:00:00+00:00")
        rec["event_id"] = None  # malformed
        r = self._drive(_FakeTraceReader([rec]), caplog)
        assert r.decision == GateDecision.FAILURE
        _assert_all_bounded(_records_for(caplog, GATE_LOGGER))

    def test_b6_invalid_agent_id_failure_is_bounded(self, caplog):
        """驗證分支的 reason 內含輸入 repr → 必須被截斷（不得整段 dump）。"""
        with caplog.at_level(logging.INFO, logger=GATE_LOGGER):
            r = gate_proactive_dm(
                agent_id=["Z"] * 5000,  # 非 str → 走驗證 FAILURE, reason 內含長 repr
                now=self._NOW,
                trace_reader=_FakeTraceReader([]),
            )
        assert r.decision == GateDecision.FAILURE
        _assert_all_bounded(_records_for(caplog, GATE_LOGGER))

    @pytest.mark.parametrize("reader_kind", ["gated", "emitted", "unavailable", "failure"])
    def test_b7_zero_behaviour_change_wrapper_is_passthrough(self, reader_kind):
        """0 行為變更：公開 wrapper 的 GateResult 與判定本體逐欄位相同。"""
        readers = {
            "gated": _FakeTraceReader(
                [_trace_record("agent_ruka", "2026-09-13T11:55:00+00:00")]
            ),
            "emitted": _FakeTraceReader(
                [_trace_record("agent_ruka", "2026-09-13T11:00:00+00:00")]
            ),
            "unavailable": _FakeTraceReader([]),
            "failure": _RaisingTraceReader(),
        }
        impl = _gate_proactive_dm_impl(
            agent_id="agent_ruka", now=self._NOW, trace_reader=readers[reader_kind]
        )
        public = gate_proactive_dm(
            agent_id="agent_ruka", now=self._NOW, trace_reader=readers[reader_kind]
        )
        assert public == impl, "wrapper 必須原樣透傳判定結果"
        assert public.decision is impl.decision
        assert public.reason == impl.reason
        assert public.last_event_id == impl.last_event_id
        assert public.last_event_ts == impl.last_event_ts
        assert public.elapsed_minutes == impl.elapsed_minutes

    def test_b8_decision_values_frozen(self):
        """4 態字串值域凍結（既有消費者依賴）。"""
        assert GateDecision.EMITTED.value == "emitted"
        assert GateDecision.GATED.value == "gated_inner_life_activity"
        assert GateDecision.UNAVAILABLE.value == "gate_unavailable"
        assert GateDecision.FAILURE.value == "gate_failure"

    def test_b9_gate_module_no_longer_logger_silent(self):
        """反假宣告：本模組不得再出現「定義了 logger 但 0 次呼叫」。

        4 態由單一 helper `_log_gate_decision` 分流（INFO ×3 + WARNING ×1）,
        故此處斷言 (a) 模組內確實有 logger 呼叫, (b) 4 態全部被 helper 覆蓋。
        """
        src = (ROOT / "src/agency/inner_life_gate.py").read_text(encoding="utf-8")
        assert src.count("logger.info(") + src.count("logger.warning(") >= 2
        for state in ("EMITTED", "GATED", "UNAVAILABLE", "FAILURE"):
            assert f"GateDecision.{state}" in src, f"{state} 未被判定點覆蓋"
        assert "GateDecision.FAILURE" in src and "logger.warning" in src


# ────────────────────────────────────────────────────────────────────
# 【C】四個無痕閘門：G3 / G4 / G9' / G9
# ────────────────────────────────────────────────────────────────────

def _deterministic_scheduler(**kw) -> SoulScheduler:
    sched = SoulScheduler(**kw)
    sched._get_proactive_agents = lambda: ["agent_ruka"]  # type: ignore[method-assign]
    sched._last_proactive_dm_time = None
    sched._is_quiet_hours = lambda now: False  # type: ignore[method-assign]
    sched._bryan_last_seen_minutes = lambda: None  # type: ignore[method-assign]
    sched._get_agent_longing = lambda agent_id: 0.99  # type: ignore[method-assign]
    return sched


class TestCBlockGatesLeaveTrace:
    """【C】G3／G4／G9′／G9 每一次阻擋都留一行可辨識的 log。"""

    def test_c1_g3_cooldown_block_logged(self, caplog):
        sched = _deterministic_scheduler()
        sched._last_proactive_dm_time = now_local()  # elapsed ~0 < 7200s

        with caplog.at_level(logging.INFO, logger=SCHEDULER_LOGGER):
            asyncio.run(sched._fire_proactive_dm())

        msgs = _records_for(caplog, SCHEDULER_LOGGER)
        hit = [m for m in msgs if "gate=G3" in m]
        assert len(hit) == 1, f"G3 阻擋必須留痕: {msgs}"
        assert "冷卻窗" in hit[0]
        _assert_all_bounded(msgs)

    def test_c2_g4_quiet_hours_block_logged(self, caplog):
        sched = _deterministic_scheduler()
        sched._is_quiet_hours = lambda now: True  # type: ignore[method-assign]

        with caplog.at_level(logging.INFO, logger=SCHEDULER_LOGGER):
            asyncio.run(sched._fire_proactive_dm())

        msgs = _records_for(caplog, SCHEDULER_LOGGER)
        hit = [m for m in msgs if "gate=G4" in m]
        assert len(hit) == 1, f"G4 阻擋必須留痕: {msgs}"
        assert "靜音時段" in hit[0]
        _assert_all_bounded(msgs)

    def test_c3_g9_prime_no_diary_file_logged(self, tmp_path, caplog):
        _isolated_data_root(tmp_path)
        try:
            sched = SoulScheduler()
            with caplog.at_level(logging.INFO, logger=SCHEDULER_LOGGER):
                out = sched._get_recent_shareable_activity("agent_ruka")
            assert out is None  # 0 行為變更
            msgs = _records_for(caplog, SCHEDULER_LOGGER)
            hit = [m for m in msgs if "reason=no_diary_file" in m]
            assert len(hit) == 1, f"G9' 第一條 return None 必須留痕: {msgs}"
            assert "agent_ruka" in hit[0] and "gate=G9'" in hit[0]
            _assert_all_bounded(msgs)
        finally:
            _restore_data_root()

    def test_c4_g9_prime_no_qualifying_entry_logged(self, tmp_path, caplog):
        _isolated_data_root(tmp_path)
        try:
            soul_dir = data_root() / "soul" / "agent_ruka" / "diary"
            soul_dir.mkdir(parents=True, exist_ok=True)
            today = datetime.now().strftime("%Y-%m-%d")
            # 今日檔存在, 但沒有 slot=="event" + shareable + source=="llm" 的 entry
            (soul_dir / f"{today}.jsonl").write_text(
                json.dumps({"ts": "t", "slot": "daily", "source": "llm"}) + "\n",
                encoding="utf-8",
            )
            sched = SoulScheduler()
            with caplog.at_level(logging.INFO, logger=SCHEDULER_LOGGER):
                out = sched._get_recent_shareable_activity("agent_ruka")
            assert out is None  # 0 行為變更
            msgs = _records_for(caplog, SCHEDULER_LOGGER)
            hit = [m for m in msgs if "reason=no_qualifying_entry" in m]
            assert len(hit) == 1, f"G9' 第二條 return None 必須留痕: {msgs}"
            _assert_all_bounded(msgs)
        finally:
            _restore_data_root()

    def test_c5_g9_prime_exception_path_still_logged(self, tmp_path, caplog):
        """第三條 return None（例外）本來就有 warning → 三條全部可歸因。

        以「非法 UTF-8 位元組」強制 read_text(encoding='utf-8') 拋 UnicodeDecodeError
        （跨平台可行，不依賴 chmod 語意）。
        """
        _isolated_data_root(tmp_path)
        try:
            soul_dir = data_root() / "soul" / "agent_ruka" / "diary"
            soul_dir.mkdir(parents=True, exist_ok=True)
            today = datetime.now().strftime("%Y-%m-%d")
            (soul_dir / f"{today}.jsonl").write_bytes(b"\xff\xfe\x00\x00bad\xff")
            sched = SoulScheduler()
            with caplog.at_level(logging.INFO, logger=SCHEDULER_LOGGER):
                out = sched._get_recent_shareable_activity("agent_ruka")
            assert out is None  # 0 行為變更（例外仍 fail-safe 回 None）
            msgs = _records_for(caplog, SCHEDULER_LOGGER)
            assert any("讀 diary 失敗" in m for m in msgs), f"例外路徑必須留痕: {msgs}"
            _assert_all_bounded(msgs)
        finally:
            _restore_data_root()

    @pytest.mark.parametrize("reason_kind", ["no_activity", "dedup_same_ts"])
    def test_c6_g9_enrichment_skip_is_attributable(self, reason_kind, caplog):
        """G9：原本「沒帶活動」完全無 log → 去重 vs 真沒活動不可區分。"""
        sched = _deterministic_scheduler()
        published: List[Any] = []

        async def _stub_publish(agent_id, trigger_type="proactive_dm", extra=None):
            published.append((agent_id, trigger_type, extra))

        sched._publish_agency_trigger = _stub_publish  # type: ignore[method-assign]

        if reason_kind == "no_activity":
            sched._get_recent_shareable_activity = lambda a: None  # type: ignore[method-assign]
        else:
            activity = {"activity": "工作", "ts": "T1"}
            sched._get_recent_shareable_activity = lambda a: dict(activity)  # type: ignore[method-assign]
            sched._last_shared_activity_ts["agent_ruka"] = "T1"  # 同一 ts → 去重

        with caplog.at_level(logging.INFO, logger=SCHEDULER_LOGGER):
            asyncio.run(sched._fire_proactive_dm())

        msgs = _records_for(caplog, SCHEDULER_LOGGER)
        hit = [m for m in msgs if f"reason={reason_kind}" in m and "gate=G9" in m]
        assert len(hit) == 1, f"G9 未帶活動必須可歸因 ({reason_kind}): {msgs}"
        _assert_all_bounded(msgs)

    def test_c7_g9_zero_behaviour_change_extra_unchanged(self, caplog):
        """0 行為變更：G9 命中時, publish 收到的 extra 與改動前一致（仍帶活動）。"""
        sched = _deterministic_scheduler()
        published: List[Any] = []

        async def _stub_publish(agent_id, trigger_type="proactive_dm", extra=None):
            published.append(extra)

        sched._publish_agency_trigger = _stub_publish  # type: ignore[method-assign]
        activity = {"activity": "烘焙", "category": "life", "content": "c", "ts": "T2"}
        sched._get_recent_shareable_activity = lambda a: activity  # type: ignore[method-assign]

        with caplog.at_level(logging.INFO, logger=SCHEDULER_LOGGER):
            asyncio.run(sched._fire_proactive_dm())

        assert published == [{"trigger_source": "activity", "activity": activity}]
        # 去重狀態照常寫入（既有副作用不變）
        assert sched._last_shared_activity_ts["agent_ruka"] == "T2"


# ────────────────────────────────────────────────────────────────────
# 【D】[SM-3 Decision] 四行：各自可被觸發且被捕捉
# ────────────────────────────────────────────────────────────────────

def _seed_pending_motive(motive_trace_path: Path, agent_id: str) -> str:
    motive_trace_path.parent.mkdir(parents=True, exist_ok=True)
    mid = new_motive_id()
    record = {
        "motive_id": mid,
        "agent_id": agent_id,
        "status": "pending",
        "content": "我想告訴你今天的事",
        "target": "bryan",
        "provenance_ref": "evt_obs1",
        "created_at": now_utc_iso(),
        "updated_at": now_utc_iso(),
    }
    with open(motive_trace_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return mid


def _decision_result(motive, decision: str) -> DecisionResult:
    return DecisionResult(
        decision=decision,
        transmit=(decision == "transmit"),
        reason="obs1-reason",
        motive_id=motive.motive_id,
        motive_content=motive.content,
        provenance_ref=motive.provenance_ref,
    )


class TestDSM3DecisionLines:
    """【D】四行各自可被觸發且被捕捉（caplog）→ 證明 logging 配置本身沒問題。"""

    def test_d1_no_pending_motive_line(self, isolated_root, monkeypatch, caplog):
        sched = SoulScheduler(bus=None)
        with caplog.at_level(logging.INFO, logger=SCHEDULER_LOGGER):
            returned = asyncio.run(sched._decision_check("agent_yua"))

        assert returned is False  # 0 行為變更（fail-closed F1）
        msgs = _records_for(caplog, SCHEDULER_LOGGER)
        hit = [m for m in msgs if "[SM-3 Decision]" in m and "pending motive" in m]
        assert len(hit) == 1, f"D1 行必須被觸發且被捕捉: {msgs}"
        assert "F1" in hit[0]
        _assert_all_bounded(msgs)

    def test_d2_transmit_line(self, isolated_root, monkeypatch, caplog):
        motive_trace = isolated_root / "soul" / "motive_trace.jsonl"
        _seed_pending_motive(motive_trace, "agent_yua")

        async def _fake_decide(self, motive, agent_id, *a, **kw):
            return _decision_result(motive, "transmit")

        monkeypatch.setattr(MotiveEngine, "decide", _fake_decide)
        sched = SoulScheduler(bus=None)
        with caplog.at_level(logging.INFO, logger=SCHEDULER_LOGGER):
            returned = asyncio.run(sched._decision_check("agent_yua"))

        assert returned is True  # 0 行為變更（只有 transmit 放行）
        msgs = _records_for(caplog, SCHEDULER_LOGGER)
        hit = [m for m in msgs if "[SM-3 Decision]" in m and "transmit motive=" in m]
        assert len(hit) == 1, f"D2 (transmit) 行必須被捕捉: {msgs}"
        _assert_all_bounded(msgs)

    def test_d3_not_transmit_line(self, isolated_root, monkeypatch, caplog):
        motive_trace = isolated_root / "soul" / "motive_trace.jsonl"
        _seed_pending_motive(motive_trace, "agent_yua")

        async def _fake_decide(self, motive, agent_id, *a, **kw):
            return _decision_result(motive, "reflect")

        monkeypatch.setattr(MotiveEngine, "decide", _fake_decide)
        sched = SoulScheduler(bus=None)
        with caplog.at_level(logging.INFO, logger=SCHEDULER_LOGGER):
            returned = asyncio.run(sched._decision_check("agent_yua"))

        assert returned is False  # 0 行為變更
        msgs = _records_for(caplog, SCHEDULER_LOGGER)
        hit = [m for m in msgs if "[SM-3 Decision]" in m and "not_transmit motive=" in m]
        assert len(hit) == 1, f"D3 (not_transmit) 行必須被捕捉: {msgs}"
        _assert_all_bounded(msgs)

    def test_d4_exception_line(self, isolated_root, monkeypatch, caplog):
        motive_trace = isolated_root / "soul" / "motive_trace.jsonl"
        _seed_pending_motive(motive_trace, "agent_yua")

        async def _boom(self, motive, agent_id, *a, **kw):
            raise RuntimeError("obs1-boom-" + "Q" * 3000)

        monkeypatch.setattr(MotiveEngine, "decide", _boom)
        sched = SoulScheduler(bus=None)
        with caplog.at_level(logging.INFO, logger=SCHEDULER_LOGGER):
            returned = asyncio.run(sched._decision_check("agent_yua"))

        assert returned is False  # fail-closed：任何異常都不放行
        msgs = _records_for(caplog, SCHEDULER_LOGGER)
        hit = [m for m in msgs if "[SM-3 Decision]" in m and "exception" in m]
        assert len(hit) == 1, f"D4 (exception) 行必須被捕捉: {msgs}"
        assert caplog.records[-1].levelno == logging.WARNING
        _assert_all_bounded(msgs)

    def test_d5_four_lines_exist_at_info_or_warning_in_source(self):
        """四行逐一在原始碼中以可落盤級別存在（INFO ×3 + WARNING ×1）。"""
        src = (ROOT / "src/soul/scheduler.py").read_text(encoding="utf-8")
        assert "[SM-3 Decision] {agent_id} 无 pending motive" in src
        assert "[SM-3 Decision] {agent_id} transmit motive=" in src
        assert "[SM-3 Decision] {agent_id} not_transmit motive=" in src
        assert "[SM-3 Decision] {agent_id} exception (fail-closed = skip)" in src
        # 四行皆非 debug（debug 級在生產 INFO 日誌不落盤）
        for needle in ("无 pending motive", "transmit motive=", "not_transmit motive="):
            idx = src.index(needle)
            window = src[max(0, idx - 260):idx]
            assert "logger.debug" not in window, f"{needle} 不得為 debug 級"

    def test_d6_root_cause_g5_precedes_publish(self):
        """🔴 合法變更（DELIVERABILITY-RELEASE-1, Owner 裁定 B, 2026-09-13）。

        本測試原本釘死「根因：G5（4h 可送達檢查）在 _fire_proactive_dm 內先於
        publish，故 G5 阻擋時 `_publish_agency_trigger`（含 `_inner_life_gate_check`
        與 `_decision_check`）根本不會被執行 → `[SM-3 Decision]` 該窗口 0 筆是
        『路徑未走到』而非 logging 配置故障」。

        Owner 已裁定放寬 4h 硬阻斷 → **該根因已消失**（G5 不再 early-return），
        因此本測試依裁定**改釘新不變量**（不是放寬斷言，是把釘子移到新的真相上）：

          1. G5 仍**先於** publish（量測與放行痕跡的位置不變），
             但**區塊內不得有任何 return**（新語意的核心）。
          2. G3／G4 的阻擋痕跡仍先於 publish（頻率閘門未被連帶放寬）。
          3. publish 之前仍不得出現任何 `[SM-3 Decision]`（SM-3 只在 publish 內部）。
        """
        src = (ROOT / "src/soul/scheduler.py").read_text(encoding="utf-8")
        import ast as _ast

        tree = _ast.parse(src)
        fn = next(
            n
            for n in _ast.walk(tree)
            if isinstance(n, _ast.AsyncFunctionDef) and n.name == "_fire_proactive_dm"
        )
        # 以 ast.unparse 取得「無註解」的函式本體 —— 字串索引在含註解的原文上
        # 會誤命中註解裡提到的 gate 名稱（本票已在註解中記載 G3/G4/G5/G7b）。
        body = _ast.unparse(fn)
        g5_trace = body.index("gate=G5")
        publish = body.index("_publish_agency_trigger")
        assert g5_trace < publish, "G5 放行痕跡必須在 publish 之前（量測位置不變）"
        # G3／G4 仍先於 publish（既有阻擋語意未被本票連帶放寬）
        assert body.index("gate=G3") < publish
        assert body.index("gate=G4") < publish
        # publish 之前不得有任何 [SM-3 Decision] 行（SM-3 只在 publish 內部）
        assert "[SM-3 Decision]" not in body[:publish]
        # 🔴 新語意（AST 級）：G5 的 if 區塊內不得有任何 return（否則又變回硬阻斷）
        g5_ifs = [
            n
            for n in _ast.walk(fn)
            if isinstance(n, _ast.If)
            and "PROACTIVE_DM_BRYAN_INACTIVE_HOURS" in (_ast.get_source_segment(src, n) or "")
        ]
        assert len(g5_ifs) == 1, f"G5 的 if 區塊應恰好 1 個, 實際 {len(g5_ifs)}"
        assert not any(isinstance(n, _ast.Return) for n in _ast.walk(g5_ifs[0])), (
            "G5 不得再 early-return（DELIVERABILITY-RELEASE-1 已放寬 4h 硬阻斷）"
        )


# ────────────────────────────────────────────────────────────────────
# 通用驗收：有界 helper identity / 0 frozen contract
# ────────────────────────────────────────────────────────────────────

class TestBoundedAndFrozenDiscipline:
    def test_scheduler_bounded_helper_identity(self):
        from src.soul.scheduler import _bounded_log

        short = "[M7-2] proactive_dm 未帶活動: agent_ruka reason=no_activity (gate=G9)"
        assert _bounded_log(short) == short
        assert len(_bounded_log("B" * 1000)) == BOUNDED_LIMIT

    def test_gate_bounded_helper_identity(self):
        from src.agency.inner_life_gate import _bounded_line

        short = "[M5.8-4 Inner Life Gate] decision=emitted agent=agent_ruka reason=x"
        assert _bounded_line(short) == short
        assert len(_bounded_line("C" * 1000)) == BOUNDED_LIMIT

    def test_no_frozen_contract_file_touched(self):
        """0 frozen contract：本票只動 3 個非 frozen 檔 + 本測試檔。"""
        import subprocess

        out = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        ).stdout
        changed = {ln.strip() for ln in out.splitlines() if ln.strip()}
        allowed = {
            "src/io/channels/router.py",
            "src/soul/scheduler.py",
            "src/agency/inner_life_gate.py",
        }
        frozen_dirs = (
            "src/agency/stages.py",
            "src/agency/trigger_handler.py",
            "src/agency/event_handler.py",
            "src/agency/dream_handler.py",
            "src/agency/diary_handler.py",
            "src/inner_life/submission_gate.py",
            "src/social/identity_firewall.py",
            "src/social/producer_gate.py",
            "src/world/middleware.py",
            "src/memory/sage/writer.py",
            "src/soul/decision.py",
            "src/inner_life/elevation_adapter.py",
        )
        for f in frozen_dirs:
            assert f not in changed, f"不得改動 frozen contract / out-of-scope 檔: {f}"
        src_changes = {c for c in changed if c.startswith("src/")}
        assert src_changes <= allowed, f"src/ 改動超出許可集: {src_changes - allowed}"
