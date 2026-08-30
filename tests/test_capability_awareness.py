"""
tests/test_capability_awareness.py — CA-2 Soul Capability Awareness Implementation

照 docs/SOUL-CAPABILITY-AWARENESS-DESIGN.md（CA-1）驗收：
  1. capability.py 落地（CAPABILITY_DEFINITIONS，id + expression，v1 只 communicate）。
  2. proxy.py 兩處 CAPABILITY block 投影（identity 之後、emergent 之前）。
  3. sidecar 可觀測性（append-only trace，對齊 emergent projection 模式）。
  4. 回歸測試（Q5 斷言）：capability 存在與否 → Agency 4-stage 輸出逐字段相等
     （證明 Capability 只 expand action space，不 select action）。
  5. 刪除全部 capability 定義 → scheduler / permission / agency 行為逐字節不變。
  6. 不碰 frozen contract（stages.py 不新增 capability 參數，Stage 2 不讀 capability）。
"""
import json
import os
import sys
import tempfile
import unittest
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agency.stages import (
    check_eligibility,
    execute_action_stub,
    make_decision,
    select_action,
)
from src.agency.state import AgencyState
from src.agency.trigger import TriggerEnvelope
from src.llm.proxy import _build_messages_group, _build_messages_private
from src.soul.capability import (
    CAPABILITY_DEFINITIONS,
    CAPABILITY_TRACE_FILENAME,
    CapabilityDefinition,
    format_capability_block,
)
from src.work.roles import Role, has_capability


def _fake_memory() -> MagicMock:
    return MagicMock()


def _run_agency_4_stages() -> dict:
    """固定輸入跑完整 4-stage pipeline，回傳逐字段輸出 dict（Q5 比較基準）。"""
    now = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)
    state = AgencyState()
    trigger = TriggerEnvelope(
        trigger_type="proactive_dm",
        agent_id="agent_yua",
        reason="scheduler.proactive_dm",
        elapsed_mins=30.0,
        timestamp=now,
    )
    s1 = check_eligibility(state, now)
    s2 = make_decision(s1, None, state, now, trigger)
    s3 = select_action(s2.decision_type)
    s4 = execute_action_stub(s3)
    return {
        "stage1_eligibility": asdict(s1),
        "stage2_decision": asdict(s2),
        "stage3_action_type": s3,
        "stage4_execution": asdict(s4),
    }


class TestCapabilityDefinition(unittest.TestCase):
    """Q1/Q2: capability.py 落地 — CAPABILITY_DEFINITIONS schema"""

    def test_01_definitions_exist_and_v1_only_communicate(self):
        self.assertIsInstance(CAPABILITY_DEFINITIONS, dict)
        self.assertEqual(set(CAPABILITY_DEFINITIONS.keys()), {"communicate"})

    def test_02_each_entry_is_capability_definition_with_id_and_expression(self):
        for key, cap in CAPABILITY_DEFINITIONS.items():
            self.assertIsInstance(cap, CapabilityDefinition)
            self.assertEqual(cap.id, key)
            self.assertIsInstance(cap.expression, str)
            self.assertTrue(cap.expression.strip())

    def test_03_expression_states_can_not_should(self):
        """措辭原則: 陳述「能」(can), 不陳述「應」(should)"""
        for cap in CAPABILITY_DEFINITIONS.values():
            self.assertIn("可以", cap.expression)
            self.assertNotIn("应该", cap.expression)
            self.assertNotIn("應", cap.expression)

    def test_04_communicate_anchors_proactive_message(self):
        self.assertIn("proactive_message", CAPABILITY_DEFINITIONS["communicate"].expression)


