"""
test_stale_filter_v1.py — 修法 7+9 現況契約: proxy.py 有過濾 stale Bry user 訊息

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

這個 v1 驗證現況 (2026-10-12 校準後 = 修法 7+9 現況):
- _build_messages_group / _build_messages_private **有**過濾 stale Bry user 訊息
- proxy.py 有 STALE_THRESHOLD_SEC (= 30*60) 與 _is_bry_online helper, 且真的被呼叫

2026-10-12 校準: 本檔原為「修法 7 之前」baseline (斷言過濾**不存在**), 修法早已落地。
production 正確, 測試過期 —— 校準為驗證現況契約 (不刪測試), 並**強化**:
不只斷言 helper 存在, 還斷言 (a) 具體常數值, (b) Bry 不在線時 Bry 訊息真的被丟掉,
(c) Bry 在線 / reason=user_message 時 Bry 訊息**保留** — 只丟 Bry 一個會被三種實作都滿足,
真正能擋住「把過濾拿掉」的revert 的是 (b)+(c) 的組合。

Mock 範圍:
- mock memory.get_group_history 回傳 20 條, 內含 1 條 Bry 在 4h42m 前, 其他角色訊息 fresh
- mock memory.get_recent_with_meta 回傳 10 條 (修法 7 改成帶 timestamp 的 method, proxy.py L1467)
- 模擬 8/4 07:30 EDT (Bry 還在睡覺, Bry 不在線)
- 直接呼叫 _build_messages_group / _build_messages_private, 驗證 stale Bry 訊息被過濾掉
"""
import re
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
    """驗證現況 (修法 7+9 已落地) — proxy.py 有過濾 stale Bry user 訊息"""

    def setUp(self):
        self.fake_group_history = _make_group_history()
        self.fake_private_history = _make_private_history()
        self.soul_text = "你是 Miku。... (Miku 的人格設定) ..."

    def test_a_baseline_group_stale_bry_injected(self):
        """現況 (修法 7+9, proxy.py L895-900): group history 內 stale Bry 訊息**被過濾**
        模擬 miku 8/4 07:30 觸發, Bry 4h42m 前講過一句
        證據: `_build_messages_group` L715 `bry_online = _is_bry_online(bry_latest_ts, now)`,
              L895-899 `if m["speaker"] == "bryan": if not bry_online: continue`
        強化: 同時驗證 Bry 在線時**保留** —— 只斷言丟 Bry 擋不住無差別過濾的壞實作。
        """
        def _build(bry_latest_ts):
            fake_memory = MagicMock()
            fake_memory.get_group_history.return_value = self.fake_group_history
            return proxy._build_messages_group(
                agent_id="agent_miku",
                soul=self.soul_text,
                current_input="",  # proactive 觸發
                memory_context="",
                memory=fake_memory,
                mood=0.0,
                user_id="bryan",
                current_time="2026-08-04 07:30 (EDT)",
                bry_latest_ts=bry_latest_ts,
            )

        with patch.object(proxy, "_load_bry_recent", return_value=[]), \
             patch.object(proxy, "MAX_GROUP", 20), \
             patch("time.time", return_value=NOW):
            # 情況 1: Bry 不在線 (跨 session 最後訊息 4h42m 前 > STALE_THRESHOLD_SEC 1800s)
            offline_msgs = _build(BRY_GROUP_STALE_TS)
            offline_users = [m["content"] for m in offline_msgs if m["role"] == "user"]
            self.assertNotIn(
                "Bry 8/4 02:48 講的話 (stale)", offline_users,
                f"現況: Bry 不在線時 stale Bry 訊息應該被過濾 (修法 7, proxy.py L895-899), "
                f"實際 user msgs: {offline_users}"
            )
            # 其他角色的訊息不受影響 (過濾只針對 Bry, 不是整段 history 丟掉)
            offline_system = "\n".join(
                m["content"] for m in offline_msgs if m["role"] == "system"
            )
            self.assertIn(
                "yua 8/4 07:25 講的話 (fresh)", offline_system,
                "現況: 過濾應該只針對 Bry user 訊息, 其他角色的上下文要保留"
            )

            # 情況 2: Bry 在線 (60 秒前 < 1800s) → Bry 那條 user 訊息**必須保留**
            online_msgs = _build(NOW - 60)
            online_users = [m["content"] for m in online_msgs if m["role"] == "user"]
            self.assertIn(
                "Bry 8/4 02:48 講的話 (stale)", online_users,
                f"現況: Bry 在線時不該過濾他的訊息 (修法 7 只是在線判定, proxy.py L898), "
                f"實際 user msgs: {online_users}"
            )
        print("[v1 校準] group: Bry 不在線 → stale Bry 訊息被過濾; Bry 在線 → 保留")

    def test_b_baseline_private_stale_bry_injected(self):
        """現況 (修法 7+9 + 2026-09-01 工單): private history 內 Bry user 訊息**被過濾**
        模擬 mahiru 8/4 07:30 觸發, Bry 3.5 天前講過一句
        證據: `_build_messages_private` L1467 改用 `get_recent_with_meta` (拿 timestamp),
              L1470 `bry_online = _is_bry_online(bry_latest_ts, now)`,
              L1513 `if m.get("role") == "user" and (reason != "user_message" or not bry_online): continue`
        強化: Bry 在線且 reason=user_message 時**保留** (擋掉無差別過濾)。
        """
        def _build(bry_latest_ts, reason):
            fake_memory = MagicMock()
            # 修法 7: 改用 get_recent_with_meta (proxy.py L1467), 需要 timestamp
            fake_memory.get_recent_with_meta.return_value = self.fake_private_history
            return proxy._build_messages_private(
                agent_id="agent_mahiru",
                soul=self.soul_text,
                current_input="",  # proactive 觸發
                memory_context="",
                memory=fake_memory,
                mood=0.0,
                user_id="bryan",
                current_time="2026-08-04 07:30 (EDT)",
                bry_latest_ts=bry_latest_ts,
                reason=reason,
            )

        with patch("time.time", return_value=NOW):
            # 情況 1: Bry 不在線 (3.5 天前 > 1800s) → Bry 那條 user 訊息不該出現
            offline_msgs = _build(BRY_PRIVATE_STALE_TS, "proactive_dm")
            offline_users = [m["content"] for m in offline_msgs if m["role"] == "user"]
            self.assertNotIn(
                "Bry 8/2 07:17 講的話 (stale)", offline_users,
                f"現況: Bry 不在線時 private 內 stale Bry 訊息應該被過濾 "
                f"(修法 7, proxy.py L1513-1519), 實際 user msgs: {offline_users}"
            )
            # assistant 歷史要留著當「自己最近說過什麼」參考
            offline_assistants = [
                m["content"] for m in offline_msgs if m["role"] == "assistant"
            ]
            self.assertIn(
                "mahiru 8/4 07:25 講的話 (fresh)", offline_assistants,
                "現況: 過濾只丟 Bry user 訊息, assistant 歷史要保留 (proxy.py L1513 只擋 role=user)"
            )

            # 情況 2: Bry 在線 + Bry 主動來的回合 → Bry 訊息**必須保留**
            online_msgs = _build(NOW - 60, "user_message")
            online_users = [m["content"] for m in online_msgs if m["role"] == "user"]
            self.assertIn(
                "Bry 8/2 07:17 講的話 (stale)", online_users,
                f"現況: Bry 在線且 reason=user_message 時不該過濾 "
                f"(proxy.py L1513 條件), 實際 user msgs: {online_users}"
            )
        print("[v1 校準] private: Bry 不在線 → stale Bry 訊息被過濾; Bry 在線 → 保留")

    def test_c_baseline_proxy_has_no_stale_threshold_constant(self):
        """現況 (修法 7): proxy 有 STALE_THRESHOLD_SEC 且值正確 (= 30 分鐘 = 1800s)
        證據: proxy.py L93-100 定義, 註解明說跟 proactive_dm silence_timeout 對齊。
        強化: 直接用 `_is_bry_online` 打邊界 (1799 在線 / 1801 不在線),
        這樣「常數存在但沒接上」或「值被改錯」都會紅。
        """
        self.assertTrue(
            hasattr(proxy, "STALE_THRESHOLD_SEC"),
            "現況: proxy 應該有 STALE_THRESHOLD_SEC 常數 (修法 7, proxy.py L100)"
        )
        self.assertEqual(
            proxy.STALE_THRESHOLD_SEC, 30 * 60,
            f"現況: STALE_THRESHOLD_SEC 應該是 30 分鐘 (1800s), "
            f"實際 = {proxy.STALE_THRESHOLD_SEC}"
        )
        # 邊界行為: 閾值內 = 在線, 閾值外 = 不在線
        self.assertTrue(
            proxy._is_bry_online(NOW - (proxy.STALE_THRESHOLD_SEC - 1), NOW),
            "1799 秒前 Bry 講過話 → 應該判為在線"
        )
        self.assertFalse(
            proxy._is_bry_online(NOW - (proxy.STALE_THRESHOLD_SEC + 1), NOW),
            "1801 秒前 Bry 講過話 → 應該判為不在線"
        )
        self.assertFalse(
            proxy._is_bry_online(0, NOW),
            "bry_latest_ts=0 (Bry 從未跟這個 agent 講過話) → 不在線 (proxy.py L944-945)"
        )
        print(f"[v1 校準] proxy.STALE_THRESHOLD_SEC = {proxy.STALE_THRESHOLD_SEC}s, 邊界行為正確")

    def test_d_baseline_proxy_source_no_stale_filter(self):
        """現況 (修法 7+9): proxy.py source 內**有** _is_bry_online / STALE_THRESHOLD_SEC,
        而且兩個 builder 都真的呼叫它 (不是定義了擱著)
        證據: proxy.py L919 `def _is_bry_online`, L946 用 STALE_THRESHOLD_SEC,
              L715 (group) / L1470 (private) 兩處 `bry_online = _is_bry_online(...)`
        """
        proxy_source = Path(proxy.__file__).read_text(encoding="utf-8")
        self.assertIn(
            "def _is_bry_online(", proxy_source,
            "現況: proxy 應該有 _is_bry_online helper (修法 7+9, proxy.py L919)"
        )
        self.assertIn(
            "STALE_THRESHOLD_SEC", proxy_source,
            "現況: proxy 應該有 STALE_THRESHOLD_SEC 常數 (修法 7, proxy.py L100)"
        )
        # 強化: helper 必須被兩個 builder 各呼叫一次, 否則定義就是死的
        call_sites = re.findall(r"bry_online\s*=\s*_is_bry_online\(", proxy_source)
        self.assertEqual(
            len(call_sites), 2,
            "現況: _is_bry_online 應該在 _build_messages_group 與 _build_messages_private "
            f"各被呼叫一次 (proxy.py L715 / L1470), 實際 = {len(call_sites)} 次"
        )
        print("[v1 校準] proxy 有 _is_bry_online / STALE_THRESHOLD_SEC, 且兩個 builder 都接上")


if __name__ == "__main__":
    unittest.main(verbosity=2)
