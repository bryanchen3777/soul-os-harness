"""
test_proactive_temporal_v1.py — 修法 8 baseline: proxy.py 沒注入 proactive/heartbeat 觸發的時間上下文

Bry 拍板 2026-08-04 17:18 (5 條細節):
- 觸發範圍: 時段行 (現在是 X EDT (時段)) 所有觸發都注入
  沉默時長行 (距離 Bry 上次跟你說話已經 X 小時/天) 只在 _is_bry_online() == False 時注入
- 函式範圍: _build_messages_group / _build_messages_private 兩個都動
- 精確字眼: 時段用 _period_label 標籤 (早上/上午/中午/下午/傍晚/晚上/深夜)
  沉默時長 <24h = "X 小時" (四捨五入) / >=24h = "X 天" (取整天數)
- 注入位置: 緊接在 f9105f1 現有 "## 當下時間" 那行後面
- 邊界情況: Bry 從未講過話直接跳過沉默時長行 (不寫 "Bry 從未跟你說過話")

Bry 拍板背景:
- mai 4:36 EDT 觸發說 "晚安" 配下午 4:35 時段不合理 (缺現在時段資訊)
- miku 2:22 EDT 觸發讀起來像在回應 Bry 47.4 小時前的舊訊息 (缺沉默時長資訊)
- 兩個都不是修法 7 的範圍, 是另一層問題: LLM 不知道現在幾點、不知道沉默多久
- 修法 8 = 注入兩行明確文字, 跟 β2 event background 設計同方向延伸, 不是新機制

這個 v1 驗證現狀 (before 修法 8):
- _build_messages_group / _build_messages_private 沒注入時段行
- 沒注入沉默時長行
- proxy 沒有 _format_temporal_context / _compute_silence_str helper
- proxy source 沒有修法 8 注入邏輯

Mock 範圍:
- 模擬 8/4 14:22 EDT (miku 2:22 觸發時間, NOW=1785867740) + 8/4 16:35 EDT (mai 4:36 觸發時間)
- mock memory.get_group_history / get_recent_with_meta 回傳含 Bry 47.4h 前訊息的 history
- patch time.time 回傳對應 NOW
- 修法前 v1 期望: system prompt 內沒有「現在是」「距離 Bry」字眼
- 修法後 v2 期望: 修法 8 兩行注入
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm import proxy


# miku 14:22 EDT 觸發
NOW_MIKU = 1785867740
# mai 16:35 EDT 觸發
NOW_MAI = 1785875727
# Bry 最後 user 訊息 (8/2 14:59:09 EDT)
BRY_LAST_USER_TS = 1785697149


def _make_group_history_with_stale_bry():
    """miku 2:22 EDT 觸發時 group history 模擬:
    - 1 條 Bry 47.4h 前 user 訊息
    - 19 條其他角色訊息
    """
    history = [{
        "role": "user", "speaker": "bryan",
        "content": "Bry 47.4h 前群組訊息",
        "timestamp": BRY_LAST_USER_TS, "is_private": False,
    }]
    for i in range(19):
        history.append({
            "role": "assistant", "speaker": "agent_yua",
            "content": f"yua 群組訊息 {i}",
            "timestamp": NOW_MIKU - 600 - i * 30, "is_private": False,
        })
    return history


def _make_private_history_with_stale_bry():
    """miku 14:22 EDT 觸發時 private history 模擬:
    - 1 條 Bry 47.4h 前 user 訊息「……在。」
    - 19 條 miku 自己訊息
    """
    history = [{
        "role": "user", "content": "……在。",
        "timestamp": BRY_LAST_USER_TS,
        "speaker": "bryan", "is_private": True,
    }]
    for i, ts in enumerate(
        [1785727384, 1785787850, 1785799555, 1785804695, 1785821174,
         1785833423, 1785844368] + [1785850000 + i * 1000 for i in range(12)]
    ):
        history.append({
            "role": "assistant", "content": f"miku reply {i}",
            "timestamp": ts, "speaker": "agent_miku", "is_private": True,
        })
    return history


class TestProactiveTemporalBaseline(unittest.TestCase):
    """驗證現狀 (before 修法 8) — proxy 沒注入時段行 + 沉默時長行"""

    def setUp(self):
        self.fake_group_history = _make_group_history_with_stale_bry()
        self.fake_private_history = _make_private_history_with_stale_bry()
        self.soul_text = "你是 Miku。... (三玖的人格設定) ..."

    def test_a_baseline_group_no_temporal_line(self):
        """Baseline: _build_messages_group 內 system prompt 沒「現在是」字眼
        f9105f1 注入的 current_time 已經有「（下午）」, 但 Bry 派工原話 example 的明確
        字眼「現在是 X EDT（時段）」是修法 8 額外注入的, 修法前不存在
        """
        with patch.object(proxy, "_load_bry_recent", return_value=[]), \
             patch.object(proxy, "MAX_GROUP", 20), \
             patch("time.time", return_value=NOW_MIKU):
            fake_memory = MagicMock()
            fake_memory.get_group_history.return_value = self.fake_group_history

            messages = proxy._build_messages_group(
                agent_id="agent_miku",
                soul=self.soul_text,
                current_input="",
                memory_context="",
                memory=fake_memory,
                mood=0.0,
                user_id="bryan",
                current_time="2026-08-04 週一 14:22 America/New_York（下午）",
            )

            all_system_content = "\n".join(
                m["content"] for m in messages if m["role"] == "system"
            )
            # 修法 8 才會出現的明確字眼
            self.assertNotIn(
                "現在是", all_system_content,
                "Baseline (v1) 期望 system prompt 沒「現在是」字眼, 修法 8 才注入"
            )
            self.assertNotIn(
                "EDT", all_system_content,
                "Baseline (v1) 期望 system prompt 沒「EDT」縮寫, 修法 8 才注入"
            )
            print(f"[v1 baseline] group 內 system prompt 沒「現在是」/「EDT」字眼")

    def test_b_baseline_group_no_silence_line(self):
        """Baseline: _build_messages_group 內 system prompt 沒「距離 Bry」字眼"""
        with patch.object(proxy, "_load_bry_recent", return_value=[]), \
             patch.object(proxy, "MAX_GROUP", 20), \
             patch("time.time", return_value=NOW_MIKU):
            fake_memory = MagicMock()
            fake_memory.get_group_history.return_value = self.fake_group_history

            messages = proxy._build_messages_group(
                agent_id="agent_miku",
                soul=self.soul_text,
                current_input="",
                memory_context="",
                memory=fake_memory,
                mood=0.0,
                user_id="bryan",
                current_time="2026-08-04 週一 14:22 America/New_York（下午）",
            )

            all_system_content = "\n".join(
                m["content"] for m in messages if m["role"] == "system"
            )
            self.assertNotIn(
                "距離 Bry", all_system_content,
                "Baseline (v1) 期望 system prompt 沒「距離 Bry」字眼, 修法 8 才注入"
            )
            self.assertNotIn(
                "上次跟你說話", all_system_content,
                "Baseline (v1) 期望 system prompt 沒「上次跟你說話」字眼"
            )
            print(f"[v1 baseline] group 內 system prompt 沒「距離 Bry」/「上次跟你說話」")

    def test_c_baseline_private_no_temporal_line(self):
        """Baseline: _build_messages_private 內 system prompt 沒「現在是」字眼"""
        with patch("time.time", return_value=NOW_MAI):
            fake_memory = MagicMock()
            fake_memory.get_recent_with_meta.return_value = self.fake_private_history

            messages = proxy._build_messages_private(
                agent_id="agent_mai",
                soul=self.soul_text,
                current_input="",
                memory_context="",
                memory=fake_memory,
                mood=0.0,
                user_id="bryan",
                current_time="2026-08-04 週一 16:35 America/New_York（下午）",
            )

            all_system_content = "\n".join(
                m["content"] for m in messages if m["role"] == "system"
            )
            self.assertNotIn(
                "現在是", all_system_content,
                "Baseline (v1) 期望 private 內 system prompt 沒「現在是」字眼"
            )
            print(f"[v1 baseline] private 內 system prompt 沒「現在是」字眼")

    def test_d_baseline_no_temporal_helpers(self):
        """Baseline: proxy 沒有 _format_temporal_context / _compute_silence_str helper"""
        self.assertFalse(
            hasattr(proxy, "_format_temporal_context"),
            "Baseline (v1) 期望 proxy 沒有 _format_temporal_context, 修法 8 才加"
        )
        self.assertFalse(
            hasattr(proxy, "_compute_silence_str"),
            "Baseline (v1) 期望 proxy 沒有 _compute_silence_str, 修法 8 才加"
        )
        print(f"[v1 baseline] proxy 沒有 _format_temporal_context / _compute_silence_str")

    def test_e_baseline_proxy_source_no_method_8(self):
        """Baseline: proxy source 沒有修法 8 注入的明確字眼
        注意: proxy.py 內已有「現在是」字眼 (silence_timeout prompt L2467),
        但那不是修法 8 注入的, 修法 8 用更明確的 pattern
        修法 8 注入的 pattern: 「現在是 2026-」+「EDT」+「（時段）」
        修法 8 沉默時長: 「距離 Bry 上次跟你說話已經」
        """
        proxy_source = Path(proxy.__file__).read_text(encoding="utf-8")
        # 修法 8 才會出現的明確字眼 (排除既有 silence_timeout prompt 內的「現在是」)
        self.assertNotIn(
            "現在是 2026", proxy_source,
            "Baseline (v1) 期望 proxy source 沒「現在是 2026」字眼, 修法 8 才加"
        )
        self.assertNotIn(
            "距離 Bry 上次跟你說話已經", proxy_source,
            "Baseline (v1) 期望 proxy source 沒「距離 Bry 上次跟你說話已經」字眼"
        )
        self.assertNotIn(
            "_format_temporal_context", proxy_source,
            "Baseline (v1) 期望 proxy source 沒 _format_temporal_context 函式"
        )
        self.assertNotIn(
            "_compute_silence_str", proxy_source,
            "Baseline (v1) 期望 proxy source 沒 _compute_silence_str 函式"
        )
        print(f"[v1 baseline] proxy source 沒修法 8 注入字眼 (排除既有 silence_timeout 的「現在是」)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