class TestProxyProjection(unittest.TestCase):
    """Q3: proxy.py 兩處 CAPABILITY block 投影（identity 之後、emergent 之前）"""

    def setUp(self):
        # 隔離 data_root：capability sidecar 寫進 temp，emergent 讀不到節點 → 確定性
        from src.paths import reset_data_root

        self._tmp = tempfile.TemporaryDirectory()
        self._old_env = os.environ.get("SOUL_OS_DATA_DIR")
        os.environ["SOUL_OS_DATA_DIR"] = self._tmp.name
        reset_data_root()
        self.soul = "# 角色設定\n你是一個有靈魂的角色, 跟 Bry 互動。\n"
        self.memory = _fake_memory()

    def tearDown(self):
        from src.paths import reset_data_root

        reset_data_root()
        if self._old_env is None:
            os.environ.pop("SOUL_OS_DATA_DIR", None)
        else:
            os.environ["SOUL_OS_DATA_DIR"] = self._old_env
        self._tmp.cleanup()

    def _sys_content(self, messages) -> str:
        sys_msgs = [m for m in messages if m["role"] == "system"]
        self.assertEqual(len(sys_msgs), 1)
        return sys_msgs[0]["content"]

    def test_01_group_injects_capability_block(self):
        messages = _build_messages_group(
            agent_id="agent_yua", soul=self.soul, current_input="",
            memory_context="", memory=self.memory,
        )
        content = self._sys_content(messages)
        self.assertIn("[CAPABILITY]", content)
        self.assertIn("proactive_message", content)

    def test_02_private_injects_capability_block(self):
        messages = _build_messages_private(
            agent_id="agent_yua", soul=self.soul, current_input="",
            memory_context="", memory=self.memory,
        )
        content = self._sys_content(messages)
        self.assertIn("[CAPABILITY]", content)
        self.assertIn("proactive_message", content)

    def test_03_block_position_identity_before_capability_before_emergent(self):
        """語義邊界: IDENTITY → CAPABILITY → EMERGENT"""
        messages = _build_messages_group(
            agent_id="agent_yua", soul=self.soul, current_input="",
            memory_context="", memory=self.memory,
        )
        content = self._sys_content(messages)
        soul_pos = content.find("你是一個有靈魂的角色")
        cap_pos = content.find("[CAPABILITY]")
        emergent_pos = content.find("[EMERGENT]")
        self.assertGreater(cap_pos, soul_pos,
                           f"CAPABILITY 應在 identity 之後, 實際 cap_pos={cap_pos}, soul_pos={soul_pos}")
        if emergent_pos != -1:
            self.assertLess(cap_pos, emergent_pos,
                            f"CAPABILITY 應在 EMERGENT 之前, 實際 cap_pos={cap_pos}, emergent_pos={emergent_pos}")

    def test_04_private_block_position_identity_before_capability(self):
        messages = _build_messages_private(
            agent_id="agent_yua", soul=self.soul, current_input="",
            memory_context="", memory=self.memory,
        )
        content = self._sys_content(messages)
        soul_pos = content.find("你是一個有靈魂的角色")
        cap_pos = content.find("[CAPABILITY]")
        self.assertGreater(cap_pos, soul_pos)

    def test_05_fail_silent_when_definitions_empty(self):
        """刪除全部 capability 定義 → 無 [CAPABILITY] block（fail-silent）"""
        with patch("src.soul.capability.CAPABILITY_DEFINITIONS", {}):
            messages = _build_messages_group(
                agent_id="agent_yua", soul=self.soul, current_input="",
                memory_context="", memory=self.memory,
            )
        content = self._sys_content(messages)
        self.assertNotIn("[CAPABILITY]", content)

    def test_06_fail_silent_when_projection_raises(self):
        """投影函數拋異常 → 空字串，prompt 照常組裝（雙重失敗隔離）"""
        with patch("src.soul.capability.format_capability_block", side_effect=RuntimeError("boom")):
            messages = _build_messages_private(
                agent_id="agent_yua", soul=self.soul, current_input="",
                memory_context="", memory=self.memory,
            )
        content = self._sys_content(messages)
        self.assertNotIn("[CAPABILITY]", content)
        self.assertIn("你是一個有靈魂的角色", content)


class TestSidecarObservability(unittest.TestCase):
    """可觀測性: 每次投影記錄 append-only sidecar（對齊 emergent projection 模式）"""

    def test_01_format_capability_block_records_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            block = format_capability_block("agent_yua", store_dir=tmp)
            self.assertIn("[CAPABILITY]", block)
            self.assertIn("proactive_message", block)
            trace_path = Path(tmp) / CAPABILITY_TRACE_FILENAME
            self.assertTrue(trace_path.exists(), "投影後應有 sidecar 檔案")
            lines = trace_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            self.assertEqual(record["event_type"], "capability_projected")
            self.assertEqual(record["agent_id"], "agent_yua")
            self.assertEqual(record["projected_capability_ids"], ["communicate"])
            self.assertEqual(record["capability_count"], 1)

    def test_02_trace_is_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            format_capability_block("agent_yua", store_dir=tmp)
            format_capability_block("agent_ruka", store_dir=tmp)
            trace_path = Path(tmp) / CAPABILITY_TRACE_FILENAME
            lines = trace_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[1])["agent_id"], "agent_ruka")

    def test_03_proxy_projection_records_trace_to_data_root(self):
        from src.paths import reset_data_root

        with tempfile.TemporaryDirectory() as tmp:
            old_env = os.environ.get("SOUL_OS_DATA_DIR")
            os.environ["SOUL_OS_DATA_DIR"] = tmp
            reset_data_root()
            try:
                _build_messages_group(
                    agent_id="agent_yua", soul="# 角色設定\nsoul\n",
                    current_input="", memory_context="", memory=_fake_memory(),
                )
            finally:
                reset_data_root()
                if old_env is None:
                    os.environ.pop("SOUL_OS_DATA_DIR", None)
                else:
                    os.environ["SOUL_OS_DATA_DIR"] = old_env
            trace_path = Path(tmp) / "soul" / CAPABILITY_TRACE_FILENAME
            self.assertTrue(trace_path.exists(), "proxy 投影應經 data_root()/soul 記錄 sidecar")
            record = json.loads(trace_path.read_text(encoding="utf-8").strip().splitlines()[0])
            self.assertEqual(record["projected_capability_ids"], ["communicate"])


