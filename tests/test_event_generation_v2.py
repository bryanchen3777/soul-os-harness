"""
test_event_generation_v2.py
β2.1 Mock Test 2 (修法驗證):
  觸發 agent_akane heartbeat, 確認事件背景生成 + 注入正確。
  預期全部 4 個 assert PASS:
    1. AGENT_INTENT_ENRICHED payload 有 'event' 跟 'event_meta' 欄位
    2. Mock LLM 收到事件生成 call (event_gen_call_count == 1)
    3. data/events/{date}.jsonl 有 1 行 JSONL 紀錄
    4. LLMProxy._handle_event_impl 把 event 注入 system prompt (有 [當下事件] 區塊)

執行:
  python tests/test_event_generation_v2.py
"""
import asyncio
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.eventbus import SoulEventBus
from src.llm.proxy import LLMBackend, LLMProxy
from src.memory.middleware import MemoryMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("soul_os.test.event_gen_v2")


# ─────────────────────────────────────────────
# 1. Mock LLM Backend
# ─────────────────────────────────────────────

class MockLLMBackend(LLMBackend):
    """
    Spy LLM:
    - 事件生成 call (system prompt 含「世界觀敘事者」) → 回 canned_event
    - 角色訊息生成 call → 回 canned_response
    """

    def __init__(
        self,
        canned_event: str = (
            "[場所:茶水間] [對象:無人] [情緒:平靜] 排練完剛回到大廳, 杯子還溫著"
        ),
        canned_response: str = "[MOCK-ROLE] ……在喔。",
    ):
        self.canned_event = canned_event
        self.canned_response = canned_response
        self.call_count = 0
        self.event_gen_call_count = 0
        self.role_gen_call_count = 0
        self.received_messages: List[List[Dict[str, str]]] = []
        self._event_call_signature = "世界觀敘事者"

    async def complete(
        self,
        messages: List[Dict[str, str]],
        model: str,
        max_tokens: int,
        temperature: float,
        **kwargs,
    ) -> str:
        self.call_count += 1
        self.received_messages.append(list(messages))
        sys_content = next(
            (m["content"] for m in messages if m["role"] == "system"),
            "",
        )
        if self._event_call_signature in sys_content:
            self.event_gen_call_count += 1
            return self.canned_event
        self.role_gen_call_count += 1
        return self.canned_response


# ─────────────────────────────────────────────
# 2. Enriched Event Capture
# ─────────────────────────────────────────────

class EnrichedCapture:
    """Spy: 記錄所有 AGENT_INTENT_ENRICHED 事件."""

    def __init__(self, bus: SoulEventBus):
        self.bus = bus
        self.captured: List[SoulEvent] = []

    def register(self) -> None:
        self.bus.subscribe(
            subscriber_id="enriched_capture",
            handler=self._handle,
            event_filter={EventType.AGENT_INTENT_ENRICHED},
        )

    async def _handle(self, event: SoulEvent) -> None:
        self.captured.append(event)
        logger.info(
            f"  [EnrichedCapture] 收到 | agent={event.payload.get('agent_id')} | "
            f"reason={event.payload.get('reason')} | "
            f"event={event.payload.get('event', '<none>')[:30]!r}"
        )


# ─────────────────────────────────────────────
# 3. 主測試 — Part A: middleware 層驗證
# ─────────────────────────────────────────────

