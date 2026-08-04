"""
test_stale_filter_v1.py — 修法 7 baseline: proxy.py 沒過濾 stale Bry user 訊息

Bry 拍板 2026-08-04 13:24:
- 根因: 凌晨 07:07-07:56 EDT 多角色主動傳訊, reason=proactive_dm / heartbeat, user_msg='',
  Bry 還在睡覺 (Bry 上次跟 mahiru 私訊 08-02 07:17 = 3.5 天前, group session Bry 上一條 8/4 02:48 UTC
  = 8 小時前)
- LLM 組裝時, group/private history 內 Bry user 訊息是 stale (幾小時甚至幾天前),
  但被當成「Bry 剛說的話」餵進 role=user, LLM 看到就「回覆 X」
- 修法 7 方向: 從源頭過濾 stale Bry user 訊息 (N=30 分鐘, 跟 proactive_dm silence_timeout 對齊)
- 範圍: 只動 proxy.py 的 _build_messages_group / _build_messages_private (Bry 派工原話列的 4 個
  function 中, _load_group / _load_private 純讀 JSON 沒 timestamp, 過濾邏輯必須在 _build_* 內做
  用 memory.db 的 timestamp)

這個 v1 驗證現狀 (before 修法 7):
- _build_messages_group / _build_messages_private 沒有過濾 stale Bry user 訊息
- 修法前 stale Bry 訊息照樣被注入 messages 陣列
- proxy.py 沒有 STALE_THRESHOLD_SEC 常數

Mock 範圍:
- mock memory.get_group_history 回傳 20 條, 內含 1 條 Bry 在 4h42m 前, 其他角色訊息 fresh
- mock memory.get_recent 回傳 10 條, 內含 1 條 Bry 在 3.5 天前, 其他角色訊息 fresh
- 模擬 8/4 07:30 EDT (Bry 還在睡覺, Bry 不在線)
- 直接呼叫 _build_messages_group / _build_messages_private, 驗證 stale Bry 訊息沒被過濾
- 修法前 v1 期望: stale Bry 訊息照樣出現在 messages 陣列 (沒過濾)
- 修法後 v2 期望: stale Bry 訊息被過濾掉 (Bry 不在線)
"""
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm import proxy


# 模擬 8/4 07:30 EDT (= 11:30 UTC)
# 為了 mock test 簡化, 用 now = 1_700_000_000 當作一個固定時間點
NOW = 1_700_000_000
# 4h42m 前 (4*3600 + 42*60 = 16920 秒)
BRY_GROUP_STALE_TS = NOW - 16920  # group 內 Bry 訊息時間
# 3.5 天前 (3.5 * 86400 = 302400 秒)
BRY_PRIVATE_STALE_TS = NOW - 302400  # private 內 Bry 訊息時間
# 5 分鐘前 (5*60 = 300 秒), agent_yua 訊息
AGENT_FRESH_TS = NOW - 300


def _make_group_history() -> list:
    """模擬 miku 8/4 07:30 觸發時 group history 的 20 條
    包含 1 條 Bry 8/4 02:48 訊息 (4h42m 前, stale)
    + 1 條 agent_yua 8/4 07:25 訊息 (5 分鐘前, fresh)
    + 其他角色訊息
    """
    history = []
    # Bry 4h42m 前一條 stale 訊息 (模擬 8/4 02:48 Bry 真實講過一句)
    history.append({
        "role": "user", "speaker": "bryan", "content": "Bry 8/4 02:48 講的話 (stale)",
        "timestamp": BRY_GROUP_STALE_TS, "is_private": False,
    })
    # 1 條 agent_yua 5 分鐘前 fresh 訊息
    history.append({
        "role": "assistant", "speaker": "agent_yua",
        "content": "yua 8/4 07:25 講的話 (fresh)",
        "timestamp": AGENT_FRESH_TS, "is_private": False,
    })
    # 填滿 20 條 (其他角色 fake 訊息)
    for i in range(18):
        history.append({
            "role": "assistant", "speaker": "agent_other",
            "content": f"other agent msg {i}",
            "timestamp": NOW - 600 - i * 30, "is_private": False,
        })
    return history


def _make_private_history() -> list:
    """模擬 mahiru 8/4 07:30 觸發時 private history 的 10 條
    包含 1 條 Bry 8/2 07:17 訊息 (3.5 天前, 嚴重 stale)
    + 1 條 agent_mahiru 8/4 07:25 訊息 (5 分鐘前, fresh)
    """
    history = []
    # Bry 3.5 天前一條 stale 訊息 (模擬 8/2 07:17 Bry 真實講過一句)
    history.append({
        "role": "user", "content": "Bry 8/2 07:17 講的話 (stale)",
        "timestamp": BRY_PRIVATE_STALE_TS,
    })
    # 1 條 agent_mahiru 5 分鐘前 fresh 訊息
    history.append({
        "role": "assistant", "content": "mahiru 8/4 07:25 講的話 (fresh)",
        "timestamp": AGENT_FRESH_TS,
    })
    # 填滿 10 條
    for i in range(8):
        history.append({
            "role": "assistant", "content": f"mahiru msg {i}",
            "timestamp": NOW - 700 - i * 40,
        })
    return history