class TestQ5AgencyRegression(unittest.TestCase):
    """Q5 斷言: capability 存在與否 → Agency 4-stage 輸出逐字段相等"""

    def test_01_agency_4_stage_output_identical_without_capability(self):
        with_cap = _run_agency_4_stages()
        with patch("src.soul.capability.CAPABILITY_DEFINITIONS", {}):
            without_cap = _run_agency_4_stages()
        self.assertEqual(
            with_cap, without_cap,
            "Q5: capability 存在與否 → 4-stage 輸出必須逐字段相等 "
            "(Capability 只 expand action space, 不 select action)",
        )

    def test_02_agency_4_stage_output_identical_with_extra_capability(self):
        """反向: 新增第二個 capability 也不改變 4-stage 輸出"""
        from src.soul.capability import CapabilityDefinition as CD

        with_cap = _run_agency_4_stages()
        extra = {
            "communicate": CD(id="communicate", expression="你可以主动给 Bryan 发消息。"),
            "dream": CD(id="dream", expression="你可以做梦。"),
        }
        with patch("src.soul.capability.CAPABILITY_DEFINITIONS", extra):
            with_extra = _run_agency_4_stages()
        self.assertEqual(with_cap, with_extra)

    def test_03_scheduler_bridge_trigger_envelope_unchanged(self):
        """scheduler → Agency bridge (TriggerEnvelope.from_payload) 逐字節不變"""
        payload = {
            "trigger_type": "proactive_dm",
            "agent_id": "agent_yua",
            "reason": "scheduler.proactive_dm",
            "elapsed_mins": 30.0,
            "timestamp": "2026-08-20T12:00:00+00:00",
            "extra": {"last_proactive_ts": 123},
        }
        with_cap = TriggerEnvelope.from_payload(payload)
        with patch("src.soul.capability.CAPABILITY_DEFINITIONS", {}):
            without_cap = TriggerEnvelope.from_payload(payload)
        self.assertIsNotNone(with_cap)
        self.assertEqual(asdict(with_cap), asdict(without_cap))

    def test_04_permission_gate_unchanged(self):
        """permission (roles.has_capability) 逐字節不變"""
        cases = [
            (Role.DEVELOPER, "test.execute"),
            (Role.RESEARCHER, "research"),
            (Role.CHIEF, "orchestration"),
            (Role.HUMAN, "approval"),
        ]
        with_cap = [has_capability(r, c) for r, c in cases]
        with patch("src.soul.capability.CAPABILITY_DEFINITIONS", {}):
            without_cap = [has_capability(r, c) for r, c in cases]
        self.assertEqual(with_cap, without_cap)

    def test_05_frozen_contract_stages_do_not_read_capability(self):
        """frozen contract guard: stages.py 不讀 capability（Stage 2 不讀來 YES）"""
        import inspect

        from src.agency import stages

        src_text = inspect.getsource(stages)
        self.assertNotIn("src.soul.capability", src_text)
        self.assertNotIn("CAPABILITY_DEFINITIONS", src_text)
        # make_decision 簽名不新增 capability 參數
        sig = inspect.signature(make_decision)
        self.assertNotIn("capability", sig.parameters)

    def test_06_frozen_contract_roles_do_not_import_soul_capability(self):
        """Q7 隔離: src/work/roles.py 不引用 src.soul.capability"""
        import inspect

        from src.work import roles

        src_text = inspect.getsource(roles)
        self.assertNotIn("src.soul.capability", src_text)
        self.assertNotIn("CAPABILITY_DEFINITIONS", src_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