async def test_middleware_event_gen(
    tmp_dir: Path,
    events_dir: Path,
) -> Dict[str, Any]:
    """
    β2.1 Mock Test 2 Part A: middleware 層
    觸發 agent_akane heartbeat → middleware 應生成事件 + 寫檔 + 注入 payload
    """
    logger.info("=" * 60)
    logger.info("  β2.1 Mock Test 2 Part A: middleware 層驗證")
    logger.info("=" * 60)

    bus = SoulEventBus()
    data_dir = tmp_dir / "memory"
    data_dir.mkdir(parents=True, exist_ok=True)

    mock_backend = MockLLMBackend()
    llm_proxy = LLMProxy(bus=bus, backend=mock_backend, model="mock-model")

    # 修法: 傳 llm_proxy + events_dir
    memory = MemoryMiddleware(
        bus=bus,
        data_dir=str(data_dir),
        llm_proxy=llm_proxy,
        events_dir=str(events_dir),
    )
    capture = EnrichedCapture(bus)

    memory.register()
    capture.register()
    await bus.start()

    try:
        logger.info(
            "\n── Step 1: 發 AGENT_INTENT (agent_akane + heartbeat) ──"
        )
        await bus.publish(SoulEvent(
            event_type=EventType.AGENT_INTENT,
            source="agent_akane",
            target="broadcast",
            priority=EventPriority.NORMAL,
            payload={
                "agent_id": "agent_akane",
                "reason": "heartbeat",
                "draft": "……在喔。",
                "mood": 0.0,
                "mode": "group",
                "target_user_id": "bryan",
            },
            session_id="session_test_b21_v2",
        ))
        # 給 middleware 處理時間 (prefetch + 事件生成 LLM call + re-publish)
        await asyncio.sleep(1.0)

        logger.info("\n── 斷言 ──")
        # 斷言 1: AGENT_INTENT_ENRICHED 收到 + payload 有 event/event_meta
        assert len(capture.captured) >= 1, "AGENT_INTENT_ENRICHED 沒被發布"
        enriched = capture.captured[0]
        payload = enriched.payload
        assert payload.get("agent_id") == "agent_akane"
        assert payload.get("reason") == "heartbeat"
        assert "event" in payload, (
            f"payload 應該有 'event' 欄位, 實際 keys: "
            f"{sorted(payload.keys())}"
        )
        assert "event_meta" in payload, (
            f"payload 應該有 'event_meta' 欄位, 實際 keys: "
            f"{sorted(payload.keys())}"
        )
        # event 內容應該是 mock backend 回的 canned_event
        assert payload["event"] == mock_backend.canned_event, (
            f"event 內容不符 | 預期 {mock_backend.canned_event!r}, "
            f"實際 {payload['event']!r}"
        )
        # event_meta 應該是 dict 帶必要欄位
        meta = payload["event_meta"]
        assert isinstance(meta, dict), f"event_meta 應該是 dict, 實際 {type(meta)}"
        assert "generated_at" in meta
        assert meta.get("agent_id") == "agent_akane"
        assert meta.get("reason") == "heartbeat"
        logger.info(
            f"  ✓ enriched payload 有 event (len={len(payload['event'])}) + event_meta"
        )

        # 斷言 2: mock LLM 收到事件生成 call 1 次
        assert mock_backend.event_gen_call_count == 1, (
            f"事件生成 call 預期 1 次, "
            f"實際 {mock_backend.event_gen_call_count} 次"
        )
        logger.info(
            f"  ✓ mock LLM event_gen_call_count={mock_backend.event_gen_call_count}"
        )

        # 斷言 3: data/events/{date}.jsonl 有 1 行
        today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        events_file = events_dir / f"{today}.jsonl"
        assert events_file.exists(), f"{events_file} 應該存在"
        with open(events_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 1, (
            f"{events_file} 預期 1 行, 實際 {len(lines)} 行"
        )
        log_entry = json.loads(lines[0])
        assert log_entry["agent_id"] == "agent_akane"
        assert log_entry["reason"] == "heartbeat"
        assert log_entry["content"] == mock_backend.canned_event
        logger.info(
            f"  ✓ {events_file.name} 有 1 行, content={log_entry['content'][:30]!r}"
        )

        return {
            "enriched_count": len(capture.captured),
            "event_text": payload["event"],
            "event_meta": meta,
            "event_gen_call_count": mock_backend.event_gen_call_count,
            "events_file": str(events_file),
            "log_entry": log_entry,
            "events_dir": str(events_dir),
            "data_dir": str(data_dir),
            "mock_backend": mock_backend,
        }
    finally:
        await bus.stop()
        memory.shutdown()


# ─────────────────────────────────────────────
# 4. Part B: LLMProxy 注入驗證
# ─────────────────────────────────────────────

async def test_llmproxy_injection(
    mock_backend: MockLLMBackend,
    event_text: str,
) -> Dict[str, Any]:
    """
    β2.1 Mock Test 2 Part B: LLMProxy 注入驗證
    直接構造帶 event 的 SoulEvent, call _handle_event_impl,
    確認 LLMProxy 把 event 注入 system prompt 給 LLM.
    """
    logger.info("\n" + "=" * 60)
    logger.info("  β2.1 Mock Test 2 Part B: LLMProxy 注入驗證")
    logger.info("=" * 60)

    bus = SoulEventBus()
    llm_proxy = LLMProxy(bus=bus, backend=mock_backend, model="mock-model")
    await bus.start()

    try:
        # 構造 SoulEvent, payload 帶 event (模擬 middleware 注入後)
        event = SoulEvent(
            event_type=EventType.AGENT_INTENT_ENRICHED,
            source="agent_akane",
            target="broadcast",
            priority=EventPriority.NORMAL,
            payload={
                "agent_id": "agent_akane",
                "reason": "heartbeat",
                "draft": "……在喔。",
                "mood": 0.0,
                "mode": "group",
                "target_user_id": "bryan",
                "event": event_text,  # middleware 注入
                "event_meta": {
                    "generated_at": "2026-08-02T13:00:00+00:00",
                    "agent_id": "agent_akane",
                    "reason": "heartbeat",
                    "model": "mock-model",
                },
                "memory_context": "",  # 避免 RAG 干擾
            },
            session_id="session_test_b21_v2_partB",
        )

        # 直接 call _handle_event_impl (不走 SPEAKER_TOKEN_GRANTED 鏈條, 簡化)
        await llm_proxy._handle_event_impl(event)
        # 等 mock backend 收 messages
        await asyncio.sleep(0.2)

        # 找最新一次 (role 生成) call 的 messages
        assert len(mock_backend.received_messages) >= 1, (
            "LLMProxy 應該有 call mock backend"
        )
        last_messages = mock_backend.received_messages[-1]
        sys_contents = [
            m["content"] for m in last_messages if m["role"] == "system"
        ]
        all_sys = "\n".join(sys_contents)
        assert "[當下事件]" in all_sys, (
            f"system prompt 應該有 [當下事件] 區塊, 實際 sys content:\n{all_sys[:500]}"
        )
        assert event_text in all_sys, (
            f"system prompt 應該包含 event 內容 {event_text!r}, "
            f"實際 sys content:\n{all_sys[:500]}"
        )
        logger.info(
            f"  ✓ LLMProxy 收到 {len(last_messages)} 條 messages, "
            f"system 含 [當下事件] 區塊 + event 內容"
        )

        return {
            "llm_messages_count": len(last_messages),
            "sys_has_event_block": "[當下事件]" in all_sys,
            "sys_has_event_text": event_text in all_sys,
        }
    finally:
        await bus.stop()


# ─────────────────────────────────────────────
# 5. Acceptance
# ─────────────────────────────────────────────

def assert_v2_acceptance(
    part_a: Dict[str, Any],
    part_b: Dict[str, Any],
) -> None:
    errors: List[str] = []
    if part_a["enriched_count"] < 1:
        errors.append(
            f"[Part A enriched] 預期 ≥1, 實際 {part_a['enriched_count']}"
        )
    if not part_a["event_text"]:
        errors.append("[Part A event_text] 預期非空, 實際空")
    if part_a["event_gen_call_count"] != 1:
        errors.append(
            f"[Part A 事件生成 call] 預期 1, "
            f"實際 {part_a['event_gen_call_count']}"
        )
    if not part_a["log_entry"]:
        errors.append("[Part A log_entry] 預期非空, 實際空")
    if not part_b["sys_has_event_block"]:
        errors.append(
            "[Part B system prompt] 應該有 [當下事件] 區塊, 沒有"
        )
    if not part_b["sys_has_event_text"]:
        errors.append(
            f"[Part B system prompt] 應該含 event 內容, 沒有"
        )
    if errors:
        raise AssertionError(
            "β2.1 Mock Test 2 失敗:\n" + "\n".join(f"  - {e}" for e in errors)
        )


# ─────────────────────────────────────────────
# 6. Main
# ─────────────────────────────────────────────

async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_dir = Path(tmp_str)
        # events_dir 用 tmp_dir 子目錄, 避免污染真實 data/events/
        events_dir = tmp_dir / "events"
        events_dir.mkdir(parents=True, exist_ok=True)

        part_a = await test_middleware_event_gen(tmp_dir, events_dir)
        part_b = await test_llmproxy_injection(
            part_a["mock_backend"],
            part_a["event_text"],
        )
        assert_v2_acceptance(part_a, part_b)
        logger.info(
            "\n✅ β2.1 Mock Test 2 (修法) PASS"
        )
        logger.info(
            "   全部 4 個 assert 通過: "
            "enriched 有 event, event_gen call 1 次, "
            "data/events/ 有 1 行, LLMProxy 注入 [當下事件] 區塊"
        )


if __name__ == "__main__":
    asyncio.run(main())
