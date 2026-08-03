"""
test_event_generation_v1.py
β2.1 Mock Test 1 (現狀驗證):
  觸發 agent_akane heartbeat, 確認 LLM 沒有事件脈絡。
  預期全部 3 個 assert PASS (現狀就是這樣, 這是 baseline):
    1. AGENT_INTENT_ENRICHED payload 沒有 'event' / 'event_meta' 欄位
    2. Mock LLM 沒被觸發事件生成 call (event_gen_call_count == 0)
    3. data/events/{date}.jsonl 不存在

執行:
  python tests/test_event_generation_v1.py
"""
import asyncio
import logging
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
logger = logging.getLogger("soul_os.test.event_gen_v1")


# ─────────────────────────────────────────────
# 1. Mock LLM Backend
# ─────────────────────────────────────────────

class MockLLMBackend(LLMBackend):
    """
    Spy LLM: 記錄所有收到的 messages。
    區分事件生成 call 跟角色訊息生成 call: 事件生成 call 的 system prompt
    會包含「世界觀敘事者」(見 β2.1 設計的事件生成 prompt)。
    """

    def __init__(
        self,
        canned_event: str = "[MOCK-EVENT]",
        canned_response: str = "[MOCK] 收到！",
    ):
        self.canned_event = canned_event
        self.canned_response = canned_response
        self.call_count = 0
        self.event_gen_call_count = 0
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
            f"reason={event.payload.get('reason')}"
        )


# ─────────────────────────────────────────────
# 3. 主測試
# ─────────────────────────────────────────────

async def test_event_gen_v1(tmp_dir: Path) -> Dict[str, Any]:
    """
    β2.1 Mock Test 1 (現狀):
    觸發 agent_akane heartbeat, 確認 LLM 沒有事件脈絡。
    """
    logger.info("=" * 60)
    logger.info("  β2.1 Mock Test 1: 現狀驗證 (無事件生成)")
    logger.info("=" * 60)

    bus = SoulEventBus()
    data_dir = tmp_dir / "memory"
    data_dir.mkdir(parents=True, exist_ok=True)

    mock_backend = MockLLMBackend(
        canned_event="[場所:茶水間] [情緒:平靜] 排練完剛回到大廳",
        canned_response="……在喔。",
    )
    llm_proxy = LLMProxy(bus=bus, backend=mock_backend, model="mock-model")

    # 現狀: MemoryMiddleware 還沒接受 llm_proxy 參數 (這就是 baseline)
    memory = MemoryMiddleware(bus=bus, data_dir=str(data_dir))
    capture = EnrichedCapture(bus)

    memory.register()
    capture.register()
    await bus.start()

    try:
        logger.info("\n── Step 1: 發 AGENT_INTENT (agent_akane + heartbeat) ──")
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
            session_id="session_test_b21_v1",
        ))
        # 給 MemoryMiddleware 處理時間 (prefetch + re-publish)
        await asyncio.sleep(0.5)

        logger.info("\n── 斷言 ──")
        assert len(capture.captured) >= 1, "AGENT_INTENT_ENRICHED 沒被發布"
        enriched = capture.captured[0]
        payload = enriched.payload
        assert payload.get("agent_id") == "agent_akane"
        assert payload.get("reason") == "heartbeat"
        # 核心斷言: 現狀下 payload 沒有 event 欄位
        assert "event" not in payload, (
            f"現狀不該有 event 欄位, 但 payload 有: {list(payload.keys())}"
        )
        assert "event_meta" not in payload, (
            f"現狀不該有 event_meta 欄位, payload keys: {list(payload.keys())}"
        )
        # mock LLM 也沒被觸發事件生成 call
        assert mock_backend.event_gen_call_count == 0, (
            f"現狀不該觸發事件生成 call, "
            f"但有 {mock_backend.event_gen_call_count} 次"
        )

        # 現狀下 data/events/ 應該不存在或為空
        today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        events_file = Path("data/events") / f"{today}.jsonl"
        # 因為這是 test 環境, 我們也檢查 tmp_dir/events (如果 middleware 寫到那邊)
        tmp_events_file = tmp_dir / "events" / f"{today}.jsonl"
        if events_file.exists():
            assert events_file.stat().st_size == 0, (
                f"現狀下 {events_file} 應該是空的"
            )
        if tmp_events_file.exists():
            assert tmp_events_file.stat().st_size == 0, (
                f"現狀下 {tmp_events_file} 應該是空的"
            )
        logger.info(
            f"  ✓ 現狀無 event 欄位 ({len(payload)} payload keys: "
            f"{sorted(payload.keys())})"
        )
        logger.info(
            f"  ✓ mock LLM event_gen_call_count={mock_backend.event_gen_call_count} (預期 0)"
        )
        logger.info(
            f"  ✓ data/events/{today}.jsonl 不存在或為空"
        )

        return {
            "enriched_count": len(capture.captured),
            "enriched_payload_keys": sorted(list(payload.keys())),
            "event_gen_call_count": mock_backend.event_gen_call_count,
            "data_events_exists": events_file.exists() or tmp_events_file.exists(),
        }
    finally:
        await bus.stop()
        memory.shutdown()


def assert_v1_acceptance(result: Dict[str, Any]) -> None:
    errors: List[str] = []
    if result["enriched_count"] < 1:
        errors.append(f"[enriched] 預期 ≥1, 實際 {result['enriched_count']}")
    if "event" in result["enriched_payload_keys"]:
        errors.append(
            f"[enriched payload] 不該有 'event' key, 實際 keys: "
            f"{result['enriched_payload_keys']}"
        )
    if "event_meta" in result["enriched_payload_keys"]:
        errors.append(
            f"[enriched payload] 不該有 'event_meta' key, 實際 keys: "
            f"{result['enriched_payload_keys']}"
        )
    if result["event_gen_call_count"] != 0:
        errors.append(
            f"[事件生成 call] 預期 0, 實際 {result['event_gen_call_count']}"
        )
    if result["data_events_exists"]:
        errors.append(
            "[data/events/] 現狀不該存在, 但存在了"
        )
    if errors:
        raise AssertionError(
            "β2.1 Mock Test 1 失敗:\n" + "\n".join(f"  - {e}" for e in errors)
        )


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp_dir = Path(tmp_str)
        result = await test_event_gen_v1(tmp_dir)
        assert_v1_acceptance(result)
        logger.info("\n✅ β2.1 Mock Test 1 (現狀) PASS")
        logger.info("   全部 3 個 assert 通過: 無 event 欄位, 無事件生成, 無 data/events/")


if __name__ == "__main__":
    asyncio.run(main())
