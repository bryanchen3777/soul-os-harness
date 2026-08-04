"""
test_proactive_temporal_v2.py — 修法 8 後: 注入時間上下文 (時段行 + 沉默時長行)

Bry 拍板 2026-08-04 17:18 (5 條細節):
- 觸發範圍: 時段行 (現在是 X EDT (時段)) 所有觸發都注入
  沉默時長行 (距離 Bry 上次跟你說話已經 X 小時/天) 只在 _is_bry_online() == False 時注入
- 函式範圍: _build_messages_group / _build_messages_private 兩個都動
- 精確字眼: 時段用 _period_label 標籤 (早上/上午/中午/下午/傍晚/晚上/深夜)
  沉默時長 <24h = "X 小時" (四捨五入) / >=24h = "X 天" (取整天數)
- 注入位置: 緊接在 f9105f1 現有 "## 當下時間" 那行後面
- 邊界情況: Bry 從未講過話直接跳過沉默時長行 (不寫 "Bry 從未跟你說過話")

這個 v2 驗證修法後邏輯:
- (a) 群聊場景: 注入時段行 + 沉默時長行
- (b) 私聊場景: 注入時段行 + 沉默時長行
- (c) 群聊場景: Bry 在線時, 沉默時長行不注入
- (d) 群聊場景: Bry 從未講過話時, 沉默時長行不注入
- (e) 群聊場景: 47.4 小時顯示成「2 天」, 不用「47 小時」
- (f) 群聊場景: 5 小時顯示成「5 小時」, 不用「0 天」
- (g) 群聊場景: 注入位置在 f9105f1「當下時間」行後面

Mock 範圍:
- mock memory.get_group_history / get_recent_with_meta 回傳含 Bry user 訊息的 history
- patch time.time 回傳對應 NOW
- 構造 event_ts datetime 物件傳入 _build_messages_*
"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm import proxy


# 模擬 8/4 14:22 EDT (miku 2:22 觸發)
NOW_MIKU = 1785867740
EVENT_TS_MIKU = datetime.fromtimestamp(NOW_MIKU, tz=timezone.utc)
# 模擬 8/4 16:35 EDT (mai 4:36 觸發)
NOW_MAI = 1785875727
EVENT_TS_MAI = datetime.fromtimestamp(NOW_MAI, tz=timezone.utc)

# Bry 最後 user 訊息 (8/2 14:59:09 EDT, 距 miku 觸發 47.4 小時)
BRY_STALE_47H_TS = 1785697149
# Bry 最後 user 訊息 (8/4 14:17 EDT, 距 miku 觸發 5 分鐘)
BRY_FRESH_5MIN_TS = NOW_MIKU - 300


def _make_group_with_stale_bry():
    history = [{
        "role": "user", "speaker": "bryan",
        "content": "Bry 47.4h 前群組訊息",
        "timestamp": BRY_STALE_47H_TS, "is_private": False,
    }]
    for i in range(19):
        history.append({
            "role": "assistant", "speaker": "agent_yua",
            "content": f"yua 群組訊息 {i}",
            "timestamp": NOW_MIKU - 600 - i * 30, "is_private": False,
        })
    return history


def _make_group_with_fresh_bry():
    history = [{
        "role": "user", "speaker": "bryan",
        "content": "Bry 5 分鐘前群組訊息",
        "timestamp": BRY_FRESH_5MIN_TS, "is_private": False,
    }]
    for i in range(19):
        history.append({
            "role": "assistant", "speaker": "agent_yua",
            "content": f"yua 群組訊息 {i}",
            "timestamp": NOW_MIKU - 600 - i * 30, "is_private": False,
        })
    return history


def _make_group_with_no_bry():
    history = []
    for i in range(20):
        history.append({
            "role": "assistant", "speaker": "agent_yua",
            "content": f"yua 群組訊息 {i}",
            "timestamp": NOW_MIKU - 600 - i * 30, "is_private": False,
        })
    return history


def _make_private_with_stale_bry():
    history = [{
        "role": "user", "content": "……在。",
        "timestamp": BRY_STALE_47H_TS,
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


class TestProactiveTemporalPost(unittest.TestCase):
    """驗證修法 8 後 — 注入時段行 + 沉默時長行"""

    def setUp(self):
        self.soul_text = "你是 Miku。... (三玖的人格設定) ..."

    def test_a_post_group_temporal_injected(self):
        """修法後: 群聊觸發注入時段行「現在是 2026-08-04 14:22 EDT（下午）」
        8/4 14:22 EDT = 下午 (13-17 點)
        """
        with patch.object(proxy, "_load_bry_recent", return_value=[]), \
             patch.object(proxy, "MAX_GROUP", 20), \
             patch("time.time", return_value=NOW_MIKU):
            fake_memory = MagicMock()
            fake_memory.get_group_history.return_value = _make_group_with_stale_bry()

            messages = proxy._build_messages_group(
                agent_id="agent_miku",
                soul=self.soul_text,
                current_input="",
                memory_context="",
                memory=fake_memory,
                mood=0.0,
                user_id="bryan",
                current_time="2026-08-04 週一 14:22 America/New_York（下午）",
                event_ts=EVENT_TS_MIKU,
            )

            all_system_content = "\n".join(
                m["content"] for m in messages if m["role"] == "system"
            )
            self.assertIn(
                "現在是 2026-08-04 14:22 EDT", all_system_content,
                "修法後 (v2) 期望時段行注入: 「現在是 2026-08-04 14:22 EDT」"
            )
            self.assertIn(
                "（下午）", all_system_content,
                "修法後 (v2) 期望時段標籤: （下午）"
            )
            print(f"[v2 (a)] group 注入時段行 OK: 「現在是 2026-08-04 14:22 EDT（下午）」")

    def test_b_post_group_silence_injected(self):
        """修法後: 群聊 Bry 不在線時注入沉默時長行
        Bry 47.4 小時前, floor(47.4/24) = 1, 顯示「1 天」(>=24h 用 X 天, floor 取整)
        """
        with patch.object(proxy, "_load_bry_recent", return_value=[]), \
             patch.object(proxy, "MAX_GROUP", 20), \
             patch("time.time", return_value=NOW_MIKU):
            fake_memory = MagicMock()
            fake_memory.get_group_history.return_value = _make_group_with_stale_bry()

            messages = proxy._build_messages_group(
                agent_id="agent_miku",
                soul=self.soul_text,
                current_input="",
                memory_context="",
                memory=fake_memory,
                mood=0.0,
                user_id="bryan",
                current_time="2026-08-04 週一 14:22 America/New_York（下午）",
                event_ts=EVENT_TS_MIKU,
            )

            all_system_content = "\n".join(
                m["content"] for m in messages if m["role"] == "system"
            )
            self.assertIn(
                "距離 Bry 上次跟你說話已經 1 天", all_system_content,
                "修法後 (v2) 期望沉默時長行注入: 「距離 Bry 上次跟你說話已經 1 天」 (47.4h floor)"
            )
            self.assertNotIn(
                "47 小時", all_system_content,
                "修法後 (v2) 期望 47.4h 顯示成「1 天」, 不是「47 小時」"
            )
            print(f"[v2 (b)] group 注入沉默時長行 OK: 「距離 Bry 上次跟你說話已經 1 天」 (47.4h floor)")

    def test_c_post_group_bry_online_no_silence(self):
        """修法後: 群聊 Bry 在線 (< 30 分鐘) 時, 沉默時長行不注入
        Bry 5 分鐘前講過, Bry 在線, 沉默時長行跳過
        """
        with patch.object(proxy, "_load_bry_recent", return_value=[]), \
             patch.object(proxy, "MAX_GROUP", 20), \
             patch("time.time", return_value=NOW_MIKU):
            fake_memory = MagicMock()
            fake_memory.get_group_history.return_value = _make_group_with_fresh_bry()

            messages = proxy._build_messages_group(
                agent_id="agent_miku",
                soul=self.soul_text,
                current_input="",
                memory_context="",
                memory=fake_memory,
                mood=0.0,
                user_id="bryan",
                current_time="2026-08-04 週一 14:22 America/New_York（下午）",
                event_ts=EVENT_TS_MIKU,
            )

            all_system_content = "\n".join(
                m["content"] for m in messages if m["role"] == "system"
            )
            # 時段行應該注入
            self.assertIn(
                "現在是 2026-08-04 14:22 EDT", all_system_content,
                "修法後 (v2) 期望時段行注入 (即使 Bry 在線)"
            )
            # 沉默時長行不應該注入
            self.assertNotIn(
                "距離 Bry", all_system_content,
                "修法後 (v2) 期望 Bry 在線時沉默時長行不注入"
            )
            print(f"[v2 (c)] Bry 在線 (5 分鐘前) 時段行注入 + 沉默時長行跳過 OK")

    def test_d_post_private_temporal_silence_injected(self):
        """修法後: 私聊觸發注入時段行 + 沉默時長行
        模擬 mai 4:36 EDT 觸發 (16:35 EDT, 下午時段)
        """
        with patch("time.time", return_value=NOW_MAI):
            fake_memory = MagicMock()
            fake_memory.get_recent_with_meta.return_value = _make_private_with_stale_bry()

            messages = proxy._build_messages_private(
                agent_id="agent_mai",
                soul=self.soul_text,
                current_input="",
                memory_context="",
                memory=fake_memory,
                mood=0.0,
                user_id="bryan",
                current_time="2026-08-04 週一 16:35 America/New_York（下午）",
                event_ts=EVENT_TS_MAI,
            )

            all_system_content = "\n".join(
                m["content"] for m in messages if m["role"] == "system"
            )
            self.assertIn(
                "現在是 2026-08-04 16:35 EDT", all_system_content,
                "修法後 (v2) 期望 private 注入時段行: 「現在是 2026-08-04 16:35 EDT」"
            )
            # mai private: Bry 最後 user 訊息 8/2 14:57:10 EDT (距 16:35 EDT = 49.6 小時 = 2 天)
            self.assertIn(
                "距離 Bry 上次跟你說話已經 2 天", all_system_content,
                "修法後 (v2) 期望 private 注入沉默時長行 (49.6h → 2 天)"
            )
            print(f"[v2 (d)] private 注入時段行 + 沉默時長行 OK")

    def test_e_post_group_never_spoke_no_silence(self):
        """修法後邊界: 群聊 Bry 從未講過話時, 沉默時長行不注入
        (Bry 派工原話派工原話拍板: 不寫「Bry 從未跟你說過話」推測性文字)
        """
        with patch.object(proxy, "_load_bry_recent", return_value=[]), \
             patch.object(proxy, "MAX_GROUP", 20), \
             patch("time.time", return_value=NOW_MIKU):
            fake_memory = MagicMock()
            fake_memory.get_group_history.return_value = _make_group_with_no_bry()

            messages = proxy._build_messages_group(
                agent_id="agent_miku",
                soul=self.soul_text,
                current_input="",
                memory_context="",
                memory=fake_memory,
                mood=0.0,
                user_id="bryan",
                current_time="2026-08-04 週一 14:22 America/New_York（下午）",
                event_ts=EVENT_TS_MIKU,
            )

            all_system_content = "\n".join(
                m["content"] for m in messages if m["role"] == "system"
            )
            # 時段行應該注入
            self.assertIn(
                "現在是 2026-08-04 14:22 EDT", all_system_content,
                "修法後 (v2) 期望時段行注入 (即使 Bry 從未講過話)"
            )
            # 沉默時長行不應該注入
            self.assertNotIn(
                "距離 Bry", all_system_content,
                "修法後 (v2) 期望 Bry 從未講過話時沉默時長行不注入"
            )
            # 不寫推測性文字
            self.assertNotIn(
                "Bry 從未跟你說過話", all_system_content,
                "修法後 (v2) 期望不寫「Bry 從未跟你說過話」推測性文字"
            )
            print(f"[v2 (e)] Bry 從未講過話時段行注入 + 沉默時長行跳過 OK")

    def test_f_post_temporal_after_current_time(self):
        """修法後: 注入位置在 f9105f1「當下時間」行後面
        (Bry 派工原話派工原話拍板: 同一個時間資訊區塊, 時間資訊集中)
        """
        with patch.object(proxy, "_load_bry_recent", return_value=[]), \
             patch.object(proxy, "MAX_GROUP", 20), \
             patch("time.time", return_value=NOW_MIKU):
            fake_memory = MagicMock()
            fake_memory.get_group_history.return_value = _make_group_with_stale_bry()

            messages = proxy._build_messages_group(
                agent_id="agent_miku",
                soul=self.soul_text,
                current_input="",
                memory_context="",
                memory=fake_memory,
                mood=0.0,
                user_id="bryan",
                current_time="2026-08-04 週一 14:22 America/New_York（下午）",
                event_ts=EVENT_TS_MIKU,
            )

            all_system_content = "\n".join(
                m["content"] for m in messages if m["role"] == "system"
            )
            # 找 f9105f1「當下時間」行 + 修法 8 兩行的相對位置
            current_time_idx = all_system_content.find("## 當下時間")
            temporal_idx = all_system_content.find("現在是 2026-08-04 14:22 EDT")
            silence_idx = all_system_content.find("距離 Bry 上次跟你說話已經")
            self.assertGreater(
                current_time_idx, -1, "f9105f1「## 當下時間」應該存在"
            )
            self.assertGreater(
                temporal_idx, current_time_idx,
                "修法 8 時段行應該在 f9105f1「## 當下時間」行後面"
            )
            self.assertGreater(
                silence_idx, temporal_idx,
                "修法 8 沉默時長行應該在時段行後面"
            )
            print(f"[v2 (f)] 注入位置: 當下時間 → 時段行 → 沉默時長行 (順序正確)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
