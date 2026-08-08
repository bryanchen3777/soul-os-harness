"""
tests/test_m3_prompt_integrity.py — M3 Phase 1 P10 Prompt Integrity Audit

Bry 拍板 2026-08-07 20:12 P10:
確認 M3 沒有偷偷改變既有 prompt hierarchy。
實際 dump prompt 確認:
  1. M3 增加的是新 context layer, 不是破壞既有 PromptContext 結構
  2. world_context = "" 時 prompt 與 M3 之前完全等價 (backward compat invariant)
  3. order: Inner Life → World Context → Chrono-Social (Bry 派工示意圖)
     但實際 production order 可能不同, 以真實為準
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm.proxy import _build_messages_group, _build_messages_private


def _fake_chrono() -> str:
    """模擬 consciousness.py 產的 chrono_context 字串"""
    return (
        "[CHRONO_SOCIAL_CONTEXT v2.2]\n"
        "time_period=afternoon\n"
        "silence=2.5h\n"
        "arrival_deviation=normal\n"
        "vulnerability_window=False\n"
        "carryover_worry=0.20\n"
        "attachment_heat=0.30\n"
        "reaction_bias=neutral\n"
        "temporal_salience=low\n"
        "expression_mode=implicit\n"
        "[/CHRONO_SOCIAL_CONTEXT]"
    )


class TestM3PromptIntegrity(unittest.TestCase):
    """P10 hardening: 確認 prompt 結構不破壞既有 hierarchy"""

    def setUp(self):
        self.soul = (
            "# 角色設定\n"
            "你是一個有靈魂的角色, 跟 Bry 互動。\n"
        )
        self.fake_memory = MagicMock()
        self.chrono_block = _fake_chrono()

    def test_01_prompt_with_world_context_adds_layer(self):
        """world_context 不空時, 應新增 [世界感知] 區塊"""
        messages = _build_messages_group(
            agent_id="agent_yua",
            soul=self.soul,
            current_input="",
            memory_context="",
            memory=self.fake_memory,
            world_context="\n[世界感知] 今天下雨了。\n",
        )
        sys_msgs = [m for m in messages if m["role"] == "system"]
        self.assertEqual(len(sys_msgs), 1)
        content = sys_msgs[0]["content"]
        # 確認 [世界感知] 區塊存在
        self.assertIn("[世界感知]", content)
        self.assertIn("今天下雨了", content)
        print(f"[P10] with world_context: prompt 含 [世界感知] 區塊 ✓")

    def test_02_prompt_without_world_context_no_injection(self):
        """world_context = "" 時, 不應該有 [世界感知] 區塊 (backward compat)"""
        messages = _build_messages_group(
            agent_id="agent_yua",
            soul=self.soul,
            current_input="",
            memory_context="",
            memory=self.fake_memory,
            world_context="",  # 沒 world events
        )
        sys_msgs = [m for m in messages if m["role"] == "system"]
        content = sys_msgs[0]["content"]
        self.assertNotIn("[世界感知]", content,
                         f"P10 backward compat: world_context='' 不應注入, 實際: {content[:500]}")
        print(f"[P10] without world_context: 沒 [世界感知] (backward compat ✓)")

    def test_03_prompt_order_world_after_inner_life(self):
        """
        Bry 派工示意圖: Inner Life → World Context → Chrono-Social
        實際 production order: 在 _build_messages_group / _build_messages_private 內:
          system_parts.append(identity_anchor + soul)
          system_parts.append(memory)         (if memory_context)
          system_parts.append(mood)           (if mood)
          system_parts.append(inner_life)     (if inner_life)
          system_parts.append(world_context)  (M3)
          system_parts.append(current_time + temporal)
          system_parts.append(bry_recent)      (group only)

        驗證: world_context 位置在 inner_life 之後, current_time/chrono 之前
        """
        # 製造 inner_life: 寫一個 mock diary jsonl
        import json
        import tempfile
        from datetime import datetime
        with tempfile.TemporaryDirectory() as diary_dir:
            agent_id = "agent_yua_test"
            today = datetime.now().strftime("%Y-%m-%d")
            agent_path = Path(diary_dir) / agent_id / "diary"
            agent_path.mkdir(parents=True, exist_ok=True)
            (agent_path / f"{today}.jsonl").write_text(
                json.dumps({
                    "ts": "2026-08-07T08:00:00+00:00",
                    "slot": "morning",
                    "content": "今天早上喝了咖啡。",
                    "source": "llm",
                }, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            from src.llm import proxy as proxy_mod
            original_dir = proxy_mod.INNER_LIFE_DATA_DIR
            proxy_mod.INNER_LIFE_DATA_DIR = diary_dir
            try:
                from datetime import datetime as _dt
                # 給 current_time 才能看到 [當下時間] 區塊
                messages = _build_messages_group(
                    agent_id=agent_id,
                    soul=self.soul,
                    current_input="",
                    memory_context="",
                    memory=self.fake_memory,
                    world_context="\n[世界感知] 外面下雨了。\n",
                    current_time="2026-08-07 14:30 (afternoon)",
                    event_ts=_dt(2026, 8, 7, 14, 30, 0),
                )
            finally:
                proxy_mod.INNER_LIFE_DATA_DIR = original_dir

        sys_msgs = [m for m in messages if m["role"] == "system"]
        content = sys_msgs[0]["content"]
        # 找 [最近內在生活] 跟 [世界感知] 跟 ## 當下時間 位置
        inner_life_pos = content.find("[最近內在生活]")
        world_pos = content.find("[世界感知]")
        current_time_pos = content.find("## 當下時間")

        # 全部都應存在
        self.assertGreater(inner_life_pos, 0,
                           f"應有 [最近內在生活] 區塊, 實際: {content[:500]}")
        self.assertGreater(world_pos, 0,
                           f"應有 [世界感知] 區塊, 實際: {content[:500]}")
        self.assertGreater(current_time_pos, 0,
                           f"應有 ## 當下時間 區塊, 實際: {content[:500]}")

        # Order: inner_life < world < current_time
        self.assertLess(inner_life_pos, world_pos,
                        f"P10 期望 inner_life 在 world 之前, 實際 inner_life={inner_life_pos}, world={world_pos}")
        self.assertLess(world_pos, current_time_pos,
                        f"P10 期望 world 在 current_time 之前, 實際 world={world_pos}, current_time={current_time_pos}")
        print(f"[P10] order: inner_life({inner_life_pos}) < world({world_pos}) < current_time({current_time_pos}) ✓")

    def test_04_backward_compat_with_and_without_world(self):
        """
        Bry 拍板 P10: world_context = "" 時, prompt 必須與 M3 之前完全等價。
        驗證: 同一個 soul/memory/mood/inner_life/chrono, world="" vs world="X" 差別只有
        world="X" 多一個 [世界感知] 區塊, 其他部分完全一致。
        """
        # 製造 inner_life
        import json
        import tempfile
        from datetime import datetime
        with tempfile.TemporaryDirectory() as diary_dir:
            agent_id = "agent_yua_test"
            today = datetime.now().strftime("%Y-%m-%d")
            agent_path = Path(diary_dir) / agent_id / "diary"
            agent_path.mkdir(parents=True, exist_ok=True)
            (agent_path / f"{today}.jsonl").write_text(
                json.dumps({
                    "ts": "2026-08-07T08:00:00+00:00",
                    "slot": "morning",
                    "content": "今天早上喝了咖啡。",
                    "source": "llm",
                }, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            from src.llm import proxy as proxy_mod
            original_dir = proxy_mod.INNER_LIFE_DATA_DIR
            proxy_mod.INNER_LIFE_DATA_DIR = diary_dir
            try:
                # 沒 world_context
                msgs_no_world = _build_messages_group(
                    agent_id=agent_id, soul=self.soul,
                    current_input="", memory_context="",
                    memory=self.fake_memory, world_context="",
                )
                # 有 world_context
                msgs_with_world = _build_messages_group(
                    agent_id=agent_id, soul=self.soul,
                    current_input="", memory_context="",
                    memory=self.fake_memory, world_context="\n[世界感知] 外面下雨了。\n",
                )
            finally:
                proxy_mod.INNER_LIFE_DATA_DIR = original_dir

        sys_no = [m for m in msgs_no_world if m["role"] == "system"][0]["content"]
        sys_with = [m for m in msgs_with_world if m["role"] == "system"][0]["content"]

        # 1. with_world 必須有 [世界感知], no_world 必須沒有
        self.assertNotIn("[世界感知]", sys_no, "P10: world='' 不應有 [世界感知]")
        self.assertIn("[世界感知]", sys_with, "P10: world='X' 應有 [世界感知]")

        # 2. 移除 [世界感知] 區塊後, 兩者應完全等價 (backward compat)
        # [世界感知] 區塊到下一個區塊 (Bry 最近訊息 或 ## 當下時間) 之前的部分
        # 簡化: 找 [世界感知] 開頭, 找下一個 \n## 或 \n[Bry (group chat) 結尾
        world_start = sys_with.find("\n[世界感知]")
        # 找下一個 [ 或 ## (新區塊開頭)
        next_section = sys_with.find("\n##", world_start)
        if next_section == -1:
            # group chat 會有 \n[Bry 最近訊息]
            next_section = sys_with.find("\n[Bry", world_start)
        if next_section == -1:
            next_section = len(sys_with)
        sys_with_no_world_block = sys_with[:world_start] + sys_with[next_section:]

        self.assertEqual(
            sys_no, sys_with_no_world_block,
            f"P10 backward compat 失敗: world='' 跟 world='X' (去掉 [世界感知] 區塊後) 應等價"
        )
        print(f"[P10] backward compat: world='' ≡ world='X' (去掉 world 區塊後) ✓")

    def test_05_private_chat_also_supports_world_context(self):
        """_build_messages_private (私聊) 也要支援 world_context, 跟 group 一致"""
        messages = _build_messages_private(
            agent_id="agent_yua",
            soul=self.soul,
            current_input="",
            memory_context="",
            memory=self.fake_memory,
            world_context="\n[世界感知] 私聊感知到。\n",
        )
        sys_msgs = [m for m in messages if m["role"] == "system"]
        content = sys_msgs[0]["content"]
        self.assertIn("[世界感知]", content)
        self.assertIn("私聊感知到", content)

        # 沒 world_context 時
        messages_empty = _build_messages_private(
            agent_id="agent_yua",
            soul=self.soul,
            current_input="",
            memory_context="",
            memory=self.fake_memory,
            world_context="",
        )
        sys_msgs_empty = [m for m in messages_empty if m["role"] == "system"]
        self.assertNotIn("[世界感知]", sys_msgs_empty[0]["content"])
        print(f"[P10] private chat: world_context 有/無 都正確處理 ✓")


if __name__ == "__main__":
    unittest.main(verbosity=2)