class TestStaleFilterBaseline(unittest.TestCase):
    """驗證現狀 (before 修法 7) — proxy.py 沒過濾 stale Bry user 訊息"""

    def setUp(self):
        self.fake_group_history = _make_group_history()
        self.fake_private_history = _make_private_history()
        self.soul_text = "你是 Miku。... (Miku 的人格設定) ..."

    def test_a_baseline_group_stale_bry_injected(self):
        """Baseline: group history 內 stale Bry 訊息照樣被注入 (沒過濾)
        模擬 miku 8/4 07:30 觸發, Bry 4h42m 前講過一句
        LLM 看到 stale Bry 訊息可能誤以為 Bry 剛講的 → 回應跟現實脫節
        """
        with patch.object(proxy, "_load_bry_recent", return_value=[]), \
             patch.object(proxy, "MAX_GROUP", 20), \
             patch("time.time", return_value=NOW):
            fake_memory = MagicMock()
            fake_memory.get_group_history.return_value = self.fake_group_history

            messages = proxy._build_messages_group(
                agent_id="agent_miku",
                soul=self.soul_text,
                current_input="",  # proactive 觸發
                memory_context="",
                memory=fake_memory,
                mood=0.0,
                user_id="bryan",
                current_time="2026-08-04 07:30 (EDT)",
            )

            # 找 user role 訊息 (Bry 訊息)
            user_msgs = [m for m in messages if m["role"] == "user"]
            # Baseline 期望: stale Bry 訊息照樣被注入 (沒過濾)
            stale_bry_found = any(
                "Bry 8/4 02:48 講的話 (stale)" in m.get("content", "")
                for m in user_msgs
            )
            self.assertTrue(
                stale_bry_found,
                "Baseline (v1) 期望 group 內 stale Bry 訊息照樣被注入, 實際 user msgs: "
                f"{[m['content'][:50] for m in user_msgs]}"
            )
            print(f"[v1 baseline] group 內 stale Bry 訊息照樣被注入 (Bry 不在線, 沒過濾)")

    def test_b_baseline_private_stale_bry_injected(self):
        """Baseline: private history 內 stale Bry 訊息照樣被注入 (沒過濾)
        模擬 mahiru 8/4 07:30 觸發, Bry 3.5 天前講過一句
        LLM 看到 stale Bry 訊息可能誤以為 Bry 剛講的 → 回應跟現實脫節
        """
        with patch("time.time", return_value=NOW):
            fake_memory = MagicMock()
            # 修法前用 get_recent (不含 timestamp)
            fake_memory.get_recent.return_value = [
                {"role": m["role"], "content": m["content"]}
                for m in self.fake_private_history
            ]

            messages = proxy._build_messages_private(
                agent_id="agent_mahiru",
                soul=self.soul_text,
                current_input="",  # proactive 觸發
                memory_context="",
                memory=fake_memory,
                mood=0.0,
                user_id="bryan",
                current_time="2026-08-04 07:30 (EDT)",
            )

            # 找 user role 訊息 (Bry 訊息)
            user_msgs = [m for m in messages if m["role"] == "user"]
            # Baseline 期望: stale Bry 訊息照樣被注入 (沒過濾)
            stale_bry_found = any(
                "Bry 8/2 07:17 講的話 (stale)" in m.get("content", "")
                for m in user_msgs
            )
            self.assertTrue(
                stale_bry_found,
                "Baseline (v1) 期望 private 內 stale Bry 訊息照樣被注入, 實際 user msgs: "
                f"{[m['content'][:50] for m in user_msgs]}"
            )
            print(f"[v1 baseline] private 內 stale Bry 訊息照樣被注入 (Bry 3.5 天沒說話, 沒過濾)")

    def test_c_baseline_proxy_has_no_stale_threshold_constant(self):
        """Baseline: proxy 沒有 STALE_THRESHOLD_SEC 常數"""
        self.assertFalse(
            hasattr(proxy, "STALE_THRESHOLD_SEC"),
            "Baseline (v1) 期望 proxy 沒有 STALE_THRESHOLD_SEC 常數, 修法後才加"
        )
        print(f"[v1 baseline] proxy 沒有 STALE_THRESHOLD_SEC 常數")

    def test_d_baseline_proxy_source_no_stale_filter(self):
        """Baseline: proxy.py source 內沒有 _is_bry_online 或 stale 過濾邏輯"""
        proxy_source = Path(proxy.__file__).read_text(encoding="utf-8")
        # 修法前不應該有 stale 過濾相關代碼
        self.assertNotIn(
            "_is_bry_online", proxy_source,
            "Baseline (v1) 期望 proxy 沒有 _is_bry_online helper, 修法後才加"
        )
        self.assertNotIn(
            "STALE_THRESHOLD_SEC", proxy_source,
            "Baseline (v1) 期望 proxy 沒有 STALE_THRESHOLD_SEC 常數, 修法後才加"
        )
        print(f"[v1 baseline] proxy 沒有 _is_bry_online / STALE_THRESHOLD_SEC")


if __name__ == "__main__":
    unittest.main(verbosity=2)
